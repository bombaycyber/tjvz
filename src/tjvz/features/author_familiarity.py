"""author_familiarity — how much history you have with this item's author(s).

Count of items by the author you've judged (read + stacked + dismissed),
squashed to [0, 1) by saturate(n, scale). An *exposure* signal, orthogonal
to author_avg_rating (quality): an author you rate highly but have barely
seen is a promising stranger; pin a small negative weight on this feature
and unfamiliar authors get a mild boost (crude feature-value-uncertainty
exploration). Max over the item's authors.
"""
from __future__ import annotations

from tjvz.features import register, saturate
from tjvz.features._entity import authors, familiarity

MANIFEST = {
    "id": "author_familiarity",
    "kind": "feature",
    "version": 1,
    "since": "2026-09-06",
    "description": "saturate(count of judged items by this item's author(s), scale); exposure signal, orthogonal to quality. Max over authors.",
    "needs": ["archive", "stack", "trash"],
    "params": {
        "scale": {"type": "number", "default": 3.0, "tunable": True,
                  "description": "count at which the feature reaches 0.5"},
        "agg": {"type": "string", "default": "max", "description": "max | sum over the item's authors"},
    },
    "checks": [
        {"name": "unseen author -> 0", "fixture": "features/author_familiarity/unseen.json",
         "expect": 0.0, "tol": 1e-9},
        {"name": "six judged items -> saturate(6, 3)", "fixture": "features/author_familiarity/familiar.json",
         "expect": 0.666667, "tol": 1e-4},
    ],
    "supersedes": None,
}


@register
class AuthorFamiliarity:
    MANIFEST = MANIFEST

    def compute(self, item, ctx, params):
        p = {"scale": 3.0, "agg": "max", **(params or {})}
        per = [familiarity(a, authors, ctx) for a in authors(item)]
        if not per:
            return 0.0
        n = max(per) if p["agg"] == "max" else sum(per)
        return saturate(n, p["scale"])

    def explain(self, item, ctx, params):
        p = {"scale": 3.0, "agg": "max", **(params or {})}
        per = {a: familiarity(a, authors, ctx) for a in authors(item)}
        return {"feature": "author_familiarity", "scale": p["scale"],
                "counts": per}
