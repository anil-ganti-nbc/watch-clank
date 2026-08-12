# Timex historical-freshness hardening (Sprint 10, 2026-08-12)

## Phase 1 audit: `timex_news` end to end

Live-checked against the real feed (2026-08-12):

- **Entries currently exposed:** 30 (Shopify blog Atom feed's fixed page
  size)
- **Oldest reachable:** 2026-06-01T10:24:28-04:00
- **Newest reachable:** 2026-08-11T09:00:04-04:00
- **Pagination/archive traversal:** none. `?page=N` was tested (2, 3) and
  returns the *identical* 30 entries regardless of page number -- Shopify's
  blog Atom feed does not support real pagination the way `/products.json`
  does. There is no live mechanism to reach articles older than the
  feed's current ~30-entry window through this endpoint.
- **Can feed membership/order change?** Yes -- new posts push the oldest
  entry out of the reachable window. An article that has already rolled
  off cannot be "rediscovered" through this real endpoint; Phase 5's test
  below is a deliberate simulation (per the brief's own instruction) of
  what must happen *if* an old article ever becomes newly reachable
  (e.g. a max_items increase, a Shopify feed-size change, or a
  differently-configured mirror), not a claim that today's feed can
  currently produce it.
- **Canonical identity/dedup:** stable -- `ReleaseLead.announcement_url`
  is unique, `process_news_announcement` dedups by URL and by merge_key
  exactly like every other brand.
- **Is `published_at` always preserved?** Yes, verified live: all 30
  reachable entries have a real `published_at`; 0 NULLs observed live.
- **Can `discovered_at` substitute for publication time?** No, by
  construction -- see the fix below; `_record_watch_event` never reads
  `discovered_at` for the freshness decision, only `lead.announcement_date`.
- **NULL/invalid timestamps possible?** Not observed live, but handled
  explicitly (see Phase 2) rather than assumed impossible.

**Trace:** Timex Atom entry -> `TimexNewsCollector.discover_from_feed`
(one GET, no per-item fetch) -> `parse_timex_news_entry` (title/published/
short excerpt/SKU regex) -> `process_news_announcement` (creates/dedups
`ReleaseLead`, resolves `Watch` per extracted SKU) -> `_record_watch_event`
(**the layer that decides NEW_REFERENCE/NEW_REGION -> `Event`**) -> GUI
(`get_recent_events`, unfiltered on Event since `Event` has no historical/
baseline rows to begin with -- see the finding below) -> future Discord
(`format_alert`, only ever called for a real `Event`).

## The real gap found (not assumed)

Sprint 8 added `SpecialistLead.editorial_freshness` -- but that only
covers Layer B (CASIOBLOG/G-Central/Plus9Time). **Layer A official news
(`ReleaseLead` -> `Event` via `_record_watch_event`) had NO publication-age
gate at all.** The only existing suppression was `is_baseline_active()`/
`force_baseline` (epoch-scoped). A genuinely old official article, first
discovered *after* baseline completed, would have fired a real
`NEW_REFERENCE` Event purely because the watch was new *to Clank* --
exactly the class of bug this sprint's brief describes, just one layer
over from where Sprint 8 fixed it.

## Phase 2 fix: source-scoped publication-freshness gate

`app/services/pipeline.py`:
- `_ISO_TIMESTAMP_NEWS_SOURCES = frozenset({"timex_news"})` -- the only
  official news source whose `announcement_date` is a genuine ISO-8601
  timestamp. Confirmed live that Casio ("July 15, 2026"), Citizen ("23
  July 2026", sometimes "2 July2026" with no space), and Seiko ("January
  07, 2026") all store free-text strings that a strict
  `datetime.fromisoformat()` call safely and predictably fails on.
- `_stale_official_announcement(lead)`: returns `None` (no suppression)
  for any source not in the allowlist -- Casio/Citizen/Seiko structurally
  unaffected, zero regression risk, proven by a dedicated test. For
  `timex_news`: unparseable/missing date -> `"unknown_publication_timestamp"`
  (never assumed fresh); parseable but older than
  `specialist_freshness_window_hours` (72h, confirmed via `scripts/status.py`-
  adjacent config, unchanged from Sprint 8) -> `"stale_publication"`;
  otherwise `None` (event proceeds normally).
- Wired into `_record_watch_event` right after the existing baseline
  guards, before `NEW_REFERENCE`/`NEW_REGION` classification.

## Phase 3: product/catalogue semantics untouched

`_record_product_transition` (RESTOCK/SOLD_OUT/PRICE_CHANGE/
AVAILABILITY_CHANGE) and the catalogue-discovery NEW_REFERENCE path in
`process_fetch_result` were **not modified at all** -- the fix lives
exclusively in `_record_watch_event`, the news-announcement path. A
years-old Timex watch that restocks tomorrow, or a genuinely new SKU that
appears in the healthy catalogue, is completely unaffected. Proven with a
dedicated regression test (`test_old_timex_product_restock_still_fires_current_event`).

## Phase 4/5 regression fixtures (all in `tests/test_core.py`)

1. `test_real_historical_timex_article_does_not_become_current_news` --
   uses the REAL "Todd Snyder x Timex Marlin Mesh" entry from the live
   fixture capture (title/published 2026-07-28T07:00:07-04:00/URL all
   real; a SKU sentence appended since the real content has none, an
   already-documented parser characteristic). Historical evidence stored,
   zero Event.
2. `test_fresh_timex_article_still_creates_event` -- proves the gate
   doesn't over-suppress.
3. `test_timex_article_with_null_publication_timestamp_does_not_become_news`
   -- NULL timestamp on `timex_news` -> not assumed fresh.
4. `test_future_rediscovery_of_old_timex_article_produces_no_alert` --
   Phase 5's simulated scenario: a canonical URL genuinely new to Clank,
   publication date ~400 days old. New DB evidence created; zero Event.
5. `test_casio_and_citizen_official_news_unaffected_by_timex_hardening` --
   Phase 7 proof.
6. `test_old_timex_product_restock_still_fires_current_event` -- Phase 3
   proof.

## Phase 6: live audit of currently stored Timex news (real production DB)

Before this sprint's live re-run: 15 Timex `ReleaseLead` rows, all from
Sprint 9's `force_baseline` population, publication dates 2026-07-06
through 2026-08-11, 0 NULL timestamps, 0 Events (all correctly `is_baseline`
via `force_baseline`). Classification was already entirely correct --
nothing to reclassify, no historical data touched.

**Live proof captured during this sprint's own verification:** re-running
`timex_news` with `max_items=30` (vs. Sprint 9's default 15) surfaced 13
more real articles genuinely new to Clank (published 2026-06-01 through
2026-07-03, first discovered 2026-08-12) -- **this is the exact
first-discovered-after-baseline scenario the brief describes, caught
live, not simulated.** Result: 13 new `ReleaseLead` rows stored as real
historical evidence, **0 new Events**. `scripts/status.py`'s
`Latest official event` timestamp did not move.
