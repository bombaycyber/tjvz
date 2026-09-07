"""RSS/Atom parsing + RssSource.fetch against file:// fixtures."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
for pth in (str(_SRC), str(Path(__file__).parent)):
    if pth not in sys.path:
        sys.path.insert(0, pth)

from tjvz import paths  # noqa: E402
from tjvz.canon import canonicalise  # noqa: E402
from tjvz.cli import main  # noqa: E402
from tjvz.schema import validate  # noqa: E402
from tjvz.sources.rss import RssSource, parse  # noqa: E402

FEEDS = Path(__file__).parent / "fixtures" / "feeds"
RSS_URL = (FEEDS / "rss2.xml").as_uri()
ATOM_URL = (FEEDS / "atom.xml").as_uri()


def test_parse_rss():
    ch, entries = parse((FEEDS / "rss2.xml").read_bytes())
    assert ch["title"] == "Kasurian"
    assert len(entries) == 2
    e = entries[0]
    assert e["title"] == "The Rise & Fall of New Atheism"
    assert e["link"] == "https://www.kasurian.com/rise-fall-new-atheism/"
    assert e["guid"] == "6a5b658274803a00011eaeeb"           # opaque, not the URL
    assert e["creators"] == ["Kasurian"]
    assert e["categories"] == ["Culture", "politics"]
    assert e["published"] == "2026-06-28"
    assert "Full article body" in e["content"]   # raw HTML kept; only text_source is used in v0


def test_parse_atom():
    ch, entries = parse((FEEDS / "atom.xml").read_bytes())
    assert ch["title"] == "Some Lab"
    e = entries[0]
    assert e["link"] == "https://lab.example/notes/one"
    assert e["creators"] == ["Dana Lee"]
    assert e["categories"] == ["biology", "methods"]
    assert e["published"] == "2026-04-07"
    assert "with" in e["content"]                            # xhtml itertext
    assert e["enclosures"] == [("https://lab.example/one.mp3", "audio/mpeg")]


def test_to_item_kinds():
    _ch, entries = parse((FEEDS / "rss2.xml").read_bytes())
    src = RssSource()

    pub = src.to_item(entries[0], {"title": "Kasurian"}, {"id": "k", "url": RSS_URL, "kind": "publication"})
    assert pub["publication"] == "Kasurian" and pub["text_source"] == "content_encoded"

    aut = src.to_item(entries[0], {"title": "Kasurian"}, {"id": "k", "url": RSS_URL, "kind": "author"})
    assert aut["publication"] == "www.kasurian.com"          # per-item host

    rdr = src.to_item(entries[0], {"title": "Kasurian"}, {"id": "friend", "url": RSS_URL, "kind": "reader"})
    assert rdr["readers"] == [{"source": "friend", "rating": None, "blurb": "Where did the New Atheists go?"}]

    item, stream = canonicalise(pub, extra_tags=["cs.CL"])
    validate(item, "item")
    assert stream == "feed"
    assert item["canonical_url"] == "https://www.kasurian.com/rise-fall-new-atheism"
    assert "cs.cl" in item["tags"] and "culture" in item["tags"]


def test_fetch_writes_cache_and_carries_first_seen():
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td) / "k.json"
        src = RssSource()
        sub = {"id": "k", "url": RSS_URL, "kind": "publication", "persist": "forever"}

        r1 = src.fetch(sub, cache)
        assert r1["status"] == "ok" and r1["count"] == 2 and r1["new"] == 2
        c1 = json.loads(cache.read_text())
        first_seen = {it["id"]: it["first_seen"] for it in c1["items"]}
        for it in c1["items"]:
            validate(it, "item")

        r2 = src.fetch(sub, cache)                            # file:// has no ETag -> re-parses
        assert r2["new"] == 0                                 # nothing new
        c2 = json.loads(cache.read_text())
        assert {it["id"]: it["first_seen"] for it in c2["items"]} == first_seen   # carried forward


def test_fetch_max_items_cap():
    with tempfile.TemporaryDirectory() as td:
        r = RssSource().fetch(
            {"id": "k", "url": RSS_URL, "kind": "publication", "persist": 0, "max_items": 1},
            Path(td) / "k.json",
        )
        assert r["count"] == 1


def test_fetch_bad_feed_keeps_going():
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td) / "x.json"
        r = RssSource().fetch({"id": "x", "url": "file:///no/such/feed.xml", "kind": "publication", "persist": 0}, cache)
        assert r["status"] == "error"
        assert json.loads(cache.read_text())["ok"] is False


def test_sub_and_fetch_via_cli(capsys):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        paths._REGISTRY = td / ".config" / "tjvz" / "profiles.json"
        assert main(["init", str(td / "p"), "--name", "p"]) == 0
        p = paths.Profile((td / "p").resolve())

        assert main(["--profile", "p", "sub", "add", RSS_URL,
                     "--kind", "publication", "--persist", "forever", "--id", "kasurian"]) == 0
        feeds = yaml_feeds(p)
        assert feeds and feeds[0]["id"] == "kasurian" and feeds[0]["persist"] == "forever"

        capsys.readouterr()
        assert main(["--profile", "p", "fetch"]) == 0
        assert "kasurian" in capsys.readouterr().out
        assert (p.feeds_cache / "kasurian.json").exists()

        assert main(["--profile", "p", "sub", "remove", "kasurian"]) == 0
        assert yaml_feeds(p) == []


def yaml_feeds(p):
    import yaml
    return (yaml.safe_load(p.subscriptions.read_text()) or {}).get("feeds") or []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
