# WATCH EXPANSION DOGFOOD REPORT
Unified Clank Architecture dogfood scorecard for this programme.

## Scorecard — bespoke-code cost per collector

| Collector | Brand-specific code | Reusable machinery used | Config lines | Fixtures/tests | Time vs collector 1 |
|---|---|---|---|---|---|
| casio_uk_sitemap (pre-existing) | ~200 lines (the original pattern) | none — it *is* the pattern | n/a | pre-existing | baseline |
| **Tissot sitemap** (collector 1 of expansion) | 1 regex + constants in `tissot_sitemap.py` (~35 lines); registry block (~14 lines); normalizer entry | SitemapDeltaCollector family, http_util health helpers, pipeline process path, generic normalizer | config object ~10 | 11 family tests shared + live-shaped fixture | 1.0× (family built here) |
| Hamilton/Bulova (next, via family) | same shape as Tissot: regex+constants | identical | ~10 | reuse family suite with new fixture file | projected ~0.3× |
| Shopify-family brands (post-extraction) | mapping only | family TBD | TBD | TBD | projected ~0.2× |

Verdict against the programme's own test: collector 2 onward is configuration,
mapping and fixtures. The family extraction is what bought that; the scorecard
should be re-filled after the next brand to confirm the trajectory held.

## Unified Architecture principles exercised

- **Registry-driven onboarding** — product-registry entry added for tissot;
  friction recorded (inline import block inside a 700-line method) rather than
  worked around.
- **FIRST_SEEN ≠ NEW_REFERENCE** (Fleet Law 2 / DB-002 / DB-012) — untouched;
  regional-presence identity pin test added.
- **Law 1 no-flood** — Tissot first run inherits auto-baseline; burst annotator
  already counts FIRST_SEEN floods.
- **Law 3 health honesty** — family statuses flow through http_util so
  BLOCKED/FAILED/ZERO_ITEMS render truthfully in the repaired two-axis health.
- **Law 6 provenance** — every observation carries collector_id/version,
  source_url, lastmod provenance in extra_specs.
- **Observer separation** — no Motherclank/Diagnostic write-backs; Diagnostic
  corpus consumed read-only as evidence.
- **Absence ≠ zero / UNKNOWN over invented certainty** — price/availability
  stay None with parser warnings; no event emitted on lastmod-only changes.
- **ADR-0007 destructive safety** — production DB untouched; backups pre-date
  all work; NAS/L corpus read-only.

## Architectural friction encountered (for canon review)

1. Lazy inline `_PRODUCT_REGISTRY` population: adding a brand means editing
   imports buried mid-function. Proposal: declarative module-level registry
   table loaded by name — ADR candidate, not a unilateral change.
2. No canonical "regional presence" event type exists yet; regional deltas
   currently stop at observation level. Documented in WATCH_EVENT_SEMANTICS.md;
   needs an eligibility-contract decision before implementation.
3. `KNOWN_COLLECTORS` list in health.py must be hand-synced when registering
   collectors (no compile-time check). Candidate for the same registry table.

## Fleet-wide lessons proposed for canonicalisation

- The **failure-corpus methodology** itself (past incident → law → regression
  fixture at collector-add time) worked well enough to propose as an ADR:
  "expansion conformance" gate for any new Clank participant.
- Two-axis health (acquisition × yield) — already validated in Watch Clank's
  repair; generalisable beyond watches.

## What was NOT done (honesty)

- Wave-1 brands beyond Tissot: blocked on vantage/network reality recorded in
  the source matrix — six brittle scrapers were explicitly worse than four
  robust ones; the matrix turns them into cheap follow-ups instead.
- Scheduler enablement, production deployment, Diagnostic Clank writes: all
  out of scope by constraint; nothing was promoted.
