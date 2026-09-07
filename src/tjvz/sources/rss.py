"""RSS / Atom adapter — stdlib only (urllib + ElementTree).

Per-feed conditional GET (ETag / If-Modified-Since). A 304 arrives as a
raised HTTPError, not a response. urllib neither requests nor decompresses
gzip, so we ask for it and inflate ourselves. A feed that fails writes its
error into the cache, keeps its last good items, and never aborts the run.

Item identity is the canonical id (see ident.py), not a watermark:
`first_seen` is carried forward from the prior cache so a backdated or
re-published item isn't treated as brand new (and, once surfaced, the feed
manifest holds `first_seen` durably).
"""
from __future__ import annotations

import gzip
import urllib.error
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from tjvz.sources import register
from tjvz.store import load_json, now_iso, save_json

_UA = "tjvz/0.1 (+https://github.com/bombaycyber/tjvz)"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


class _NotModified(Exception):
    pass


# --- fetch --------------------------------------------------------------------

def _http_get(url: str, headers: dict) -> tuple[bytes, str | None, str | None]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return raw, resp.headers.get("ETag"), resp.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            raise _NotModified from None
        raise


# --- parse -------------------------------------------------------------------

def _text(el) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()  # handles CDATA and Atom xhtml children


def _date(rfc822: str | None = None, iso: str | None = None) -> str | None:
    try:
        if rfc822:
            return parsedate_to_datetime(rfc822).date().isoformat()
        if iso:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        pass
    return None


def parse(body: bytes) -> tuple[dict, list[dict]]:
    root = ET.fromstring(body)
    tag = root.tag.split("}")[-1]
    if tag == "feed":
        return _parse_atom(root)
    if tag == "rss":
        return _parse_rss(root)
    raise ET.ParseError(f"not an RSS or Atom document (<{tag}>)")


def _parse_rss(root) -> tuple[dict, list[dict]]:
    ch = root.find("channel")
    channel = {"title": _text(ch.find("title")), "link": _text(ch.find("link")),
               "description": _text(ch.find("description"))}
    entries = []
    for it in ch.findall("item"):
        entries.append({
            "title": _text(it.find("title")),
            "link": _text(it.find("link")),
            "guid": _text(it.find("guid")),
            "summary": _text(it.find("description")),
            "content": _text(it.find("content:encoded", NS)),
            "creators": [_text(c) for c in it.findall("dc:creator", NS) if _text(c)],
            "published": _date(rfc822=_text(it.find("pubDate")) or None),
            "categories": [_text(c) for c in it.findall("category") if _text(c)],
            "enclosures": [(en.get("url"), en.get("type") or "") for en in it.findall("enclosure")],
        })
    return channel, entries


def _parse_atom(root) -> tuple[dict, list[dict]]:
    channel = {"title": _text(root.find("atom:title", NS)),
               "link": "", "description": _text(root.find("atom:subtitle", NS))}
    for lk in root.findall("atom:link", NS):
        if lk.get("rel") in (None, "alternate"):
            channel["link"] = lk.get("href", "")
            break
    entries = []
    for en in root.findall("atom:entry", NS):
        link = ""
        for lk in en.findall("atom:link", NS):
            if lk.get("rel") in (None, "alternate"):
                link = lk.get("href", "")
                break
        content_el = en.find("atom:content", NS)
        entries.append({
            "title": _text(en.find("atom:title", NS)),
            "link": link,
            "guid": _text(en.find("atom:id", NS)),
            "summary": _text(en.find("atom:summary", NS)),
            "content": _text(content_el),
            "creators": [_text(a.find("atom:name", NS)) for a in en.findall("atom:author", NS)
                         if _text(a.find("atom:name", NS))],
            "published": _date(iso=(_text(en.find("atom:published", NS))
                                    or _text(en.find("atom:updated", NS)) or None)),
            "categories": [c.get("term") for c in en.findall("atom:category", NS) if c.get("term")],
            "enclosures": [(lk.get("href"), lk.get("type") or "")
                           for lk in en.findall("atom:link", NS) if lk.get("rel") == "enclosure"],
        })
    return channel, entries


# --- adapter ----------------------------------------------------------------

def _host(url: str | None) -> str:
    try:
        return (urlsplit(url).hostname or "") if url else ""
    except ValueError:
        return ""


def _format(entry: dict) -> str:
    for _u, t in entry.get("enclosures", []):
        if t.startswith("audio/"):
            return "podcast"
        if t.startswith("video/"):
            return "video"
    return "essay"


@register
class RssSource:
    kind = "rss"
    stream = "feed"

    def to_item(self, entry: dict, channel: dict, sub: dict) -> dict:
        link = entry.get("link") or None
        host = _host(link)
        pub = channel.get("title") if sub["kind"] == "publication" else (host or channel.get("title"))
        text_source = ("content_encoded" if entry.get("content")
                       else "description" if entry.get("summary") else "title")
        partial = {
            "url": link,
            "title": entry.get("title") or "(untitled)",
            "authors": [{"name": c, "role": "author"} for c in entry.get("creators", [])],
            "publication": pub or "unknown",
            "format": _format(entry),
            "tags": list(entry.get("categories", [])),
            "summary": entry.get("summary") or None,
            "published": entry.get("published"),
            "text_source": text_source,
            "source": {"kind": "rss", "feed_id": sub["id"], "feed_url": sub["url"],
                       "guid": entry.get("guid") or ""},
            "stream": "feed",
        }
        cover = next((u for u, t in entry.get("enclosures", []) if t.startswith("image/")), None)
        if cover:
            partial["ext"] = {"cover_image": cover}
        if sub["kind"] == "reader":
            partial["readers"] = [{"source": sub["id"], "rating": None, "blurb": entry.get("summary") or None}]
        return partial

    def fetch(self, sub: dict, cache_path: Path, *, force: bool = False) -> dict:
        from tjvz.canon import canonicalise

        prior = load_json(cache_path, {})
        http = prior.get("http", {}) if not force else {}
        headers = {"User-Agent": _UA, "Accept-Encoding": "gzip"}
        if http.get("etag"):
            headers["If-None-Match"] = http["etag"]
        if http.get("last_modified"):
            headers["If-Modified-Since"] = http["last_modified"]

        def _keep(status: str, **extra) -> dict:
            prior.update(fetched_at=now_iso())
            save_json(cache_path, prior)
            return {"feed": sub["id"], "status": status, "count": len(prior.get("items", [])), **extra}

        try:
            body, etag, last_mod = _http_get(sub["url"], headers)
        except _NotModified:
            prior.update(ok=True, error=None)
            return _keep("not modified")
        except Exception as e:  # noqa: BLE001 — one bad feed must not abort the run
            prior.update(ok=False, error=f"{type(e).__name__}: {e}")
            return _keep("error", error=prior["error"])

        try:
            channel, entries = parse(body)
        except ET.ParseError as e:
            prior.update(ok=False, error=f"parse: {e}")
            return _keep("error", error=prior["error"])

        cap = sub.get("max_items")
        if cap:
            entries = entries[:cap]
        prior_first = {it["id"]: it.get("first_seen") for it in prior.get("items", [])}
        items, seen = [], set()
        for entry in entries:
            item, _ = canonicalise(self.to_item(entry, channel, sub), extra_tags=sub.get("apply_tags"))
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            item["first_seen"] = prior_first.get(item["id"]) or now_iso()
            items.append(item)

        new = sum(1 for it in items if it["id"] not in prior_first)
        save_json(cache_path, {
            "http": {"etag": etag, "last_modified": last_mod},
            "fetched_at": now_iso(), "ok": True, "error": None,
            "channel": channel, "items": items,
        })
        return {"feed": sub["id"], "status": "ok", "count": len(items), "new": new}
