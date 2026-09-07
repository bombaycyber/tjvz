"""M5 — model learn / status, feature recompute cache, verify.

    python -m pytest tests/test_learn.py    (or)    python tests/test_learn.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
for _p in (str(_SRC), str(Path(__file__).parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tjvz import config, learn, paths, store, verify  # noqa: E402
from tjvz.cli import main  # noqa: E402


def _profile(tmp: Path) -> paths.Profile:
    paths._REGISTRY = tmp / ".config" / "tjvz" / "profiles.json"
    root = tmp / "p"
    assert main(["init", str(root), "--name", "p"]) == 0
    return paths.Profile(root.resolve())


def _seed_events(p: paths.Profile, n_ai: int, n_cook: int) -> None:
    """Judge tagged feed items: ai-tagged read, cooking-tagged dismissed —
    a signal `relevance` should recover once the archive fills with ai tags."""
    for i in range(n_ai):
        main(["--profile", "p", "add", "--title", f"AI {i}", "--url", f"https://f.com/ai{i}",
              "--author", "Ann", "--tag", "ai", "--tag", "ml", "--stream", "feed"])
        main(["--profile", "p", "read", f"https://f.com/ai{i}", "--rating", "4"])
    for i in range(n_cook):
        main(["--profile", "p", "add", "--title", f"Cook {i}", "--url", f"https://f.com/ck{i}",
              "--author", "Bo", "--tag", "cooking", "--stream", "feed"])
        main(["--profile", "p", "dismiss", f"https://f.com/ck{i}"])


def test_learn_reports_and_gates():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _seed_events(p, 8, 6)  # 14 events — below the default adopt gates
        cfg = config.load(p)

        r = learn.learn(p, cfg)
        assert r["n_events"] == 14 and r["n_pos"] == 8 and r["n_neg"] == 6
        assert r["beats_prior"]                       # relevance separates them
        assert not r["adopt_ready"]                   # n_pos / n_neg < 20
        assert any("n_pos" in b for b in r["blockers"])
        assert not r["adopted"]
        assert not p.model.exists()                   # nothing written

        # the feature cache was populated under the fingerprint
        fp = learn.fingerprint(cfg["model"]["scoring"]["features"])
        assert (p.cache / "features" / f"{fp}.json").exists()


def test_learn_adopts_when_gates_clear():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _seed_events(p, 24, 22)
        cfg = config.load(p)

        r = learn.learn(p, cfg, adopt=True)
        assert r["adopt_ready"] and r["adopted"]
        assert r["beats_prior"]
        assert r["weights"]["relevance"] > 1.0                     # the recovered signal
        assert r["weights"]["relevance"] == max(r["weights"].values())

        state = json.loads(p.model.read_text())
        assert state["source"] == "learned"
        assert state["features_fingerprint"] == r["fingerprint"]
        assert state["learned"]["n_pos"] == 24 and state["learned"]["cv_metric"] == "log_loss"

        s = learn.status(p, cfg)
        assert s["source"] == "learned" and not s["stale"]

        # a learned model.json feeds ranking
        from tjvz.model import LinearModel
        m = LinearModel(cfg["model"], store.load_json(p.model, None))
        assert abs(m.weights["relevance"] - r["weights"]["relevance"]) < 1e-3  # r is rounded to 4dp


def test_feature_cache_reused_and_recomputed():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _seed_events(p, 5, 5)
        cfg = config.load(p)

        rows1, _, fp = learn.feature_rows(p, cfg)
        cache = p.cache / "features" / f"{fp}.json"
        mtime = cache.stat().st_mtime_ns
        rows2, _, _ = learn.feature_rows(p, cfg)          # served from cache
        assert rows1 == rows2
        assert cache.stat().st_mtime_ns == mtime         # not rewritten

        assert learn.recompute(p) == 1
        assert not cache.exists()
        learn.feature_rows(p, cfg)
        assert cache.exists()                             # rebuilt


def test_stale_fingerprint_flagged():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        _seed_events(p, 22, 22)
        cfg = config.load(p)
        learn.learn(p, cfg, adopt=True)

        state = json.loads(p.model.read_text())
        state["features_fingerprint"] = "deadbeef0000"
        p.model.write_text(json.dumps(state))
        assert learn.status(p, cfg)["stale"] is True


def test_verify_passes_on_fresh_profile():
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        res = verify.verify(p, config.load(p))
        assert res["ok"], [c for c in res["checks"] if c["status"] == "FAIL"]
        assert res["counts"].get("FAIL", 0) == 0
        names = {c["name"] for c in res["checks"]}
        assert "schema/item.schema.json" in names
        assert any(n.startswith("feature/relevance") for n in names)


def test_verify_cli_exit_code(capsys):
    with tempfile.TemporaryDirectory() as td:
        p = _profile(Path(td))
        # corrupt a stored event -> verify must fail and exit non-zero
        store.append_jsonl(p.shard("events", "2026-09"),
                           {"schema_version": 1, "id": "u:" + "0" * 16, "decision": "nope",
                            "at": "2026-09-01T00:00:00Z", "model": "linear"})
        capsys.readouterr()
        assert main(["--profile", "p", "verify"]) == 1
        assert "FAIL" in capsys.readouterr().out


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
