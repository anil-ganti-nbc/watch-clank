# Watch Clank — Cloud Readiness Checklist (Tier D: architecture prep only)

**Date:** 2026-08-08
**Branch:** `cloud/watch-prep`
**Scope of this pass:** documentation and draft artifacts only. No deploy, no
Docker build, no changes to collector/parser/normalization/pipeline logic or
the database schema/migrations. See `CLOUD_READINESS_BLOCKERS.md` for the
reasons this stays prep-only right now.

This is a checklist of what an eventual real containerization/deploy would
need, and an honest read of how much of it Watch Clank already has "for
free" versus what's missing. It does not authorize starting that work.

## Already portable (no action needed)

- [x] **Config is fully environment-driven.** `app/core/config.py` uses
  Pydantic Settings reading from `.env`; every path (`database_url`,
  `snapshot_storage_root`, `log_dir`) has a sane relative default and is
  resolved against `project_root` at runtime via `pathlib`, not hard-coded
  absolute paths.
- [x] **No hard-coded Windows filesystem paths in source or config.** Fresh
  grep across the tree (excluding `.venv`) confirms this — see the Windows-
  assumption audit section below for exact evidence and the one caveat
  (a documentation-only path reference in `HANDOFF.md`).
- [x] **No Windows-only Python imports or dependencies.** `pyproject.toml`'s
  dependency list (fastapi, uvicorn, sqlalchemy, alembic, pydantic,
  pydantic-settings, httpx, selectolax, jinja2, structlog, orjson) is
  entirely cross-platform; no `pywin32`, `winreg`, `ctypes.windll`, etc.
  anywhere in `app/` or `scripts/`.
- [x] **Already a headless service, not a GUI/scheduler-process app.** Unlike
  Smartphone Clank's long-running in-process scheduler model, Watch Clank is
  already split conceptually into a FastAPI web app (`app/main.py`) and a
  standalone CLI (`scripts/run_pipeline.py`) with its own exit-code contract
  (`0`=nonfatal outcomes, `1`=FAILED, `2`=setup/fatal, `3`=migration
  failure, per README.md). That CLI already behaves like a one-shot batch
  job today (it's invoked once per Task Scheduler firing, not looped
  in-process) — it maps onto an `external scheduler -> one-shot container ->
  exit` model close to as-is, without restructuring.
- [x] **`/health` does real dependency checks, not a liveness stub.** See
  `health_identity_adapter.draft.md` for the full assessment — it already
  checks DB connectivity, required tables, and snapshot-root accessibility,
  and returns a genuine `503` with itemized `issues` on failure. This is
  ahead of where a lot of local-first tools start.
- [x] **State is clearly identified and already separated from code.**
  `data/watch_clank.db` (Alembic-versioned SQLite, WAL mode) and
  `data/snapshots/` (~70 content-addressed gzip blobs, sha256-sharded,
  currently ~2.6 MB) are the only persistent state, both already under a
  single `data/` directory — a clean unit to point one volume at.
- [x] **`.env.example` already covers container-relevant config.** See the
  dedicated section below — no gaps found requiring new environment
  variables, only interpretation of relative-vs-absolute paths inside a
  container.
- [x] **PowerShell scripts already resolve paths dynamically.**
  `scripts/*.ps1` use `$PSScriptRoot`/`Join-Path` rather than hard-coded
  drive letters — confirmed by this pass's grep (see below). These scripts
  are Windows/Task-Scheduler-specific by design (they wrap
  `install_windows_task.ps1` / `WatchClank-CasioJapan`) and are expected to
  stay Windows-only; they are not part of the container path and don't need
  to become cross-platform.

## Windows-assumption audit (fresh evidence, this pass)

Searched the full source tree (excluding `.venv`, `.git`, `.pytest_cache`,
`.ruff_cache`) for hard-coded `C:\` / `C:/` paths and Windows-only imports.

- **Hard-coded `C:\` paths:** exactly one match in the whole tree —
  `HANDOFF.md:6`, `` **Repository path:** `C:\Users\anil\Desktop\Watch clank\watch-clank` ``.
  This is a human-readable status line in a handoff document, not code, and
  is not read by the application at runtime. No source file, config file, or
  script contains a hard-coded drive-letter path. This **confirms** the
  prior audit's "no hard-coded Windows paths found" claim, with one minor
  correction: the claim should say "found in source/config," since the
  HANDOFF.md doc reference does technically exist as text in the repo.
- **Windows-only imports/APIs:** zero matches for `winreg`, `win32*`,
  `ctypes.windll`, `pywin32`, `winsound`, `msvcrt`, `os.startfile`, `ntpath`
  across `app/`, `scripts/`, `tests/`.
- **Platform-conditional code:** zero matches for `platform.system`,
  `os.name`, `sys.platform` in `app/` or `scripts/` — the codebase makes no
  runtime OS branching decisions at all (consistent with there being nothing
  Windows-specific to branch around).
- **PowerShell scripts (`scripts/*.ps1`):** these do reference paths, but
  exclusively via `Join-Path $ScriptDir ...` / `Join-Path $RepoRoot ...` and
  `$PSScriptRoot` — no literal drive letters. These scripts are intentionally
  Windows-only (Task Scheduler integration) and out of scope for
  containerization; flagged here only to confirm they don't hide a stray
  absolute path.
- **`app/core/config.py`:** all filesystem defaults (`snapshot_storage_root`,
  `log_dir`, `database_url`) are relative (`./data/...`) and resolved against
  `project_root = Path(__file__).resolve().parents[2]` at runtime — this
  pattern works identically under Linux/containers as it does on Windows,
  since it's pure `pathlib`.

**Conclusion:** the "no Windows-path/no Windows-import blockers" claim from
the prior read-only audit holds up under a fresh, targeted grep. This is one
of the cleaner Tier D services from a portability-of-source standpoint.

## `.env.example` review

Current contents (`​.env.example`, unchanged in this pass):

| Variable | Container-readiness note |
|---|---|
| `DATABASE_URL` | Relative (`sqlite:///./data/watch_clank.db`) resolved against `project_root`. In a container, an operator would override with an absolute in-volume path, e.g. `sqlite:////data/watch_clank.db` (see `Dockerfile.draft`). No new variable needed — this is a value change, not a schema change. |
| `SNAPSHOT_STORAGE_ROOT` | Same pattern — override to `/data/snapshots` in-container. |
| `LOG_DIR` | Same pattern — override to `/data/logs` in-container. |
| `COLLECTOR_USER_AGENT`, `COLLECTOR_TIMEOUT_SECONDS`, `COLLECTOR_MAX_RETRIES`, `COLLECTOR_BACKOFF_BASE`, `COLLECTOR_JITTER` | Behavioral, no path/secret implications. Portable as-is. |
| `LOG_LEVEL`, `LOG_FORMAT` | Portable as-is. |
| `APP_HOST`, `APP_PORT`, `DEBUG` | `APP_HOST=127.0.0.1` is correct for local dev; a containerized dashboard needs `0.0.0.0` *inside* the container (with the actual public/private exposure controlled at the Docker/compose port-binding layer, not by this variable) — see `docker-compose.draft.yml` for the loopback-only binding approach. |
| `SCHEDULE_INTERVAL_MINUTES`, `STALE_RUN_THRESHOLD_MINUTES`, `MAX_RUN_DURATION_SECONDS` | Behavioral. Note: in a one-shot-container-per-run model, `SCHEDULE_INTERVAL_MINUTES` becomes informational metadata read by the dashboard for "next expected run" display, not an actual driver of cadence — the external scheduler owns cadence. |

**Finding:** no secrets present in `.env.example` (confirms prior audit — all
entries are paths/tuning knobs, no API keys/tokens/passwords). No new
environment variables are required to run this app in a container; only
value overrides for the three path-bearing variables. No code changes
needed — `.env.example` is already clean.

**Persistent-path/secrets convention for a future container** (documented,
not implemented): mount one volume at `/data`, point `DATABASE_URL`,
`SNAPSHOT_STORAGE_ROOT`, and `LOG_DIR` at subpaths of it via environment
overrides (not `.env` baked into the image), and inject them the same way
any secrets would be injected if this app ever needs one (it doesn't today) —
via the orchestrator's env/secret mechanism, never committed to the image or
to `.env` in version control.

## Two-service split (recommended future direction, not decided now)

Sketched in `Dockerfile.draft` and `docker-compose.draft.yml`:

1. **`pipeline`** — one-shot `python -m scripts.run_pipeline --scheduled`,
   invoked by an external scheduler, no ports, `restart: "no"`.
2. **`dashboard`** — long-running `uvicorn app.main:app`, optional
   (`profiles: ["dashboard"]` in the draft compose file so it isn't started
   by default), bound to loopback only if ever actually run, read-only
   relative to pipeline state.

Both share one persistent volume covering `data/watch_clank.db` and
`data/snapshots/`. This is presented as the natural direction given the
existing code shape, not as an approved plan.

## Explicitly out of scope / not done in this pass

- No `docker build` was run. `Dockerfile.draft` and `docker-compose.draft.yml`
  are unbuilt, untested sketches.
- No changes to `app/collectors/`, `app/parsers/`, `app/normalization/`,
  `app/services/`, `alembic/`, or any test file.
- No changes to `app/main.py`'s `/health` route — see
  `health_identity_adapter.draft.md` for assessment-only notes.
- No interaction with the live soak test, its DB, its scheduled task, or its
  logs.
