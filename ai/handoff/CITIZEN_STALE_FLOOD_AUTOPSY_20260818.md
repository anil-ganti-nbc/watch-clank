# Citizen stale/out-of-stock flood autopsy — 2026-08-18

## The incident

`citizen_products` run 61 (field-test DB, started 2026-08-17 21:40:49
UTC, completed 21:41:07 UTC, `is_baseline=0`) fetched 300 items, parsed
300, and produced 47 new watches → 47 real `NEW_REFERENCE` events, all
created at exactly `2026-08-17 21:41:06`. Visible references included
BN5058-07E, JY8129-53H, JY8120-58E, and 44 others — a large batch of
Citizen Promaster-family and adjacent dive/field watches, many old and/or
out of stock, arriving together and indistinguishable on the dashboard
from a genuine new-launch story.

## Ledger

| | Value |
|---|---|
| Collector run | 61 (`citizen_products`, `is_baseline=0`) |
| Total Citizen items in run | 300 fetched, 300 parsed |
| New watches / events | 47 / 47, all `NEW_REFERENCE` |
| NEW_REGION / RESTOCK / other | 0 / 0 / 0 |
| Available (verified live) | 2 of 47 (JY8129-53H: stock 1; BN5058-07E: stock 7) |
| Out of stock (verified live) | 45 of 47 (`orderable: false`, `stockLevel: 0`) |
| Unknown (as recorded by Watch Clank at discovery time) | 47 of 47 — `availability_status` was `NULL` for every one |
| `market_status` distribution (source's own field, captured but unused) | Current: 3, DWS: 17, Phase-Out: 2, Promotion: 25 |
| `model_intro_date` range | 2017-06-01 to 2025-01-01 — none are recent launches |
| Editorially plausible | JY8129-53H (Current status, 1 unit left — a real "almost gone" story) is the clearest case; the rest are, on the evidence, low-value clearance noise, not zero-value — a human judgment call, which is exactly what the QC system now exists for |

## Root cause

**Primary: CATALOGUE_STALE_INVENTORY**, driven by genuine, provable
source-side catalogue churn — not a Watch Clank bug in the "wrong
computation" sense. Live-verified:

- citizenwatch.com's `mens` collection total grew from 348 (2026-08-11,
  per the collector's own docstring) to 396 (2026-08-18, this
  investigation's live check) — a real +48 change in one week.
- `discover_via_search()` always paginates the **entire currently-reported
  catalogue** each run (no `max_items` truncation until after full
  discovery); items not yet known to the database are always prioritized
  first into the processed slice, never starved. So the 47 references were
  genuinely absent from the discoverable search-index result set at run 22
  (16:21 UTC, same day) and genuinely present by run 61 (21:40 UTC) — a
  real appearance/reappearance in Citizen's own enumeration surface, most
  plausibly tied to the Promotion/DWS/Phase-Out lifecycle state of these
  specific SKUs (25 of 47 are tagged `Promotion`, 17 `DWS`), not a Watch
  Clank pagination or ordering defect.

**Secondary: AVAILABILITY_SEMANTICS.** The collector's primary discovery
path (`discover_via_search`, Sprint 4's breadth-over-depth tradeoff) never
carries real inventory data — every first observation via that path
recorded `availability_status=NULL` regardless of true stock state, so
Watch Clank had no way to distinguish JY8129-53H (genuinely available,
1 left) from the other 45 (genuinely sold out) at discovery time. This
**was** a real, closeable gap — see Fix below.

**Contributing: SOURCE_DATA_QUALITY.** The individual product page (and,
it turns out, the search-hit payload itself) already carries a
`c_marketStatus` field (`Current`/`Promotion`/`DWS`/`Phase-Out`) that the
parser already captures into `extra_specs`, but nothing downstream ever
reads it. Left as **context, not a filter** — see "what was deliberately
not suppressed" below; a Promotion/DWS tag correlates with but does not
reliably determine stock state (BN5058-07E is `Phase-Out` yet has 7 units
in stock), so it must never gate discovery or scoring on its own.

### Explicit answers

- **Were the events technically correct under current semantics?** Yes —
  every one was a genuine first observation of that reference in this
  database; `NEW_REFERENCE` fired correctly per its literal definition.
- **Were they editorially useful?** Mixed — 2/47 genuinely current/
  low-stock (real signal), 45/47 confirmed out of stock at check time
  (low but not necessarily zero editorial value on their own).
- **Why current?** `is_baseline=0` correctly, because this collector had
  already run multiple times with real history (not a fresh/zero-epoch
  DB) — the auto-baseline invariant from the prior sprint worked exactly
  as designed and did not (and should not) apply here.
- **Why mass burst?** Real, simultaneous source-catalogue-enumeration
  change (see above), not a Watch Clank timing artifact.
- **Same as the baseline/empty-DB flood?** **No.** That failure was a
  fresh/zero-epoch database misinterpreting an entire catalogue's
  first-ever sweep as current news. This is a routine, correctly-
  non-baselined run reacting to genuine upstream catalogue churn — a
  structurally different mechanism with a different fix.
- **Recurrence of an earlier documented Citizen Promaster incident?**
  **Partial.** No prior commit or handoff doc in this repository
  documents this exact pattern being diagnosed before, so it cannot be
  called a literal repeat of a *fixed* bug. But the underlying mechanism
  (source catalogue visibility churn + no availability signal on the
  cheap discovery path) is a standing structural property of this
  collector, not a one-off fluke — if the operator observed a
  similar-shaped batch before, it was very likely this same mechanism,
  simply never root-caused until now.

## Systemic fix (not a Citizen-SKU special case)

`app/collectors/citizen_products.py::CitizenProductsCollector.run()` now
does one bounded, targeted extra step: for items **not already known to
this database** (i.e. exactly the ones that will produce a
`NEW_REFERENCE` event — a handful to a few dozen per steady-state run,
capped at `MAX_AVAILABILITY_ENRICHMENT_FETCHES = 60` regardless), it
fetches the individual product page (which does carry real
`inventory.orderable`/`stockLevel`) instead of relying on the cheap
search-hit record. `parse_citizen_search_hit`
(`app/parsers/citizen_products.py`) now distinguishes a real HTML payload
from the repackaged search-hit JSON by the simple, robust signal that real
HTML is never valid JSON, and delegates to `parse_citizen_product_html`
when it sees one — no content-type sniffing, no per-SKU logic.

Gated on `max_items is not None` (pipeline.py's own existing signal for
"this is a normal, bounded, non-baseline run" — a force-baseline/
first-run sweep passes `max_items=None` and is already fully suppressed
downstream, so enriching it would only add source load for zero editorial
benefit). Falls back to the pre-existing cheap record on any fetch/parse
failure — **recall never regresses** from this change; it can only add
real availability data where none existed, never drop an item.

### What was deliberately NOT suppressed

- No Citizen SKU, family, or Promaster-name special-casing anywhere.
- `available=true` is not required for discovery — an interesting watch
  can launch as sold out, preorder, or nearly-gone (exactly JY8129-53H's
  real situation: 1 unit left, still surfaced, still useful).
- `market_status` (`Promotion`/`DWS`/`Phase-Out`) is not used as a filter
  — proven unsafe by BN5058-07E (`Phase-Out`, 7 units genuinely in
  stock). It remains captured context only.
- Out-of-stock watches are not hidden, blacklisted, or scored down. They
  now carry a real, honest `availability_status` (instead of `UNKNOWN`)
  and flow into the new human-QC queue (see
  `HUMAN_QC_FEEDBACK_CONTRACT.md`) for a human to triage — the intended,
  recall-first resolution path this whole sprint exists to build.

## Verification

- New tests: `test_citizen_search_hit_parser_dispatches_real_html_to_depth_parser`,
  `test_citizen_search_hit_parser_still_handles_repackaged_json`,
  `test_citizen_products_run_enriches_new_items_with_real_availability`,
  `test_citizen_products_run_enrichment_falls_back_when_no_detail_fixture`,
  `test_citizen_products_run_skips_enrichment_on_baseline_sweep`
  (`tests/test_core.py`) — all pass against real captured fixtures
  (`citizen_product_at8294.html`), zero network calls (offline `search_pages`
  mode never issues a real fetch, matching every pre-existing Citizen test's
  determinism).
- Live-verified against the real, current citizenwatch.com catalogue (not
  fixture-only): all 47 affected references still enumerable today;
  ground-truth availability checked per-reference via direct product-page
  fetches (2 available, 45 out of stock, matching the ledger above).
