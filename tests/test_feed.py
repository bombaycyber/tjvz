"""M3 — the feed loop: suppression, ranking, expiry, judgement, the manifest.

    python -m pytest tests/test_feed.py    (or)    python tests/test_feed.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
for _p in (str(_SRC), str(Path(__file__).parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tjvz import config, ctx, paths, rank, store, suppress  # noqa: E402
from tjvz.canon import canonicalise  # noqa: E402
from tjvz.cli import main  # noqa: E402


def _profile(tmp: Path) -> paths.Profile:
    paths._REGISTRY = tmp / ".config" / "tjvz" / "profiles.json"
    root = tmp / "p"
    assert main(["init", str(root), "--name", "p"]) == 0
    return paths.Profile(root.resolve())


def _cache_feed(p: paths.Profile, feed_id: str, raws: list[dict]) -> None:
    """Write a .tjvz/feeds/<id>.json cache the way RssSource.fetch would."""
    items = []
    for raw in raws:
        raw = {**raw, "source": {"kind": "rss", "feed_id": feed_id, "feed_url": "x", "guid": raw["url"]}}
        item, _ = canonicalise(raw)
        items.append(item)
    p.feeds_cache.mkdir(parents=True, exist_ok=True)
    store.save_json(p.feeds_cache / f"{feed_id}.json",
                    {"http": {}, "ok": True, "error": None, "channel": {"title": feed_id}, "items": items})


def _subs(p: paths.Profile, feeds: list[dict]) -> None:
    p.subscriptions.write_text("feeds:\n" + "".join(
        f"  - id: {f['id']}\n    url: {f['url']}\n    kind: {f['kind']}\n    persist: {f['persist']}\n"
        for f in feeds
    ))


# --- suppression -------------------------------------------------------------

def test_suppressed_ids_and_cache():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        arc, _ = canonicalise({"title": "Read", "url": "https://e.com/a", "stream": "archive"})
        store.append_jsonl(p.shard("archive", "2026-09"), arc)
        stk, _ = canonicalise({"title": "Stacked", "url": "https://e.com/b"})
        store.save_stack(p, [stk])

        ids = suppress.suppressed_ids(p)
        assert arc["id"] in ids and stk["id"] in ids
        assert p.suppressed.exists()
        assert suppress.suppressed_ids(p) == ids  # served from cache

        tr, _ = canonicalise({"title": "Trashed", "url": "https://e.com/c"})
        store.append_jsonl(p.shard("trash", "2026-09"), tr)
        assert tr["id"] in suppress.suppressed_ids(p)  # cache key moved on


# --- ranking ---------------------------------------------------------------

def test_rank_scores_and_drops_suppressed():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        # archive gives 'ai' relevance signal
        for i in range(3):
            a, _ = canonicalise({"title": f"AI paper {i}", "url": f"https://e.com/ai{i}",
                                 "tags": ["ai", "ml"], "stream": "archive"})
            store.append_jsonl(p.shard("archive", "2026-09"), a)

        _subs(p, [{"id": "blog", "url": "https://b.com/rss", "kind": "publication", "persist": "forever"}])
        _cache_feed(p, "blog", [
            {"title": "New AI result", "url": "https://b.com/ai", "tags": ["ai", "ml"]},
            {"title": "Gardening tips", "url": "https://b.com/garden", "tags": ["gardening"]},
            {"title": "Already read", "url": "https://e.com/ai0", "tags": ["ai"]},  # suppressed
        ])
        cfg = config.load(p)
        res = rank.rank_feed(p, cfg)
        titles = [r.item["title"] for r in res.ranked]
        assert "Already read" not in titles
        assert titles[0] == "New AI result"  # relevance beats the gardening post
        assert res.ranked[0].is_new


def test_expiry_drops_stale_items():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _subs(p, [{"id": "hot", "url": "https://h.com/rss", "kind": "topic", "persist": 7}])
        _cache_feed(p, "hot", [{"title": "Fresh", "url": "https://h.com/1"},
                               {"title": "Stale", "url": "https://h.com/2"}])
        blob = store.load_json(p.feeds_cache / "hot.json", {})
        old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for it in blob["items"]:
            if it["title"] == "Stale":
                it["first_seen"] = old
        store.save_json(p.feeds_cache / "hot.json", blob)

        res = rank.rank_feed(p, config.load(p))
        assert [r.item["title"] for r in res.ranked] == ["Fresh"]
        assert len(res.expired) == 1


# --- judgement -----------------------------------------------------------------

def test_judge_moves_stores_and_logs_events():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _subs(p, [{"id": "b", "url": "https://b.com/x", "kind": "publication", "persist": "forever"}])
        _cache_feed(p, "b", [
            {"title": "To Promote", "url": "https://b.com/1"},
            {"title": "To Dismiss", "url": "https://b.com/2"},
            {"title": "To Read", "url": "https://b.com/3"},
        ])
        cfg = config.load(p)
        # populate the manifest
        assert main(["--profile", "p", "feed"]) == 0
        assert sum(1 for _ in store.read_jsonl(p.feed_manifest)) == 3

        pid = rank._resolve(p, "https://b.com/1")[0]["id"]
        rank.judge(p, cfg, "https://b.com/1", "promote")
        rank.judge(p, cfg, "https://b.com/2", "dismiss")
        rank.judge(p, cfg, "https://b.com/3", "read", rating=4)

        assert [s["id"] for s in store.load_stack(p)] == [pid]
        arc = list(store.iter_records(p.archive_dir, kind="item"))
        assert arc[0]["title"] == "To Read" and arc[0]["rating"] == 4 and arc[0]["read"]
        tr = list(store.iter_records(p.trash_dir, kind="item"))
        assert tr[0]["title"] == "To Dismiss" and tr[0]["dismissed"]

        evs = list(store.iter_records(p.events_dir, kind="event"))
        assert {e["decision"] for e in evs} == {"promote", "dismiss", "read"}
        assert all(e["rank"] and e["score"] is not None for e in evs)

        assert sum(1 for _ in store.read_jsonl(p.feed_manifest)) == 0  # all judged
        # judged items never resurface
        assert rank.rank_feed(p, cfg).ranked == []


def test_rate_only_on_archive():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _cache_feed(p, "b", [{"title": "X", "url": "https://b.com/x"}])
        _subs(p, [{"id": "b", "url": "u", "kind": "publication", "persist": "forever"}])
        cfg = config.load(p)
        try:
            rank.rate(p, "https://b.com/x", 5)
        except ValueError as e:
            assert "archive" in str(e)
        else:
            raise AssertionError("expected ValueError rating a feed item")
        rank.judge(p, cfg, "https://b.com/x", "read")
        rank.rate(p, "https://b.com/x", 5)
        assert next(store.iter_records(p.archive_dir, kind="item"))["rating"] == 5


# --- ctx -------------------------------------------------------------------

def test_ctx_as_of_leave_one_out():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        a, _ = canonicalise({"title": "Old", "url": "https://e.com/old", "stream": "archive",
                             "read": "2026-01-01T00:00:00Z"})
        b, _ = canonicalise({"title": "Newer", "url": "https://e.com/new", "stream": "archive",
                             "read": "2026-08-01T00:00:00Z"})
        store.append_jsonl(p.shard("archive", "2026-01"), a)
        store.append_jsonl(p.shard("archive", "2026-08"), b)
        cfg = config.load(p)

        bundle = ctx.as_of(p, cfg, "2026-06-01T00:00:00Z", exclude_id=a["id"])
        got = {r["id"] for r in bundle["archive"]}
        assert got == set()  # 'Old' excluded (LOO), 'Newer' not yet read as of June
        assert ctx.live(p, cfg)["archive"]  # live sees everything


# --- CLI end to end --------------------------------------------------------

def test_cli_feed_flow(capsys):
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _subs(p, [{"id": "b", "url": "https://b.com/x", "kind": "publication", "persist": "forever"}])
        _cache_feed(p, "b", [{"title": "Alpha", "url": "https://b.com/a"},
                             {"title": "Beta", "url": "https://b.com/b"}])
        capsys.readouterr()
        assert main(["--profile", "p", "feed"]) == 0
        assert "Alpha" in capsys.readouterr().out

        assert main(["--profile", "p", "promote", "https://b.com/a"]) == 0
        assert main(["--profile", "p", "stack"]) == 0
        assert "Alpha" in capsys.readouterr().out

        assert main(["--json", "--profile", "p", "feed"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert [f["title"] for f in out["feed"]] == ["Beta"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
