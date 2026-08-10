# Watch Clank — Development Handoff
**Last updated:** 2026-08-09
**Current phase:** Stage 1 complete / live soak test (soak clock restarted 2026-08-09, see below)
**Next developer:** Claude
**Primary environment:** Windows 10/11, local-first
**Repository path:** `C:\Users\anil\Desktop\Watch clank\watch-clank`
**Dashboard:** `http://127.0.0.1:8765`

(See prior handoff sections 0–64 as provided at session start for full architecture,
mission, and philosophy notes — omitted here for brevity, unchanged.)

---

# Checkpoint log

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
