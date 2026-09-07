"""jsonfile adapter — drains inbox/*.json.

Each file is a partial item (the schema/README "inbox drop format"). It may
carry `stream` (feed | stack | archive) and `readers[]`. `tjvz ingest`
canonicalises each, routes it, and moves the file to inbox/done/.
`tjvz add` builds one such record from flags and ingests it directly.
"""
from __future__ import annotations

import json
from pathlib import Path

from tjvz.sources import register


@register
class JsonFileSource:
    kind = "jsonfile"
    stream = "feed"

    def __init__(self, inbox: Path):
        self.inbox = Path(inbox)

    def pull(self, cfg: dict | None = None, ctx=None):
        for path in sorted(self.inbox.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                yield {"_path": str(path), "_error": f"invalid JSON: {e}"}
                continue
            if isinstance(raw, dict):
                raw["_path"] = str(path)
                yield raw

    def to_item(self, raw: dict) -> dict:
        item = {k: v for k, v in raw.items() if not k.startswith("_")}
        item.setdefault("source", {"kind": "jsonfile", "path": raw.get("_path", "")})
        return item
