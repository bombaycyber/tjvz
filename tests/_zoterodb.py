"""Build a minimal zotero.sqlite fixture — just the tables the adapter reads."""
from __future__ import annotations

import sqlite3
from pathlib import Path

_DDL = """
CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT, dateAdded TEXT, itemTypeID INTEGER);
CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE itemCreators (itemID INTEGER, creatorID INTEGER, creatorTypeID INTEGER, orderIndex INTEGER);
CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE itemTags (itemID INTEGER, tagID INTEGER);
CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT);
CREATE TABLE collectionItems (collectionID INTEGER, itemID INTEGER);
CREATE TABLE deletedItems (itemID INTEGER);
CREATE TABLE settings (setting TEXT, key TEXT, value TEXT);
"""


def build(path: Path, items: list[dict]) -> None:
    """items: [{key, type, dateAdded, fields:{name:val}, creators:[(first,last,type)],
               tags:[...], collections:[...], deleted:bool}]"""
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    c.executescript(_DDL)
    c.execute("INSERT INTO settings VALUES ('globalSchema','', '{}')")

    types: dict[str, int] = {}
    fields: dict[str, int] = {}
    ctypes: dict[str, int] = {}
    vid = cid = 0

    for n, it in enumerate(items, start=1):
        tid = types.setdefault(it["type"], len(types) + 1)
        c.execute("INSERT OR IGNORE INTO itemTypes VALUES (?,?)", (tid, it["type"]))
        c.execute("INSERT INTO items VALUES (?,?,?,?)", (n, it["key"], it.get("dateAdded", "2024-01-01 00:00:00"), tid))
        if it.get("deleted"):
            c.execute("INSERT INTO deletedItems VALUES (?)", (n,))
        for fname, val in it.get("fields", {}).items():
            fid = fields.setdefault(fname, len(fields) + 1)
            c.execute("INSERT OR IGNORE INTO fields VALUES (?,?)", (fid, fname))
            vid += 1
            c.execute("INSERT INTO itemDataValues VALUES (?,?)", (vid, val))
            c.execute("INSERT INTO itemData VALUES (?,?,?)", (n, fid, vid))
        for i, (first, last, ctype) in enumerate(it.get("creators", [])):
            ctid = ctypes.setdefault(ctype, len(ctypes) + 1)
            c.execute("INSERT OR IGNORE INTO creatorTypes VALUES (?,?)", (ctid, ctype))
            cid += 1
            c.execute("INSERT INTO creators VALUES (?,?,?)", (cid, first, last))
            c.execute("INSERT INTO itemCreators VALUES (?,?,?,?)", (n, cid, ctid, i))
        for t in it.get("tags", []):
            c.execute("INSERT INTO tags (name) VALUES (?)", (t,))
            c.execute("INSERT INTO itemTags VALUES (?, last_insert_rowid())", (n,))
        for col in it.get("collections", []):
            c.execute("INSERT INTO collections (collectionName) VALUES (?)", (col,))
            c.execute("INSERT INTO collectionItems VALUES (last_insert_rowid(), ?)", (n,))

    conn.commit()
    conn.close()
