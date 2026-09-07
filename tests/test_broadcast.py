"""M-broadcast — the Atom feed of the archive, and the reader-side round trip.

    python -m pytest tests/test_broadcast.py   (or)   python tests/test_broadcast.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
for _p in (str(_SRC), str(Path(__file__).parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tjvz import broadcast, config, paths, store  # noqa: E402
from tjvz.canon import canonicalise  # noqa: E402
from tjvz.cli import main  # noqa: E402
from tjvz.sources.rss import RssSource, parse  # noqa: E402


def _profile(tmp: Path) -> paths.Profile:
    paths._REGISTRY = tmp / ".config" / "tjvz" / "profiles.json"
    root = tmp / "p"
    assert main(["init", str(root), "--name", "p"]) == 0
    return paths.Profile(root.resolve())


def _archive(p, raw):
    raw = {**raw, "stream": "archive"}
    item, _ = canonicalise(raw)
    if "ext" in raw:
        item.setdefault("ext", {}).update(raw["ext"])
    store.append_jsonl(p.shard("archive", store.ym_of(item["read"])), item)
    return item


def _set_broadcast(p, **kv):
    import yaml
    cfg = yaml.safe_load(p.config.read_text())
    cfg.setdefault("broadcast", {}).update(kv)
    p.config.write_text(yaml.safe_dump(cfg))


def test_is_public_url():
    ok = ["https://www.kasurian.com/x", "http://blog.example.org/p"]
    bad = ["file:///Users/me/x.pdf", "http://localhost/x", "https://192.168.1.4/x",
           "http://box.local/wiki", "https://127.0.0.1", "", None, "https://intranet/x"]
    assert all(broadcast.is_public_url(u) for u in ok)
    assert not any(broadcast.is_public_url(u) for u in bad)


def test_build_feed_shape_and_privacy():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _archive(p, {"title": "Public One", "url": "https://www.site.com/a",
                     "authors": ["Jane Doe"], "tags": ["ai", "thesis-ch3"],
                     "read": "2026-05-14T09:12:00Z", "rating": 5, "blurb": "sharp piece",
                     "summary": "the source's abstract",
                     "ext": {"collection_tags": ["thesis-ch3"]}})
        _archive(p, {"title": "Local Only", "url": "file:///Users/me/draft.pdf",
                     "read": "2026-06-02T00:00:00Z"})
        cfg = config.load(p)

        xml, stats = broadcast.build(p, cfg)
        assert stats == {"included": 2, "archived": 2, "excluded": 0, "link_suppressed": 1}

        ch, entries = parse(xml.encode())
        assert len(entries) == 2
        pub = next(e for e in entries if e["title"] == "Public One")
        loc = next(e for e in entries if e["title"] == "Local Only")
        assert pub["link"] == "https://www.site.com/a"
        assert loc["link"] == ""                          # file:// dropped
        assert pub["published"] == "2026-05-01"           # coarsened to 1st of month
        assert pub["rating"] == 5
        assert set(pub["categories"]) == {"ai", "thesis-ch3"}
        assert pub["summary"] == "sharp piece"            # blurb -> <summary>
        assert "the source's abstract" in pub["content"]  # summary -> <content>


def test_share_tags_modes():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _archive(p, {"title": "T", "url": "https://s.com/t", "tags": ["ai", "client-x"],
                     "read": "2026-05-01", "ext": {"collection_tags": ["client-x"]}})
        cfg = config.load(p)

        cfg["broadcast"]["share_tags"] = "none"
        assert parse(broadcast.build(p, cfg)[0].encode())[1][0]["categories"] == []

        cfg["broadcast"]["share_tags"] = "no-collections"
        assert parse(broadcast.build(p, cfg)[0].encode())[1][0]["categories"] == ["ai"]

        cfg["broadcast"]["share_tags"] = ["ai"]
        assert parse(broadcast.build(p, cfg)[0].encode())[1][0]["categories"] == ["ai"]


def test_include_filters_and_limit():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        for i in range(5):
            _archive(p, {"title": f"n{i}", "url": f"https://s.com/{i}",
                         "read": f"2026-0{i + 1}-01", "rating": 5 if i % 2 else None,
                         "blurb": "yes" if i == 0 else None})
        cfg = config.load(p)

        cfg["broadcast"].update(include="rated", min_rating=4)
        assert broadcast.build(p, cfg)[1]["included"] == 2

        cfg["broadcast"]["include"] = "blurbed"
        assert broadcast.build(p, cfg)[1]["included"] == 1

        cfg["broadcast"].update(include="all", limit=3)
        assert broadcast.build(p, cfg)[1]["included"] == 3


def test_round_trip_through_reader_subscription():
    """A broadcaster's feed, ingested by a subscriber as kind: reader, must
    land the rating + blurb on a readers[] entry keyed by the chosen id."""
    with tempfile.TemporaryDirectory() as td:
        pub = _profile(Path(td) / "pub")
        _archive(pub, {"title": "Recommended", "url": "https://site.com/rec",
                       "authors": ["A. Writer"], "read": "2026-05-20T10:00:00Z",
                       "rating": 4, "blurb": "worth your time"})
        xml, _ = broadcast.build(pub, config.load(pub))

        src = RssSource()
        ch, entries = parse(xml.encode())
        item = src.to_item(entries[0], ch, {"id": "friend", "url": "u", "kind": "reader"})
        canon, stream = canonicalise(item)
        assert stream == "feed"
        assert canon["readers"] == [
            {"source": "friend", "rating": 4, "blurb": "worth your time", "at": None}
        ]
        # same canonical URL on both sides -> same id
        assert canon["id"] == "u:" + __import__("hashlib").sha256(
            b"https://site.com/rec").hexdigest()[:16]


def test_cli_dry_run_then_write_then_publish(capsys):
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _archive(p, {"title": "X", "url": "https://s.com/x", "read": "2026-05-01"})

        capsys.readouterr()
        assert main(["--profile", "p", "broadcast", "--dry-run"]) == 0
        assert "would write" in capsys.readouterr().out
        assert not p.broadcast_dir.exists()

        assert main(["--profile", "p", "broadcast"]) == 0
        assert (p.broadcast_dir / "atom.xml").is_file()

        _set_broadcast(p, publish_cmd="true")
        capsys.readouterr()
        assert main(["--profile", "p", "broadcast", "--publish"]) == 0
        assert "publish: ok" in capsys.readouterr().out

        _set_broadcast(p, publish_cmd="false")
        assert main(["--profile", "p", "broadcast", "--publish"]) == 1  # exit 1 on publish failure


def test_blurb_command():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _cache = p.feeds_cache
        _cache.mkdir(parents=True)
        it, _ = canonicalise({"title": "Y", "url": "https://s.com/y",
                              "source": {"kind": "rss", "feed_id": "b", "feed_url": "u", "guid": "g"}})
        store.save_json(_cache / "b.json", {"ok": True, "items": [it], "channel": {}})
        p.subscriptions.write_text("feeds:\n  - {id: b, url: u, kind: publication, persist: forever}\n")

        assert main(["--profile", "p", "read", "https://s.com/y"]) == 0
        assert main(["--profile", "p", "blurb", "https://s.com/y", "my take here"]) == 0
        rec = next(store.iter_records(p.archive_dir, kind="item"))
        assert rec["blurb"] == "my take here"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
