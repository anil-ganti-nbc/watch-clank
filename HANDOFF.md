# Watch Clank — Development Handoff
**Last updated:** 2026-08-11
**Current phase:** Casio Stage 1 soak (unaffected, still running) + experimental Citizen/Seiko discovery AND product/catalogue observation lanes (both real, live-validated, both schedulable, neither on any real schedule yet)
**Sprint priority note (record, don't erase history):** Sprint 1 deliberately held Stage 2 during soak. Sprint 2 was an explicit owner-directed priority change — recall over precision for the experimental lane, journalist is the verification layer — executed 2026-08-11, 3 days into the original soak hold. That original soak-hold reasoning was correct at the time; this is a documented pivot, not a retraction.
**Next developer:** Claude
**Primary environment:** Windows 10/11, local-first
**Repository path:** `C:\Users\anil\Desktop\Watch clank\watch-clank`
**Dashboard:** `http://127.0.0.1:8765`

(See prior handoff sections 0–64 as provided at session start for full architecture,
mission, and philosophy notes — omitted here for brevity, unchanged.)

---

# Checkpoint log

## 2026-08-11 (Sprint 3) — Real Citizen + Seiko product observations flowing

**Starting point verified:** HEAD was `82d1e44` as expected, clean tree,
62/62 tests passing, Ruff clean, Alembic at head. Casio soak checked
healthy: run 66 `PARTIAL` (normal — `casio_intl_news SUCCESS`, `casio_japan
BLOCKED`), and the Sprint 2 run-lock fix confirmed still in effect (run 65
correctly shows `FAILED` with `stale_recovery: true`, no leftover lock
files).

**Priority 1 — Citizen product data: found and built.**
`citizenwatch.com` (Citizen Watch America's real D2C store, Salesforce
Commerce Cloud/Mobify PWA Kit) is server-side rendered — the full product
record (name, brand, price, promotional price, inventory/availability,
case material, movement, water resistance, dial color, band material,
crystal, intro date) is embedded as JSON directly in the static HTML of
`/us/en/product/<reference>` pages (React Query hydration state). No API
call, no JS execution, no anti-bot circumvention needed — this is the same
plain `httpx` GET every other collector in this project already does.
Discovery: a small fixed set of collection pages
(`/us/en/collection/{attesa,tsuyosa,collabs}`) link directly to product
URLs with the reference embedded in the path. `app/collectors/
citizen_products.py` + `app/parsers/citizen_products.py`.

**Priority 2 — wired into transition events.** `process_fetch_result`
(previously 100% Casio-hardcoded — `parse_casio_product_html`, hardcoded
`region="JP"`) generalized with `parse_fn`/`default_region`/`emit_events`/
`notify`/`experimental` kwargs, all defaulting to the exact prior Casio
values, so the production Casio catalog-enrichment path is provably
unaffected (regression tests + the full pre-existing suite pass unchanged).
New `PipelineService._record_product_transition`: compares a new
`SourceObservation` against the most recent prior one for the same
watch+region and calls `classify_price_availability_transition` (built
dormant in Sprint 2, now live). Safety is structural, not a flag that could
be set wrong: `process_fetch_result` only ever reaches observation
creation after a successful fetch + successful parse, so any two
`SourceObservation` rows it compares were, by construction, both healthy —
a failed fetch never creates one, so it can never be compared against or
mistaken for a transition.

**Priority 3 — cross-source correlation: real, not just tested.** Sprint
1's Citizen news fixture references `CC4107-80H` (the Attesa titanium
limited edition). Live validation on 2026-08-11 found this exact reference
still listed on citizenwatch.com's live Attesa collection page, at
$2,195.00, `AVAILABLE`. Processing both the Sprint-1 news announcement and
the live product page resolves to the same `Watch` row (`reference_
canonical == "CC4107-80H"`, `new_watch=False` on the second), proven by
`test_citizen_news_and_product_references_correlate_to_same_watch` and
directly observed with real data in this session — not a coincidence
engineered for the test, an actual discovered fact about the live catalog.

**Priority 4 — Seiko: found, time-boxed correctly.** Additional
investigation of `seikowatches.com`'s `/v3/api/` confirmed it's real
(Azure-backed, `200 application/json`) but the exact route/payload still
wasn't recovered from a further round of reasonable guesses — time-boxed
and abandoned per instruction, not force-fit. Pivoted to alternative
first-party paths per Priority 4's explicit permission: found
`seikousa.com`. **Verified first-party before use** — its own Terms of
Service states "This website is operated by Seiko Watch of America LLC"
(Seiko's official US importer, the same corporate relationship Citizen
Watch America has to citizenwatch.com), not a third-party retailer despite
marketing copy ("Shop authentic Seiko watches") that read ambiguously at
first glance. It runs Shopify, which by default publicly exposes
`/collections/all/products.json` — a standard, publicly-documented Shopify
storefront feature returning full product records including
title/handle/vendor/product_type/tags/variants (sku/price/available) in
one request; `product_type == "Wrist Watches"` cleanly filters out straps
and other non-watch products. Currency (`USD`) confirmed via the store's
own `Shopify.currency = {"active":"USD"}` page state, not assumed.
`app/collectors/seiko_products.py` + `app/parsers/seiko_products.py`.

**Tests:** 62 → **77 passed**. New: Citizen product discovery/parsing
(real captured fixtures + synthetic price-drop/sold-out variants for
offline transition testing), the 12 Sprint-3 Citizen acceptance criteria
(baseline-no-event, repeat-no-duplicate-event, PRICE_CHANGE, SOLD_OUT→
RESTOCK, failed-fetch-cannot-fabricate-SOLD_OUT, news/product identity
correlation), Seiko product discovery/parsing/watch-type-filtering, and a
Seiko baseline→PRICE_CHANGE pipeline test. `ruff check .`: all checks
passed. `alembic current`: unchanged — no new migration needed (reuses
`SourceObservation`'s existing price/currency/availability_status/region
columns).

**Live validation (real network, throwaway SQLite DBs — never touched
`data/watch_clank.db`):**
```
Citizen: Run 1 (baseline) -> 8 new watches, 0 events (correct: all baseline)
         Run 2 (repeat, same live data) -> 0 new watches, 0 events (correct: no transition)
Seiko:   Run 1 (baseline) -> 15 new watches, 0 events
         Run 2 (repeat) -> 0 new watches, 0 events
```
Both runs used isolated lock files (`citizen_products.run.lock`,
`seiko_products.run.lock`) that were created and cleanly released — no
leftover locks in the real `data/` directory, confirmed after each run.

**Promotion status:** Citizen and Seiko product observation both pass the
sprint's stated bar for the experimental scheduled lane (fixtures, tests,
isolated live validation, repeated-run dedup, failure-transition
isolation) — per this sprint's explicit instruction not to require further
days of manual testing before experimental scheduling, they are ready to
add to `scripts/systemd/watch-clank-experimental.*` / an equivalent Windows
task once the owner wants real observations flowing on a schedule. Not
added automatically this session — scheduling installation is a
system-affecting action left for explicit confirmation.

**Next action / top 5 priorities:** (1) decide whether to actually schedule
the experimental product lanes now that they're proven; (2) if Seiko's
`/v3/api/` is ever cracked, it would add non-US-market Seiko coverage
seikousa.com can't (that store is US-only, English-only); (3) widen
Citizen's discovery collection-page seed list once initial alert quality
from the current three is reviewed; (4) consider a small correlation pass
that proactively cross-checks existing `ReleaseLead.model_references`
against product observations for brands/regions beyond the one live
example found this sprint; (5) Casio's own product/catalog path
(`app/parsers/casio_japan.py`) already has price/availability support —
once Akamai unblocks even occasionally, wire `emit_events=True` there too
(currently default False, unchanged) after a short review, since the
infrastructure is now identical across all three brands.

## 2026-08-11 (Sprint 2) — Recall tuning, price/availability logic, Discord, overlap-lock bug fix, portability

**Starting point verified:** HEAD was `b76da6b` as expected, clean tree
(only untracked `data/`), 48/48 tests passing, Ruff clean, Alembic at head.

**Real defect found and fixed during Phase 0 verification (not something
this sprint was looking for — found while checking soak health):**
`collector_runs` id=65 (`casio_multi`) was stuck `RUNNING` since 07:12 UTC
with a confirmed-dead process (pid checked via `Get-Process`, not present).
Root cause: `RunLockService` (`app/services/run_lock.py`) hardcoded
`collector_id == "casio_japan"` in its DB queries (`find_active_run`,
`recover_stale_runs`) regardless of what it was protecting.
`run_multi_source_pipeline` creates rows under `collector_id="casio_multi"`
but reused this same class — so a crashed `casio_multi` run's RUNNING row
could never be recovered, and would never even be detected as "active" by a
concurrent-run check either. Fixed by adding a `collector_id` constructor
parameter (default `"casio_japan"`, so every pre-existing caller is
byte-identical) and passing `collector_id="casio_multi"` explicitly from
`run_multi_source_pipeline`. Regression test added
(`test_run_lock_is_scoped_to_collector_id`). **Manually recovered run 65
against the real DB using the fixed code** (not hand SQL) — now `FAILED`
with `stale_recovery: true`, stale lock file removed, and a fresh scheduled
run (id=66) completed `PARTIAL` cleanly afterward. This also means the
experimental brand lane (see below) is now genuinely safe to schedule — its
lock was built with this fix already in place, isolated per-brand.

**Phase 1 — seikowatches.com:** time-boxed further investigation. Confirmed
`/v3/api/` is a live endpoint namespace served through Azure Front Door
(ARRAffinity cookie, `x-azure-ref` header — real backend, not a static 404
page) and returns `200 application/json` for guessed paths, but every
guessed route/payload (`/v3/api/news`, `/News`, `/news/list`,
`/News/GetList`, query-string and POST-body variants using the
`Country`/`Language` config values seen in `news.js`) returned an empty
body. The exact route/payload was not recovered in the time budgeted.
**Not built.** Still the single highest-value next task for Seiko.

**Phase 2 — product/catalogue observation:** not built this sprint. Casio's
catalog collector/parser already supports price+availability
(`app/parsers/casio_japan.py`), but it's Akamai-blocked. Citizen and Seiko
have no product-page collectors yet — only news-announcement collectors,
which rarely carry a numeric price (confirmed live: Citizen's Attesa
announcement said "Price ... subject to change", no figure). Building
real Citizen/Seiko catalog collectors from scratch was judged lower
priority than fixing the scheduling bug above and shipping the
recall-tuned scoring + safe Discord/portability work in the time available.
**Top priority for a future session.**

**Phase 3 — price/availability events:** the deterministic classifier is
built and tested (`app/services/editorial.py::classify_price_availability_
transition`), producing `PRICE_CHANGE` / `AVAILABILITY_CHANGE` / `SOLD_OUT`
/ `RESTOCK` from a healthy before/after observation pair. Guards: never
infers SOLD_OUT from a failed/unhealthy fetch on either side, never compares
different regions, never compares different currencies (no conversion
layer, explicitly out of scope). **Not wired to any live data source yet**
— it has nothing to compare until Phase 2 product collectors exist. Ready
to call the moment they do.

**Phase 4 — cross-region:** `NEW_REGION` (from Sprint 1) unchanged.
Confirmed via the existing test plus manual review that the merge-key
dedup caveat documented in Sprint 1 still applies — realistic `NEW_REGION`
needs more than one source per brand per region to be common.

**Phase 5 — recall tuning (`app/services/editorial.py`,
`SCORING_RULE_VERSION` 0.1.0 → 0.2.0):**
- Confidence thresholds lowered (HIGH ≥55→≥50, MEDIUM ≥30→≥25) —
  more genuine evidence now reaches MEDIUM/HIGH instead of LOW.
- New scoring dimensions, all evidence-gated (never guessed): limited
  edition (+10, +quantity if stated), named collaboration (+10), unusual
  material (+10). Extracted conservatively from the announcement **title
  only** via `PipelineService._extract_product_character` — e.g. "Limited-
  Edition Recrystallised Titanium" title text, not inferred from anything
  unstated.
- PRICE_CHANGE now scales with magnitude (`price_delta_pct`), capped at +35.
- `score_event` now rejects unknown `event_type` values (`ValueError`) —
  a real safety net against a typo silently producing an unscored/garbage
  event.
- **Live proof this changed real output:** the same live Citizen ATTESA
  announcement scored 60/100 in Sprint 1 and **80/100 in Sprint 2** (added
  "+10 limited edition" and "+10 unusual material (recrystallised
  titanium)") — same underlying evidence, better-tuned scoring.

**Phase 6 — Discord (`app/services/discord_notify.py`, new):**
`DiscordNotifier` with separate `send_editorial_alert`/`send_health_alert`,
each reading its own webhook URL from `Settings` (env/.env only, nothing
committed — `discord_editorial_webhook_url`, `discord_health_webhook_url`,
both `None` by default). Every public method catches all exceptions and
returns `False` on any failure — verified with a test that mocks
`httpx.post` to raise `ConnectionError` and confirms no exception escapes.
Wired into `_record_watch_event` via new `notify`/`experimental` params on
`process_news_announcement`, used only by `run_brand_news_pipeline`
(`notify=True, experimental=True`) — **the Casio production path never
calls `notify=True`**, so it cannot send anything even if a webhook were
configured. Threshold for the experimental lane is
`DISCORD_EXPERIMENTAL_MIN_SCORE` (default 0 — alert on everything during
tuning, per Sprint 2's explicit instruction). No real webhook is configured
anywhere in this repo/environment, so this is built and tested but
currently inert.

**Phase 7/8 — portability:** added `scripts/run_scheduled.sh` (same
exit-code contract, same log files as `run_scheduled.ps1`),
`scripts/setup-linux.sh`, and `scripts/systemd/` (service/timer templates
for both the Casio production lane and the experimental brand lane, plus a
`README.md`). **Decision recorded per Phase 8's explicit requirement:**
Windows and Linux/cloud use **independent SQLite databases** if run
simultaneously — no synchronization is built or planned; see
`scripts/systemd/README.md` for the full reasoning. The canonical runtime
command stays `python -m scripts.run_pipeline` on both platforms
(`--scheduled` for Casio, `--experimental-brand {citizen,seiko}` for the
new lane) — no entrypoint was renamed.

**Tests:** 48 → **62 passed**. New: run-lock collector_id scoping,
price/availability transition classifier (6 tests covering the
false-positive guards explicitly), recall-tuning scoring dimensions,
unknown-event-type rejection, Discord notifier safety (no-op, never-raises,
channel separation), product-character extraction. `ruff check .`: all
checks passed. `alembic current`: unchanged, `003_release_leads (head)` —
no new migration was needed (Discord config is env-only; price/availability
reuses existing `SourceObservation` columns).

**Promotion status:** still nothing new is scheduled. Citizen/Seiko are
schedulable now (isolated lock fixed) but deliberately not yet added to any
real scheduler — recommend a few days of manual `--experimental-brand` runs
first to observe real event/alert volume before enabling a systemd timer or
Windows task for them.

**Next action / top 5 priorities**, in order: (1) recover the
seikowatches.com `/v3/api/` route — likely the single highest-leverage
remaining task; (2) build a real Citizen product-detail-page collector for
price/availability (Citizen's news pages already link to product pages);
(3) same for Seiko once/if the API is cracked; (4) once Phase 2 exists, wire
`classify_price_availability_transition` into `process_fetch_result`; (5)
after a few days of experimental-lane data, decide Citizen/Seiko systemd/
Task Scheduler promotion using real observed alert quality, not just test
passing.

## 2026-08-11 — Multi-brand sprint: Citizen + Seiko experimental discovery, deterministic event/scoring layer

**Context:** owner-directed sprint to extend coverage to Citizen and Seiko and
add change-intelligence/editorial scoring, explicitly overriding the "hold
during soak" default (soak was 3 days in at the time). Executed conservatively
per the sprint brief's own non-negotiable rule: the Casio production path
must not be destabilized. Everything new is additive and isolated.

**Casio production path: unaffected.** All 35 pre-existing tests still pass
unmodified; two new regression tests
(`test_casio_production_path_emits_no_events_by_default`) explicitly guard
that the generalized `process_news_announcement`/`_resolve_or_create_watch`
produce byte-identical behavior for Casio's default call path. `scripts/run_
pipeline.py --scheduled` (the scheduled task's entrypoint) was not touched
and does not call any new code.

**Source investigation (live probes, 2026-08-11):**
- `citizenwatch-global.com/news/` — HTTP 200, static HTML, and (unlike Casio
  or Seiko) is *already* a pure watch-product feed with no corporate noise.
  Chosen as the Citizen source.
- `seiko.co.jp/en/news/` — HTTP 200, static HTML, but Seiko Group
  Corporation's *corporate* feed (watches + clocks + financial results +
  cultural sponsorships). Requires topic filtering; used as the Seiko source
  with a conservative `is_watch_announcement` filter (mirrors Casio's).
- `seikowatches.com/global-en/news` — HTTP 200 but is a JS-rendered Vue SPA
  backed by a `/v3/api/` REST endpoint. The endpoint path was not recovered
  from the minified bundle in the time available. **Documented gap, not
  built**: this is probably Seiko's best watch-only source and should be the
  first thing a future session investigates for Seiko (see priorities below).
- Grand Seiko's news page is the same SPA pattern — same gap.

**Code added:**
- `app/collectors/citizen_news.py`, `app/parsers/citizen_news.py` —
  discovery + parsing for the Citizen global news feed. Reference format
  `[A-Z]{2}[0-9]{4}-[0-9A-Z]{2,4}` confirmed against live announcement text
  (e.g. `CC4107-80H`).
- `app/collectors/seiko_news.py`, `app/parsers/seiko_news.py` — discovery
  (with topic filter) + parsing for seiko.co.jp. Reference format
  `S[A-Z]{2}[0-9]{3}[A-Z0-9]{0,3}` (e.g. `SPB255`).
- `app/normalization/references.py` — `normalize_citizen_reference`,
  `normalize_seiko_reference`: conservative pass-through (canonical == raw),
  per the brief's explicit instruction not to blindly apply Casio's JDM
  suffix rules to other brands. Documented in the module docstring as
  policy: a brand gets suffix-stripping rules only once evidence justifies
  it, same bar Casio's JDM allowlist had to clear.
- `app/services/editorial.py` (new) — deterministic, explainable event
  scoring. `EventEvidence` → `score_event()` → `ScoredEvent{score, confidence,
  reasons[]}`. Every point added has a reason string; unscored dimensions
  say `UNKNOWN` rather than being silently omitted. `format_alert()` renders
  the Phase 6 human-readable block. No LLM, no black-box classifier.
- `app/services/pipeline.py`:
  - `process_news_announcement` generalized with optional `manufacturer`,
    `brand`, `parse_fn`, `merge_key_prefix`, `default_region`, `emit_events`
    kwargs, all defaulting to the exact prior Casio-only values/behavior.
  - `_resolve_or_create_watch` now dispatches reference normalization by
    manufacturer via a small registry, defaulting to
    `normalize_casio_reference` (unchanged call for Casio).
  - New `_prior_regions_for_watch` / `_record_watch_event`: deterministic
    `NEW_REFERENCE` / `NEW_REGION` classification, writing to the existing
    (previously unused) `Event`/`EventWatch` tables from `001_initial` — no
    new migration needed. Only fires when `emit_events=True`, which the
    Casio production path never passes.
  - New `run_brand_news_pipeline(brand, ...)` — experimental orchestrator
    for Citizen/Seiko. Does **not** share `RunLockService`/overlap
    protection with the Casio `casio_japan` lock; writes its own
    `collector_runs` rows under brand-specific `collector_id`s
    (`citizen_news`, `seiko_jp_news`) and `source_component_states` rows,
    fully isolated from Casio's. Not called from any scheduled path.

**Known false-positive-protection finding (important, not a bug):** the
pre-existing `merge_key` duplicate-lead detection (unchanged, Casio-proven)
catches a second announcement of the *same* reference before event detection
ever runs — so `NEW_REGION` cannot fire from literally re-processing the
same reference text under a new URL; it only fires when a genuinely separate
lead (different merge_key, e.g. from a different source) references a watch
already seen in another region. This is correct/desired — it's the same
duplicate-suppression Casio already relies on — but it means realistic
`NEW_REGION` detection will depend on having more than one source per brand
per region eventually. Documented in
`test_brand_news_pipeline_new_region_detected_not_new_reference`'s docstring.

**Tests added (13):** discovery parsing (Citizen, Seiko), Seiko topic-filter
inclusion/exclusion, reference extraction + collection guessing for both,
conservative-normalization tests for both, an end-to-end experimental
pipeline test (lead + watch + event created from a fixture), NEW_REGION vs.
NEW_REFERENCE classification, a same-region-repeat produces-no-event
false-positive guard, the Casio-path-emits-no-events-by-default regression
guard, and two `editorial.py` unit tests (score bounds/explainability, alert
formatting only echoes supplied evidence). **48 passed** (was 35).
`ruff check .`: all checks passed.

**Live validation (real network, throwaway SQLite DB — never touched
`data/watch_clank.db`):**
```
citizen: run status=SUCCESS, 8 leads, 19 references, 19 watches, 19 NEW_REFERENCE events
  scores: HIGH(60) for recognisable families (Tsuyosa/Attesa/Promaster),
  MEDIUM(40) for unrecognised ones (Rainell, Eco-Drive, etc.)
seiko:   run status=SUCCESS, 1 lead discovered (Credor announcement) out of
  the corporate feed, 0 watches (Credor's reference format isn't covered by
  the current MODEL_RE — correctly produced 0 fabricated watches/events
  rather than guessing)
```
This is real evidence the discovery+identity+scoring pipeline works
end-to-end for a live first-party Citizen source today, and that the Seiko
corporate-feed filter is conservative (under- rather than over-discovers) —
consistent with "prefer missing a story to fabricating one."

**Discord/alerting:** not implemented. `format_alert()` produces the text
block; no webhook, no network call, no credentials. Deferred per the brief's
own Phase 7 sequencing ("first make the intelligence good") and because
there is no existing Discord infrastructure to reuse.

**Promotion status — nothing new is scheduled/production:**
| Source | Status | Notes |
|---|---|---|
| casio_intl_news | PRODUCTION, soaking | unaffected |
| casio_japan (catalog) | PRODUCTION, soaking, BLOCKED (Akamai) | unaffected |
| citizen_news | EXPERIMENTAL | live-validated once, needs multi-day soak before scheduling |
| seiko_jp_news | EXPERIMENTAL | live-validated once; corporate-feed filter needs more real samples before trust |
| seikowatches.com (Seiko brand SPA) | NOT BUILT | API endpoint not reverse engineered |

**Next action:** do not add these experimental brands to the Windows
scheduled task yet. Run `run_brand_news_pipeline` manually a few more times
over the coming days against the real `data/watch_clank.db`, inspect leads
for false positives (especially Seiko's topic filter), then decide whether
to graduate. Reverse-engineer `seikowatches.com`'s `/v3/api/` news endpoint
as the highest-value next step for Seiko coverage.

## 2026-08-09 — Soak-day timezone comparison outage found and fixed

**Root cause:** `SourceComponentState.backoff_until` (and `last_success_at`,
`last_blocked_at`) are declared `DateTime(timezone=True)`, but SQLite does not
actually preserve timezone offsets on that column type — any value reloaded
from the database (i.e. in every process after the one that wrote it) comes
back as a naive `datetime`. `PipelineService._should_skip_backed_off`
(`app/services/pipeline.py`) compared that naive, persisted `backoff_until`
directly against a fresh timezone-aware `datetime.now(UTC)`:

```python
return state.backoff_until > datetime.now(UTC)
```

Once `_update_component_state` had written a real `backoff_until` (which
first happened as a side effect of the previous checkpoint's manual pipeline
run, run 24, which hit the Casio Japan catalogue and got BLOCKED), every
subsequent scheduled invocation raised
`TypeError: can't compare offset-naive and offset-aware datetimes` inside the
`try` block of `run_multi_source_pipeline`, was caught by the existing
`except Exception` handler, and correctly written to a terminal `FAILED` row
— so the scheduler and the fail-safe terminal-status logic were never at
fault. The news-discovery half of the pipeline (the part that actually
matters editorially) never got a chance to run.

**Affected runs:** `collector_runs` 25–34 (2026-08-08 10:12 through
2026-08-09 08:42), all `FAILED` with
`"fatal_error": "can't compare offset-naive and offset-aware datetimes"`.
Each has a populated `completed_at` — none were left stuck in `RUNNING`.
These rows were left untouched as historical evidence, per instruction.

**Datetime policy established:** all operational timestamps are persisted
and compared in UTC. New shared utility `app/core/time.py`:
- `utc_now()` — `datetime.now(UTC)`.
- `ensure_utc(value)` — normalizes a possibly-naive persisted datetime to
  aware UTC (assumes naive means "already UTC", which is the only thing this
  codebase ever writes). Returns `None` for `None`.

**Code changed:**
- `app/core/time.py` (new) — the shared policy described above.
- `app/services/pipeline.py::_should_skip_backed_off` — normalizes
  `state.backoff_until` through `ensure_utc` before comparing against
  `datetime.now(UTC)`. This was the exact line that raised.
- `app/services/run_lock.py` — `find_active_run`, `recover_stale_runs`, and
  `_lock_is_stale` already had ad hoc `if x.tzinfo is None: x =
  x.replace(tzinfo=UTC)` normalization (which is why the stale-run-recovery
  path was never affected by this bug); replaced with calls to the shared
  `ensure_utc` for consistency, no behavior change.
- `app/main.py::dashboard` — same ad hoc normalization for
  `latest_run.started_at` replaced with `ensure_utc`, no behavior change.
- `tests/test_core.py` — 11 new regression tests (see below) plus two
  pre-existing unrelated fixes carried over from the previous checkpoint's
  Ruff/pytest pass.

No new migration was needed — the fix is entirely application-level
normalization at the read side, exactly as the columns already being
`DateTime(timezone=True)` intends; no destructive schema change was made or
required.

**Regression tests added** (`tests/test_core.py`):
1. `test_ensure_utc_normalizes_naive_and_aware`
2. `test_should_skip_backed_off_naive_stored_value` — reproduces the exact
   crash scenario (state written, then forced to reload naive from SQLite via
   `session.expire_all()`, matching what a second process/run actually sees)
3. `test_should_skip_backed_off_aware_stored_value`
4. `test_should_skip_backed_off_expired_backoff_allows_run`
5. `test_should_skip_backed_off_no_state_allows_run`
6. `test_repeated_403_increases_backoff`
7. `test_success_resets_backoff`
8. `test_multi_source_active_backoff_skips_catalog_cleanly` — asserts the
   catalog collector is never even invoked while backed off, and the run
   never reaches `FAILED` or stays `RUNNING`
9. `test_multi_source_expired_backoff_allows_catalog_run`
10. `test_news_success_catalog_backed_off_is_not_failure`
11. `test_news_deduplication_repeated_announcement_no_duplicate_lead`

Combined with the pre-existing `test_multi_source_news_success_catalog_blocked`,
this covers both BLOCKED and BACKED_OFF non-failure paths. Full suite: 35
passed (was 24 before this checkpoint's additions). `ruff check .`: all
checks passed.

**Manual verification against the real, in-use database (not a fresh one):**
- `alembic current` → `003_release_leads (head)`, unchanged.
- Confirmed the live DB actually held the naive-datetime landmine before the
  fix: `source_component_states` row for `casio_japan` had
  `backoff_until = '2026-08-08 12:51:05.885066'` (no offset) with
  `last_status = BLOCKED`.
- `python -m scripts.run_pipeline --scheduled` run manually against this real
  DB produced run_id=35, completing `PARTIAL` with no exception. The stored
  backoff window had actually expired by the time of this run, so it
  correctly re-attempted the catalogue live, got a clean fresh 403, and
  re-armed a new backoff window — `casio_intl_news SUCCESS` (10 discovered),
  `casio_japan BLOCKED`, overall `PARTIAL`.

**Scheduler-wrapper verification:**
`powershell -File .\scripts\run_scheduled.ps1` run directly, producing
`collector_runs` id=36: START/END logged in
`data/logs/scheduled-wrapper.log`, `exit_code=0`, `casio_intl_news SUCCESS`
(10 discovered), `casio_japan BACKED_OFF` (the fresh backoff window set by
run 35 was still active seconds later), overall `SUCCESS` — no timezone
exception, no reinstall of the Task Scheduler task needed.

**Current soak status:** the soak clock effectively restarts from this
checkpoint. Runs 25–34 (the outage window) are excluded from any future
"healthy soak" analysis; run 35 onward (2026-08-09 08:49 and later) is the
real signal. Casio Japan catalogue remains Akamai-blocked as expected — no
change to that known limitation. Next scheduled firing should confirm the
fix holds unattended; no further code changes are planned.

## 2026-08-08 — Soak-day migration outage found and fixed

**Delta:** DB was pinned at `002_ops_statuses` while code/head was `003_release_leads`.
Every scheduled run since at least 2026-08-07 20:12 (checked back through the full
wrapper log) failed with `sqlite3.OperationalError: no such table:
source_component_states`, exit_code=2. The scheduler itself was healthy (firing every
~90 min exactly as configured) — this was a pure "forgot to run `alembic upgrade head`
after pulling the 003 migration" gap. Per section 25's lesson, task registration
(and even task *execution*) is not proof the pipeline runs meaningfully; only
`collector_runs` rows with real content prove it.

**Migrations added:** none — applied the existing `003_release_leads` migration that
had not yet been run on this machine (`002_ops_statuses` → `003_release_leads`).

**Files substantially changed:**
- `tests/test_core.py` — `test_parser` was calling `.read_text()` without an encoding
  on UTF-8 fixtures containing Japanese text; on this machine Python defaults to
  cp1252, causing `UnicodeDecodeError`. Fixed to `.read_text(encoding="utf-8")`,
  matching the pattern already used elsewhere in the same file (news-list fixture read).
- `alembic/versions/003_release_leads.py` — Ruff auto-fix for unsorted import block
  (`I001`), no logic change.

**Test result:** 24 passed (was 23 passed, 1 failed before fix).

**Ruff result:** All checks passed (was 1 error before fix).

**Live probes performed:**
- Manual run of `python -m scripts.run_pipeline --scheduled` after the migration fix
  (run_id=24): `casio_intl_news` SUCCESS (10 discovered, 10 fetched), `casio_japan`
  BLOCKED (five URLs, all HTTP 403, correctly classified as BLOCKED not ZERO_ITEMS),
  overall status PARTIAL, 10 new release leads, 9 new watches. This matches the
  documented "healthy soak" pattern exactly (section 42).
- Casio International News: HTTP 200, confirmed live during this run.
- Casio Japan catalogue + G-Shock/Edifice/Oceanus/Pro Trek listing pages: HTTP 403,
  confirmed still Akamai-blocked, as expected.

**DB counts after fix:**
```
collector_runs: 24
release_leads: 10
watches: 22
source_observations: 0
snapshot_fetches: 10
snapshot_blobs: 10
pipeline_ledger: 44
source_component_states: 2
```

**Scheduler status:** Task exists, Ready, Enabled, last run 2026-08-08 14:12:25
(pre-fix, still failing), next run 2026-08-08 15:42:24 — should now succeed since it
shares the same DB path that was just migrated.

**Known limitations:** unchanged from prior handoff (Casio Japan catalogue still
Akamai-blocked; Citizen/Discord/editorial scoring still not implemented).

**Next action:** No further intervention needed. Let the scheduled task run at
15:42 and subsequent 90-minute intervals confirm success (status SUCCESS/PARTIAL,
not FAILED) now that the schema is current. Continue the soak per section 42;
do not start Stage 2. If a future session pulls new migrations, always run
`alembic upgrade head` immediately — this outage happened because that step was
skipped after `003_release_leads` was added to the codebase.
