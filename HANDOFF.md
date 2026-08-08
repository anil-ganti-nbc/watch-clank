# Watch Clank — Development Handoff
**Last updated:** 2026-08-08
**Current phase:** Stage 1 complete / live soak test
**Next developer:** Claude
**Primary environment:** Windows 10/11, local-first
**Repository path:** `C:\Users\anil\Desktop\Watch clank\watch-clank`
**Dashboard:** `http://127.0.0.1:8765`

(See prior handoff sections 0–64 as provided at session start for full architecture,
mission, and philosophy notes — omitted here for brevity, unchanged.)

---

# Checkpoint log

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
