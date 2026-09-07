"""reader_overlap — how much of this item's reader(s)' reading you share.

For each reader source on the item (a `kind: reader` subscription, or an AI
drop), the count of items in YOUR archive that also carry that reader's
signal — things you have both read — `saturate`d, max over the item's
readers.

A collaborative-filtering-style cold-start reputation: a reader whose
archive overlaps yours gets a positive push on everything they surface,
before the learned `reader_recs:<id>` weight has any of your judgements to
move it. Positive weight, **not** pinned — unlike `author_familiarity` /
`pub_familiarity`, where the pinned negative weight is an exploration dial.
"""
from __future__ import annotations

from tjvz.features import register, saturate
from tjvz.features._entity import reader_sources, store

MANIFEST = {
    "id": "reader_overlap",
    "kind": "feature",
    "version": 1,
    "since": "2026-09-07",
    "description": "saturate(count of your archive items this item's reader source(s) have also read, scale); max over readers. Cold-start reader reputation — positive.",
    "needs": ["archive"],
    "params": {
        "scale": {"type": "number", "default": 5.0, "tunable": True,
                  "description": "shared-read count at which the feature reaches 0.5"},
    },
    "checks": [
        {"name": "no readers -> 0", "fixture": "features/reader_overlap/none.json",
         "expect": 0.0, "tol": 1e-9},
        {"name": "reader you've never overlapped -> 0", "fixture": "features/reader_overlap/unseen.json",
         "expect": 0.0, "tol": 1e-9},
        {"name": "five shared reads -> saturate(5, 5)", "fixture": "features/reader_overlap/shared.json",
         "expect": 0.5, "tol": 1e-9},
    ],
    "supersedes": None,
}


def _overlap(src: str, ctx) -> int:
    return sum(1 for a in store(ctx, "archive") if src in reader_sources(a))


@register
class ReaderOverlap:
    MANIFEST = MANIFEST

    def compute(self, item, ctx, params):
        p = {"scale": 5.0, **(params or {})}
        srcs = reader_sources(item)
        if not srcs:
            return 0.0
        return saturate(max(_overlap(s, ctx) for s in srcs), p["scale"])

    def explain(self, item, ctx, params):
        return {"feature": "reader_overlap",
                "shared_reads": {s: _overlap(s, ctx) for s in reader_sources(item)}}
