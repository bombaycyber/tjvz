"""ZoteroSource against a generated fixture DB — mapping + dual-mode filter."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from _zoterodb import build  # noqa: E402
from tjvz.canon import canonicalise  # noqa: E402
from tjvz.schema import validate  # noqa: E402
from tjvz.sources.zotero import ZoteroSource, reading_flow_status  # noqa: E402

BLOG = {
    "key": "AAA11111", "type": "blogPost", "dateAdded": "2024-05-10 12:00:00",
    "fields": {"title": "The Rise & Fall of New Atheism", "url": "https://www.kasurian.com/x/",
               "blogTitle": "Kasurian", "abstractNote": "…", "date": "2024-03"},
    "creators": [("", "Kasurian", "author")],
    "tags": ["Culture"], "collections": ["To Cite"],
}
PAPER = {
    "key": "BBB22222", "type": "journalArticle", "dateAdded": "2024-06-01 09:00:00",
    "fields": {"title": "On Dakani", "publicationTitle": "JSALL", "DOI": "10.1/x",
               "date": "2025"},
    "creators": [("Joshua H.", "Pien", "author")],
    "tags": ["dakani", "linguistics"], "collections": ["Dakani"],
}


def test_reading_flow_status():
    assert reading_flow_status('ReadingFlow: {"s":"read"}') == "read"
    assert reading_flow_status('ReadingFlow: {"p":{"a":0.98}}') == "read"
    assert reading_flow_status('ReadingFlow: {"p":{"a":0.3}}') == "reading"
    assert reading_flow_status("nothing here") is None


def test_all_read_mode_and_mapping():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "zotero.sqlite"
        build(db, [BLOG, PAPER])
        src = ZoteroSource(library=str(db), collections_as_tags=True)
        raws = list(src.pull())
        assert len(raws) == 2  # no flow data -> everything is "read"

        by_title = {src.to_item(r)["title"]: src.to_item(r) for r in raws}
        blog = by_title["The Rise & Fall of New Atheism"]
        assert blog["format"] == "essay"
        assert blog["publication"] == "Kasurian"
        assert blog["authors"] == [{"name": "Kasurian", "role": "author"}]
        assert set(blog["tags"]) == {"Culture", "To Cite"}
        assert blog["read"].startswith("2024-05-10")
        assert blog["source"] == {"kind": "zotero", "library": td.rsplit("/", 1)[-1] if False else src.library_name, "key": "AAA11111"}

        paper = by_title["On Dakani"]
        assert paper["format"] == "paper" and paper["ext"]["doi"] == "10.1/x"
        assert paper["authors"] == [{"given": "Joshua H.", "family": "Pien", "role": "author"}]
        assert paper["publication"] == "JSALL"

        item, stream = canonicalise(blog)
        validate(item, "item")
        assert stream == "archive" and item["canonical_url"] == "https://www.kasurian.com/x"


def test_graded_mode_filters_to_done():
    read_it = dict(BLOG, fields=dict(BLOG["fields"], extra='ReadingFlow: {"s":"read"}'))
    todo = dict(PAPER, fields=dict(PAPER["fields"], extra='ReadingFlow: {"s":"to-read"}'))
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "zotero.sqlite"
        build(db, [read_it, todo])
        titles = [src_item["title"] for src_item in
                  (ZoteroSource(library=str(db)).to_item(r) for r in ZoteroSource(library=str(db)).pull())]
        assert titles == ["The Rise & Fall of New Atheism"]

        # --all overrides the filter
        both = list(ZoteroSource(library=str(db), import_all=True).pull())
        assert len(both) == 2


def test_deleted_items_skipped():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "zotero.sqlite"
        build(db, [BLOG, dict(PAPER, deleted=True)])
        assert len(list(ZoteroSource(library=str(db)).pull())) == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
