"""Features — each is  f(item, ctx, params) -> real number.

One module per feature, exposing a MANIFEST dict (validated against
schema/feature-manifest.schema.json) and a compute() / explain() pair.
`@register` adds it to the registry.

Key rules:
  * compute() returns a real number. The model does a plain linear combine
    `bias + sum(w_i * f_i)` — there is NO normalisation layer. By
    CONVENTION a feature keeps its output at a sensible scale (~[0,1] or
    [-1,1]); a naturally-unbounded feature applies its own saturate() inside
    compute() with the scale as a param, and says so in its description.
    `tjvz verify` warns if a feature's check values come out wildly scaled.
  * Feature values are NEVER stored on item records or events. They are
    recomputed on demand — leave-one-out, against the archive as of the
    decision's timestamp — so a feature can be added or changed and the
    whole training set reflects it after `tjvz features recompute`.
  * `params` are fixed structural knobs (v1: not gradient-fit); config.yaml
    overrides the manifest defaults.
  * `needs` lists which `ctx` members compute() reads — see schema/ctx.md.
    The runtime only builds what some active feature needs.

Interaction / composite features (relevance x author_familiarity) are
ordinary features under this same contract — the nonlinearity lives here,
the model stays linear.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Protocol, runtime_checkable

from tjvz.schema import validate

REGISTRY: dict[str, "type[Feature]"] = {}
_LOADED = False


def register(cls: "type[Feature]") -> "type[Feature]":
    validate(cls.MANIFEST, "feature-manifest")
    fid = cls.MANIFEST["id"]
    if fid in REGISTRY:
        raise ValueError(f"duplicate feature id: {fid!r}")
    REGISTRY[fid] = cls
    return cls


def load_all() -> None:
    """Import every feature module so the registry is populated. Idempotent."""
    global _LOADED
    if _LOADED:
        return
    for m in pkgutil.iter_modules(__path__):
        if not m.name.startswith("_"):
            importlib.import_module(f"{__name__}.{m.name}")
    _LOADED = True


def evaluate(item: dict, ctx: Any, feature_cfg: dict) -> dict[str, float]:
    """The full feature dict for one item under `ctx`.

    `feature_cfg` is config.yaml's `model.scoring.features` — {id: {params?,
    weight?, ...}}. A plain feature contributes one entry `{id: value}`; a
    column-group feature (`expands`) contributes one entry per column
    (`{"reader_recs:<src>": value}`). Params are the manifest defaults with
    the config's `params` merged over them.
    """
    load_all()
    out: dict[str, float] = {}
    for fid, spec in (feature_cfg or {}).items():
        cls = REGISTRY.get(fid)
        if cls is None:
            continue
        man = cls.MANIFEST
        params = {k: v.get("default") for k, v in (man.get("params") or {}).items()}
        params.update((spec or {}).get("params") or {})
        inst = cls()
        if man.get("expands"):
            out.update({k: float(v) for k, v in inst.columns(item, ctx, params).items()})
        else:
            out[fid] = float(inst.compute(item, ctx, params))
    return out


def saturate(x: float, scale: float) -> float:
    """A feature's own optional squash for a naturally-unbounded quantity:
    x / (x + scale), monotone, in [0, 1), = 0.5 at x == scale. Features call
    this inside compute() when they choose to; the model never does."""
    return x / (x + scale) if x > 0 else 0.0


@runtime_checkable
class Feature(Protocol):
    MANIFEST: dict

    def compute(self, item: dict, ctx: Any, params: dict) -> float:
        """The raw feature value for `item` given world-state `ctx`."""
        ...

    def explain(self, item: dict, ctx: Any, params: dict) -> dict:
        """A human-readable breakdown of that value: the inputs it used and
        how they combined. Surfaced by `tjvz explain`."""
        ...
