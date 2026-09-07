"""End-to-end: import-zotero / ingest / add through the CLI into a profile."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
for p in (str(_SRC), str(Path(__file__).parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from _zoterodb import build  # noqa: E402
from tjvz import paths, store  # noqa: E402
from tjvz.cli import main  # noqa: E402


def _profile(tmp: Path) -> paths.Profile:
    paths._REGISTRY = tmp / ".config" / "tjvz" / "profiles.json"
    root = tmp / "p"
    assert main(["init", str(root), "--name", "p"]) == 0
    return paths.Profile(root.resolve())


def _archive_ids(p) -> set[str]:
    return {r["id"] for r in store.iter_records(p.archive_dir, kind="item")}


def test_import_zotero_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        p = _profile(td)
        db = td / "Zotero" / "zotero.sqlite"
        db.parent.mkdir()
        build(db, [
            {"key": "K1", "type": "blogPost", "dateAdded": "2024-05-10 00:00:00",
             "fields": {"title": "One", "url": "https://ex.com/1", "blogTitle": "Ex"}},
            {"key": "K2", "type": "book", "dateAdded": "2023-02-02 00:00:00",
             "fields": {"title": "Two"}, "creators": [("", "Postman", "author")]},
        ])
        assert main(["--profile", "p", "import-zotero", "--library", str(db), "--dry-run"]) == 0
        assert _archive_ids(p) == set()  # dry run wrote nothing

        assert main(["--profile", "p", "import-zotero", "--library", str(db)]) == 0
        ids = _archive_ids(p)
        assert len(ids) == 2
        # sharded by read (dateAdded) year-month
        assert (p.archive_dir / "2024-05.jsonl").exists()
        assert (p.archive_dir / "2023-02.jsonl").exists()

        assert main(["--profile", "p", "import-zotero", "--library", str(db)]) == 0
        assert _archive_ids(p) == ids  # second run adds nothing


def test_ingest_inbox(capsys):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        p = _profile(td)
        (p.inbox / "a.json").write_text(json.dumps({
            "title": "A Feed Item", "url": "https://ex.com/feed", "authors": ["Someone"],
            "publication": "ex.com", "tags": ["x"],
        }))
        (p.inbox / "b.json").write_text(json.dumps({
            "stream": "archive", "title": "Read This", "url": "https://ex.com/read",
            "rating": 4,
        }))
        (p.inbox / "bad.json").write_text("{ not json")

        assert main(["--profile", "p", "ingest"]) == 0
        assert (p.inbox_done / "a.json").exists() and (p.inbox_done / "b.json").exists()
        assert (p.inbox / "bad.json").exists()  # left in place
        assert len(_archive_ids(p)) == 1  # only the archive-stream drop
        assert (p.feeds_cache / "ingest.jsonl").exists()  # the feed drop


def test_add_and_dedup():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        p = _profile(td)
        assert main(["--profile", "p", "add", "--title", "Manual", "--url",
                     "https://ex.com/m", "--stream", "archive", "--rating", "5"]) == 0
        assert len(_archive_ids(p)) == 1
        r = store.iter_records(p.archive_dir, kind="item")
        rec = next(r)
        assert rec["rating"] == 5 and rec["source"]["kind"] == "manual"
        # same url again -> skipped
        assert main(["--profile", "p", "add", "--title", "Manual again",
                     "--url", "https://ex.com/m", "--stream", "archive"]) == 0
        assert len(_archive_ids(p)) == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
