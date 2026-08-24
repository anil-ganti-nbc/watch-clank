# WATCH EXPANSION FAILURE CORPUS
Source: Diagnostic Clank `state/diagnostic.db` (integrity ok, extracted from
owner-supplied diagnostic-clank.zip) + raw evidence
`historical-l-register-v0.md` (sha256 f4bed499…886f6, ingestion-verified).
Method: past fleet failure → architectural law → expansion regression test.
Epistemic status preserved verbatim from the corpus; nothing upgraded.

## Watch Clank lessons (L-WATCH-001…012) → expansion conformance

| Corpus ID | Lesson (verbatim) | Status in corpus | Expansion implementation |
|---|---|---|---|
| L-WATCH-001 / DB-012 | "FIRST_SEEN_BY_CLANK ≠ NEW_TO_MARKET." | CONFIRMED FIXED / CANONICAL REGRESSION CLASS | Untouched novelty inversion; new collectors inherit auto-baseline (`_auto_baseline_for_first_run`); FIRST_SEEN default asserted in test_field_test_repairs |
| L-WATCH-002 | "Successful fetch/parser execution does not prove identity extraction succeeded. Identity evidence can exist outside obvious title/H1 fields." | CONFIRMED FIXED / REGRESSION REQUIRED | Tissot identity = URL-slug SKU (stronger than titles); parser-honesty tests pin field_confidence and warnings |
| L-WATCH-003 (Gear Patrol/Waterbury) | "Official pages can be authoritative and still be too late for journalism." | OPEN/PARTIALLY MITIGATED | Source matrix ranks newsroom surfaces as future lanes; sitemap lastmod gives earlier delta signal than product pages |
| L-WATCH-004 (Seiko HCC005J/006J) | "Market novelty and newsroom novelty are different axes." | CONFIRMED / PARTLY ARCHITECTURAL | QC memory (2026-08-24 repair) attaches prior review context to later events without blacklisting |
| L-WATCH-005 (Citizen Series 8 hands-on) | "Document type must be understood before event semantics are inferred." | PARTIALLY FIXED | Expansion collectors emit observation-class facts only; no document-type inference exists to get wrong |
| L-WATCH-006 (Citizen regional miss) | "'Citizen collector healthy' is meaningless without region/surface context." | OPEN | Every collector declares REGION; health rows keyed by collector_id+region; regional-gap map queued (Phase F) |
| L-WATCH-007 (CasioBlog March-in-August) | "DISCOVERY TIME ≠ EVENT TIME." | CONFIRMED FIXED | Future-timestamp rejection + 72h window unchanged; sitemap family carries lastmod as provenance only |
| L-WATCH-008 (ZERO_ITEMS healthy) | "Health/zero semantics belong to source type." | CONFIRMED FIXED | Sitemap family emits uniform component_status via http_util; ZERO_ITEMS never reads HEALTHY (health.py yield states) |
| L-WATCH-009 (launcher/timer fan-out) | "Execution provenance is part of data provenance." | CONFIRMED | Scheduler tasks remain disabled during programme; no scheduler changes made |
| L-WATCH-010 (cross-container PID locks) | "Process identity is contextual. Distributed/container locking requires appropriate primitives." | CONFIRMED architecture lesson | No new locking introduced; family uses existing RunLockService only |
| L-WATCH-011 (out-of-stock haul) | "Correct discovery ≠ useful newsroom lead." | OPEN (QC substrate now live) | Availability stays None when absent — no fake AVAILABLE inflation; QC dispositions recorded per event |
| L-WATCH-012 (old stories escaping freshness) | "Specialist sources increase recall but also increase archive leakage risk." | PARTIALLY_FIXED | Sitemap family has no freshness claims at all: lastmod is provenance, never a publish date |

## Cross-fleet lessons applied

- **L-FLEET-001** ("unhealthy observation must not advance authoritative
  state"): failed fetches never create observations — pipeline returns early;
  family BLOCKED/FAILED statuses tested (`test_blocked_status_when_fetch_denied`,
  `test_failed_status_on_network_error`).
- **L-FLEET-003** (baseline flood): Tissot's ~648-SKU first baseline will be
  silent by design (Law 1); burst annotation (`_annotate_new_reference_burst`)
  counts FIRST_SEEN floods automatically.
- **L-OEM-004** ("ANNOUNCED → AVAILABLE can itself be the story"): availability
  transition capability remains a declared gap for the sitemap family (fields
  honestly None). Recorded as a known limitation, not silently accepted as done.
- **DB-006** (watch shared-writer contention): all new code paths go through
  existing process_fetch_result transaction boundaries; no second writer added.

## DiagnosticBench golden-case linkage

New tests reference these cases by ID in docstrings so future agents can trace:
DB-002/L-WATCH-001 → `test_regional_presence_is_observation_not_new_watch`;
DB-008/L-WATCH-007 → future-timestamp rejection suite;
DB-012 → Tissot collector module docstring records the SKU-chronology caveat.

## Evidence-integrity note

The NAS "L database" referenced in the programme brief was positively
identified as this **Historical L Register** corpus inside Diagnostic Clank
(ingestion record `report_ingestions` row: filename historical-l-register-v0.md,
sha256 f4bed4990f52de7cc2362e11169e8d8196d87fa3f6b82bccab6e0eaeb1a886f6,
raw_storage_path `/app/data/evidence/reports/f4bed499…`). It is a document
corpus, not a SQLite watch-history database; it was consulted read-only and
nothing was written back to Diagnostic Clank.
