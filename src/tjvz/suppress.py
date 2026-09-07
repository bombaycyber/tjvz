"""Suppression — the ids that must never resurface in the feed.

A projection of the current state stores, not of the event log: an item is
suppressed iff it currently sits in the archive, the stack, or the trash
(`archive ∪ stack ∪ trash`). Rescue an item from the trash and it leaves
the set the moment its record moves; that is why this is derived from the
stores and never from `events/`.

Cached at `.tjvz/suppressed.json` keyed on the stores' newest mtime, so a
`tjvz feed` right after a judgement rebuilds it. The cache is pure
optimisation — deleting it changes nothing but the next run's speed.
"""
from __future__ import annotations

import json

from tjvz import store


def _key(profile) -> int:
    """Newest mtime_ns across the three state stores — changes on any write."""
    paths = [profile.stack, *store._shard_files(profile.archive_dir),
             *store._shard_files(profile.trash_dir)]
    best = 0
    for p in paths:
        try:
            best = max(best, p.stat().st_mtime_ns)
        except FileNotFoundError:
            pass
    return best


def _compute(profile) -> set[str]:
    ids: set[str] = set()
    for r in store.iter_records(profile.archive_dir, profile.trash_dir, kind="item"):
        ids.add(r["id"])
        ids.update(r.get("aliases") or [])
    for r in store.load_stack(profile):
        ids.add(r["id"])
        ids.update(r.get("aliases") or [])
    return ids


def suppressed_ids(profile) -> set[str]:
    key = _key(profile)
    try:
        cached = json.loads(profile.suppressed.read_text())
        if cached.get("key") == key:
            return set(cached["ids"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    ids = _compute(profile)
    try:
        store.save_json(profile.suppressed, {"key": key, "ids": sorted(ids)})
    except OSError:
        pass
    return ids


def invalidate(profile) -> None:
    profile.suppressed.unlink(missing_ok=True)
