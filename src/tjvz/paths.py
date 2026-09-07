"""Profile resolution and the paths inside a profile.

A *profile* is a directory `tjvz init` created: a `.tjvz-profile` marker,
`config.yaml`, `subscriptions.yaml`, and the record stores. It is fully
self-contained and portable. An optional registry at
`~/.config/tjvz/profiles.json` maps names to paths and remembers the
`current` one — losing it loses names, not data.

Resolution order (first hit wins):
    1. --home PATH  /  --profile NAME     (CLI flags)
    2. $TJVZ_HOME   /  $TJVZ_PROFILE      (env)
    3. cwd walk-up for a `.tjvz-profile` marker   (git-style)
    4. registry `current`
    5. a helpful error
"""
from __future__ import annotations

import json
import os
from pathlib import Path

MARKER = ".tjvz-profile"
CACHE_DIR = ".tjvz"
_REGISTRY = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tjvz" / "profiles.json"


class ProfileError(RuntimeError):
    pass


# --- the registry ----------------------------------------------------------

def _load_registry() -> dict:
    try:
        return json.loads(_REGISTRY.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"current": None, "profiles": {}}


def _save_registry(reg: dict) -> None:
    _REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = _REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    os.replace(tmp, _REGISTRY)


def register(name: str, root: Path, make_current: bool = True) -> None:
    reg = _load_registry()
    reg.setdefault("profiles", {})[name] = str(Path(root).resolve())
    if make_current or not reg.get("current"):
        reg["current"] = name
    _save_registry(reg)


def unregister(name: str) -> None:
    reg = _load_registry()
    reg.get("profiles", {}).pop(name, None)
    if reg.get("current") == name:
        reg["current"] = next(iter(reg.get("profiles", {})), None)
    _save_registry(reg)


def set_current(name: str) -> None:
    reg = _load_registry()
    if name not in reg.get("profiles", {}):
        raise ProfileError(f"no profile named {name!r} in the registry")
    reg["current"] = name
    _save_registry(reg)


def list_profiles() -> tuple[dict[str, str], str | None]:
    reg = _load_registry()
    return dict(reg.get("profiles", {})), reg.get("current")


# --- resolution ----------------------------------------------------------------

def _is_profile(path: Path) -> bool:
    return (path / MARKER).is_file()


def _checked(path: Path) -> Path:
    path = Path(path).expanduser().resolve()
    if not _is_profile(path):
        raise ProfileError(f"{path} is not a tjvz profile (no {MARKER}); run `tjvz init` there first")
    return path


def _from_registry(name: str) -> Path:
    profiles, _ = list_profiles()
    if name not in profiles:
        raise ProfileError(f"no profile named {name!r}; `tjvz profile list` to see them")
    return _checked(Path(profiles[name]))


def _walk_up(start: Path) -> Path | None:
    for d in [start, *start.parents]:
        if _is_profile(d):
            return d
    return None


def resolve_profile(profile: str | None = None, home: str | None = None) -> "Profile":
    if home:
        root = _checked(Path(home))
    elif profile:
        root = _from_registry(profile)
    elif os.environ.get("TJVZ_HOME"):
        root = _checked(Path(os.environ["TJVZ_HOME"]))
    elif os.environ.get("TJVZ_PROFILE"):
        root = _from_registry(os.environ["TJVZ_PROFILE"])
    elif (walk := _walk_up(Path.cwd())) is not None:
        root = walk
    else:
        _profiles, current = list_profiles()
        if current:
            root = _from_registry(current)
        else:
            raise ProfileError(
                "no tjvz profile in scope — run `tjvz init <dir>`, pass --home/--profile, "
                "or set $TJVZ_HOME"
            )
    return Profile(root)


# --- paths inside a profile --------------------------------------------------

class Profile:
    def __init__(self, root: Path):
        self.root = Path(root)

    def __fspath__(self) -> str:
        return str(self.root)

    # hand-edited state
    @property
    def config(self) -> Path: return self.root / "config.yaml"
    @property
    def subscriptions(self) -> Path: return self.root / "subscriptions.yaml"

    # machine-managed state
    @property
    def stack(self) -> Path: return self.root / "stack.json"
    @property
    def model(self) -> Path: return self.root / "model.json"
    @property
    def archive_dir(self) -> Path: return self.root / "archive"
    @property
    def trash_dir(self) -> Path: return self.root / "trash"
    @property
    def events_dir(self) -> Path: return self.root / "events"
    @property
    def feed_manifest(self) -> Path: return self.root / "feed" / "manifest.jsonl"
    @property
    def inbox(self) -> Path: return self.root / "inbox"
    @property
    def inbox_done(self) -> Path: return self.inbox / "done"

    # cache — `rm -rf` here loses nothing but time
    @property
    def cache(self) -> Path: return self.root / CACHE_DIR
    @property
    def feeds_cache(self) -> Path: return self.cache / "feeds"
    @property
    def text_store(self) -> Path: return self.cache / "text"
    @property
    def suppressed(self) -> Path: return self.cache / "suppressed.json"

    def shard(self, kind: str, year_month: str) -> Path:
        """kind in {archive, trash, events}; year_month like '2026-09'."""
        return (self.root / kind) / f"{year_month}.jsonl"
