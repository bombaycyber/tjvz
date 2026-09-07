"""tjvz CLI — argument parsing, dispatch, and _emit().

Every subcommand builds a plain dict; `_emit()` renders it once (human text
from the `_text` key, or the whole dict as JSON with `--json`), so the two
output modes can't drift.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from tjvz import __version__
from tjvz import paths
from tjvz.paths import Profile, ProfileError, resolve_profile
from tjvz.schema import SchemaError, validate

CONFIG_STUB = """\
# tjvz profile config. The fully-commented version is examples/profile/config.yaml
# in the repo. Every value has a default.
schema: 1
id_scheme: 1

model:
  scoring:
    features:
      relevance:          { weight: 3.0, params: { topk: 3 } }
      author_avg_rating:  { weight: 0.4 }
      pub_avg_rating:     { weight: 0.2 }
      author_familiarity: { weight: -0.5, pinned: true }
      pub_familiarity:    { weight: -0.25, pinned: true }
      reader_recs:        {}
    bias: 0.0
  learning:
    utility: log_odds
    label: { positive: [read, stack] }
    trainable: weights
    regularization: { lambda0: 1.0, anneal_n0: 50 }
    adopt: { min_pos: 20, min_neg: 20, cv_folds: 5, metric: log_loss }

feed:
  limit: 10

zotero:
  collections_as_tags: true

broadcast: { title: "", description: "", link: "", share_bodies: false }
"""

SUBS_STUB = """\
# RSS / Atom feeds. `tjvz sub add <url>` appends here (prompting for kind +
# persist). Field docs: examples/profile/subscriptions.yaml in the repo.
feeds: []
"""

# subcommands that exist but aren't wired yet
_PLANNED = {
    "explain": "M4", "dedup": "later", "migrate": "later",
}


def _clip(s, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps({k: v for k, v in result.items() if k != "_text"},
                         ensure_ascii=False, indent=2))
    elif result.get("_text") is not None:
        print(result["_text"])
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


# --- commands --------------------------------------------------------------------

def cmd_init(args) -> dict:
    root = Path(args.dir or ".").expanduser().resolve()
    marker = root / paths.MARKER
    if marker.is_file() and not args.force:
        raise SystemExit(f"{root} is already a tjvz profile (use --force to re-stub)")
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "schema": 1, "id_scheme": 1,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2))
    p = Profile(root)
    for path, stub in ((p.config, CONFIG_STUB), (p.subscriptions, SUBS_STUB)):
        if not path.exists() or args.force:
            path.write_text(stub)
    for d in (p.archive_dir, p.trash_dir, p.events_dir, p.inbox):
        d.mkdir(parents=True, exist_ok=True)
    name = args.name or root.name
    paths.register(name, root, make_current=True)
    return {
        "_text": f"initialised profile {name!r} at {root}\n"
                 f"  config.yaml  subscriptions.yaml  archive/ trash/ events/ inbox/\n"
                 f"  registered as current — `tjvz feed` here or from any subdir",
        "name": name, "root": str(root),
    }


def cmd_profile(args) -> dict:
    if args.action == "list":
        profiles, current = paths.list_profiles()
        lines = [f"{'*' if n == current else ' '} {n:<16} {pth}" for n, pth in sorted(profiles.items())]
        return {"_text": "\n".join(lines) or "(no profiles registered)",
                "profiles": profiles, "current": current}
    if args.action == "use":
        paths.set_current(args.name)
        return {"_text": f"current profile -> {args.name}", "current": args.name}
    if args.action == "remove":
        paths.unregister(args.name)
        return {"_text": f"unregistered {args.name!r} (its directory is untouched)", "removed": args.name}
    raise SystemExit("usage: tjvz profile {list|use NAME|remove NAME}")


def cmd_planned(args) -> dict:
    raise SystemExit(f"`tjvz {args._name}` is not implemented yet (planned for {_PLANNED[args._name]})")


# --- ingest -------------------------------------------------------------------

def _ingest(profile, items_and_streams, dry_run: bool) -> dict:
    """items_and_streams: iterable of (canonical_item, stream). Dedupe by id
    against archive + trash + stack; route by stream. Returns a report."""
    from tjvz import store

    p = profile
    seen = {r["id"] for r in store.iter_records(p.archive_dir, p.trash_dir, kind="item")}
    stack = store.load_stack(p)
    seen |= {r["id"] for r in stack}

    new = skipped = 0
    added, touched_stack = [], False
    for item, stream in items_and_streams:
        validate(item, "item")
        if item["id"] in seen:
            skipped += 1
            continue
        seen.add(item["id"])
        new += 1
        added.append({"id": item["id"], "stream": stream, "title": item["title"][:72]})
        if dry_run:
            continue
        if stream == "archive":
            store.append_jsonl(p.shard("archive", store.ym_of(item.get("read"))), item)
        elif stream == "stack":
            stack.append(item)
            touched_stack = True
        else:  # feed candidate
            store.append_jsonl(p.feeds_cache / "ingest.jsonl", item)
    if touched_stack:
        store.save_stack(p, stack)
    return {"new": new, "skipped": skipped, "added": added, "dry_run": dry_run}


def _report_text(kind: str, r: dict) -> str:
    head = f"{kind}: {r['new']} new, {r['skipped']} already present" + (" (dry run)" if r["dry_run"] else "")
    lines = [f"  [{a['stream']:<7}] {a['title']}" for a in r["added"][:40]]
    if len(r["added"]) > 40:
        lines.append(f"  … and {len(r['added']) - 40} more")
    return "\n".join([head, *lines])


def cmd_import_zotero(args) -> dict:
    from tjvz import config
    from tjvz.canon import canonicalise
    from tjvz.sources.zotero import ZoteroSource

    p = args.profile_obj
    cfg = config.load(p)
    src = ZoteroSource(
        library=args.library or (cfg["zotero"].get("library")),
        collections_as_tags=cfg["zotero"]["collections_as_tags"],
        import_all=args.all,
    )
    pairs = (canonicalise(src.to_item(raw)) for raw in src.pull())
    r = _ingest(p, pairs, args.dry_run)
    r["_text"] = _report_text(f"import-zotero ({src.library_name})", r)
    return r


def cmd_ingest(args) -> dict:
    from tjvz.canon import canonicalise
    from tjvz.sources.jsonfile import JsonFileSource

    p = args.profile_obj
    src = JsonFileSource(p.inbox)
    processed, errors = [], []
    pairs = []
    for raw in src.pull():
        if raw.get("_error"):
            errors.append({"path": raw["_path"], "error": raw["_error"]})
            continue
        try:
            pairs.append(canonicalise(src.to_item(raw)))
            processed.append(raw["_path"])
        except ValueError as e:
            errors.append({"path": raw.get("_path", "?"), "error": str(e)})
    r = _ingest(p, iter(pairs), args.dry_run)
    if not args.dry_run:
        p.inbox_done.mkdir(parents=True, exist_ok=True)
        for path in processed:
            src_path = Path(path)
            src_path.rename(p.inbox_done / src_path.name)
    r["errors"] = errors
    r["_text"] = _report_text("ingest", r) + (
        "".join(f"\n  ! {e['path']}: {e['error']}" for e in errors) if errors else "")
    return r


def cmd_add(args) -> dict:
    from tjvz.canon import canonicalise

    p = args.profile_obj
    raw = {
        "url": args.url, "title": args.title, "publication": args.publication,
        "format": args.format, "tags": args.tag or [], "stream": args.stream,
        "source": {"kind": "manual"},
    }
    if args.author:
        raw["authors"] = args.author
    if args.stream == "archive" and args.rating:
        raw["rating"] = args.rating
    r = _ingest(p, iter([canonicalise(raw)]), dry_run=False)
    r["_text"] = _report_text("add", r)
    return r


# --- rss: fetch / sub ------------------------------------------------------------

def _slug(url: str, existing: list[str]) -> str:
    import re
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "feed").lower()
    for pre in ("www.", "m.", "feeds.", "rss."):
        host = host[len(pre):] if host.startswith(pre) else host
    base = re.sub(r"[^a-z0-9-]+", "-", host.split(".")[0]).strip("-") or "feed"
    s, i = base, 2
    while s in existing:
        s, i = f"{base}-{i}", i + 1
    return s


def _parse_persist(v: str):
    v = str(v).strip().lower()
    if v == "forever":
        return "forever"
    if v.isdigit():
        return int(v)
    raise SystemExit("persist must be 'forever', a number of days, or 0")


def cmd_fetch(args) -> dict:
    from tjvz import config
    from tjvz.sources.rss import RssSource

    p = args.profile_obj
    feeds = config.load_subscriptions(p)
    if args.feed:
        feeds = [f for f in feeds if f["id"] == args.feed]
        if not feeds:
            raise SystemExit(f"no subscription with id {args.feed!r}")

    src = RssSource()
    reports = []
    for f in feeds:
        if f.get("paused") and not args.feed:
            reports.append({"feed": f["id"], "status": "paused", "count": 0})
            continue
        reports.append(src.fetch(f, p.feeds_cache / f"{f['id']}.json", force=args.force))

    lines = []
    for r in reports:
        tail = f" ({r['new']} new)" if r.get("new") else ""
        err = f"  — {r['error']}" if r.get("error") else ""
        lines.append(f"  {r['feed']:<18} {r['status']:<13} {r['count']} items{tail}{err}")
    return {"_text": "\n".join(lines) or "(no subscriptions — `tjvz sub add <url>`)", "feeds": reports}


def cmd_sub(args) -> dict:
    from tjvz import config
    from tjvz.sources.rss import _UA, _http_get, parse
    from tjvz.store import update_yaml_list

    p = args.profile_obj
    feeds = config.load_subscriptions(p) if p.subscriptions.exists() else []

    if args.action == "list":
        lines = [
            f"  {f['id']:<18} {f['kind']:<11} persist={str(f['persist']):<8} {f['url']}"
            + ("  [paused]" if f.get("paused") else "")
            for f in feeds
        ]
        return {"_text": "\n".join(lines) or "(no subscriptions)", "feeds": feeds}

    if args.action == "remove":
        kept = [f for f in feeds if f["id"] != args.value]
        if len(kept) == len(feeds):
            raise SystemExit(f"no subscription with id {args.value!r}")
        update_yaml_list(p.subscriptions, "feeds", kept)
        return {"_text": f"removed {args.value}", "removed": args.value}

    # add
    url = args.value
    if not url:
        raise SystemExit("usage: tjvz sub add <url> [--kind K --persist P]")
    try:
        body, _e, _l = _http_get(url, {"User-Agent": _UA, "Accept-Encoding": "gzip"})
        channel, entries = parse(body)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"could not fetch {url}: {e}")

    kind, persist = args.kind, args.persist
    if kind is None or persist is None:
        if not sys.stdin.isatty():
            raise SystemExit("pass --kind and --persist (non-interactive)")
        sample = entries[0]["title"][:64] if entries else "(no items)"
        print(f"  {channel.get('title') or url}  —  {len(entries)} items;  e.g. {sample}")
        while kind not in ("publication", "author", "reader", "topic"):
            kind = input("  kind [publication/author/reader/topic]: ").strip().lower()
        while persist is None:
            v = input("  persist [forever / <days> / 0]: ").strip().lower()
            persist = "forever" if v == "forever" else (int(v) if v.isdigit() else None)
    else:
        persist = _parse_persist(persist)

    fid = args.id or _slug(url, [f["id"] for f in feeds])
    entry = {"id": fid, "url": url, "kind": kind, "persist": persist}
    if args.apply_tags:
        entry["apply_tags"] = args.apply_tags
    feeds.append(entry)
    if not p.subscriptions.exists():
        p.subscriptions.write_text("feeds: []\n")
    update_yaml_list(p.subscriptions, "feeds", feeds)
    return {
        "_text": f"added {fid} ({kind}, persist={persist}) — {channel.get('title')}, {len(entries)} items",
        "feed": entry,
    }


# --- feed loop: feed / promote / dismiss / read / rate / stack -----------------

def _ranked_row(i: int, r) -> str:
    it = r.item
    terms = r.explanation["terms"]
    top = next((t for t in terms if t["contribution"]), None)
    reason = f"{top['feature']} {top['contribution']:+.2f}" if top else "—"
    mark = " ·new" if r.is_new else ""
    return (f"  {i:>2}  {r.score:+7.2f}  {_clip(it['title'], 44):<44}  "
            f"{_clip(it['publication'], 16):<16}  {reason}{mark}   {it['id']}")


def cmd_feed(args) -> dict:
    from tjvz import config, rank

    p = args.profile_obj
    cfg = config.load(p)
    res = rank.rank_feed(p, cfg)
    rank.reconcile_manifest(p, res)

    limit = args.limit or cfg["feed"]["limit"]
    floor = cfg["feed"].get("min_score")
    eligible = res.ranked if (args.all or floor is None) else [r for r in res.ranked if r.score >= floor]
    shown = eligible if args.all else eligible[:limit]
    held = len(res.ranked) - len(shown)

    lines = [_ranked_row(i, r) for i, r in enumerate(shown, 1)]
    text = "\n".join(lines) or "(nothing in the feed — `tjvz fetch` first)"
    if held > 0 and not args.all:
        text += f"\n  … {held} more not shown (--all)"
    return {
        "_text": text,
        "feed": [{"rank": i, "id": r.item["id"], "score": round(r.score, 4),
                  "title": r.item["title"], "publication": r.item["publication"],
                  "url": r.item.get("url"), "format": r.item["format"], "tags": r.item["tags"],
                  "is_new": r.is_new, "first_seen": r.first_seen,
                  "terms": r.explanation["terms"]}
                 for i, r in enumerate(shown, 1)],
        "held": held, "total": len(res.ranked), "expired": sorted(res.expired),
    }


def cmd_judge(args) -> dict:
    from tjvz import config, rank

    p = args.profile_obj
    r = rank.judge(p, config.load(p), args.ref, args._decision,
                   rating=getattr(args, "rating", None), blurb=getattr(args, "blurb", None))
    verb = {"promote": "promoted", "dismiss": "dismissed", "read": "read"}[args._decision]
    where = f" from {r['from']}" if r["from"] and r["from"] != "feed" else ""
    seat = f" (was rank {r['rank']}, score {r['score']:+.2f})" if r["rank"] else ""
    extra = f", rated {args.rating}/5" if getattr(args, "rating", None) else ""
    r["_text"] = f"{verb}{where}: {r['title']}{extra}{seat}"
    return r


def cmd_rate(args) -> dict:
    from tjvz import rank

    p = args.profile_obj
    r = rank.rate(p, args.ref, args.rating)
    r["_text"] = f"rated {r['rating']}/5: {r['title']}"
    return r


def cmd_blurb(args) -> dict:
    from tjvz import rank

    r = rank.set_blurb(args.profile_obj, args.ref, args.text)
    r["_text"] = f"blurb set — {r['title']}"
    return r


def cmd_broadcast(args) -> dict:
    from tjvz import broadcast, config

    p = args.profile_obj
    return broadcast.run(p, config.load(p), out=args.out,
                         publish=args.publish, dry_run=args.dry_run)


def cmd_stack(args) -> dict:
    from tjvz import store

    p = args.profile_obj
    items = store.load_stack(p)
    lines = [f"  {i:>2}  {_clip(it['title'], 50):<50}  {_clip(it['publication'], 18):<18}   {it['id']}"
             for i, it in enumerate(items, 1)]
    return {"_text": "\n".join(lines) or "(the stack is empty — `tjvz promote <id>`)",
            "stack": [{"id": it["id"], "title": it["title"], "publication": it["publication"]}
                      for it in items]}


def cmd_archive(args) -> dict:
    from tjvz import store

    p = args.profile_obj
    items = list(store.iter_records(p.archive_dir, kind="item"))
    items.sort(key=lambda it: it.get("read") or "", reverse=True)
    if args.limit:
        items = items[: args.limit]

    def _row(i, it):
        stars = "★" * (it.get("rating") or 0)
        return (f"  {i:>3}  {(it.get('read') or '')[:10]:<10}  {stars:<5}  "
                f"{_clip(it['title'], 46):<46}  {_clip(it['publication'], 16):<16}   {it['id']}")

    lines = [_row(i, it) for i, it in enumerate(items, 1)]
    return {
        "_text": "\n".join(lines) or "(the archive is empty — `tjvz read <id>` or `tjvz import-zotero`)",
        "archive": [
            {"id": it["id"], "title": it["title"], "authors": it["authors"],
             "publication": it["publication"], "url": it.get("url"), "format": it["format"],
             "tags": it["tags"], "read": it.get("read"), "rating": it.get("rating"),
             "blurb": it.get("blurb"), "source": it.get("source", {}).get("kind")}
            for it in items
        ],
        "total": len(items),
    }


# --- model learn / status · verify · features ---------------------------------

def cmd_model(args) -> dict:
    from tjvz import config, learn

    p = args.profile_obj
    cfg = config.load(p)
    if args.action == "status":
        s = learn.status(p, cfg)
        head = f"source: {s['source']}" + ("" if not s["has_model_json"] else
               f"  (learned {s['learned_at']})")
        wl = "\n".join(f"    {k:<22} {v:+.4f}" for k, v in sorted(s["weights"].items()))
        stale = "\n  STALE — features changed since this fit; `tjvz model learn`" if s["stale"] else ""
        s["_text"] = f"{head}\n  bias {s.get('bias', 0.0):+.4f}\n{wl}{stale}"
        return s

    r = learn.learn(p, cfg, adopt=args.adopt, rebuild=args.rebuild)
    gates = "clear" if r["adopt_ready"] else "; ".join(r["blockers"])
    verdict = ("adopted → model.json" if r["adopted"]
               else r.get("refused") or ("would adopt (pass --adopt)" if r["adopt_ready"]
               else "not adopting"))
    wl = "\n".join(f"    {k:<22} {v:+.4f}" for k, v in sorted(r["weights"].items()))
    miss = f"\n  {len(r['missing_bodies'])} events skipped (item body gone)" if r["missing_bodies"] else ""
    r["_text"] = (
        f"  {r['n_events']} events · {r['n_pos']} pos / {r['n_neg']} neg{miss}\n"
        f"  CV log-loss {r['cv_logloss']:.4f}  vs prior {r['cv_logloss_prior']:.4f}  "
        f"({'beats prior' if r['beats_prior'] else 'no improvement'})\n"
        f"  adopt gates: {gates}\n"
        f"  bias {r['bias']:+.4f}\n{wl}\n  → {verdict}"
    )
    return r


def _maybe_profile(args):
    try:
        return resolve_profile(args.profile, args.home)
    except ProfileError:
        return None


def cmd_verify(args) -> dict:
    from tjvz import config, verify

    p = _maybe_profile(args)
    cfg = config.load(p) if p is not None else None
    res = verify.verify(p, cfg)
    if not res["ok"]:
        res["_text"] += "\n  verify FAILED"
    return res


def cmd_features(args) -> dict:
    from tjvz import config, features, learn

    features.load_all()
    if args.action == "recompute":
        p = resolve_profile(args.profile, args.home)
        n = learn.recompute(p)
        return {"_text": f"cleared {n} feature-cache file(s) — next `tjvz model learn` rebuilds",
                "removed": n}

    p = _maybe_profile(args)
    fcfg = config.load(p)["model"]["scoring"]["features"] if p is not None else {}
    rows = []
    for fid, cls in sorted(features.REGISTRY.items()):
        m = cls.MANIFEST
        active = fid in fcfg
        rows.append({"id": fid, "version": m["version"], "expands": bool(m.get("expands")),
                     "active": active, "needs": m.get("needs", [])})
    lines = [f"  {'*' if r['active'] else ' '} {r['id']:<22} v{r['version']}"
             f"{'  (group)' if r['expands'] else ''}   needs: {', '.join(r['needs'])}"
             for r in rows]
    fp = learn.fingerprint(fcfg) if fcfg else None
    return {"_text": "\n".join(lines) + (f"\n  fingerprint: {fp}" if fp else ""),
            "features": rows, "fingerprint": fp}


# --- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tjvz", description="a local, interpretable reading recommender")
    ap.add_argument("--version", action="version", version=f"tjvz {__version__}")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--profile", metavar="NAME", help="use this registered profile")
    ap.add_argument("--home", metavar="PATH", help="use the profile at this path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="create a profile in a directory")
    pi.add_argument("dir", nargs="?", help="target directory (default: cwd)")
    pi.add_argument("--name", help="registry name (default: directory name)")
    pi.add_argument("--force", action="store_true")
    pi.set_defaults(_fn=cmd_init, _needs_profile=False)

    pp = sub.add_parser("profile", help="manage the profile registry")
    pp.add_argument("action", choices=["list", "use", "remove"])
    pp.add_argument("name", nargs="?")
    pp.set_defaults(_fn=cmd_profile, _needs_profile=False)

    pz = sub.add_parser("import-zotero", help="import a Zotero library into the archive")
    pz.add_argument("--library", help="path to the Zotero data dir or zotero.sqlite")
    pz.add_argument("--all", action="store_true", help="import the whole library, not just read items")
    pz.add_argument("--dry-run", action="store_true")
    pz.set_defaults(_fn=cmd_import_zotero, _needs_profile=True)

    pg = sub.add_parser("ingest", help="drain inbox/*.json into the profile")
    pg.add_argument("--dry-run", action="store_true")
    pg.set_defaults(_fn=cmd_ingest, _needs_profile=True)

    pa = sub.add_parser("add", help="add one item from flags")
    pa.add_argument("--url")
    pa.add_argument("--title", required=True)
    pa.add_argument("--author", action="append", metavar="NAME")
    pa.add_argument("--publication")
    pa.add_argument("--format", default="essay")
    pa.add_argument("--tag", action="append", metavar="TAG")
    pa.add_argument("--stream", choices=["feed", "stack", "archive"], default="feed")
    pa.add_argument("--rating", type=int, choices=range(1, 6))
    pa.set_defaults(_fn=cmd_add, _needs_profile=True)

    pf = sub.add_parser("fetch", help="fetch subscribed feeds into the cache")
    pf.add_argument("--feed", metavar="ID", help="just this one (also fetches it when paused)")
    pf.add_argument("--force", action="store_true", help="ignore ETag / If-Modified-Since")
    pf.set_defaults(_fn=cmd_fetch, _needs_profile=True)

    psb = sub.add_parser("sub", help="manage RSS / Atom subscriptions")
    psb.add_argument("action", choices=["list", "add", "remove"])
    psb.add_argument("value", nargs="?", help="url (add) or id (remove)")
    psb.add_argument("--id")
    psb.add_argument("--kind", choices=["publication", "author", "reader", "topic"])
    psb.add_argument("--persist", help="'forever', a number of days, or 0")
    psb.add_argument("--apply-tags", action="append", metavar="TAG", dest="apply_tags")
    psb.set_defaults(_fn=cmd_sub, _needs_profile=True)

    pfd = sub.add_parser("feed", help="show the ranked feed (and reconcile the manifest)")
    pfd.add_argument("--all", action="store_true", help="show every candidate, not just the top slice")
    pfd.add_argument("--limit", type=int, metavar="N", help="how many to show (default: config feed.limit)")
    pfd.set_defaults(_fn=cmd_feed, _needs_profile=True)

    for name, dec, helptext in (
        ("promote", "promote", "move an item onto the stack"),
        ("dismiss", "dismiss", "send an item to the trash (a negative signal)"),
        ("read", "read", "mark an item read → archive"),
    ):
        pj = sub.add_parser(name, help=helptext)
        pj.add_argument("ref", help="item id, id prefix, or url")
        if name == "read":
            pj.add_argument("--rating", type=int, choices=range(1, 6), help="1–5, optional")
            pj.add_argument("--blurb", metavar="TEXT", help="your one-line take (for the broadcast feed)")
        pj.set_defaults(_fn=cmd_judge, _needs_profile=True, _decision=dec)

    prt = sub.add_parser("rate", help="set your 1–5 rating on an archived item")
    prt.add_argument("ref", help="item id, id prefix, or url")
    prt.add_argument("rating", type=int, choices=range(1, 6))
    prt.set_defaults(_fn=cmd_rate, _needs_profile=True)

    pbl = sub.add_parser("blurb", help="set your take on an archived item (for broadcast)")
    pbl.add_argument("ref", help="item id, id prefix, or url")
    pbl.add_argument("text")
    pbl.set_defaults(_fn=cmd_blurb, _needs_profile=True)

    pst = sub.add_parser("stack", help="list the stack")
    pst.set_defaults(_fn=cmd_stack, _needs_profile=True)

    parc = sub.add_parser("archive", help="list archived (read) items, newest first")
    parc.add_argument("--limit", type=int, metavar="N", help="show only the N most recent")
    parc.set_defaults(_fn=cmd_archive, _needs_profile=True)

    pbc = sub.add_parser("broadcast", help="write / publish an Atom feed of your archive")
    pbc.add_argument("--out", metavar="PATH", help="output file (default: <profile>/broadcast/atom.xml)")
    pbc.add_argument("--publish", action="store_true", help="run broadcast.publish_cmd after writing")
    pbc.add_argument("--dry-run", action="store_true", help="report what would be included, write nothing")
    pbc.set_defaults(_fn=cmd_broadcast, _needs_profile=True)

    pm = sub.add_parser("model", help="learn / inspect the ranking model")
    pm.add_argument("action", choices=["learn", "status"])
    pm.add_argument("--adopt", action="store_true", help="write model.json if the gates clear")
    pm.add_argument("--rebuild", action="store_true", help="ignore the feature cache")
    pm.set_defaults(_fn=cmd_model, _needs_profile=True)

    pv = sub.add_parser("verify", help="run the golden checks + profile sanity")
    pv.set_defaults(_fn=cmd_verify, _needs_profile=False)

    pft = sub.add_parser("features", help="list features / drop the feature cache")
    pft.add_argument("action", nargs="?", choices=["list", "recompute"], default="list")
    pft.set_defaults(_fn=cmd_features, _needs_profile=False)

    for name in _PLANNED:
        sp = sub.add_parser(name, help=f"(planned — {_PLANNED[name]})")
        sp.add_argument("rest", nargs=argparse.REMAINDER)
        sp.set_defaults(_fn=cmd_planned, _needs_profile=False, _name=name)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "_needs_profile", False):
            args.profile_obj = resolve_profile(args.profile, args.home)
        result = args._fn(args)
    except (ProfileError, ValueError, SchemaError, RuntimeError) as e:
        print(f"tjvz: {e}", file=sys.stderr)
        return 1
    _emit(result, args.json)
    return 1 if result.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
