"""relevance (v1) — tag overlap with your archive.

v1 is the mean Jaccard tag-overlap with the k archive items whose tags
overlap this item most. In [0, 1]. Becomes TF-IDF top-k cosine later (same
id, version bump -> triggers `tjvz features recompute`).
"""
from __future__ import annotations

from tjvz.features import register
from tjvz.features._entity import store

MANIFEST = {
    "id": "relevance",
    "kind": "feature",
    "version": 1,
    "since": "2026-09-06",
    "description": "Mean Jaccard tag-overlap with the k most tag-similar archive items. v0 stand-in for TF-IDF top-k cosine. In [0, 1].",
    "needs": ["archive"],
    "params": {
        "topk": {"type": "integer", "default": 3, "tunable": True,
                 "description": "how many nearest archive items to average"},
        "hyphen_expand": {"type": "boolean", "default": True,
                          "description": "urdu-hindi also contributes urdu, hindi"},
    },
    "checks": [
        {"name": "no tags -> 0", "fixture": "features/relevance/no_tags.json",
         "expect": 0.0, "tol": 1e-9},
        {"name": "exact tag match in archive -> 1", "fixture": "features/relevance/exact.json",
         "expect": 1.0, "tol": 1e-9},
        {"name": "partial overlap, mean of top-2", "fixture": "features/relevance/partial.json",
         "expect": 0.5, "tol": 1e-4},
    ],
    "supersedes": None,
}


def _tags(item: dict, expand: bool) -> set[str]:
    t = {x.strip().lower() for x in (item.get("tags") or []) if x and x.strip()}
    if expand:
        for x in list(t):
            if "-" in x:
                t.update(p for p in x.split("-") if p)
    return t


@register
class Relevance:
    MANIFEST = MANIFEST

    def compute(self, item, ctx, params):
        p = {"topk": 3, "hyphen_expand": True, **(params or {})}
        q = _tags(item, p["hyphen_expand"])
        if not q:
            return 0.0
        sims = []
        for a in store(ctx, "archive"):
            at = _tags(a, p["hyphen_expand"])
            inter = len(q & at)
            if inter:
                sims.append(inter / len(q | at))
        sims.sort(reverse=True)
        top = sims[: p["topk"]]
        return sum(top) / len(top) if top else 0.0

    def explain(self, item, ctx, params):
        p = {"topk": 3, "hyphen_expand": True, **(params or {})}
        q = _tags(item, p["hyphen_expand"])
        near = []
        for a in store(ctx, "archive"):
            at = _tags(a, p["hyphen_expand"])
            if q & at:
                near.append((len(q & at) / len(q | at), a.get("title", "?")))
        near.sort(reverse=True)
        return {"feature": "relevance", "query_tags": sorted(q),
                "nearest": [{"jaccard": round(j, 3), "title": t} for j, t in near[: p["topk"]]]}
