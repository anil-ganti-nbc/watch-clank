# Watch Clank — Development Handoff
**Last updated:** 2026-08-11
**Current phase:** EPOCH 1 (started 2026-08-11T13:11:25Z). Operating from a fresh operational database after a controlled reset (Sprint 7) — the impromptu Sprint 1-6 production soak is preserved as an archive, not deleted. EIGHT Windows-scheduled tasks total (Casio + 4 experimental brand/product lanes + CASIOBLOG + G-Central + Plus9Time, all Ready/Enabled, verified live), silently baselined with zero editorial noise, then a real repeat run confirmed zero unexplained churn. Layer B early-warning system with family-aware correlation + Discord editorial/health alerts wired (still inert — no webhook URLs configured anywhere) + a local-only Windows Control Centre GUI/EXE (Sprint 6, not pushed by policy; re-verified working against the new DB in Sprint 7 without a rebuild). Cloud deployment infra exists (merged from a separate branch) but this session has no access to the actual host to perform/verify a live deployment.
**Sprint priority note (record, don't erase history):** Sprint 1 deliberately held Stage 2 during soak. Sprint 2 was an explicit owner-directed priority change — recall over precision for the experimental lane, journalist is the verification layer — executed 2026-08-11, 3 days into the original soak hold. That original soak-hold reasoning was correct at the time; this is a documented pivot, not a retraction.
**Next developer:** Claude
**Primary environment:** Windows 10/11, local-first
**Repository path:** `C:\Users\anil\Desktop\Watch clank\watch-clank`
**Dashboard:** `http://127.0.0.1:8765`

(See prior handoff sections 0–64 as provided at session start for full architecture,
mission, and philosophy notes — omitted here for brevity, unchanged.)

---

# Checkpoint log

## 2026-08-11 (Sprint 7) — Epoch 1 reset: archive, fresh DB, baseline, G-Central + Plus9Time

**Starting point verified:** HEAD `425b0e5`, clean tree, 120/120 tests passing,
Ruff clean, schema at `005_specialist_lead_correlation_type`, 6 Windows tasks
Ready/Enabled, DB had 558 watches / 2100 observations / 12 events / 10
specialist leads / 99 collector_runs accumulated from Sprints 1-6's impromptu
production soak.

**Why:** the owner explicitly noted that initial discovery correctly treated
everything unknown to Clank as "new," which was valuable for proving the
pipeline but is not a useful newsroom starting state — most of that state
represents watches/articles that existed in the world long before Clank
started watching. This sprint distinguishes "first observed by Clank" from
"new after operational baseline" going forward.

**Phase 0-1 (freeze + archive):** disabled all 6 Windows tasks (confirmed
none RUNNING first), confirmed DB quiescent (no locks, no stale RUNNING
rows). Archived the entire pre-Epoch-1 state to
`C:\Users\anil\Desktop\Watch clank\archives\watch-clank-pre-epoch1-20260811T125715Z\`
(outside git, outside the repo's runtime paths): a safe sqlite3-backup-API
copy of the live DB (independently `PRAGMA integrity_check` verified: `ok`),
the actual retired live DB file, full status/schema/git-commit/scheduler/
DB-counts/source-registry summaries, copied logs, and a README documenting
what the archive is and how to restore it if ever needed. Never deleted.

**Phase 2 (epoch model):** new `operational_epochs` table (migration
`006_operational_epochs`) plus `epoch_id`/`is_baseline` columns on
`collector_runs`, `source_observations`, and `specialist_leads` (deliberately
NOT added to `events` — see below). New `app/services/epoch.py`
(`start_epoch`/`start_baseline`/`complete_baseline`/`is_baseline_active`) and
`scripts/epoch.py` CLI (`start`/`baseline-start`/`baseline-complete`/
`status`) — no manual SQL needed anywhere in this process.

**Phase 3 (fresh DB):** old `data/watch_clank.db` moved (not deleted) into
the archive as a second, independent provenance copy. Ran `python -m
scripts.migrate` against the now-empty path — normal migration path, no
special-casing — producing a fresh, empty, schema-current DB at the exact
same `DATABASE_URL` path every collector/the GUI/the EXE already resolve
automatically. Started epoch `epoch_1` at 2026-08-11T13:11:25Z.

**Phase 4 (baseline-suppression code):** `_record_product_transition` and
`_record_watch_event` (app/services/pipeline.py) now check
`is_baseline_active()` first and return `reason=epoch_baseline_active`
without creating an `Event` row — this is why `Event` never needed an
`epoch_id` column: baseline never creates one at all, by construction, not
by a flag. `SpecialistLeadService.ingest_candidate` still creates
`SpecialistLead` rows during baseline (stamped `is_baseline=True` — real
discovery data, needed later for correlation/lead-time), but
`notify_new_lead`/`notify_correlation` refuse to send while `is_baseline`
is true. All of `PipelineService`'s ~5 real `CollectorRun` creation sites
now stamp `epoch_id`/`is_baseline` via a new `_epoch_fields()` helper.
11 new tests cover this directly, including a real product-observation-path
stamping test and a real news-announcement-path suppression test.

**Phase 4 (specialist source expansion, before the baseline):** researched
and implemented G-Central (g-central.com, real WordPress RSS, Casio/G-Shock
regional-release/restock/collaboration coverage) and Plus9Time
(plus9time.com, real Squarespace RSS, Seiko/Grand Seiko/Citizen coverage —
honest finding: mostly historical/archival content, few extractable current
references, still implemented because it's real/cheap/safe and fills a
coverage gap). Both built with a shared `app/parsers/rss_common.py` core
(extracted, not touching the already-shipped `casioblog.py` parser). Found
and fixed a real false-positive during isolated live validation against the
actual live feed: the reference regex matched "GAme" inside the English word
"game" in a real G-Central headline about G-Shock on Roblox — fixed by
requiring a digit in the matched suffix, with a regression test using the
exact real title. Investigated Japan Select (real Shopify store, confirmed
public `/collections/casio/products.json`, 203 real Casio products with
price/availability/reference each — same proven-safe mechanism as Sprint 3's
Citizen/Seiko collectors) but deliberately deferred implementation to stay
within this sprint's bounded scope (two new sources, not three). Both new
collectors and their parsers passed the full acceptance bar (fixtures from
real live feed captures, canonical-URL/timestamp/attribution/reference tests,
duplicate suppression, silent-baseline, failure isolation) and were live-
validated end-to-end against an isolated throwaway database — not the Epoch 1
operational DB — before being included in the baseline.

**Phase 6 (silent baseline):** ran all 8 finalized sources sequentially
against the fresh operational DB with baseline active. Real results: Casio
9 new watches (casio_japan still Akamai-BLOCKED as expected, not a
regression), Citizen news 9 watches/10 leads, Seiko news 0 watches/1 lead,
Citizen products 291 watches, Seiko products 222 watches, CASIOBLOG 10
leads, G-Central 20 leads, Plus9Time 20 leads. **Zero Event rows created.
Zero Discord alerts sent** (verified directly against the DB, not assumed).
555 total watches, 50 total specialist leads, all stamped `is_baseline=true`.

**Phase 7 (repeat-run validation — the critical test):** immediately ran all
8 sources again against the now-LIVE (baseline-completed) epoch. Real
result: **0 new watches, 0 new specialist leads, 0 new events, 0 alerts**
across every source — verified directly against the DB before and after.
No unexplained churn to investigate or document.

**Phase 10-11 (GUI/EXE/scheduling):** re-enabled all 6 original Windows
tasks, then installed 2 new ones (`WatchClank-GCentral` 45min,
`WatchClank-Plus9Time` 360min — cadence justified by each source's real
observed posting frequency, see `install_windows_gcentral_plus9time_tasks.ps1`).
All 8 tasks Ready/Enabled, live-triggered GCentral and confirmed a real new
`collector_runs` row with zero side effects on watch/event/lead counts
(expected repeat). Both the GUI (`local_windows/control_centre/main.py`,
launched from source) and the already-built EXE
(`WatchClankControlCentre.exe`, launched **without rebuilding** — it didn't
need to change, since `app/services/health.py`'s query shape didn't change)
were live-screenshotted showing the new DB correctly: 555 watches, "Latest
official event: none", all 6 legacy sources HEALTHY.

**Test result:** 142 passed (up from 120 at sprint start — 22 new: epoch
lifecycle x5, baseline-suppression x6, health/DB-backup unaffected, G-Central
x5, Plus9Time x5, source-attribution x1). Ruff clean throughout.

**Next priorities:** get real Discord webhook URLs from the owner; Japan
Select implementation (technically ready, deliberately deferred); Great
G-Shock World and NEEL (deferred again, unchanged from Sprint 5/6); actual
cloud deployment execution (infra exists, still no host access from any
session so far).

## 2026-08-11 (Sprint 6) — Control Centre GUI/EXE, Discord wiring, family-aware correlation, cloud handoff prep

**Starting point verified:** HEAD was `08cf8c7` as expected, clean tree, 108/108 tests
passing (post-migration bump from 105), Ruff clean. Two Sprint 5 commits
(`61ce5ac`, `08cf8c7`) were unpushed from the prior sprint's deliberately-scoped
push authorization; this sprint's owner brief explicitly authorized pushing the
validated portable/core changes from those plus this sprint's own work, while
keeping the new Windows GUI/EXE strictly local. Pushed `08cf8c7` (containing
those two commits) first, verified remote HEAD, then did all Sprint 6 work on
top and pushed again separately as `0377718`.

**Family-aware correlation (migration `005_specialist_lead_correlation_type`):**
`SpecialistLeadService.correlate_pending_leads` now distinguishes
`EXACT_REFERENCE_MATCH` from `FAMILY_MATCH` (candidate matches a watch's
hyphen-stripped family root, e.g. lead "GWR-B3000" vs official
"GWR-B3000-1A") — deterministic, no fuzzy string similarity. Live-verified
against the real production DB: 3 real CASIOBLOG leads (including the
GWR-B3000 example documented in Sprint 5's research doc) now correctly
correlate as FAMILY_MATCH with real lead times of 66.8/87.6/119.8 days. A
new `format_correlation_followup_alert` labels FAMILY_MATCH explicitly as
"NOT EXACT", never as a confirmation.

**Discord wiring:** `SpecialistLeadService.notify_new_lead` /
`notify_correlation` send the Layer B early-warning / follow-up alerts,
gated by a new `discord_specialist_min_confidence` threshold and a new
`editorial_notifications_enabled` config flag (for the future cloud-handoff
policy: Windows can be told to stop sending once cloud becomes
authoritative, without any distributed-DB sync). Dedup via a new
`specialist_leads.notified_at` column — tested to fire once, never twice,
across repeat pipeline runs. Health-webhook alerts wired for the two real
outage classes this project has already hit: stale-run recovery
(`RunLockService.recover_stale_runs`) and schema mismatch
(`scripts/run_pipeline.py`'s existing gate) — both live-tested, and tested
to stay silent when there's nothing actionable (no health spam). **Webhook
URLs are still not configured anywhere in this environment** — nothing was
invented; see the final report for exactly what the owner needs to supply.

**CASIOBLOG scheduling:** new `WatchClank-Casioblog` Windows task, 45-minute
cadence (justified in `install_windows_casioblog_task.ps1`'s header —
cheap RSS, real ~hourly updateFrequency, well within the brief's 30-60 min
guidance). Registered and live-verified: triggered, fired, created a real
new `collector_runs` row (SUCCESS, 0 new leads — correctly deduped against
already-known leads).

**Ops improvements (kept small per the brief):** `app/services/health.py`
— a single reusable health-snapshot function (schema state, DB integrity
via `PRAGMA quick_check`, per-source HEALTHY/WARNING/FAILED/NEVER_RUN with
a heartbeat-overdue check against each collector's expected cadence, active
locks, stale RUNNING count) — used by both `scripts/status.py` (one-command
CLI, works on cloud too) and the new GUI's Health tab, so the two can never
disagree about what "healthy" means. `scripts/db_backup.py` uses sqlite3's
actual backup API (not a raw file copy, which can capture a torn snapshot
under WAL) with retention pruning — live-tested against the real production
DB (4.4MB backup produced). PowerShell wrapper logs (`scheduled-*.log`,
previously unbounded) now get a simple one-backup rotation via new
`lib_log_rotate.ps1`. Python's own log rotation (`RotatingFileHandler` in
`app/core/logging.py`) already existed from an earlier sprint — verified,
not rebuilt.

**Windows Control Centre GUI + EXE (local-only, NOT pushed):**
`local_windows/control_centre/` — Tkinter (stdlib), six tabs (Overview,
Recent Intelligence, Operations, Scheduler, Logs, Health/Diagnostics). Reads
the real DB via `app.services.health` and plain model queries; RUN NOW
buttons shell out to the real `python -m scripts.run_pipeline` (same code
path Task Scheduler uses, same lock protection — the GUI cannot bypass
`RunLockService`); Scheduler tab is restricted to a fixed list of known
Watch Clank tasks only (never arbitrary Task Scheduler administration), and
disabling the Casio production task requires an explicit confirmation
dialog. Packaged with PyInstaller as `WatchClankControlCentre.exe`
(`local_windows/dist/`) — a launcher, not a bundled second copy of the
collector stack: at startup it walks upward from its own on-disk location
looking for `alembic.ini` to find the real repo, and shells out to the
repo's own `.venv\Scripts\python.exe` for every actual run. Both the
source GUI and the built EXE were live-launched (not just code-reviewed)
against the real production DB and screenshotted mid-session: Overview
("WATCH CLANK IS ALIVE", 558 real watches, all 6 sources HEALTHY), Recent
Intelligence (real official events + real early-warning leads including
the FAMILY_MATCH correlation above), and Scheduler (real Task Scheduler
state for all 6 tasks) all confirmed rendering correctly. `.gitignore`
updated (`local_windows/`, `*.spec`, `WatchClank*.exe`) — confirmed via
`git status` that none of it is tracked.

**Test result:** 120 passed (up from 108 at sprint start — 12 new tests:
family-match correlation x3, Discord dedup x3, health snapshot x3, DB
backup x2, correlation-followup alert format x1). Ruff clean.

**Next priorities (per this sprint's own final report):** get real Discord
webhook URLs from the owner; Great G-Shock World collector (Sprint 5
research, deferred); NEEL investigation depth (Sprint 5 research,
deferred); actual cloud deployment execution (infra exists, no host
access from any session so far).

## 2026-08-11 (Sprint 5) — Shipped official hunter + started Layer B early-warning

**Starting point verified:** HEAD was `c9b39b8` as expected, clean tree,
85/85 tests passing, Ruff clean, Casio soak healthy.

**Phase 1 — push, but with a real merge first.** `git push` was rejected —
origin/main had diverged: a separate branch/session
(`feature/cloud-migration-hetzner`) had already been merged via PR #2/#3,
adding real, validated Hetzner deployment infrastructure (schema-mismatch
refusal at startup, a `resolved_lock_path` bug fix found via a genuine
Docker overlap test, Docker build, staging runbook). Investigated before
touching anything — no destructive action taken. Merged cleanly (`git merge
origin/main`, no conflicts; the one overlapping file, `scripts/run_pipeline.py`,
touched different functions on each side). Fixed 3 pre-existing Ruff import-
order issues in the merged branch, verified `--scheduled` still works with
the new schema-check gate, then pushed. **Remote HEAD independently
verified via `git ls-remote`** to match local exactly.

**Phase 2 — Windows scheduling activated for real.** New
`scripts/install_windows_experimental_tasks.ps1` (+ `run_scheduled_
experimental.ps1` from Sprint 4) registered `WatchClank-{CitizenNews,
SeikoNews,CitizenProducts,SeikoProducts}` alongside the existing
`WatchClank-CasioJapan`. All five confirmed `Ready`/`Enabled`; a real
triggered run of citizen-news produced a genuine new `collector_runs` row.
**Confirmed still running unattended later in this same session** — runs
78/79 (citizen_products, seiko_products) fired automatically via Task
Scheduler while other work was in progress, with zero manual intervention.

**Phase 3 — cloud: honest limitation, not built.** The merged Hetzner work
means deployment infrastructure (Dockerfile, docker-compose.staging.yml,
migrate-then-run procedure, the PID-namespace lock finding requiring an
external `flock` wrapper) already exists and was validated in a separate
session. This session has **no SSH/host access** to the actual Hetzner
instance — `deploy_run.sh` lives on the host, not in this repo. Cloud
deployment could not be literally performed here. Not fabricated as done.

**Phase 4-10 — specialist source research.** See new
`ai/handoff/SPECIALIST_SOURCE_RESEARCH.md` for the full table and evidence
trail. Highlights: CASIOBLOG has a real, working RSS feed
(`casioblog.com/en/feed/`) and is independently confirmed as a real
historical Notebookcheck citation (Anubhav Sharma, MRG-B5000SA-2 leak).
@geesgshock (Instagram) is the single most-cited specialist source found
in this research (multiple real NBC articles, sole-cited source in at
least one) but cannot be safely automated — no public API/RSS, and
automating it would mean crossing exactly the authentication/anti-bot
boundary this project has consistently refused (same reasoning as never
attacking Casio's Akamai protection). NEEL (neel.co.jp) confirmed real —
a legitimate Japanese authorized multi-brand retailer (Casio/Seiko/Citizen/
Grand Seiko) — but no direct historical NBC citation was found for it in
this sprint's search sample; documented, not implemented. Oracle Time
(oracleoftime.com) confirmed real but is a broad general-watch magazine
with no G-Shock/Casio leak evidence found — deprioritized. Great G-Shock
World and @morgan_gshock were found as *additional* real sources via
direct inspection of NBC articles' own "Source(s)" sections (not guessed) —
Great G-Shock World is a strong candidate for the next specialist collector
(same profile as CASIOBLOG: real blog, confirmed NBC citations, no
automation blocker).

**Phase 11-13 — early-warning data model.** New `specialist_leads` table
(migration `004_specialist_leads`), deliberately **separate** from
`release_leads` (Layer A/official) — see `app/models/specialist_lead.py`'s
docstring for why reusing the official table would risk exactly what this
sprint forbids (a leak becoming indistinguishable from a press release).
Fields cover source type/tier/URL, publication vs. discovery timestamps,
reference candidates, claim text, confidence, and correlation fields
(`correlated_watch_id`, `official_first_observed_at`, `lead_time_days`) —
enough to support future source-performance analytics (Phase 13) without
building any now. `app/services/source_registry.py` is a plain Python dict
mapping source_id -> (type, tier) — deliberately not a DB table yet, and
raises loudly on an unregistered source_id rather than defaulting a tier.

**Correlation is real and conservative, proven both ways:**
`SpecialistLeadService.correlate_pending_leads()` only matches an exact
(case-insensitive) reference string against `Watch.reference_raw`/
`reference_canonical` — no fuzzy matching. Tested and **live-verified
against the real production DB**: CASIOBLOG's real "GWR-B3000" rumor lead
did NOT correlate with the real official `GWR-B3000-1A`/`-A2`/`-B8` watches
already in the DB, because "GWR-B3000" (family-level, what the blog title
says) is not byte-equal to "GWR-B3000-1A" (full official reference). This
is the conservative design working exactly as intended, not a bug — and
it's an honest, documented limitation (family-prefix vs. full-suffix
matching would need real evidence before loosening, same bar every other
normalization decision in this project has had to clear).

**Implementation:** `app/collectors/casioblog.py` + `app/parsers/casioblog.py`
(RSS, real `<10s` fetch, no anti-bot concerns) + `app/services/
specialist_leads.py` (ingestion, correlation, `run_casioblog_pipeline`
with the same isolated-lock pattern as every Sprint 3/4 experimental lane).
Manual ingestion: `scripts/run_pipeline.py --ingest-manual-lead --lead-*`
for sources that cannot be automated (built specifically for
@geesgshock-style leads per the sprint's explicit fallback requirement).
New `format_early_warning_alert()` in `app/services/editorial.py`,
structurally distinct from the official `format_alert()` — always leads
with "EARLY WARNING — UNCONFIRMED", always shows tier/source type, never
uses "Editorial score" language.

**A real, live parser bug was found and fixed via testing before any live
run**: the real CASIOBLOG feed has a stray leading `\r\n` before its XML
declaration (confirmed live, not a fixture artifact) that Python's
`ElementTree` rejects per spec strictness even though it's harmless and
every real feed reader tolerates it. Fixed by stripping leading whitespace
before parsing.

**Tests:** 85 → **105 passed** (+1 schema-check test updated for the new
migration head, not a regression — see below). `ruff check .`: all checks
passed. `alembic current`: now `004_specialist_leads (head)`.

**Live validation (real network):**
```
CASIOBLOG: Run 1 -> 10 new leads (real, current rumors/announcements).
           Run 2 (repeat) -> 0 new leads (dedup confirmed with live data).
           Correlation pass against the real production DB -> 0 matches
           (honest — see the GWR-B3000 example above).
```
Also ran a real triggered run of `WatchClank-CitizenNews` via the new
Windows installer, and independently observed Windows firing
`citizen_products`/`seiko_products` on its own schedule later in the same
session — genuine unattended-scheduling evidence, not just installer
success.

**Discord:** still no webhook found in this environment (`.env` does not
exist, `discord_editorial_webhook_url`/`discord_health_webhook_url` both
unset). Not invented. `format_early_warning_alert()` is ready the moment
one is configured — no further code change needed to wire it in.

**Next action / top 5 priorities:** (1) implement Great G-Shock World as
the second specialist collector — same low-risk profile as CASIOBLOG,
already confirmed as a real NBC-cited source; (2) investigate whether
family-prefix-to-full-reference correlation can be done safely (e.g. only
when the family prefix uniquely identifies one Watch row) — would fix the
GWR-B3000-style non-correlation seen live this sprint; (3) get actual SSH/
host access to the Hetzner instance (or have the owner run the existing,
already-validated `STAGING_RELEASE_RUNBOOK.md` procedure directly) to
complete Phase 3; (4) investigate NEEL's own site structure the way Sprint
3/4 did for citizenwatch.com/seikousa.com, to determine if it's safely
collectible as RETAILER_EARLY_LISTING; (5) once a couple of specialist
sources have run for real days, review whether any produced a lead that
later actually correlated with an official watch, to start validating the
lead-time metric with real (not just synthetic-test) data.

## 2026-08-11 (Sprint 4) — Turned it on: scheduling + broad catalogue coverage

**Starting point verified:** HEAD was `114d7be` as expected, clean tree,
77/77 tests passing, Ruff clean, Alembic at head, Casio soak healthy (run
66 `PARTIAL`, run 65's Sprint-2 stale-recovery still in effect, no leftover
locks).

**Owner explicitly approved scheduling the four experimental lanes** based
on Sprint 3's passing acceptance criteria — not re-litigated this sprint.

**Phase 1 — scheduling infrastructure:**
- `scripts/run_pipeline.py`: new `--experimental-product {citizen,seiko}`
  flag (mirrors Sprint 3's `--experimental-brand`), default `max_items=300`
  to cover the full discovered catalogue rather than a small sample.
- `scripts/run_scheduled_experimental.ps1` (new): one generic Windows
  wrapper parameterized by `-Lane`, same exit-code contract and log-writing
  pattern as `run_scheduled.ps1`, own log files per lane
  (`scheduled-experimental-<lane>-{wrapper,python}.log`) so nothing
  interleaves with Casio's logs. Added to `validate_powershell.ps1`'s
  syntax-check list.
- `scripts/systemd/`: replaced the single ambiguous Sprint-2
  `watch-clank-experimental.*` pair (which was actually Citizen-news-only)
  with four clearly-named unit pairs: `watch-clank-{citizen-news,seiko-news,
  citizen-products,seiko-products}.{service,timer}`. Cadence: news lanes
  90 min (matches Casio's own interval — announcements are infrequent);
  product lanes 6h (same-day price/availability transitions are still
  caught well within a news cycle; keeps the paginated catalogue crawl's
  request volume low). Rationale documented in each `.timer` file and in
  `scripts/systemd/README.md`.
- **At least one real run of all four lanes was performed against the
  actual production `data/watch_clank.db`** (not a throwaway DB — the
  owner explicitly approved this): run ids 68-73. Casio's own run 67 fired
  naturally via Task Scheduler *during* this session, interleaved with the
  experimental runs, with zero interference in either direction — real
  proof of isolation, not just a design claim.

**Phase 2 — Citizen catalogue expansion (the sprint's highest-value task):**
Sprint 3's discovery only scraped product links off 3 small collection
pages (~8 references). Investigated citizenwatch.com broadly: no
sitemap.xml exists, but the collection pages themselves are backed by a
server-rendered search/listing API (`"data":{"limit":...,
"effectiveSearchMode"` JSON, same hydration pattern as the individual
product page) supporting `?offset=&limit=` pagination and reporting an
authoritative `total`. Confirmed live: `mens` collection total=348,
`womens` total=182. Each search "hit" already carries a `representedProduct`
object with almost the full spec set (case material, movement, water
resistance, dial/band/crystal, intro date, collection) — **no second HTTP
request per product needed**, unlike Sprint 3's per-page-fetch approach.
New `CitizenProductsCollector.discover_via_search()` paginates both
categories (bounded by `MAX_CANDIDATES_PER_COLLECTION=600` as
catalogue-collapse protection against an anomalous `total`), new
`parse_citizen_search_hit()` parser (shares field-mapping with the existing
`parse_citizen_product_html` via a new `_watch_from_product_data` helper).
Tradeoff, stated plainly: this broader path has no availability signal
(Citizen's search API doesn't expose inventory/orderable state, only the
individual product page does) — `availability_status` is `None`/UNKNOWN
from this path, never guessed. The old per-product-page path
(`discover_from_collection_html` + `parse_citizen_product_html`, which
*does* have availability) is kept, unused by default `run()`, available for
a future smaller/deeper pass.

**A pagination bug was found and fixed via testing before this shipped:**
the original loop terminated on "page returned fewer than `limit` items,"
which is correct for the real site (pages are always full except the last)
but broke on small test fixtures. Fixed to rely solely on the
source-reported `total` (authoritative) with the short-page heuristic only
as a fallback when `total` is unavailable. Caught by
`test_citizen_search_pagination_follows_total_across_pages` before any live
run, not after.

**Phase 3 — Seiko catalogue expansion:** `seikousa.com`'s
`/collections/all/products.json` supports Shopify's standard `?page=N`
pagination. Confirmed live: page 1 = 250 products, page 2 = 26 more, page 3
= empty (natural terminator) — **276 total products, 225 of them
`product_type == "Wrist Watches"`** (the rest: straps, clocks, gifts,
filtered out deterministically by that field, not by reference-format
guessing). This is the full catalogue, not a sample. New
`SeikoProductsCollector.discover_all_pages()`, bounded by `MAX_PAGES=20` as
catalogue-collapse protection. Seiko per-product-page enrichment (JSON-LD
`Product` schema exists and does carry a rich description — confirmed via a
time-boxed check) was investigated and found reliably obtainable, but
**deliberately not built**: it would require a second HTTP fetch per
product (225 extra requests) for enrichment beyond what the existing
Shopify listing already provides (title, sku, price, availability),
contradicting the "no second fetch per product" design just adopted for
Citizen. Documented as a real, available, not-yet-taken option — not a
blocker.

**Real, pre-existing bug found and fixed via this sprint's live
validation** (not something Sprint 4 introduced — present since Sprint 1):
`caliber_or_module`/`movement_type` are fields on both `ParsedWatch` and
`Watch`, correctly extracted by every brand's parser (confirmed: Citizen's
parser test already asserted `w.caliber_or_module == "H800"` at the
`ParsedWatch` level), but `_resolve_or_create_watch`'s `Watch(...)`
constructor in `app/services/pipeline.py` never read them from the `extra`
dict — and the `extra` dict itself never included them either. Silently
dropped for every brand, forever, until this sprint's field-completeness
check on the real production DB showed 0/311 Citizen watches had a
recorded movement despite the parser extracting one correctly. Fixed both
gaps; regression test added
(`test_citizen_product_baseline_observation_creates_no_event` now also
asserts `watches[0].caliber_or_module == "H800"`). Only affects newly
created watches going forward — the 311 Citizen/225 Seiko rows already in
the production DB from this session's live runs keep `caliber_or_module =
NULL` until a future observation backfills them (existing-watch backfill
only covers `model_name`/`collection` today, a pre-existing and unchanged
design choice, not expanded this sprint).

**Tests:** 77 → **85 passed**. New: Citizen search-hit pagination
(page-following, cross-collection dedup, safety-cap enforcement, the
baseline/no-duplicate-event pipeline path through the new parser), Seiko
multi-page pagination (follows pages until empty, stops on first empty
page, dedup across repeated pages), and the `caliber_or_module` regression.
`ruff check .`: all checks passed. `alembic current`: unchanged.

**Real production-DB state after this session's live runs:**
```
watches:                Casio 22, Citizen 311, Seiko 225
Citizen field completeness (of 311): case_material 297, collection 310,
  water_resistance_m 247, caliber_or_module 0 at capture time (bug found
  live, fixed for future observations — see above)
Citizen SourceObservations: 600 (price 600/600, availability 0/600 — all
  from the broad search-hit path this session; the depth path with real
  availability exists but wasn't the one scheduled by default)
Seiko SourceObservations: 450 (price 450/450, availability 450/450)
Events: 11 NEW_REFERENCE (all from citizen_news; product lanes correctly
  produced 0 events on both their baseline and repeat runs — real evidence,
  not fixture-only)
```
Repeat-run proof, real data, not synthetic: citizen_products run 68 → 300
new watches; run 69 (immediately after, live data unchanged) → 0 new
watches, 0 events. seiko_products run 70 → 225 new watches; run 71 → 0 new
watches, 0 events.

**Casio soak:** unaffected throughout. Run 67 fired via Task Scheduler
mid-session (`SUCCESS`), interleaved with six experimental runs, with zero
interaction — real evidence for the isolation claim, not just a design
argument.

**Discord:** still inert — no webhook found in this environment, none
invented, per instruction. `notify=True` is wired for both product lanes
identically to the news lanes (Sprint 2 pattern), so it activates the
moment a webhook is configured; no further code change needed.

**Next action / top 5 priorities:** (1) decide on Citizen's availability
gap — either accept UNKNOWN availability from the broad search path
long-term, or add a periodic smaller deep pass using the existing
per-product-page path for a curated subset (e.g. only watches with a
recent price/reference change) to recover availability without 500+ extra
requests every cycle; (2) if real Seiko availability enrichment
(movement/case material) is wanted later, the JSON-LD path is confirmed
viable — budget the extra per-product request cost explicitly; (3)
consider a backfill pass for the 311/225 watches already missing
`caliber_or_module` from before this session's fix; (4) let the four
scheduled lanes run unattended for a few real days and review event/alert
quality at real volume; (5) seikowatches.com's `/v3/api/` remains
unresolved — still not the priority while seikousa.com continues to work.

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
