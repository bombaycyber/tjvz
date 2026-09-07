"""canonicalise() — the single normaliser every source adapter feeds.

Takes a partial item (an adapter's `to_item()` output, or an `inbox/*.json`
drop) and returns a full, schema-valid item plus the routing `stream`
(which is NOT stored). It assigns the id and canonical_url (see ident.py —
frozen), fills defaults, lowercases tags, normalises authors / readers, and
coerces dates to strings. The write path validates the result.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from tjvz.ident import canonical_url, item_id
from tjvz.store import SCHEMA_VERSION, now_iso

FORMATS = {
    "essay", "article", "paper", "book", "chapter", "thread", "transcript",
    "document", "podcast", "video", "artwork", "dataset", "other",
}
STREAMS = {"feed", "stack", "archive"}


def _authors(raw) -> list:
    out = []
    for a in raw or []:
        if isinstance(a, str):
            a = a.strip()
            if a:
                out.append({"name": a, "role": "author"})
        elif isinstance(a, dict) and (a.get("name") or a.get("family")):
            d = {k: v for k, v in a.items() if v is not None}
            d.setdefault("role", "author")
            out.append(d)
    return out


def _tags(*lists) -> list:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for t in lst or []:
            t = " ".join(str(t).split()).lower()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def _readers(raw) -> list:
    out = []
    for r in raw or []:
        if not isinstance(r, dict) or not r.get("source"):
            continue
        rating = r.get("rating")
        out.append({
            "source": str(r["source"]).strip().lower(),
            "rating": rating if isinstance(rating, (int, float)) and not isinstance(rating, bool) else None,
            "blurb": r.get("blurb"),
            "at": str(r["at"]) if r.get("at") is not None else None,
        })
    return out


def _host(url: str | None) -> str:
    try:
        return (urlsplit(url).hostname or "") if url else ""
    except ValueError:
        return ""


def canonicalise(raw: dict, extra_tags: list | None = None) -> tuple[dict, str]:
    stream = str(raw.get("stream") or "feed").strip().lower()
    if stream not in STREAMS:
        stream = "feed"

    url = raw.get("url") or None
    canon = canonical_url(url)
    title = " ".join(str(raw.get("title") or "").split())
    if not title:
        raise ValueError("item has no title")

    authors = _authors(raw.get("authors"))
    fmt = str(raw.get("format") or "essay").strip().lower()
    src = {k: v for k, v in dict(raw.get("source") or {}).items() if v is not None}
    src.setdefault("kind", "jsonfile")

    item: dict = {
        "schema_version": SCHEMA_VERSION,
        "id": item_id(canon, title, authors),   # always derived — a supplied id is ignored
        "url": url,
        "canonical_url": canon,
        "title": title,
        "authors": authors,
        "publication": (str(raw.get("publication") or "").strip() or _host(canon or url) or "unknown"),
        "format": fmt if fmt in FORMATS else "other",
        "tags": _tags(raw.get("tags"), extra_tags),
        "source": src,
        "aliases": [str(a) for a in (raw.get("aliases") or [])],
        "readers": _readers(raw.get("readers")),
        "first_seen": str(raw.get("first_seen") or now_iso()),
        "ext": dict(raw.get("ext") or {}),
    }
    for opt in ("summary", "published", "text_ref", "text_source", "blurb"):
        if raw.get(opt) is not None:
            item[opt] = str(raw[opt]) if opt in ("published",) else raw[opt]
    if isinstance(raw.get("rating"), int) and not isinstance(raw["rating"], bool) and 1 <= raw["rating"] <= 5:
        item["rating"] = raw["rating"]
    if stream == "archive":
        item["read"] = str(raw.get("read") or now_iso())

    return item, stream
