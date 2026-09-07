"""`tjvz model learn` / `status` — fit the one model to the event log.

Every `events/` row is replayed at its own `at`: features are recomputed
leave-one-out and as-of that moment (`ctx.as_of`), so adding or changing a
feature and re-learning reflects it across the whole training set with no
event rewriting. The per-event feature dicts are cached under
`.tjvz/features/<fingerprint>.json`; `tjvz features recompute` drops the
cache.

`learn()` reports whether the fit beats the config weights on CV and
whether the `learning.adopt` gates clear — it does not adopt on its own.
`--adopt` writes `model.json` (`source: "learned"`) only when every gate
passes.
"""
from __future__ import annotations

import hashlib
import json

from tjvz import ctx, features, store
from tjvz.model import LinearModel
from tjvz.schema import validate


# --- feature-set fingerprint ------------------------------------------------

def fingerprint(feature_cfg: dict) -> str:
    """sha256[:12] over the sorted (id, version, manifest-hash) triples of
    the active feature set. A change here means the cached feature values
    and any learned weights are for a different model."""
    features.load_all()
    triples = []
    for fid in sorted(feature_cfg or {}):
        cls = features.REGISTRY.get(fid)
        if cls is None:
            continue
        man = cls.MANIFEST
        mh = hashlib.sha256(json.dumps(man, sort_keys=True).encode()).hexdigest()[:12]
        triples.append([fid, man.get("version", 1), mh])
    return hashlib.sha256(json.dumps(triples).encode()).hexdigest()[:12]


# --- per-event feature recompute (cached) ----------------------------------

def _bodies(profile) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in store.iter_records(profile.archive_dir, profile.trash_dir, kind="item"):
        out[r["id"]] = r
    for r in store.load_stack(profile):
        out.setdefault(r["id"], r)
    return out


def _events(profile) -> list[dict]:
    return sorted(store.iter_records(profile.events_dir, kind="event"),
                  key=lambda e: str(e.get("at") or ""))


def feature_rows(profile, cfg: dict, *, rebuild: bool = False) -> tuple[list[dict], list[str], str]:
    """(rows, missing_body_ids, fingerprint). rows: [{decision, features}]."""
    fcfg = cfg["model"]["scoring"]["features"]
    fp = fingerprint(fcfg)
    cache_path = profile.cache / "features" / f"{fp}.json"
    cache: dict = {} if rebuild else store.load_json(cache_path, {})

    bodies = _bodies(profile)
    rows: list[dict] = []
    missing: list[str] = []
    dirty = False
    for ev in _events(profile):
        key = f"{ev['id']}@{ev['at']}"
        feats = cache.get(key)
        if feats is None:
            item = bodies.get(ev["id"])
            if item is None:
                missing.append(ev["id"])
                continue
            bundle = ctx.as_of(profile, cfg, ev["at"], exclude_id=ev["id"])
            feats = features.evaluate(item, bundle, fcfg)
            cache[key] = feats
            dirty = True
        rows.append({"decision": ev["decision"], "features": feats})
    if dirty:
        store.save_json(cache_path, cache)
    return rows, missing, fp


# --- learn / adopt / status ----------------------------------------------------

def learn(profile, cfg: dict, *, adopt: bool = False, rebuild: bool = False) -> dict:
    rows, missing, fp = feature_rows(profile, cfg, rebuild=rebuild)
    if not rows:
        raise ValueError("no usable events to learn from — judge some feed items first")

    res = LinearModel(cfg["model"]).learn(rows)
    out = {
        "n_events": len(rows),
        "n_pos": res.n_pos,
        "n_neg": res.n_neg,
        "missing_bodies": missing,
        "cv_logloss": round(res.cv_logloss, 4),
        "cv_logloss_prior": round(res.cv_logloss_prior, 4),
        "beats_prior": res.beats_prior,
        "adopt_ready": res.adopt,
        "blockers": res.blockers,
        "bias": round(res.bias, 4),
        "weights": {k: round(v, 4) for k, v in res.weights.items()},
        "fingerprint": fp,
        "adopted": False,
    }

    if adopt and not res.adopt:
        out["refused"] = "adopt gates not clear: " + "; ".join(res.blockers)
    elif adopt:
        evs = _events(profile)
        state = {
            "schema_version": store.SCHEMA_VERSION,
            "source": "learned",
            "weights": {k: float(v) for k, v in res.weights.items()},
            "bias": float(res.bias),
            "features_fingerprint": fp,
            "learned_at": store.now_iso(),
            "learned": {
                "n_pos": res.n_pos,
                "n_neg": res.n_neg,
                "cv_metric": "log_loss",
                "cv_score": round(res.cv_logloss, 6),
                "cv_score_prior": round(res.cv_logloss_prior, 6),
                "events_through": evs[-1]["at"] if evs else store.now_iso(),
            },
        }
        validate(state, "model-state")
        store.save_json(profile.model, state)
        out["adopted"] = True

    return out


def status(profile, cfg: dict) -> dict:
    fp_live = fingerprint(cfg["model"]["scoring"]["features"])
    state = store.load_json(profile.model, None)
    if state is None:
        return {"source": "prior", "has_model_json": False,
                "fingerprint_live": fp_live, "stale": False,
                "weights": _prior_weights(cfg), "bias": cfg["model"]["scoring"].get("bias", 0.0)}
    return {
        "source": state.get("source"),
        "has_model_json": True,
        "weights": state.get("weights", {}),
        "bias": state.get("bias"),
        "learned_at": state.get("learned_at"),
        "learned": state.get("learned"),
        "fingerprint_model": state.get("features_fingerprint"),
        "fingerprint_live": fp_live,
        "stale": state.get("features_fingerprint") != fp_live,
    }


def _prior_weights(cfg: dict) -> dict:
    return {f: spec["weight"]
            for f, spec in cfg["model"]["scoring"]["features"].items()
            if isinstance(spec, dict) and "weight" in spec}


def recompute(profile) -> int:
    """Drop the feature cache; the next learn rebuilds it. Returns the
    number of cache files removed."""
    d = profile.cache / "features"
    n = 0
    if d.is_dir():
        for f in d.glob("*.json"):
            f.unlink()
            n += 1
    return n
