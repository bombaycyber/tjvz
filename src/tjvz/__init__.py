"""tjvz — private algorithms for personalised reading recommendations.

An RSS reader that ranks rather than lists. Three stores per profile — a
feed of scored candidates, a stack you promote onto, an archive of what
you've read — and a score that decomposes into named reasons.

Layout:
  cli          argument parsing, dispatch, the single _emit() (human / --json)
  paths        resolve_profile(): --profile flag / env / cwd walk-up / registry
  canon        canonicalise(): assign id, canonical_url, publication, dates
  ident        canonical_url + item_id hashing, the host-rewrite table
  store        load / append the month-sharded JSONL stores; MIGRATIONS
  schema       the dependency-free subset validator; loads schema/*.json
  config       load config.yaml / subscriptions.yaml over the defaults
  suppress     derive + cache .tjvz/suppressed.json  (archive ∪ stack ∪ trash)
  ctx          build the scoring bundle (live now, or as-of a past decision)
  rank         score candidates, sort, reconcile the manifest, record judgements
  model        the one LinearModel: score / explain / learn
  learn        replay the event log (LOO + as-of), fit, gate, write model.json
  verify       golden checks + profile sanity  (`tjvz verify`)
  broadcast    privacy-filtered Atom feed of the archive; --publish runs a cmd
  sources/     one adapter per ingestion stream
  features/    f(item, ctx, params) -> real ; each ships a MANIFEST + checks

Invariant, tested in the README: `rm -rf .tjvz/` must lose nothing but time.
"""

__version__ = "0.1.0.dev0"
