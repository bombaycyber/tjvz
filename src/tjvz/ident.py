"""Canonical URLs and item ids — FROZEN behaviour.

An item's id is derived here and written into every store, so a change to
`canonical_url()` or `item_id()` silently rewrites history. `ID_SCHEME`
gates that: bump it when the derivation changes; a load-time check against
config `id_scheme` fails loudly on a mismatch. Test `canonical_url()`
against hand-written cases before touching it.

`canonical_url()` is deliberately conservative — it unifies the safe cases
(scheme/host case, http->https, default ports, tracking params, trailing
slash, m./amp. mobile subdomains, a trailing /amp segment) and leaves the
rest (www vs not, custom domains) to `item.aliases` + `tjvz dedup`.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ID_SCHEME = 1

# ported from dashboard_lib.py _TRACKING_PARAM_RE (+ amp/outputType)
_TRACKING = re.compile(
    r"^(utm_|mc_[ce]id$|fbclid$|gclid$|dclid$|msclkid$|igshid$|ref_?src$|"
    r"ref$|source$|si$|spm$|_hs(enc|mi)$|mkt_tok$|vero_id$|yclid$|"
    r"srsltid$|gbraid$|wbraid$|ttclid$|twclid$|amp$|outputtype$)",
    re.IGNORECASE,
)
_VARIANT_SUBDOMAINS = ("m", "amp", "mobile")
_WS = re.compile(r"\s+")


def canonical_url(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    p = urlsplit(raw)
    if not p.scheme and not p.netloc and "." in raw.split("/", 1)[0]:
        p = urlsplit("https://" + raw)  # bare host, common in AI-authored drops
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return None

    host = p.hostname.lower().rstrip(".")
    labels = host.split(".")
    if len(labels) >= 3 and labels[0] in _VARIANT_SUBDOMAINS:
        host = ".".join(labels[1:])

    netloc = host
    if p.port and p.port not in (80, 443):
        netloc = f"{host}:{p.port}"

    path = re.sub(r"/amp/?$", "", p.path or "") or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)]
    return urlunsplit(("https", netloc, path, urlencode(kept, doseq=True), ""))


def _norm(s: str | None) -> str:
    return _WS.sub(" ", (s or "").strip()).lower()


def _first_author(authors) -> str:
    if not authors:
        return ""
    a = authors[0]
    if isinstance(a, str):
        return _norm(a)
    if a.get("name"):
        return _norm(a["name"])
    if a.get("family"):
        return _norm(f"{a.get('given', '')} {a['family']}")
    return ""


def item_id(canon: str | None, title: str, authors=None) -> str:
    """`u:` + sha256(canonical_url)[:16] when a URL exists, else `t:` +
    sha256(normalised title + first author)[:16]."""
    if canon:
        return "u:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
    key = _norm(title) + "\x00" + _first_author(authors)
    return "t:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
