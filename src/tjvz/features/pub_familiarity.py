"""pub_familiarity — how much history you have with this item's publication.

saturate(count of judged items from the publication, scale). Exposure
signal, orthogonal to pub_avg_rating. See author_familiarity.
"""
from __future__ import annotations

from tjvz.features import register, saturate
from tjvz.features._entity import familiarity, publication

MANIFEST = {
    "id": "pub_familiarity",
    "kind": "feature",
    "version": 1,
    "since": "2026-09-06",
    "description": "saturate(count of judged items from this item's publication, scale); exposure signal, orthogonal to quality.",
    "needs": ["archive", "stack", "trash"],
    "params": {
        "scale": {"type": "number", "default": 3.0, "tunable": True,
                  "description": "count at which the feature reaches 0.5"},
    },
    "checks": [
        {"name": "unseen publication -> 0", "fixture": "features/pub_familiarity/unseen.json",
         "expect": 0.0, "tol": 1e-9},
        {"name": "five judged items -> saturate(5, 3)", "fixture": "features/pub_familiarity/familiar.json",
         "expect": 0.625, "tol": 1e-9},
    ],
    "supersedes": None,
}


@register
class PubFamiliarity:
    MANIFEST = MANIFEST

    def compute(self, item, ctx, params):
        p = {"scale": 3.0, **(params or {})}
        pubs = publication(item)
        if not pubs:
            return 0.0
        return saturate(max(familiarity(pub, publication, ctx) for pub in pubs), p["scale"])

    def explain(self, item, ctx, params):
        p = {"scale": 3.0, **(params or {})}
        return {"feature": "pub_familiarity", "scale": p["scale"],
                "counts": {pub: familiarity(pub, publication, ctx) for pub in publication(item)}}
