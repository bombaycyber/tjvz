"""Reading and writing a profile's stores.

Record stores are JSONL (one object per line):
  archive/ · trash/ · events/   month-sharded  (YYYY-MM.jsonl)
  feed/manifest.jsonl           a single file
Small state is one JSON value:
  stack.json (a list) · model.json (an object)

Appends to a `*.jsonl` store are `open(a)` — O(1) and append-safe; never
`atomic_write` one. `atomic_write` is for the small JSON files and the
.tjvz/ caches. Records are validated against schema/ on the write path;
MIGRATIONS bring a lagging `schema_version` up to date on read.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tjvz.schema import validate

SCHEMA_VERSION = 1

# {kind: {from_version: fn(obj) -> obj}} — applied in sequence on read.
MIGRATIONS: dict[str, dict[int, "callable"]] = {"item": {}, "event": {}, "feed-manifest": {}}

_SCHEMA_OF = {
    "archive": "item", "trash": "item", "stack": "item", "feed": "item",
    "events": "event", "manifest": "feed-manifest",
}


# --- time -----------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ym() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def ym_of(datestr: str | None) -> str:
    """'YYYY-MM' for a shard filename, from a date string that may be a full
    ISO timestamp, 'YYYY-MM-DD', 'YYYY-MM', or bare 'YYYY'. Falls back to now."""
    s = str(datestr or "")
    if len(s) >= 7 and s[4] == "-" and s[5:7].isdigit():
        return s[:7]
    if len(s) >= 4 and s[:4].isdigit():
        return s[:4] + "-01"
    return now_ym()


# --- low-level IO ------------------------------------------------------------

def atomic_write(path: Path, text: str) -> None:
    """Write-then-rename: a reader never sees a partial file, concurrent
    writers can't interleave. (ported from dashboard_lib.atomic_write)"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def append_jsonl(path: Path, obj: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> Iterator[dict]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    for line in text.splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path: Path, obj) -> None:
    atomic_write(Path(path), json.dumps(obj, ensure_ascii=False, indent=2))


def update_yaml_list(path: Path, key: str, new_list: list) -> None:
    """Rewrite only the top-level `<key>:` block of a YAML file, preserving
    comments and formatting everywhere else. (ported from
    dashboard_lib.update_list_block — the `^[^\\s-]` terminator handles
    PyYAML's column-0 list dashes)."""
    import re

    import yaml

    text = Path(path).read_text()
    block = yaml.dump({key: new_list}, allow_unicode=True, sort_keys=False).rstrip("\n") + "\n"
    k = re.escape(key)
    pattern = rf"^{k}:.*?(?=^[^\s-])|^{k}:.*\Z"
    new_text, n = re.subn(pattern, lambda _: block, text, count=1, flags=re.M | re.S)
    if n == 0:
        raise ValueError(f"no top-level {key!r}: block in {path}")
    atomic_write(Path(path), new_text)


# --- migration ---------------------------------------------------------------

def migrate(obj: dict, kind: str) -> dict:
    steps = MIGRATIONS.get(kind, {})
    v = obj.get("schema_version", 1)
    while v in steps:
        obj = steps[v](obj)
        v = obj.get("schema_version", v + 1)
    return obj


# --- records ---------------------------------------------------------------

def iter_records(*paths_or_dirs: Path, kind: str) -> Iterator[dict]:
    """Yield migrated records from JSONL files / directories of them, in
    filename order (so month shards come out chronologically)."""
    files: list[Path] = []
    for p in paths_or_dirs:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("*.jsonl")))
        elif p.exists():
            files.append(p)
    for f in files:
        for obj in read_jsonl(f):
            yield migrate(obj, kind)


def write_record(shard: Path, obj: dict, kind: str, do_validate: bool = True) -> None:
    """Append one record to a month shard (archive/trash/events) or the
    feed manifest, validating first unless told not to."""
    if do_validate:
        validate(obj, _SCHEMA_OF.get(kind, kind))
    append_jsonl(shard, obj)


def load_stack(profile) -> list[dict]:
    return [migrate(o, "item") for o in load_json(profile.stack, [])]


def save_stack(profile, items: list[dict]) -> None:
    save_json(profile.stack, items)


# --- record mutation (month-sharded dirs) ----------------------------------

def _shard_files(directory: Path) -> list[Path]:
    return sorted(Path(directory).glob("*.jsonl"))


def _rewrite(f: Path, rows: list[dict]) -> None:
    atomic_write(f, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def find_record(*dirs: Path, id: str, kind: str = "item") -> dict | None:
    for r in iter_records(*dirs, kind=kind):
        if r.get("id") == id:
            return r
    return None


def remove_record(directory: Path, id: str) -> dict | None:
    """Delete the record with this id from whichever shard holds it, rewriting
    that shard. Returns the removed record, or None if not found."""
    for f in _shard_files(directory):
        rows = list(read_jsonl(f))
        keep = [r for r in rows if r.get("id") != id]
        if len(keep) != len(rows):
            _rewrite(f, keep)
            return next(r for r in rows if r.get("id") == id)
    return None


def update_record(directory: Path, id: str, patch: dict, kind: str = "item") -> dict | None:
    """Merge `patch` into the record with this id in place, re-validate the
    shard, rewrite it. Returns the updated record, or None if not found."""
    for f in _shard_files(directory):
        rows = list(read_jsonl(f))
        hit = None
        for r in rows:
            if r.get("id") == id:
                r.update(patch)
                hit = r
        if hit is not None:
            for r in rows:
                validate(migrate(dict(r), kind), _SCHEMA_OF.get(kind, kind))
            _rewrite(f, rows)
            return hit
    return None
