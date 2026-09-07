"""Load every feature module, validate its manifest, and run its golden checks.

    python -m pytest tests/test_features.py        (or)      python tests/test_features.py
"""
from __future__ import annotations

import importlib
import json
import math
import pkgutil
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import tjvz.features as features_pkg  # noqa: E402
from tjvz.features import REGISTRY  # noqa: E402
from tjvz.schema import validate  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _load_all() -> None:
    for m in pkgutil.iter_modules(features_pkg.__path__):
        if not m.name.startswith("_"):
            importlib.import_module(f"tjvz.features.{m.name}")


_load_all()
CASES = [
    (fid, cls, chk)
    for fid, cls in sorted(REGISTRY.items())
    for chk in cls.MANIFEST["checks"]
]


def test_registry_nonempty():
    assert REGISTRY, "no features registered"


def test_manifests_valid():
    for fid, cls in REGISTRY.items():
        validate(cls.MANIFEST, "feature-manifest")
        assert cls.MANIFEST["id"] == fid


def test_feature_checks():
    for fid, cls, chk in CASES:
        inst = cls()
        params = {k: v["default"] for k, v in cls.MANIFEST["params"].items()}
        params.update(chk.get("params", {}))
        fx = json.loads((FIXTURES / chk["fixture"]).read_text())

        if cls.MANIFEST.get("expands"):
            got = inst.columns(fx["item"], fx["ctx"], params)
            assert got == chk["expect"], f"{fid}/{chk['name']}: {got!r} != {chk['expect']!r}"
        else:
            got = inst.compute(fx["item"], fx["ctx"], params)
            assert math.isfinite(got), f"{fid}/{chk['name']}: non-finite {got!r}"
            tol = chk.get("tol", 1e-6)
            assert abs(got - chk["expect"]) <= tol, (
                f"{fid}/{chk['name']}: got {got!r}, expected {chk['expect']!r}"
            )

        exp = inst.explain(fx["item"], fx["ctx"], params)
        assert exp.get("feature") == fid


if __name__ == "__main__":
    test_registry_nonempty()
    test_manifests_valid()
    test_feature_checks()
    print(f"ok — {len(REGISTRY)} features, {len(CASES)} checks")
