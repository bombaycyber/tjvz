"""pub_avg_rating — the average rating of items from this item's publication.

Same shrinkage average as author_avg_rating, keyed on `item.publication`.
See _entity.py.
"""
from __future__ import annotations

from tjvz.features import register
from tjvz.features._entity import DRAW_DEFAULTS, avg_rating, publication

MANIFEST = {
    "id": "pub_avg_rating",
    "kind": "feature",
    "version": 1,
    "since": "2026-09-06",
    "description": "Shrinkage average rating (on a -1..5 scale) of items from this item's publication; prior mean 3.",
    "needs": ["archive", "stack", "trash"],
    "params": {
        "v_dismiss": {"type": "number", "default": -1.0},
        "v_stack": {"type": "number", "default": 2.5},
        "v_read_unrated": {"type": "number", "default": 3.0},
        "tau0": {"type": "number", "default": 2.0, "tunable": True,
                 "description": "prior pseudo-count; 0 drops the shrinkage"},
        "mu0": {"type": "number", "default": 3.0, "tunable": True,
                "description": "prior average for a publication with no history"},
    },
    "checks": [
        {"name": "unseen publication -> prior 3", "fixture": "features/pub_avg_rating/unseen.json",
         "expect": 3.0, "tol": 1e-9},
        {"name": "read 5 and 3, dismissed 1", "fixture": "features/pub_avg_rating/history.json",
         "expect": 2.6, "tol": 1e-9},
    ],
    "supersedes": None,
}


@register
class PubAvgRating:
    MANIFEST = MANIFEST

    def compute(self, item, ctx, params):
        p = {**DRAW_DEFAULTS, **(params or {})}
        pubs = publication(item)
        if not pubs:
            return p["mu0"]
        return max(avg_rating(pub, publication, ctx, p)[0] for pub in pubs)

    def explain(self, item, ctx, params):
        p = {**DRAW_DEFAULTS, **(params or {})}
        per = {pub: avg_rating(pub, publication, ctx, p) for pub in publication(item)}
        return {
            "feature": "pub_avg_rating",
            "publications": {pub: {"n_judged": n, "avg": round(v, 3)} for pub, (v, n) in per.items()},
        }
