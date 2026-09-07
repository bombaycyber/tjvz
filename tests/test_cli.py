"""CLI smoke: init writes a valid profile, the registry resolves it, planned
commands fail cleanly.

    python -m pytest tests/test_cli.py   (or)   python tests/test_cli.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import yaml  # noqa: E402

from tjvz import paths  # noqa: E402
from tjvz.canon import canonicalise  # noqa: E402
from tjvz.cli import CONFIG_STUB, main  # noqa: E402
from tjvz.schema import validate  # noqa: E402


def _isolated(monkey_home: Path):
    paths._REGISTRY = monkey_home / ".config" / "tjvz" / "profiles.json"


def test_config_stub_is_valid():
    cfg = yaml.safe_load(CONFIG_STUB)
    validate(cfg["model"], "model-config")


def test_init_and_resolve(capsys):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _isolated(td)
        assert main(["init", str(td / "p"), "--name", "p"]) == 0
        root = td / "p"
        assert (root / paths.MARKER).is_file()
        assert yaml.safe_load((root / "config.yaml").read_text())["schema"] == 1
        for d in ("archive", "trash", "events", "inbox"):
            assert (root / d).is_dir()

        prof = paths.resolve_profile(profile="p")
        assert prof.root == root.resolve()
        assert prof.archive_dir == root.resolve() / "archive"

        capsys.readouterr()
        assert main(["--json", "profile", "list"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["current"] == "p"


def test_planned_command_exits_nonzero():
    try:
        main(["explain"])
    except SystemExit as e:
        assert e.code and "not implemented" in str(e.code)
    else:
        raise AssertionError("expected SystemExit")


def test_canonicalise_roundtrips_through_schema():
    item, stream = canonicalise({
        "title": "  A  Test ", "url": "http://WWW.example.com/x/?utm_source=z",
        "authors": ["Jane Doe"], "tags": ["Foo", "foo", "bar-baz"], "stream": "archive",
        "rating": 5,
    })
    validate(item, "item")
    assert stream == "archive"
    assert item["id"].startswith("u:")
    assert item["canonical_url"] == "https://www.example.com/x"
    assert item["authors"] == [{"name": "Jane Doe", "role": "author"}]
    assert item["tags"] == ["foo", "bar-baz"]
    assert item["publication"] == "www.example.com"
    assert item["rating"] == 5 and item["read"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
