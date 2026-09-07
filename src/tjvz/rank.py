"""The feed loop — score candidates, order them, keep the manifest honest,
and record the judgements you make.

`rank_feed` scores every item in the `.tjvz/feeds/` cache (RSS pulls +
inbox drops) against the live model, drops the suppressed
(`archive ∪ stack ∪ trash`) and the expired (`persist: <N>` days elapsed),
and sorts. `reconcile_manifest` then writes `feed/manifest.jsonl` so a
surfaced-but-unjudged item survives the publisher rotating it out.

`judge` moves an item between stores (feed → stack / trash / archive),
appends the `events/` row (freezing the rank + score it was judged at), and
drops it from the manifest. Suppression is rebuilt lazily on the next read.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from tjvz import config, ctx, features, store, suppress
from tjvz.ident import canonical_url, item_id
from tjvz.model import LinearModel

_FOREVER = "forever"


# --- candidates -------------------------------------------------------------

def _load_candidates(profile) -> dict[str, dict]:
    """id -> item, merged across every `.tjvz/feeds/*.json` cache plus the
    inbox `ingest.jsonl`. A `kind: reader` feed's `readers[]` entry is
    unioned onto an item that also came through a publication feed."""
    cands: dict[str, dict] = {}
    fc = profile.feeds_cache
    if fc.is_dir():
        for f in sorted(fc.glob("*.json")):
            for it in store.load_json(f, {}).get("items", []):
                _merge(cands, it)
    for it in store.read_jsonl(fc / "ingest.jsonl"):
        _merge(cands, it)
    return cands


def _merge(cands: dict[str, dict], it: dict) -> None:
    cur = cands.get(it["id"])
    if cur is None:
        cands[it["id"]] = dict(it)
        return
    by = {r["source"]: r for r in cur.get("readers") or []}
    for r in it.get("readers") or []:
        by.setdefault(r["source"], r)
    if by:
        cur["readers"] = list(by.values())


# --- persistence / expiry --------------------------------------------------

def _persist_of(item: dict, subs: dict) -> object:
    fid = (item.get("source") or {}).get("feed_id")
    if fid and fid in subs:
        return subs[fid].get("persist", _FOREVER)
    return _FOREVER  # inbox drops and unknown feeds wait until judged


def _persistable(persist: object) -> bool:
    return persist == _FOREVER or (isinstance(persist, int) and not isinstance(persist, bool) and persist > 0)


def _expired(item: dict, persist: object, now: datetime) -> bool:
    if not (isinstance(persist, int) and not isinstance(persist, bool) and persist > 0):
        return False  # 'forever' and 0 never expire (0 is just never queued)
    try:
        fs = datetime.fromisoformat(str(item.get("first_seen")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if fs.tzinfo is None:
        fs = fs.replace(tzinfo=timezone.utc)
    return now - fs > timedelta(days=persist)


# --- ranking ---------------------------------------------------------------

@dataclass
class Ranked:
    item: dict
    score: float
    explanation: dict
    first_seen: str
    is_new: bool
    persist: object


@dataclass
class FeedResult:
    ranked: list[Ranked]
    expired: set[str] = field(default_factory=set)


def rank_feed(profile, cfg: dict) -> FeedResult:
    subs = {s["id"]: s for s in config.load_subscriptions(profile)}
    cands = _load_candidates(profile)
    suppressed = suppress.suppressed_ids(profile)
    manifest = {e["id"]: e["first_seen"] for e in store.read_jsonl(profile.feed_manifest)}
    now = datetime.now(timezone.utc)

    bundle = ctx.live(profile, cfg)
    model = LinearModel(cfg["model"], store.load_json(profile.model, None))
    fcfg = cfg["model"]["scoring"]["features"]

    ranked: list[Ranked] = []
    expired: set[str] = set()
    for iid, item in cands.items():
        if iid in suppressed:
            continue
        persist = _persist_of(item, subs)
        if _expired(item, persist, now):
            expired.add(iid)
            continue
        feats = features.evaluate(item, bundle, fcfg)
        ranked.append(Ranked(
            item=item,
            score=model.score(feats),
            explanation=model.explain(feats),
            first_seen=manifest.get(iid) or item.get("first_seen") or store.now_iso(),
            is_new=iid not in manifest,
            persist=persist,
        ))
    ranked.sort(key=lambda r: r.score, reverse=True)
    return FeedResult(ranked, expired)


def reconcile_manifest(profile, res: FeedResult) -> None:
    """Persist surfaced-and-unjudged items; drop the judged and the expired.
    Manifest-only entries whose item has rotated out of the cache are left
    in place (that is the whole point of the manifest) until judged."""
    suppressed = suppress.suppressed_ids(profile)
    prior = {e["id"]: e["first_seen"] for e in store.read_jsonl(profile.feed_manifest)}
    keep = {i: fs for i, fs in prior.items() if i not in suppressed and i not in res.expired}
    for r in res.ranked:
        if _persistable(r.persist):
            keep.setdefault(r.item["id"], r.first_seen)
    _write_manifest(profile, keep)


def _write_manifest(profile, entries: dict[str, str]) -> None:
    profile.feed_manifest.parent.mkdir(parents=True, exist_ok=True)
    store.atomic_write(
        profile.feed_manifest,
        "".join(json.dumps({"id": i, "first_seen": fs}, ensure_ascii=False) + "\n"
                for i, fs in entries.items()),
    )


# --- judgement -----------------------------------------------------------------

# state stores win over the (possibly stale) feed cache when an id is in both
_STORES = ("stack", "archive", "trash", "feed")


def _resolve(profile, ref: str) -> tuple[dict | None, str | None]:
    """Find the item a `ref` (full id, id prefix, or url) points at, across
    the stack, the feed cache, the archive, and the trash (in that order)."""
    ref = ref.strip()
    want = ref if ref.startswith(("u:", "t:")) else (
        item_id(canonical_url(ref), "", []) if "://" in ref else None
    )
    pools = {
        "stack": store.load_stack(profile),
        "feed": list(_load_candidates(profile).values()),
        "archive": list(store.iter_records(profile.archive_dir, kind="item")),
        "trash": list(store.iter_records(profile.trash_dir, kind="item")),
    }
    hits: dict[str, tuple[dict, str]] = {}
    for where in _STORES:
        for it in pools[where]:
            iid = it["id"]
            if iid == want or (want and iid.startswith(want)) or (want is None and iid.startswith(ref)):
                hits.setdefault(iid, (it, where))
    if len(hits) > 1:
        raise ValueError(f"{ref!r} is ambiguous: {', '.join(sorted(hits))}")
    return next(iter(hits.values()), (None, None))


def _rank_and_score(profile, cfg: dict, iid: str) -> tuple[int | None, float | None]:
    for i, r in enumerate(rank_feed(profile, cfg).ranked, 1):
        if r.item["id"] == iid:
            return i, round(r.score, 4)
    return None, None


def _clean(item: dict, **set_keys) -> dict:
    out = {k: v for k, v in item.items() if k not in ("read", "dismissed")}
    out.update(set_keys)
    return out


def judge(profile, cfg: dict, ref: str, decision: str, *, rating: int | None = None) -> dict:
    item, where = _resolve(profile, ref)
    if item is None:
        raise ValueError(f"no item matching {ref!r} in the feed, stack, archive, or trash")
    iid = item["id"]
    rank_i, score = _rank_and_score(profile, cfg, iid)
    at = store.now_iso()

    # leave every store this item might currently sit in
    store.remove_record(profile.trash_dir, iid)
    store.remove_record(profile.archive_dir, iid)
    store.save_stack(profile, [s for s in store.load_stack(profile) if s["id"] != iid])

    if decision == "promote":
        store.save_stack(profile, [*store.load_stack(profile), _clean(item)])
    elif decision == "dismiss":
        rec = _clean(item, dismissed=at)
        store.write_record(profile.shard("trash", store.now_ym()), rec, "trash")
    elif decision == "read":
        rec = _clean(item, read=at)
        if rating is not None:
            rec["rating"] = rating
        store.write_record(profile.shard("archive", store.ym_of(at)), rec, "archive")
    else:  # pragma: no cover
        raise ValueError(f"unknown decision {decision!r}")

    _drop_from_manifest(profile, iid)
    suppress.invalidate(profile)
    store.write_record(profile.shard("events", store.now_ym()), {
        "schema_version": store.SCHEMA_VERSION, "id": iid, "decision": decision,
        "at": at, "model": "linear", "rank": rank_i, "score": score,
    }, "events")

    return {"id": iid, "decision": decision, "title": item["title"], "from": where,
            "rank": rank_i, "score": score}


def rate(profile, ref: str, rating: int) -> dict:
    item, where = _resolve(profile, ref)
    if item is None:
        raise ValueError(f"no item matching {ref!r}")
    if where != "archive":
        raise ValueError(f"{item['title']!r} is in the {where}, not the archive — `tjvz read` it first")
    store.update_record(profile.archive_dir, item["id"], {"rating": rating})
    return {"id": item["id"], "title": item["title"], "rating": rating}


def _drop_from_manifest(profile, iid: str) -> None:
    entries = {e["id"]: e["first_seen"]
               for e in store.read_jsonl(profile.feed_manifest) if e["id"] != iid}
    _write_manifest(profile, entries)
