# WATCH EVENT SEMANTICS — Expansion Vocabulary
2026-08-25. Governing law: FIRST_SEEN ≠ NEW_REFERENCE (Fleet Law 2).
Principle: prefer UNKNOWN / observation-only over a confident false event.

## Existing vocabulary (unchanged, authoritative)

| Event type | Earned by | Expansion note |
|---|---|---|
| FIRST_SEEN_BY_CLANK | Local first sighting, weak/no launch evidence | Default for every new brand's baseline aftermath |
| NEW_REFERENCE | Fresh published_at + launch-like cluster shape, or first-party announcement | Must stay hard to earn |
| NEW_REGION | Known reference newly observed in a new region | The *intended* product of multi-locale sitemaps |
| RESTOCK / SOLD_OUT / PRICE_CHANGE / AVAILABILITY_CHANGE | Healthy before/after observation pairs with real fields | Requires price/availability evidence sitemap family honestly lacks |

## Expansion decision table (what each discovery becomes)

| Observation | Event produced? | Rationale |
|---|---|---|
| New SKU in en-us Tissot sitemap, never seen anywhere | FIRST_SEEN_BY_CLANK (baseline-silent on first run) | Local absence is not launch evidence (DB-012) |
| SKU known from Timex-style other-source history, now seen in a second locale's sitemap | NEW_REGION-class observation; event eligibility deferred until regional-availability semantics land | "Regional presence" ≠ global launch (programme constraint 7) |
| `lastmod` bump alone on an existing reference | No event. Observation only | A bare timestamp cannot say *what* changed (casio_uk precedent) |
| JSON-LD page with new price vs prior observation | PRICE_CHANGE (future StructuredProduct lane) | Only with both-sided field evidence |
| Collaboration tag (Peanuts/NASA/automotive) | Enrichment metadata (`is_collaboration`, score bonus) — never novelty | Phase H design: enrich, don't fabricate |
| Press PDF / CDN asset appearing | **No event** — future dark-matter lane stores graded evidence only | Document appearance ≠ product launch (L-WATCH-005 class) |

## Evidence grades (dark-matter spike vocabulary, preserved not acted on)

`OFFICIAL_PRODUCT_PAGE` > `OFFICIAL_SITEMAP_DELTA` >
`OFFICIAL_CDN_ASSET` / `OFFICIAL_SUPPORT_DOC` >
`CERTIFICATION_DB_ENTRY` > `THIRD_PARTY_RETAILER_LISTING` >
`RUMOUR`. Each grade is provenance metadata on an observation;
only OFFICIAL_PRODUCT_PAGE + freshness may ever claim NEW_REFERENCE today.

## Deliberate non-events

- Regional sitemap parity checks (identical SKU sets across locales) produce
  nothing.
- Re-crawl of unchanged catalogue: nothing (repeat observation is silent).
- Baseline runs: nothing (auto-baseline + source-scoped force_baseline).

## Schema honesty

No schema changes were required: observations carry their None fields and
warnings; events carry `novelty_evidence` and QC memory context. When the
programme later wants `REGIONAL_AVAILABILITY` as a *distinct eligible* event,
that requires an ADR-grade contract change (new event type + eligibility rule
+ QC dispositions), deliberately out of scope until Tissot soak evidence exists.
