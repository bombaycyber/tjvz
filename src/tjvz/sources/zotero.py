"""Zotero adapter — reads a library's zotero.sqlite directly.

The DB is locked in place while Zotero runs, so we copy it to a tempdir and
open it read-only (same trick as dashboard's zotero_db.py). Schema is
checked up front — a missing table raises rather than silently returning
nothing.

Dual mode, auto-detected: if ANY item carries a parseable Reading-Flow
status in its Extra field, only items whose status is read/completed/
skimmed are imported; otherwise every non-attachment/note item is treated
as read. `import_all=True` forces the whole library.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from tjvz.sources import register

FLOW_PREFIX = "ReadingFlow: "
DONE_STATUSES = {"read", "completed", "skimmed"}
SKIP_TYPES = {"attachment", "note", "annotation"}

_REQUIRED_TABLES = {
    "items", "itemTypes", "itemData", "fields", "itemDataValues",
    "itemCreators", "creators", "creatorTypes", "itemTags", "tags",
    "collections", "collectionItems", "deletedItems", "settings",
}

_PUB_FIELDS = (
    "publicationTitle", "blogTitle", "websiteTitle", "bookTitle",
    "proceedingsTitle", "encyclopediaTitle", "dictionaryTitle",
    "forumTitle", "programTitle",
)

_FORMAT = {
    "journalArticle": "paper", "conferencePaper": "paper", "preprint": "paper",
    "report": "paper", "thesis": "paper", "manuscript": "paper",
    "book": "book",
    "bookSection": "chapter", "encyclopediaArticle": "chapter", "dictionaryEntry": "chapter",
    "blogPost": "essay", "webpage": "essay",
    "magazineArticle": "article", "newspaperArticle": "article",
    "forumPost": "thread", "instantMessage": "thread",
    "interview": "transcript", "presentation": "transcript", "hearing": "transcript",
    "document": "document", "letter": "document", "email": "document",
    "podcast": "podcast", "radioBroadcast": "podcast", "audioRecording": "podcast",
    "videoRecording": "video", "tvBroadcast": "video", "film": "video",
    "artwork": "artwork", "map": "artwork",
    "computerProgram": "dataset", "dataset": "dataset",
}

_DEFAULT_DIRS = ("~/Zotero", "~/Documents/Zotero")


class ZoteroSchemaError(RuntimeError):
    """The DB doesn't look like a Zotero library — surfaced, never swallowed."""


def reading_flow_status(extra: str | None) -> str | None:
    for line in (extra or "").split("\n"):
        if line.startswith(FLOW_PREFIX):
            try:
                d = json.loads(line[len(FLOW_PREFIX):])
            except json.JSONDecodeError:
                return None
            if d.get("s"):
                return str(d["s"]).lower()
            best = max((d.get("p") or {}).values(), default=0)
            return "read" if best >= 0.95 else ("reading" if best > 0 else "to-read")
    return None


def _find_sqlite(library: str | None) -> Path:
    if library:
        p = Path(library).expanduser()
        p = p / "zotero.sqlite" if p.is_dir() else p
        if not p.is_file():
            raise ZoteroSchemaError(f"no zotero.sqlite at {p}")
        return p
    for d in _DEFAULT_DIRS:
        p = Path(d).expanduser() / "zotero.sqlite"
        if p.is_file():
            return p
    raise ZoteroSchemaError("no zotero.sqlite found — pass --library PATH")


@register
class ZoteroSource:
    kind = "zotero"
    stream = "archive"

    def __init__(self, library: str | None = None, collections_as_tags: bool = True,
                 import_all: bool = False):
        self.src_path = _find_sqlite(library)
        self.library_name = self.src_path.parent.name
        self.collections_as_tags = collections_as_tags
        self.import_all = import_all

    # --- Source protocol ---

    def pull(self, cfg: dict | None = None, ctx=None):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "zotero.sqlite"
            shutil.copy2(self.src_path, dst)
            conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                self._check_schema(conn)
                yield from self._items(conn)
            finally:
                conn.close()

    def to_item(self, raw: dict) -> dict:
        f = raw["fields"]
        pub = next((f[k] for k in _PUB_FIELDS if f.get(k)), "")
        tags = list(raw["tags"])
        if self.collections_as_tags:
            tags += raw["collections"]
        ext = {"zotero_collections": raw["collections"]}
        for zk, ek in (("DOI", "doi"), ("ISSN", "issn"), ("ISBN", "isbn"),
                       ("volume", "volume"), ("issue", "issue"), ("pages", "pages"),
                       ("language", "language")):
            if f.get(zk):
                ext[ek] = f[zk]
        return {
            "title": f.get("title", ""),
            "url": f.get("url") or None,
            "authors": raw["creators"],
            "publication": pub,
            "format": _FORMAT.get(raw["type"], "other"),
            "tags": tags,
            "summary": f.get("abstractNote") or None,
            "published": f.get("date") or None,
            "read": raw["dateAdded"],
            "source": {"kind": "zotero", "library": self.library_name, "key": raw["key"]},
            "ext": ext,
            "stream": "archive",
        }

    # --- internals ---

    def _check_schema(self, conn) -> None:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = _REQUIRED_TABLES - have
        if missing:
            raise ZoteroSchemaError(f"zotero.sqlite missing tables {sorted(missing)}")
        if not conn.execute("SELECT 1 FROM settings WHERE setting='globalSchema' LIMIT 1").fetchone():
            raise ZoteroSchemaError("zotero.sqlite has no globalSchema row — not a real library")

    def _items(self, conn):
        rows = conn.execute("""
            SELECT i.itemID, i.key, i.dateAdded, it.typeName,
                   f.fieldName, v.value
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            LEFT JOIN deletedItems del ON del.itemID = i.itemID
            LEFT JOIN itemData d ON d.itemID = i.itemID
            LEFT JOIN fields f ON f.fieldID = d.fieldID
            LEFT JOIN itemDataValues v ON v.valueID = d.valueID
            WHERE del.itemID IS NULL AND it.typeName NOT IN ('attachment','note','annotation')
        """).fetchall()

        items: dict[int, dict] = {}
        for r in rows:
            it = items.setdefault(r["itemID"], {
                "key": r["key"], "dateAdded": r["dateAdded"], "type": r["typeName"],
                "fields": {}, "creators": [], "tags": [], "collections": [],
            })
            if r["fieldName"]:
                it["fields"][r["fieldName"]] = r["value"]

        for iid, it in items.items():
            for c in conn.execute("""
                SELECT cr.firstName, cr.lastName, ct.creatorType
                FROM itemCreators ic
                JOIN creators cr ON cr.creatorID = ic.creatorID
                JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
                WHERE ic.itemID = ? ORDER BY ic.orderIndex
            """, (iid,)):
                role = "author" if c["creatorType"] == "author" else c["creatorType"]
                if c["lastName"] and c["firstName"]:
                    it["creators"].append({"given": c["firstName"], "family": c["lastName"], "role": role})
                elif c["lastName"] or c["firstName"]:
                    it["creators"].append({"name": (c["lastName"] or c["firstName"]), "role": role})
            it["tags"] = [t[0] for t in conn.execute(
                "SELECT t.name FROM itemTags itg JOIN tags t ON t.tagID = itg.tagID WHERE itg.itemID = ?", (iid,))]
            it["collections"] = [c[0] for c in conn.execute("""
                SELECT col.collectionName FROM collectionItems ci
                JOIN collections col ON col.collectionID = ci.collectionID WHERE ci.itemID = ?
            """, (iid,))]

        vals = list(items.values())
        graded = (not self.import_all) and any(
            reading_flow_status(it["fields"].get("extra")) for it in vals
        )
        for it in vals:
            if not it["fields"].get("title"):
                continue
            if graded and reading_flow_status(it["fields"].get("extra")) not in DONE_STATUSES:
                continue
            yield it
