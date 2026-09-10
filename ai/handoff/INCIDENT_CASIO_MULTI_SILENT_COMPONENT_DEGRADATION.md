# INCIDENT — casio_multi silent component degradation (Casio Japan)

**Date:** 2026-09-10
**Status:** REPAIRED. Forensics-first; no anti-bot bypass; no novelty/baseline semantics touched.
**Deployed:** branch `fix/casio-multi-component-health` (this commit).

## Operator-observed symptoms

1. Manual `casio_multi` attempt reported FAILED.
2. Run history showed intermittent PARTIAL (2026-08-31, 09-03, later) where
   Discovered=10 Fetched=10 Parsed=10 **Fail=0**.
3. The Casio Japan problem "does not appear in the normal logs".

## Findings (live DB + snapshot + live probes)

**Casio Japan HTML collection never worked from this vantage — this is not a
regression and not an Aug-30 code change.**

- First casio_multi run in the database (#1, 2026-08-10 07:38 UTC) was already
  `casio_japan: BLOCKED` — all five discovery URLs (`/jp/watches/`, `gshock/`,
  `edifice/`, `oceanus/`, `protrek/`) returned HTTP 403 (persisted verbatim in
  that run's `summary_metadata.components.casio_japan.discovery_fetches`).
- Every day since shows exactly the same shape: ~15 runs `BACKED_OFF`
  (component skipped via the 24 h backoff latch) + exactly 1 `BLOCKED`
  (the daily backoff-expiry re-probe, which 403s again and re-arms).
- `source_component_states.casio_japan`: `consecutive_blocks=33`,
  `last_success_at=NULL`. There is no last-healthy run: **zero successful
  polls, zero products, zero snapshots ever** (snapshot_fetches contains
  26,420 healthy `casio.com/jp` fetches — all from the sitemap lane; a 403
  discovery never persists a snapshot).
- Live reprobe 2026-09-10 (Hetzner, collector's own UA, no bypass): all five
  HTML listing URLs still 403 with Akamai markers, zero product links.
  Meanwhile `https://www.casio.com/jp/sitemap/watches.xml` returns 200 with a
  2.6 MB document and ~3,087 product references, and the production
  `casio_jp_sitemap` collector is HEALTHY (last SUCCESS with 300 items).
- Git window 2026-08-29..09-01: only qualification/delivery/UI commits; none
  touch the Japan collector, parsers, pipeline aggregation, http_util, or
  snapshot limits. No code-side cause exists in the suspect window; the
  "Aug 30→31 boundary" was an artifact of reading parent run status only.

**Why PARTIAL showed Fail 0.** `run_multi_source_pipeline` computes
`failure_count` strictly from item fetch/parse failures. A blocked discovery
fetch is component-level state: it lands in
`summary_metadata.components[*].status` (`BLOCKED` → parent PARTIAL via the
combined-status rule "any SUCCESS + any BLOCKED ⇒ PARTIAL") and never in
`failure_count`. The row therefore said PARTIAL ... Fail 0 — correct per the
column's definition, but inexplicable without opening the JSON.

**Why the logs omitted it.** Two distinct silences: (a) the daily BLOCKED
probe logged only `multi_pipeline_completed` (components inside) plus httpx
warnings; (b) worse, the ~15 BACKED_OFF runs/day logged nothing at all and
reported parent **SUCCESS** — the combined-status rule treats `BACKED_OFF`
as healthy, so the persistent component failure was invisible in run status,
run history, and logs alike.

**The "manual attempt FAILED"** — no FAILED casio_multi run exists in the
canonical Hetzner DB through 2026-09-10 00:45 UTC (latest run #7971,
SUCCESS). The FAILED attempt is therefore not production state (local
instance or a misread of the daily PARTIAL). Nothing was overwritten based
on it.

## Repair

1. **JP product component swapped to the official sitemap lane (operator
   option C).** `run_multi_source_pipeline` now runs
   `CasioJPSitemapCollector` (watches.xml, ~17.8k refs, healthy) instead of
   the dead HTML collector. New-first discovery against the JP lane's own
   observation history: an unchanged catalogue is a silent, healthy
   `ZERO_ITEMS (NO_NEW_REFERENCES)`; only genuinely new references are
   processed, with the same parser (`parse_casio_jp_sitemap_item`), region,
   collector attribution, and emit/notify/baseline threading as before.
   Legacy `CasioJapanCollector` code and its tests remain untouched.
2. **Component health surfaced in three places.**
   - One terminal structured record per component exit:
     `casio_component_finished component=... status=... reason=... http_statuses=[403] ...`
     — including the previously silent BACKED_OFF skip path.
   - `summary_metadata.component_failures` / `component_skipped` /
     `status_reason` persisted on the run row; `failure_count` semantics
     unchanged (item-level) and deliberately not overloaded.
   - Run History UI gained a Components column + a reason line under the
     status badge; Run detail gained a Components card.
3. **Semantics preserved:** no baseline flood (JP refs were already
   baselined by the standalone lane; identity resolution is global),
   no false NEW_REFERENCE (sitemap lastmod stays weak evidence — asserted
   by test), event/baseline/freshness rules untouched.

## Tests

`tests/test_casio_multi_component_health.py` (new) + updated
`test_core.py`/`test_novelty_semantics.py` component mocks:
- intl SUCCESS + JP BLOCKED ⇒ parent PARTIAL, component persisted
  (components/component_failures/status_reason), terminal log record with
  http_statuses=[403], failure_count stays 0.
- BACKED_OFF skip ⇒ parent SUCCESS but the skip is explicit in components,
  component_skipped, status_reason, and the log record.
- Unchanged healthy catalogue ⇒ ZERO_ITEMS NO_NEW_REFERENCES, parent SUCCESS,
  zero new observations (no write churn).
- Genuinely new JP reference ⇒ parsed by the real sitemap parser, watch
  created, **no** NEW_REFERENCE from a sitemap delta.
- JP parse regression ⇒ component FAILED (PARSE_FAILURES), parent PARTIAL
  with explicit reason, item failures also counted at item level.
