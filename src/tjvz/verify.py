"""`tjvz verify` — golden checks and structural sanity for the code + a profile.

Checks, each reported pass / fail / skip:
  * every feature MANIFEST validates and its `id` matches the module
  * every feature's golden `checks` reproduce (skipped if the repo's
    tests/fixtures/ tree isn't on disk — e.g. an installed wheel)
  * a feature's check values aren't wildly out of scale (|v| <= SCALE_WARN)
  * every schema/*.schema.json parses
  * config.yaml and subscriptions.yaml load and validate
  * model.json (if present) validates and is not stale vs the live features
  * a sample of stored records still validates against schema/item|event

Exit non-zero if anything failed (warnings and skips don't fail).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from tjvz import features
from tjvz.schema import SCHEMA_DIR, SchemaError, validate

SCALE_WARN = 50.0
_FIXTURES = next((p / "tests" / "fixtures"
                  for p in Path(__file__).resolve().parents
                  if (p / "tests" / "fixtures").is_dir()), None)


class _Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []  # (status, name, detail)

    def ok(self, name, detail=""):
        self.rows.append(("ok", name, detail))

    def fail(self, name, detail=""):
        self.rows.append(("FAIL", name, detail))

    def warn(self, name, detail=""):
        self.rows.append(("warn", name, detail))

    def skip(self, name, detail=""):
        self.rows.append(("skip", name, detail))

    @property
    def failed(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == "FAIL")

    def as_dict(self) -> dict:
        counts: dict[str, int] = {}
        for s, _, _ in self.rows:
            counts[s] = counts.get(s, 0) + 1
        glyph = {"ok": "✓", "skip": "·", "warn": "!", "FAIL": "✗"}
        lines = [f"  {glyph[s]} {n}" + (f"  — {d}" if d else "") for s, n, d in self.rows]
        summary = "  " + "  ".join(f"{v} {k}" for k, v in sorted(counts.items())) if counts else "  (nothing to check)"
        return {
            "checks": [{"status": s, "name": n, "detail": d} for s, n, d in self.rows],
            "counts": counts,
            "ok": self.failed == 0,
            "_text": "\n".join([*lines, summary]),
        }


def _check_features(rep: _Report) -> None:
    features.load_all()
    for fid, cls in sorted(features.REGISTRY.items()):
        try:
            validate(cls.MANIFEST, "feature-manifest")
            assert cls.MANIFEST["id"] == fid
            rep.ok(f"feature/{fid}: manifest")
        except (SchemaError, AssertionError, KeyError) as e:
            rep.fail(f"feature/{fid}: manifest", str(e))
            continue

        if _FIXTURES is None:
            rep.skip(f"feature/{fid}: checks", "no fixtures on disk")
            continue
        inst = cls()
        defaults = {k: v.get("default") for k, v in (cls.MANIFEST.get("params") or {}).items()}
        for chk in cls.MANIFEST.get("checks", []):
            params = {**defaults, **chk.get("params", {})}
            try:
                fx = json.loads((_FIXTURES / chk["fixture"]).read_text())
            except FileNotFoundError:
                rep.fail(f"feature/{fid}: {chk['name']}", f"missing fixture {chk['fixture']}")
                continue
            try:
                if cls.MANIFEST.get("expands"):
                    got = inst.columns(fx["item"], fx["ctx"], params)
                    assert got == chk["expect"], f"{got!r} != {chk['expect']!r}"
                else:
                    got = inst.compute(fx["item"], fx["ctx"], params)
                    assert math.isfinite(got), f"non-finite {got!r}"
                    assert abs(got - chk["expect"]) <= chk.get("tol", 1e-6), \
                        f"{got!r} != {chk['expect']!r}"
                    if abs(got) > SCALE_WARN:
                        rep.warn(f"feature/{fid}: {chk['name']}", f"value {got:.1f} out of scale")
                rep.ok(f"feature/{fid}: {chk['name']}")
            except (AssertionError, KeyError, TypeError) as e:
                rep.fail(f"feature/{fid}: {chk['name']}", str(e))


def _check_schemas(rep: _Report) -> None:
    for f in sorted(SCHEMA_DIR.glob("*.schema.json")):
        try:
            json.loads(f.read_text())
            rep.ok(f"schema/{f.name}")
        except json.JSONDecodeError as e:
            rep.fail(f"schema/{f.name}", str(e))


def _check_profile(rep: _Report, profile, cfg) -> None:
    from tjvz import config, learn, store

    try:
        validate(cfg["model"], "model-config")
        rep.ok("profile/config.yaml")
    except SchemaError as e:
        rep.fail("profile/config.yaml", str(e))

    try:
        config.load_subscriptions(profile)
        rep.ok("profile/subscriptions.yaml")
    except (SchemaError, ValueError) as e:
        rep.fail("profile/subscriptions.yaml", str(e))

    state = store.load_json(profile.model, None)
    if state is None:
        rep.skip("profile/model.json", "not learned yet (using config weights)")
    else:
        try:
            validate(state, "model-state")
            fp = learn.fingerprint(cfg["model"]["scoring"]["features"])
            if state.get("features_fingerprint") != fp:
                rep.warn("profile/model.json", "stale vs live features — `tjvz model learn`")
            else:
                rep.ok("profile/model.json")
        except SchemaError as e:
            rep.fail("profile/model.json", str(e))

    for kind, directory, schema in (("item", profile.archive_dir, "item"),
                                    ("item", profile.trash_dir, "item"),
                                    ("event", profile.events_dir, "event")):
        bad = 0
        seen = 0
        for rec in store.iter_records(directory, kind=schema):
            seen += 1
            try:
                validate(rec, schema)
            except SchemaError:
                bad += 1
            if seen >= 500:
                break
        if seen == 0:
            rep.skip(f"records/{directory.name}", "empty")
        elif bad:
            rep.fail(f"records/{directory.name}", f"{bad}/{seen} invalid")
        else:
            rep.ok(f"records/{directory.name}", f"{seen} checked")


def verify(profile=None, cfg=None) -> dict:
    rep = _Report()
    _check_features(rep)
    _check_schemas(rep)
    if profile is not None and cfg is not None:
        _check_profile(rep, profile, cfg)
    return rep.as_dict()
