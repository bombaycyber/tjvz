"""LinearModel: score / explain determinism, and learn() on a separable toy set.

    python -m pytest tests/test_model.py     (or)     python tests/test_model.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import tjvz.features.reader_recs  # noqa: E402,F401  (registers the group feature)
from tjvz.model import LinearModel  # noqa: E402

CFG = {
    "scoring": {
        "features": {
            "signal": {"weight": 0.0},
            "noise": {"weight": 0.0},
            "novelty": {"weight": -0.3, "pinned": True},
        },
        "bias": 0.0,
    },
    "learning": {
        "utility": "log_odds",
        "label": {"positive": ["read", "stack"]},
        "trainable": "weights",
        "regularization": {"lambda0": 0.15, "anneal_n0": 50},
        "adopt": {"min_pos": 10, "min_neg": 10, "min_varying_features": 1, "cv_folds": 5, "metric": "log_loss"},
    },
}


def _toy_rows(n=60):
    rng = random.Random(0)
    rows = []
    for i in range(n):
        sig = 0.9 if i % 2 == 0 else 0.1
        rows.append({
            "decision": "read" if sig > 0.5 else "dismiss",
            "features": {"signal": sig, "noise": rng.uniform(0, 1), "novelty": rng.uniform(0, 1)},
        })
    return rows


def test_score_and_explain_deterministic():
    m = LinearModel(CFG)
    feats = {"signal": 0.8, "noise": 0.5, "novelty": 0.4}
    # bias 0 + 0*0.8 + 0*0.5 + (-0.3)*0.4
    assert abs(m.score(feats) - (-0.12)) < 1e-9
    assert m.score(feats) == m.score(feats)
    ex = m.explain(feats)
    assert [t["feature"] for t in ex["terms"]][0] == "novelty"  # only nonzero contribution
    assert abs(ex["score"] - (-0.12)) < 1e-4


def test_learn_recovers_signal():
    m = LinearModel(CFG)
    res = m.learn(_toy_rows())

    assert res.n_pos == 30 and res.n_neg == 30
    assert res.weights["signal"] > 1.5, res.weights
    assert abs(res.weights["noise"]) < 0.6, res.weights
    assert res.weights["novelty"] == -0.3          # pinned, untouched
    assert res.beats_prior
    assert res.adopt, res.blockers
    assert res.cv_logloss < res.cv_logloss_prior


def test_learn_fits_per_reader_weights():
    cfg = {
        "scoring": {"features": {"signal": {"weight": 0.0}, "reader_recs": {}}, "bias": 0.0},
        "learning": {
            "utility": "log_odds",
            "label": {"positive": ["read", "stack"]},
            "regularization": {"lambda0": 0.15, "anneal_n0": 50},
            "adopt": {"min_pos": 10, "min_neg": 10, "cv_folds": 5},
        },
    }
    m = LinearModel(cfg)
    assert m.groups == {"reader_recs"}
    rng = random.Random(2)
    rows = []
    for i in range(160):
        # trusty's rating drives the read decision, but not perfectly (~15% flips)
        rating = rng.choice([1.0, 2.0, 4.0, 5.0])
        read = (rating >= 3.0) != (rng.random() < 0.15)
        rows.append({
            "decision": "read" if read else "dismiss",
            "features": {
                "signal": 0.0,  # no scalar signal — the readers carry it
                "reader_recs:trusty": rating,
                "reader_recs:flaky": float(rng.choice([1, 3, 5])),  # independent of the decision
            },
        })
    res = m.learn(rows)
    assert res.weights["reader_recs:trusty"] > 0.3, res.weights
    assert abs(res.weights["reader_recs:flaky"]) < 0.25, res.weights
    # score() uses the learned per-reader weights
    m.weights = res.weights
    hi = m.score({"reader_recs:trusty": 5.0})
    lo = m.score({"reader_recs:trusty": 1.0})
    assert hi > lo


def test_learn_needs_learning_block():
    m = LinearModel({"scoring": {"features": {"a": {"weight": 1.0}}}})
    try:
        m.learn([])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError with no learning block")


if __name__ == "__main__":
    test_score_and_explain_deterministic()
    test_learn_recovers_signal()
    test_learn_fits_per_reader_weights()
    test_learn_needs_learning_block()
    r = LinearModel(CFG).learn(_toy_rows())
    print(f"ok — signal weight {r.weights['signal']:.3f}, noise {r.weights['noise']:.3f}, "
          f"cv {r.cv_logloss:.4f} vs prior {r.cv_logloss_prior:.4f}, adopt={r.adopt}")
