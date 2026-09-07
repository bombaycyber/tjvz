"""Source adapters — one per ingestion stream.

An adapter does field-mapping only: it turns source-shaped records into
partial canonical items. The shared `tjvz.canon.canonicalise` then assigns
the id, canonical_url, publication, text tier, and normalised dates. Adding
a stream is one new module here plus `@register`.

    rss        subscribed RSS/Atom feeds            -> stream "feed"
    zotero     a Zotero library (SQLite, read-only) -> stream "archive"
    jsonfile   inbox/*.json drops (often AI-authored)
    csv        a reading-history CSV (Goodreads export, a personal log, ...)

`stream` is the adapter's default lane; a jsonfile drop may override it per
record with a "stream" key ("feed" | "stack" | "archive"), which
canonicalise() consumes and discards.
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable

REGISTRY: dict[str, "type[Source]"] = {}


def register(cls: "type[Source]") -> "type[Source]":
    """Class decorator: add a Source implementation to the registry by kind."""
    if cls.kind in REGISTRY:
        raise ValueError(f"duplicate source kind: {cls.kind!r}")
    REGISTRY[cls.kind] = cls
    return cls


@runtime_checkable
class Source(Protocol):
    kind: str          # "rss" | "zotero" | "jsonfile" | "csv"  — matches item.source.kind
    stream: str        # default lane: "feed" | "archive"

    def pull(self, cfg: dict, ctx: Any) -> Iterable[dict]:
        """Yield source-shaped records. All network / disk IO lives here.

        `cfg` is this source's slice of config.yaml (e.g. one subscription
        entry, or {"path": ...} for csv). Failures are the caller's to
        handle per-source: one bad feed must not abort a run.
        """
        ...

    def to_item(self, raw: dict) -> dict:
        """Map one source record to a partial canonical item.

        Return the fields you can fill directly (title, url, authors,
        publication, tags, summary, published, source, ...). Leave id,
        canonical_url, first_seen, text_ref to canonicalise(). May include a
        top-level "stream" hint.
        """
        ...
