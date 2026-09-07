"""`tjvz broadcast` — an Atom feed of your archive, for friends to subscribe to.

Regenerates `<profile>/broadcast/atom.xml` from the archive. Deliberately
lossy, for privacy:

  * `<link>` is emitted only for http(s) URLs to real public hosts — a
    `file://`, `localhost`, LAN-IP, or `*.local` link is dropped and the
    item stays linkless.
  * dates are coarsened to the 1st of the month (`broadcast.coarsen_dates`),
    so the feed carries no activity-timing signal.
  * which items (`broadcast.include`), how many (`broadcast.limit`), and
    which tags (`broadcast.share_tags`) are all config-gated.

`--publish` then runs `broadcast.publish_cmd` (e.g. a `wrangler pages
deploy {dir} …`). A subscriber adds the hosted feed with
`tjvz sub add <url> --kind reader --id <handle>`; their tjvz keys every
signal to `<handle>` and folds the `<tjvz:rating>` values into
`reader_recs`.
"""
from __future__ import annotations

import ipaddress
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from tjvz import store

ATOM = "http://www.w3.org/2005/Atom"
TJVZ_NS = "https://github.com/bombaycyber/tjvz/ns"
_PRIVATE_SUFFIX = (".local", ".internal", ".test", ".example", ".invalid",
                   ".localhost", ".lan", ".home", ".corp")

ET.register_namespace("", ATOM)
ET.register_namespace("tjvz", TJVZ_NS)


# --- privacy helpers -------------------------------------------------------

def is_public_url(url: str | None) -> bool:
    """True only for an http(s) URL to a routable, named public host."""
    if not url:
        return False
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(_PRIVATE_SUFFIX):
        return False
    try:
        ipaddress.ip_address(host)          # a bare IP literal is not shareable
        return False
    except ValueError:
        pass
    return "." in host


def _coarse(d: str | None) -> str:
    s = str(d or "")
    if len(s) >= 7 and s[4] == "-" and s[5:7].isdigit():
        return f"{s[:7]}-01T00:00:00Z"
    if len(s) >= 4 and s[:4].isdigit():
        return f"{s[:4]}-01-01T00:00:00Z"
    return "1970-01-01T00:00:00Z"


def _rfc3339(d: str | None) -> str:
    try:
        dt = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return _coarse(d)


# --- item / tag selection ---------------------------------------------------

def _authors(item: dict) -> list[str]:
    out = []
    for a in item.get("authors") or []:
        if isinstance(a, str):
            out.append(a)
        elif a.get("name"):
            out.append(a["name"])
        elif a.get("family"):
            out.append(f"{a.get('given', '')} {a['family']}".strip())
    return [a for a in (x.strip() for x in out) if a]


def _categories(item: dict, share) -> list[str]:
    tags = [t for t in (item.get("tags") or []) if t]
    if share == "none":
        return []
    if share == "no-collections":
        coll = set((item.get("ext") or {}).get("collection_tags") or [])
        return [t for t in tags if t not in coll]
    if isinstance(share, (list, tuple)):
        allow = {str(s).strip().lower() for s in share}
        return [t for t in tags if t in allow]
    return tags  # "all"


def _rating(item: dict):
    r = item.get("rating")
    return r if isinstance(r, int) and not isinstance(r, bool) and 1 <= r <= 5 else None


def _select(items: list[dict], bc: dict) -> list[dict]:
    inc = bc.get("include", "all")
    keep = []
    for it in items:
        if inc == "rated":
            r = _rating(it)
            if r is None or r < bc.get("min_rating", 4):
                continue
        elif inc == "blurbed" and not (it.get("blurb") or "").strip():
            continue
        keep.append(it)
    keep.sort(key=lambda it: str(it.get("read") or ""), reverse=True)
    lim = bc.get("limit")
    return keep[:lim] if lim else keep


# --- feed build ----------------------------------------------------------------

def _el(parent, tag: str, text=None, **attrs):
    qname = tag if tag.startswith("{") else f"{{{ATOM}}}{tag}"
    el = ET.SubElement(parent, qname, {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = text
    return el


def build(profile, cfg: dict) -> tuple[str, dict]:
    bc = cfg["broadcast"]
    fmt = _coarse if bc.get("coarsen_dates", True) else _rfc3339
    archive = list(store.iter_records(profile.archive_dir, kind="item"))
    chosen = _select(archive, bc)

    feed = ET.Element(f"{{{ATOM}}}feed")
    _el(feed, "title", bc.get("title") or "tjvz reading")
    if bc.get("description"):
        _el(feed, "subtitle", bc["description"])
    _el(feed, "id", bc.get("feed_url") or "urn:tjvz:feed")
    if bc.get("feed_url"):
        _el(feed, "link", rel="self", href=bc["feed_url"], type="application/atom+xml")
    if bc.get("link"):
        _el(feed, "link", rel="alternate", href=bc["link"], type="text/html")
    _el(feed, "updated", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if bc.get("author_name") or bc.get("author_email"):
        au = _el(feed, "author")
        _el(au, "name", bc.get("author_name") or bc.get("author_email"))
        if bc.get("author_email"):
            _el(au, "email", bc["author_email"])
    _el(feed, "generator", "tjvz", uri="https://github.com/bombaycyber/tjvz")

    link_suppressed = 0
    for it in chosen:
        e = _el(feed, "entry")
        _el(e, "title", it.get("title") or "(untitled)")
        _el(e, "id", f"urn:tjvz:{it['id']}")
        cu = it.get("canonical_url")
        if is_public_url(cu):
            _el(e, "link", rel="alternate", href=cu, type="text/html")
        else:
            link_suppressed += 1
        when = fmt(it.get("read"))
        _el(e, "updated", when)
        _el(e, "published", when)
        for name in _authors(it):
            _el(_el(e, "author"), "name", name)
        for term in _categories(it, bc.get("share_tags", "all")):
            _el(e, "category", term=term)
        blurb = (it.get("blurb") or "").strip()
        summary = (it.get("summary") or "").strip()
        _el(e, "summary", blurb or summary or it.get("title") or "(untitled)", type="text")
        if blurb and summary:
            _el(e, "content", summary, type="html")
        r = _rating(it)
        if r is not None:
            _el(e, f"{{{TJVZ_NS}}}rating", str(r))

    if hasattr(ET, "indent"):
        ET.indent(feed)
    xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(feed, encoding="unicode")
    stats = {
        "included": len(chosen),
        "archived": len(archive),
        "excluded": len(archive) - len(chosen),
        "link_suppressed": link_suppressed,
    }
    return xml, stats


# --- run -----------------------------------------------------------------------

def _out_path(profile, cfg: dict, out: str | None) -> Path:
    if out:
        return Path(out).expanduser()
    d = cfg["broadcast"].get("out_dir")
    base = Path(d).expanduser() if d else profile.broadcast_dir
    return base / "atom.xml"


def _publish(bc: dict, out_path: Path) -> dict:
    cmd = (bc.get("publish_cmd") or "").strip()
    if not cmd:
        raise ValueError("broadcast.publish_cmd is not set in config.yaml")
    argv = [tok.replace("{dir}", str(out_path.parent)).replace("{file}", str(out_path))
            for tok in shlex.split(cmd)]
    p = subprocess.run(argv, capture_output=True, text=True)  # noqa: S603
    return {"argv": argv, "returncode": p.returncode,
            "stdout": p.stdout[-4000:], "stderr": p.stderr[-4000:]}


def _text(r: dict) -> str:
    head = (f"broadcast{' (dry run)' if r['dry_run'] else ''}: "
            f"{r['included']} of {r['archived']} archived items")
    bits = []
    if r["excluded"]:
        bits.append(f"{r['excluded']} filtered out")
    if r["link_suppressed"]:
        bits.append(f"{r['link_suppressed']} kept without a link (non-public URL)")
    lines = [head] + (["  " + " · ".join(bits)] if bits else [])
    lines.append(f"  {'would write' if r['dry_run'] else 'wrote'} {r['out']}")
    if r.get("publish") is not None:
        pub = r["publish"]
        status = "ok" if r["published"] else f"FAILED (exit {pub['returncode']})"
        lines.append(f"  publish: {status}")
        tail = (pub["stderr"] or pub["stdout"]).strip().splitlines()
        if not r["published"] and tail:
            lines.append("    " + tail[-1])
    return "\n".join(lines)


def run(profile, cfg: dict, *, out: str | None = None,
        publish: bool = False, dry_run: bool = False) -> dict:
    xml, stats = build(profile, cfg)
    out_path = _out_path(profile, cfg, out)
    r = {**stats, "out": str(out_path), "dry_run": dry_run, "published": False, "publish": None}

    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        store.atomic_write(out_path, xml)
        if publish:
            r["publish"] = _publish(cfg["broadcast"], out_path)
            r["published"] = r["publish"]["returncode"] == 0
            if not r["published"]:
                r["ok"] = False

    r["_text"] = _text(r)
    return r
