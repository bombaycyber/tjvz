"""author_avg_rating — the average rating of items by this item's author(s).

Shrinkage posterior mean of your draws on that author's past items
(dismiss = -1, stack = 2.5, read = your 1-5 rating, unrated read = 3),
prior-pulled toward a neutral 3. Max over the item's authors. Cold-start
friendly: an author in your archive carries signal before any promote /
dismiss feedback; an unseen author sits at 3. See _entity.py.
"""
from __future__ import annotations

from tjvz.features import register
from tjvz.features._entity import DRAW_DEFAULTS, authors, avg_rating

MANIFEST = {
    "id": "author_avg_rating",
    "kind": "feature",
    "version": 1,
    "since": "2026-09-06",
    "description": "Shrinkage average rating (on a -1..5 scale) of items by this item's author(s); max over authors; prior mean 3.",
    "needs": ["archive", "stack", "trash"],
    "params": {
        "v_dismiss": {"type": "number", "default": -1.0},
        "v_stack": {"type": "number", "default": 2.5},
        "v_read_unrated": {"type": "number", "default": 3.0},
        "tau0": {"type": "number", "default": 2.0, "tunable": True,
                 "description": "prior pseudo-count; 0 drops the shrinkage"},
        "mu0": {"type": "number", "default": 3.0, "tunable": True,
                "description": "prior average for an author with no history"},
        "agg": {"type": "string", "default": "max", "description": "max | mean over the item's authors"},
    },
    "checks": [
        {"name": "unseen author -> prior 3", "fixture": "features/author_avg_rating/unseen.json",
         "expect": 3.0, "tol": 1e-9},
        {"name": "one read rated 5", "fixture": "features/author_avg_rating/one_rated.json",
         "expect": 3.666667, "tol": 1e-4},
        {"name": "loved vs dropped, max wins", "fixture": "features/author_avg_rating/mixed.json",
         "expect": 4.2, "tol": 1e-9},
    ],
    "supersedes": None,
}


@register
class AuthorAvgRating:
    MANIFEST = MANIFEST

    def compute(self, item, ctx, params):
        p = {**DRAW_DEFAULTS, "agg": "max", **(params or {})}
        vals = [avg_rating(a, authors, ctx, p)[0] for a in authors(item)]
        if not vals:
            return p["mu0"]
        return max(vals) if p["agg"] == "max" else sum(vals) / len(vals)

    def explain(self, item, ctx, params):
        p = {**DRAW_DEFAULTS, "agg": "max", **(params or {})}
        per = {a: avg_rating(a, authors, ctx, p) for a in authors(item)}
        return {
            "feature": "author_avg_rating",
            "authors": {a: {"n_judged": n, "avg": round(v, 3)} for a, (v, n) in per.items()},
        }
