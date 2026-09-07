# `ctx` — the scoring bundle

Every feature is `compute(item, ctx, params) → real`. `ctx` is the world
state a feature is allowed to read. It is assembled once per scoring pass
(one `tjvz feed`, or one as-of bucket during `tjvz features recompute`) and
shared across all features and all items in that pass.

A feature's manifest lists the members it reads in `needs`; the runtime
builds only those. Nothing else is in scope — a feature cannot open files,
hit the network, or read another feature's output. That is what makes
feature values reproducible and recompute-safe.

## Members

| member | type | contents |
|---|---|---|
| `archive` | list[item] | archive records, **already filtered**: for a historical recompute, `{records with read-date < as_of} \ {the item being scored}` (leave-one-out + as-of-date). For live scoring, the whole archive (the candidate isn't in it). |
| `stack` | list[item] | items currently promoted-not-yet-read. For a recompute, reconstructed as-of `as_of` from the event log (promote before, no read before). |
| `trash` | list[item] | dismissed items, filtered to `dismissed < as_of`. |
| `events` | list[event] | the decision log up to `as_of`. Carries what the item stores don't: `rank`, `score`, decision timing. |
| `readers` | list[reader-signal] | the union of `item.readers[]` across the archive, plus the current item's — others' ratings/blurbs, for `reader_recs`. |
| `text` | `(item) → str \| None` | a lookup into `.tjvz/text/` returning an item's extracted body, or None. Only present once body storage lands (post-v0). |
| `clusters` | derived | tag → coarse-cluster map, built from the archive's tag co-occurrence. Cached under `.tjvz/`, rebuilt when the archive changes. Used by `topic_unfamiliarity`, `*_topical`. |
| `idf` | derived | (post-v0) the archive IDF table + L2-normalised doc vectors + `idf_version`. Cached, rebuilt on archive change. |
| `config` | dict | the profile's `config.yaml`, read-only. |
| `as_of` | str | ISO-8601 timestamp this pass is scoring "as of". `now` for a live feed; the event's `at` during recompute. |

## Rules

- **Deterministic.** `compute(item, ctx, params)` with the same inputs must
  return the same number. No clocks (`as_of` is passed in), no RNG, no IO.
- **Leave-one-out is the runtime's job, not the feature's.** `ctx.archive`
  already excludes the item being scored. A feature just reads `ctx.archive`.
- **`archive` and `events` are already as-of-filtered.** A feature never
  filters by date itself — it would get it wrong.
- **Derived members (`clusters`, `idf`) are caches.** A feature treats them
  as given; the runtime handles invalidation.
- Feature output is any real number; the manifest's `output.transform` maps
  it to [0,1] for the model. `explain()` reports the pre-transform value and
  the inputs behind it.
