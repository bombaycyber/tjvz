"""Dependency-free JSON Schema validator — the subset tjvz's schema files use.

Enforced keywords:
    type · required · enum · const · properties · items · oneOf ·
    additionalProperties
Everything else (`pattern`, `format`, `minLength`, `minItems`, `$id`,
`description`, ...) is ignored — it is there for external JSON Schema tooling
and for humans reading the files.

    from tjvz.schema import validate
    validate(record, "item")            # by schema name  -> schema/item.schema.json
    validate(manifest, "feature-manifest")
    validate(obj, {"type": "object"})   # or an inline schema
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# schema/ ships beside the package in the wheel (pyproject force-include);
# in a source checkout it sits at the repo root.
_HERE = Path(__file__).resolve()
_CANDIDATES = (_HERE.parent / "schema", _HERE.parents[2] / "schema")
SCHEMA_DIR = next((p for p in _CANDIDATES if p.is_dir()), _CANDIDATES[0])

_PY = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


class SchemaError(ValueError):
    """A value did not conform to its schema."""


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    path = SCHEMA_DIR / f"{name}.schema.json"
    if not path.is_file():
        raise SchemaError(f"no schema {name!r} (looked in {SCHEMA_DIR})")
    return json.loads(path.read_text())


def validate(obj, schema, path: str = "$") -> None:
    """Raise SchemaError on the first violation. `schema` is a name or a dict."""
    _check(obj, load(schema) if isinstance(schema, str) else schema, path)


def _check(obj, schema: dict, path: str) -> None:
    if "oneOf" in schema:
        matched = 0
        for branch in schema["oneOf"]:
            try:
                _check(obj, branch, path)
                matched += 1
            except SchemaError:
                pass
        if matched != 1:
            raise SchemaError(f"{path}: matched {matched} oneOf branches, want exactly 1")
        return

    if "const" in schema and obj != schema["const"]:
        raise SchemaError(f"{path}: {obj!r} != const {schema['const']!r}")

    if "enum" in schema and obj not in schema["enum"]:
        raise SchemaError(f"{path}: {obj!r} not in {schema['enum']}")

    t = schema.get("type")
    if t is not None:
        names = [t] if isinstance(t, str) else t
        ok = tuple(x for n in names for x in _PY[n])
        # bool is a subclass of int — only accept it when "boolean" is allowed
        if isinstance(obj, bool):
            if "boolean" not in names:
                raise SchemaError(f"{path}: expected {t}, got boolean")
        elif not isinstance(obj, ok):
            raise SchemaError(f"{path}: expected {t}, got {type(obj).__name__}")

    if isinstance(obj, dict):
        for key in schema.get("required", []):
            if key not in obj:
                raise SchemaError(f"{path}: missing required key {key!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, val in obj.items():
            if key in props:
                _check(val, props[key], f"{path}.{key}")
            elif extra is False:
                raise SchemaError(f"{path}: unexpected key {key!r}")
            elif isinstance(extra, dict):
                _check(val, extra, f"{path}.{key}")

    if isinstance(obj, list) and "items" in schema:
        for i, val in enumerate(obj):
            _check(val, schema["items"], f"{path}[{i}]")
