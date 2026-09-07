"""reader_recs (feature 4) — a column-group feature.

For an item, one column per reader with a signal on it (from a `kind:
reader` subscription or an AI drop), column value = that reader's 1-5
rating (or `v_surfaced` when they surfaced it without rating). `learn()`
fits a per-reader weight `w_R` (id `reader_recs:<source>`), prior 0, no box,
ordinary annealed shrinkage — a reader you've barely seen stays at ~0. The
score contribution is `Σ_R w_R · rating_R`; the outer model weight is fixed
at 1 (do NOT give `reader_recs` a weight in config). Contributes 0 until
reader feeds populate `readers[]` and a learn run has moved some `w_R`.
"""
from __future__ import annotations

from tjvz.features import register

MANIFEST = {
    "id": "reader_recs",
    "kind": "feature",
    "version": 1,
    "since": "2026-09-06",
    "expands": True,
    "description": "Column-group: one column per reader on the item, value = their 1-5 rating (v_surfaced if unrated). Per-reader weights are learned (prior 0). Score contribution = sum_R w_R * rating_R.",
    "needs": ["item"],
    "params": {
        "v_surfaced": {"type": "number", "default": 3.0,
                       "description": "imputed rating for a reader who surfaced the item without rating it"},
    },
    "checks": [
        {"name": "no readers -> no columns", "fixture": "features/reader_recs/none.json", "expect": {}},
        {"name": "two rated readers", "fixture": "features/reader_recs/two.json",
         "expect": {"reader_recs:birkar": 5.0, "reader_recs:alice": 2.0}},
        {"name": "unrated surface is imputed", "fixture": "features/reader_recs/surfaced.json",
         "expect": {"reader_recs:hn": 3.0}},
    ],
    "supersedes": None,
}


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


@register
class ReaderRecs:
    MANIFEST = MANIFEST

    def columns(self, item, ctx, params) -> dict[str, float]:
        v_surf = (params or {}).get("v_surfaced", 3.0)
        out: dict[str, float] = {}
        for r in item.get("readers") or []:
            src = (r.get("source") or "").strip().lower()
            if not src:
                continue
            rat = r.get("rating")
            out[f"reader_recs:{src}"] = float(rat) if _num(rat) else float(v_surf)
        return out

    def explain(self, item, ctx, params) -> dict:
        return {"feature": "reader_recs", "columns": self.columns(item, ctx, params)}
