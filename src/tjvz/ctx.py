"""Build the scoring bundle a feature reads — see schema/ctx.md.

`ctx` is a plain dict; features reach it through `features._entity.store()`
which tolerates both dict and attribute access. It is assembled once per
scoring pass and shared across every feature and every candidate.

Two entry points:

  live(profile, cfg)              — score feed candidates as of now. The
                                    whole archive / stack / trash; the
                                    candidate is not in any of them.

  as_of(profile, cfg, at, id)     — recompute one past decision's features.
                                    Leave-one-out (drop `id`) + as-of-date
                                    (archive/trash filtered to before `at`,
                                    the stack reconstructed from the event
                                    log). Used by `tjvz features recompute`.

`text` / `clusters` / `idf` are post-v0 and stay None; no active feature
needs them.
"""
from __future__ import annotations

from tjvz import store


def _archive(profile) -> list[dict]:
    return list(store.iter_records(profile.archive_dir, kind="item"))


def _trash(profile) -> list[dict]:
    return list(store.iter_records(profile.trash_dir, kind="item"))


def _events(profile) -> list[dict]:
    return list(store.iter_records(profile.events_dir, kind="event"))


def _bundle(archive, stack, trash, events, cfg, as_of_ts) -> dict:
    return {
        "archive": archive,
        "stack": stack,
        "trash": trash,
        "events": events,
        "readers": None,
        "text": None,
        "clusters": None,
        "idf": None,
        "config": cfg,
        "as_of": as_of_ts,
    }


def live(profile, cfg: dict) -> dict:
    return _bundle(
        _archive(profile), store.load_stack(profile), _trash(profile),
        _events(profile), cfg, store.now_iso(),
    )


def as_of(profile, cfg: dict, at: str, exclude_id: str | None = None) -> dict:
    at = str(at)
    archive = [r for r in _archive(profile)
               if str(r.get("read") or "") < at and r["id"] != exclude_id]
    trash = [r for r in _trash(profile)
             if str(r.get("dismissed") or "") < at and r["id"] != exclude_id]
    events = [e for e in _events(profile) if str(e.get("at") or "") < at]
    stack = _stack_as_of(profile, events, exclude_id)
    return _bundle(archive, stack, trash, events, cfg, at)


def _stack_as_of(profile, events: list[dict], exclude_id: str | None) -> list[dict]:
    """Items whose most recent decision before the cutoff was `promote` —
    i.e. on the stack, not yet read or dismissed. Bodies come from wherever
    the item lives now (still on the stack, or since read / dismissed)."""
    bodies: dict[str, dict] = {}
    for r in (*_archive(profile), *_trash(profile), *store.load_stack(profile)):
        bodies.setdefault(r["id"], r)
    last: dict[str, str] = {}
    for e in sorted(events, key=lambda e: str(e.get("at") or "")):
        last[e["id"]] = e["decision"]
    return [
        bodies[iid] for iid, dec in last.items()
        if dec == "promote" and iid != exclude_id and iid in bodies
    ]
