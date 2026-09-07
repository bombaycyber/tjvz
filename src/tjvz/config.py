"""Load a profile's config.yaml over built-in defaults.

The only place PyYAML is used at runtime. Missing keys fall back to
DEFAULTS (deep-merged); the `model` block is validated against
schema/model-config.schema.json.
"""
from __future__ import annotations

from typing import Any

import yaml

from tjvz.schema import validate

DEFAULTS: dict[str, Any] = {
    "schema": 1,
    "id_scheme": 1,
    "model": {
        "scoring": {
            "features": {
                "relevance": {"weight": 3.0, "params": {"topk": 3}},
                "author_avg_rating": {"weight": 0.4},
                "pub_avg_rating": {"weight": 0.2},
                "author_familiarity": {"weight": -0.5, "pinned": True},
                "pub_familiarity": {"weight": -0.25, "pinned": True},
                "reader_overlap": {"weight": 0.5},
                "reader_recs": {"default_weight": 0.5},
            },
            "bias": 0.0,
        },
        "learning": {
            "utility": "log_odds",
            "label": {"positive": ["read", "stack"]},
            "trainable": "weights",
            "regularization": {"lambda0": 1.0, "anneal_n0": 50},
            "adopt": {"min_pos": 20, "min_neg": 20, "cv_folds": 5, "metric": "log_loss"},
        },
    },
    "feed": {"limit": 10, "min_score": None},
    "zotero": {"collections_as_tags": True},
    "broadcast": {
        "title": "",              # feed <title>; falls back to "tjvz reading"
        "description": "",         # feed <subtitle>
        "link": "",               # your homepage → feed <link rel=alternate>
        "feed_url": "",           # where atom.xml will be hosted → feed <id> + <link rel=self>
        "author_name": "",        # feed <author><name>
        "author_email": "",       # feed <author><email> — opt-in; public, so scrape-prone
        "language": "en",
        "include": "all",         # all | rated | blurbed
        "min_rating": 4,          # when include == rated
        "limit": 100,             # newest-N by read date; null/0 = whole archive
        "share_tags": "all",      # all | none | no-collections | [allowlist]
        "coarsen_dates": True,    # dates → 1st of the month (kills timing signal)
        "out_dir": "",            # default: <profile>/broadcast/
        "publish_cmd": "",        # run by `tjvz broadcast --publish`; {dir} / {file} substituted
        "share_bodies": False,    # reserved (bodies aren't stored pre-TF-IDF)
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(profile) -> dict:
    raw = yaml.safe_load(profile.config.read_text()) if profile.config.exists() else {}
    cfg = _merge(DEFAULTS, raw or {})
    if cfg.get("id_scheme") != DEFAULTS["id_scheme"]:
        raise ValueError(
            f"config id_scheme={cfg['id_scheme']} but this tjvz uses {DEFAULTS['id_scheme']} — "
            "ids would not match; upgrade/downgrade tjvz or the profile"
        )
    validate(cfg["model"], "model-config")
    return cfg


def load_subscriptions(profile) -> list[dict]:
    raw = yaml.safe_load(profile.subscriptions.read_text()) if profile.subscriptions.exists() else {}
    feeds = (raw or {}).get("feeds") or []
    validate({"feeds": feeds}, "subscription")   # enforces id/url/kind/persist per entry
    return feeds
