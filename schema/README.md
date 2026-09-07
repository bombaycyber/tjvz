# tjvz data & contract schemas

Machine-readable schemas for everything tjvz stores or plugs in. This
document is the human (and LLM) companion: field prose, minimal valid
examples, and the **inbox drop format** for handing tjvz new records.

### Record & state stores

| file | describes | where those records live |
|---|---|---|
| `item.schema.json` | a reading item | `.tjvz/feeds/` cache · `stack.json` · `archive/YYYY-MM.jsonl` · `trash/YYYY-MM.jsonl` |
| `event.schema.json` | a judgement (promote / dismiss / read) | `events/YYYY-MM.jsonl` |
| `feed-manifest.schema.json` | a pointer to a surfaced-but-unjudged item | `feed/manifest.jsonl` |
| `model-state.schema.json` | the weights the model currently ranks with | `model.json` |

### Config & contracts

| file | describes | where |
|---|---|---|
| `model-config.schema.json` | the `model:` block of `config.yaml` (scoring + learning) | `config.yaml` |
| `feature-manifest.schema.json` | one feature's declarative contract + golden checks | `MANIFEST` dict in `src/tjvz/features/<id>.py` |
| `ctx.md` | the `ctx` bundle a feature is allowed to read | (prose) |

There is **one model** — `score = bias + Σ weightᵢ · featureᵢ(item)` — no
model plugin registry. Features *are* a registry (one file per feature).

`archive/`, `trash/`, and `events/` are month-sharded JSONL (one object per
line, `YYYY-MM.jsonl`). `stack.json` and `model.json` are single JSON
objects/arrays. `feed/manifest.jsonl` is JSONL. The only YAML is
`config.yaml` + `subscriptions.yaml` (hand-edited; schemas later).

The three item stores that are STATE are told apart by location + which
timestamp is set:

| store | meaning | timestamp |
|---|---|---|
| `stack.json` | promoted, queued to read | — |
| `archive/` | read | `read` |
| `trash/` | dismissed — out of the archive so it stays clean, kept so a dismissal can be reviewed or undone | `dismissed` |

There is no "empty trash" — trash is the negative training set. You may
*rescue* a single item from trash into the stack or the archive (never back
to the feed); that appends a `promote`/`read` event and the original
`dismiss` event stays.

## Validation

tjvz ships a dependency-free validator (`src/tjvz/schema.py`) enforcing the
subset of JSON Schema these files rely on:

```
type   required   enum   properties   items   oneOf   additionalProperties
```

Everything else (`pattern`, `format`, `minLength`, `$id`, descriptions) is
for external tooling and for reading — the built-in validator ignores it.
Validation runs on the **write path** (`tjvz ingest`, `import-*`, `add`,
`promote`, `rate`) and in `tjvz verify`; never on bulk reads.
`--no-validate` skips it.

## Versioning

Every record carries `schema_version` (currently `1`). A shape change bumps
it; `store.py` gains a migration applied on read; `tjvz migrate` rewrites
shards. Feature and model **manifests** carry their own `version` integer —
see below.

---

# Features & the model

A **feature** is `f(item, ctx, params) → a real number` — Python (real
logic), shipping a declarative `MANIFEST` (validated against
`feature-manifest.schema.json`) with golden **checks** `tjvz verify` runs
(fixture → `compute()` → assert). One file per feature; `@register` adds it.

The **model** is one thing: `score = bias + Σ weightᵢ · featureᵢ(item)`.
No plugin, no registry. **No normalisation layer** — a feature keeps its own
output at a sensible scale by convention (a naturally-unbounded feature
calls `saturate()` inside `compute()`), and a weight accounts for its
feature's natural range. Config: `model-config.schema.json`.

**Feature values are never stored** — not on items, not on events. They are
recomputed on demand:

- **leave-one-out** — a feature scoring item X never sees X in its own
  reference corpus (kills the target-leakage a read item causes by entering
  the archive).
- **as-of-date** — for a historical decision, computed against the archive
  as it was at that timestamp.

So a feature can be **added or changed** and the whole training set reflects
it after `tjvz features recompute` (just invalidates the `.tjvz/` feature
cache). No event rewriting, no "eras". `model.json`'s `features_fingerprint`
vs the live registry tells `tjvz model status` when the model is stale.

- **`params`** are fixed structural knobs (v1: not gradient-fit); `config`
  `model.scoring.features.<id>.params` overrides the manifest defaults. An
  outer tuning loop may search `tunable` ones later.
- **Interaction / composite features** (`relevance × author_familiarity`)
  and topical-trust variants are ordinary features under this contract —
  the nonlinearity lives in the feature, the model stays linear.

### v1 feature set

| feature | what | scale |
|---|---|---|
| `relevance` | mean Jaccard tag-overlap with the top-k archive items (TF-IDF cosine later) | [0, 1] |
| `author_avg_rating` | shrinkage average rating of this author's items (dismiss = −1, stack = 2.5, unrated read = 3), max over authors | ~[−1, 5] |
| `pub_avg_rating` | same, for `item.publication` | ~[−1, 5] |
| `author_familiarity` | `saturate(#judged items by this author)` — exposure, orthogonal to quality; pinned negative weight → mild novelty preference (crude feature-value-uncertainty exploration) | [0, 1) |
| `pub_familiarity` | same, for the publication | [0, 1) |
| `reader_recs` | **column-group** (`expands: true`) — one column per reader on the item, value = their 1–5 rating; the model learns a weight `w_R` per reader (`reader_recs:<source>`), prior 0, outer weight fixed at 1. Contributes 0 until `kind: reader` feeds populate `readers[]` and a learn run moves some `w_R`. | Σ wᵣ·rᵣ |

A **column-group** feature ships `columns(item, ctx, params) → {sub_id:
value}` instead of `compute()`, gets no config weight (`reader_recs: {}`),
and the model expands it to one learned weight per `sub_id`. Optional hand-
set priors/pins: `config` `model.scoring.features.reader_recs.readers.<source>`.

**Learning** — `LinearModel.learn(rows)` (`config` `model.learning`, omit to
never run one). `utility` `log_odds` (predict read; v1) or `ev` (predict
rating; later). Weights shrunk toward the config priors, not 0; annealed λ.
Newton's method, pure Python (`d≈7`), no numpy/sklearn/torch. `learn()`
reports whether the fit beats the priors on k-fold CV log-loss and whether
the adopt gates clear — it does not itself adopt. `stochastic: true` is
reserved for a future posterior-sampling (Thompson) variant.

---

# `item`

Same shape wherever it lives. It gains fields as it moves (`read` when
archived, `dismissed` when trashed); never loses them. `source` always says
where it came from. **Archive records are self-sufficient for RSS
re-broadcast** — see the last section.

### Required

| field | meaning |
|---|---|
| `schema_version` | `1` |
| `id` | tjvz-assigned. `u:` + 16 hex of `sha256(canonical_url)`, else `t:` + 16 hex of `sha256(norm title + first author)`. **Never invent it** for an inbox drop — omit it. Stable across machines: same URL → same id. |
| `title` | non-empty |
| `authors` | array (`[]` ok). Each entry `string \| {name, role?} \| {given, family, role?}`. `role` default `"author"`. |
| `publication` | channel title → host → `"unknown"`. Always resolves. |
| `format` | one of 13 (table below) |
| `tags` | array of **lowercased** strings (`[]` ok) |
| `source` | `{kind, …}` — see below |
| `aliases` | canonical URLs that are the same item (`[]`; filled by `tjvz dedup`) |
| `readers` | others' signals about this item (`[]`; see below) |
| `first_seen` | ISO-8601 UTC of first ingest |
| `ext` | open bag (`{}` ok) |

### Optional

| field | meaning |
|---|---|
| `url` / `canonical_url` | as given / tracking-stripped + host-rewritten; `null` for URL-less items |
| `summary` | the source's own blurb (`<description>`, `abstractNote`) — not the body |
| `published` | ISO-8601, partials (`2024`, `2024-08`) ok; never backfilled |
| `text_ref` / `text_source` | `sha256` of the extracted body → `.tjvz/text/<hh>/<hash>.txt` ; which tier: `content_encoded` \| `description` \| `title` \| `abstract` \| `page` |
| `read` | archive only; ISO-8601, partial ok; mutually exclusive with `dismissed`; → broadcast `<pubDate>` |
| `dismissed` | trash only; ISO-8601 UTC |
| `blurb` | your one-line take, for the broadcast `<description>` (distinct from `summary`) |
| `rating` | **your** rating, an integer `1`–`5`; set by `tjvz rate <id> <1-5>`; never backfilled. Feeds `author_avg_rating` / `pub_avg_rating` (an unrated read is imputed as 3). |

### `readers[]`

Others' signals — from a friend's broadcast feed or an AI drop. Keyed by
`source` (re-ingest updates the entry). Kept strictly separate from your
own `rating`. The substrate for `reader_avg_rating` (feature 4).

```json
{ "source": "birkar", "rating": 5, "blurb": "…", "at": "2026-05-10" }
```

### `source`

| `kind` | extra fields | set by |
|---|---|---|
| `rss` | `feed_id`, `feed_url`, `guid` | the RSS adapter |
| `zotero` | `library`, `key`, `version` | the Zotero adapter |
| `jsonfile` | `path` | `tjvz ingest` |
| `csv` | `path`, `row` | a reading-history CSV importer (a personal log, a Goodreads export, …) |
| `manual` | — | `tjvz add` |

### `format` vocabulary

`scorable` = contributes to the relevance reference set (v0: its tags;
later: its text) and gets a relevance score. Non-scorable items are still
ranked by the other features but never enter the corpus.

| `format` | scorable | Zotero `itemType` | RSS inference |
|---|---|---|---|
| `essay` | yes | `blogPost`, `webpage` | default feed post |
| `article` | yes | `magazineArticle`, `newspaperArticle` | — |
| `paper` | yes | `journalArticle`, `conferencePaper`, `preprint`, `report`, `thesis`, `manuscript` | — |
| `book` | yes | `book` | — |
| `chapter` | yes | `bookSection`, `encyclopediaArticle`, `dictionaryEntry` | — |
| `thread` | yes | `forumPost`, `instantMessage` | — |
| `transcript` | yes | `interview`, `hearing`, `presentation` | — |
| `document` | yes | `document`, `letter`, `email`, `standard`, `patent`, `bill`, `case`, `statute` | — |
| `podcast` | no | `podcast`, `radioBroadcast`, `audioRecording` | `<enclosure type="audio/*">` |
| `video` | no | `videoRecording`, `tvBroadcast`, `film` | `<enclosure type="video/*">` |
| `artwork` | no | `artwork`, `map` | — |
| `dataset` | no | `dataset`, `computerProgram` | — |
| `other` | no | anything unmapped | fallback |

### Minimal valid item — from `kasurian.com/rss` (Ghost)

```json
{
  "schema_version": 1,
  "id": "u:b4afd51433eb6cd9",
  "url": "https://www.kasurian.com/rise-fall-new-atheism/",
  "canonical_url": "https://www.kasurian.com/rise-fall-new-atheism",
  "title": "The Rise & Fall of New Atheism",
  "authors": [{ "name": "Kasurian", "role": "author" }],
  "publication": "Kasurian",
  "format": "essay",
  "tags": ["culture", "politics"],
  "summary": "Where did the New Atheists go? A story on the life and death of one of the first internet-driven cultural movements.",
  "published": "2026-06-28",
  "text_ref": null,
  "text_source": "content_encoded",
  "source": {
    "kind": "rss",
    "feed_id": "kasurian",
    "feed_url": "https://kasurian.com/rss",
    "guid": "6a5b658274803a00011eaeeb"
  },
  "aliases": [],
  "readers": [],
  "first_seen": "2026-09-06T18:22:00Z",
  "read": null,
  "ext": {}
}
```

- **`source.guid` is opaque** on Ghost — `canonical_url` must come from
  `<link>`, never `<guid>`. (A Substack feed like `www.asimov.press/feed`
  sets `guid` = the URL; relying on that breaks on Ghost.)
- **`<category>` is lowercased** at storage (`Culture` → `culture`).
- `text_source: content_encoded` records that a full body exists; `text_ref`
  is `null` in v0 because bodies aren't stored yet.

### Archive item from Zotero

```json
{
  "schema_version": 1,
  "id": "u:f2ac5157dde4cf09",
  "url": "https://www.degruyterbrill.com/document/doi/10.1515/jsall-2025-0006/html",
  "canonical_url": "https://www.degruyterbrill.com/document/doi/10.1515/jsall-2025-0006/html",
  "title": "Reconstructing the relationship between Hindi-Urdu and Dakani: a diachronic dialectological approach",
  "authors": [{ "given": "Joshua H.", "family": "Pien", "role": "author" }],
  "publication": "Journal of South Asian Languages and Linguistics",
  "format": "paper",
  "tags": ["dakani", "linguistics", "hindi-urdu", "historical-linguistics"],
  "summary": null,
  "published": "2025",
  "text_ref": null,
  "text_source": null,
  "source": { "kind": "zotero", "library": "birkar", "key": "A1B2C3D4", "version": 4412 },
  "aliases": [],
  "readers": [{ "source": "asimov-press", "rating": 4, "blurb": null, "at": "2026-07-01" }],
  "first_seen": "2026-09-06T18:22:00Z",
  "read": "2026-05",
  "rating": 5,
  "blurb": "The conservative-retention argument for Dakani's divergence, done properly with a cladistic model.",
  "ext": { "doi": "10.1515/jsall-2025-0006", "zotero_collections": ["Dakani", "To Cite"] }
}
```

---

# The inbox drop format

Write a JSON file into `inbox/` (`*.json`). `tjvz ingest` canonicalises it,
routes it, and moves the file to `inbox/done/`. The expected path for
AI-authored records.

**Supply only what you know.** tjvz fills the rest:

- **Omit `id`** — derived from `canonical_url` (or title + author). A wrong
  `id` is worse than none.
- **Omit `canonical_url`, `first_seen`, `text_ref`, `readers`** — derived / defaulted.
- **`source`** defaults to `{ "kind": "jsonfile" }`; **`ext`** to `{}`.
- `authors` may be bare strings: `["Jane Doe"]` → `[{ "name": "Jane Doe", "role": "author" }]`.
- `tags` are lowercased for you.

### `stream` — the only field that exists here and nowhere else

| `stream` | goes to | also set |
|---|---|---|
| `"feed"` *(default)* | the feed — a candidate to rank | — |
| `"stack"` | straight onto the stack — you already want to read it | — |
| `"archive"` | straight into the archive — already read | `read` (a date) |

Read once, then discarded. There is no `"trash"` — dismissal is a judgement
tjvz makes, not an import target. A `readers[]` entry on the drop is kept; a
top-level `rating` (1–5) is treated as *your* rating only when `stream` is
`archive`.

```json
{
  "stream": "stack",
  "title": "The Uses of Disorder",
  "url": "https://example.com/uses-of-disorder",
  "authors": ["Richard Sennett"],
  "publication": "example.com",
  "format": "book",
  "tags": ["cities", "sociology"]
}
```

---

# `event`

Appended to `events/YYYY-MM.jsonl` per judgement. **Immutable, append-only —
the decision log.** Feature values are *not* here (recomputed on demand,
LOO + as-of `at`). Only what can't be reconstructed is frozen:

| field | meaning |
|---|---|
| `schema_version` | `1` |
| `id` | the item judged |
| `decision` | `promote` (feed→stack) \| `dismiss` (feed→trash; negative label) \| `read` (→archive; positive) |
| `at` | ISO-8601 UTC — also the as-of point for recomputing this row's features |
| `model` | `"linear"` (+ the `features_fingerprint` in effect, for `explain --history`) |
| `rank` | 1-based feed position at decision time — frozen (feed composition is unrecoverable; position bias is signal). `null` if judged off-feed |
| `score` | the score shown at the time — a fossil, for `explain --history`, not for training |

Suppression is a projection of the current stores — `archive ∪ stack ∪
trash` ids — cached at `.tjvz/suppressed.json`, rebuilt on store change.
Not derived from this log.

```json
{ "schema_version": 1, "id": "u:b4afd51433eb6cd9", "decision": "dismiss", "at": "2026-09-06T18:40:12Z", "model": "linear", "rank": 7, "score": 0.38 }
```

---

# `feed` manifest

`feed/manifest.jsonl`, one entry per surfaced-but-unjudged item. STATE
(outside `.tjvz/`) so items you were shown survive a publisher rotating
them out. Entry removed once the item is promoted / dismissed / read.
**Membership + first-seen only** — score and rank are recomputed live every
`tjvz feed`.

```json
{ "id": "u:b4afd51433eb6cd9", "first_seen": "2026-09-06T18:22:00Z" }
```

---

# `model.json`

The weights the model ranks with. Until a learn run is adopted these mirror
`config.yaml` `model.scoring`. `features_fingerprint` ties them to a
feature-set state; a mismatch with the live registry means re-learn.

```json
{
  "schema_version": 1,
  "source": "prior",
  "weights": { "relevance": 3.0, "author_avg_rating": 0.4, "pub_avg_rating": 0.2,
               "author_familiarity": -0.5, "pub_familiarity": -0.25 },
  "bias": 0.0,
  "features_fingerprint": "e3b0c44298fc",
  "learned_at": null,
  "learned": null
}
```

---

# Archive records as a broadcast Atom feed

`tjvz broadcast` writes `<profile>/broadcast/atom.xml` — an Atom feed of
your archive, so a friend running tjvz can subscribe to what you read (the
basis of `reader_recs`). `--publish` then runs `config` `broadcast.publish_cmd`
(e.g. `wrangler pages deploy {dir} …`).

| Atom element | from | notes |
|---|---|---|
| `<entry><title>` | `title` | |
| `<id>` | `urn:tjvz:<item id>` | |
| `<link rel="alternate">` | `canonical_url` | **omitted** when the URL is not a public http(s) host — `file://`, `localhost`, a bare IP, `*.local`/`*.internal`/… The entry is still emitted, just linkless. A subscriber recomputes the same item id from this link (or from title+author when absent). |
| `<updated>` / `<published>` | `read` | coarsened to `YYYY-MM-01T00:00:00Z` when `broadcast.coarsen_dates` (default) — no activity-timing signal |
| `<author><name>` | `authors[].name` | one per author |
| `<category term>` | `tags[]` | gated by `broadcast.share_tags`: `all` (default) · `none` · `no-collections` (drops the `ext.collection_tags` subset — private Zotero collection names) · an explicit allowlist |
| `<summary type="text">` | `blurb`, else `summary`, else `title` | a subscriber reads this into `readers[].blurb` |
| `<content type="html">` | `summary` | only when a `blurb` is also present (so `<summary>` carries your take and `<content>` the source's) |
| `<tjvz:rating>` | `rating` | your 1–5, verbatim; omitted when unrated. `xmlns:tjvz="https://github.com/bombaycyber/tjvz/ns"`. A `kind: reader` subscription folds this into `readers[].rating` → `reader_recs`. |

Which items are included: `broadcast.include` (`all` · `rated` + `min_rating` · `blurbed`), newest `broadcast.limit` by read date (default 100).

Channel metadata (`<title>`, `<subtitle>`, `<link>`, `<id>`/`feed_url`,
`<author>` name + opt-in email) comes from `config.yaml` `broadcast:`, not
from records. Bodies (`<content:encoded>`) are reserved for after TF-IDF —
`broadcast.share_bodies` exists but there is nothing to share yet.
