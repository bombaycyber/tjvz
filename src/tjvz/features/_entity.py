"""Shared machinery for the entity feature families (author_avg_rating,
pub_avg_rating, author_familiarity, pub_familiarity).

An *entity* is something an item is attached to — an author, a publication,
a subscription id, a reader source. For each item you've judged that is
attached to entity E, we take one *draw* on a -1..5 scale:

    dismissed              -> params["v_dismiss"]         (-1)
    stacked (not read)     -> params["v_stack"]           (2.5, weak positive intent)
    read, rated r          -> r                           (your 1..5 rating)
    read, unrated          -> params["v_read_unrated"]    (3, "impute a 3")

`avg_rating(E)` is the shrinkage posterior mean of those draws — a
Normal-Normal posterior mean with tau0 pseudo-observations at prior mean
mu0 (=3, neutral). It reads directly as "the average rating of items from
E, counting a dismiss as -1". Feed items you haven't judged contribute
nothing. Cold start (archive only): ratings and unrated-reads drive it; an
unseen entity sits at mu0.
"""
from __future__ import annotations

from typing import Callable, Iterator

DRAW_DEFAULTS: dict = {
    "v_dismiss": -1.0,
    "v_stack": 2.5,
    "v_read_unrated": 3.0,
    "tau0": 2.0,
    "mu0": 3.0,
}


def store(ctx, name: str):
    v = ctx.get(name) if isinstance(ctx, dict) else getattr(ctx, name, None)
    return v or ()


# --- entity extractors: item -> set of normalised entity keys -----------------

def authors(item: dict) -> set[str]:
    out: set[str] = set()
    for a in item.get("authors") or []:
        if isinstance(a, str):
            name = a
        elif a.get("role", "author") != "author":
            continue
        elif a.get("name"):
            name = a["name"]
        elif a.get("family"):
            name = f"{a.get('given', '')} {a['family']}"
        else:
            continue
        n = " ".join(name.split()).lower()
        if n:
            out.add(n)
    return out


def publication(item: dict) -> set[str]:
    p = " ".join((item.get("publication") or "").split()).lower()
    return {p} if p and p != "unknown" else set()


def subscription(item: dict) -> set[str]:
    fid = ((item.get("source") or {}).get("feed_id") or "").strip().lower()
    return {fid} if fid else set()


def reader_sources(item: dict) -> set[str]:
    return {
        (r.get("source") or "").strip().lower()
        for r in (item.get("readers") or [])
        if r.get("source")
    }


# --- draws, familiarity count, shrinkage average -------------------------------

def _rating(it: dict) -> float | None:
    r = it.get("rating")
    return float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else None


def draws(entity: str, entities_of: Callable[[dict], set[str]], ctx, p: dict) -> Iterator[float]:
    for it in store(ctx, "archive"):
        if entity in entities_of(it):
            r = _rating(it)
            yield r if r is not None else p["v_read_unrated"]
    for it in store(ctx, "stack"):
        if entity in entities_of(it):
            yield p["v_stack"]
    for it in store(ctx, "trash"):
        if entity in entities_of(it):
            yield p["v_dismiss"]


def familiarity(entity: str, entities_of: Callable[[dict], set[str]], ctx) -> int:
    return sum(
        1
        for s in ("archive", "stack", "trash")
        for it in store(ctx, s)
        if entity in entities_of(it)
    )


def avg_rating(entity: str, entities_of: Callable[[dict], set[str]], ctx, p: dict) -> tuple[float, int]:
    ds = list(draws(entity, entities_of, ctx, p))
    return (p["tau0"] * p["mu0"] + sum(ds)) / (p["tau0"] + len(ds)), len(ds)
