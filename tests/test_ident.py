"""canonical_url() / item_id() against hand-written cases — these define ids
written permanently into every store, so they change only with ID_SCHEME.

    python -m pytest tests/test_ident.py   (or)   python tests/test_ident.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tjvz.ident import canonical_url, item_id  # noqa: E402

CANON = [
    # (input, expected)
    ("HTTP://Example.COM/Path/", "https://example.com/Path"),          # scheme+host lc, https, trailing /
    ("https://example.com", "https://example.com/"),                    # empty path -> /
    ("https://example.com/", "https://example.com/"),                   # root slash kept
    ("http://example.com/a?utm_source=x&id=5&ref=y", "https://example.com/a?id=5"),
    ("https://example.com/a?utm_campaign=x", "https://example.com/a"),   # ? dropped when empty
    ("https://example.com:443/x", "https://example.com/x"),             # default port dropped
    ("https://example.com:8080/x", "https://example.com:8080/x"),       # non-default kept
    ("https://m.nytimes.com/2020/01/01/foo.html", "https://nytimes.com/2020/01/01/foo.html"),
    ("https://amp.cnn.com/cnn/2021/x/index.html", "https://cnn.com/cnn/2021/x/index.html"),
    ("https://example.com/story/amp", "https://example.com/story"),
    ("https://example.com/story/amp/", "https://example.com/story"),
    ("https://www.kasurian.com/rise-fall-new-atheism/", "https://www.kasurian.com/rise-fall-new-atheism"),
    ("https://www.example.co.uk/x/", "https://www.example.co.uk/x"),    # www kept (dedup handles it)
    ("example.com/bare/path", "https://example.com/bare/path"),         # bare host tolerated
    ("  https://example.com/x  ", "https://example.com/x"),             # whitespace
    ("ftp://example.com/x", None),
    ("not a url", None),
    ("", None),
    (None, None),
]


def test_canonical_url():
    for raw, want in CANON:
        got = canonical_url(raw)
        assert got == want, f"canonical_url({raw!r}) = {got!r}, want {want!r}"


def test_canonical_url_is_idempotent():
    for raw, want in CANON:
        if want is not None:
            assert canonical_url(want) == want, f"not idempotent on {want!r}"


def test_item_id():
    u = item_id("https://example.com/a", "Title", [{"name": "X"}])
    assert u.startswith("u:") and len(u) == 18
    assert item_id("https://example.com/a", "Different", None) == u  # url wins, title ignored

    t1 = item_id(None, "  The   Tale  Of Genji ", ["Scott Alexander"])
    t2 = item_id(None, "the tale of genji", [{"name": "Scott Alexander", "role": "author"}])
    assert t1 == t2 and t1.startswith("t:") and len(t1) == 18   # normalisation

    assert item_id(None, "A", ["X"]) != item_id(None, "A", ["Y"])   # author matters


if __name__ == "__main__":
    test_canonical_url()
    test_canonical_url_is_idempotent()
    test_item_id()
    print(f"ok — {len(CANON)} canonical_url cases, item_id checks")
