# Watch Clank

> **Phase 0: UNVERIFIED_PRODUCTION — promotion frozen.** The unauthenticated
> dashboard supports loopback binding only; LAN, NAS, Tailscale, and public
> exposure are unsupported until an authenticated profile exists.

> Status: Experimental / under construction

Editorial intelligence system for discovering new analog-watch releases.

**Stage 1** focuses exclusively on the official Casio Japan source and proves the ingestion foundation.

## Mission

Casio (and later Citizen) announce a large volume of models and color variants. Watch Clank prioritizes the question:

> Is this discovery worth editorial attention?

Stage 1 does **not** implement full editorial scoring. It establishes reliable collection, snapshot storage, deterministic parsing, reference normalization, identity resolution, observations, and a dashboard.

## Technology Stack

- Python 3.12+
- FastAPI
- SQLAlchemy 2.x (typed)
- Alembic
- SQLite (WAL mode)
- Pydantic v2
- httpx
- selectolax
- Jinja2
- pytest + Ruff
- structlog

## Quick Start

### Prerequisites

- Python 3.12 or newer
- `pip`

### Setup (Unix-like)

```bash
cd watch-clank
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
mkdir -p data/snapshots
```

### Setup (Windows PowerShell)

```powershell
cd watch-clank
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
New-Item -ItemType Directory -Force -Path data\snapshots
```

### Database

```bash
alembic upgrade head
```

### Run offline pipeline against fixtures

```bash
python -m scripts.run_pipeline --fixture-mode
```

### Start dashboard

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765

### Tests

```bash
pytest -m "not live"
ruff check .
```

### Live smoke (optional, requires network access to Casio)

```bash
pytest -m live
python -m scripts.run_pipeline --live --max-items 5
```

## Architecture

```
Collector (network only)
    ↓
Fetched response
    ↓
Atomic raw-snapshot storage (content-addressed, deduplicated)
    ↓
Replayable synchronous parser
    ↓
Reference normalization
    ↓
Watch identity resolution
    ↓
Source observation
    ↓
Pipeline ledger
    ↓
Dashboard
```

Collectors and parsers **never** write to the database. Persistence is handled by the pipeline service under item-level transactions.

## Casio Japan Source

- Official domain: `https://www.casio.com/jp/watches/` (and brand sub-paths)
- Discovery prefers static HTML product links matching `/product.<REF>.html`
- Site applies Akamai bot protection; some environments receive 403
- Parser is fully offline and works from saved snapshots
- Source trust score: 100

## Known Limitations (Stage 1)

- Only Casio Japan
- No story scoring / editorial prioritization yet
- No multi-source identity merging beyond conservative canonical reference
- Live collection may be blocked by upstream bot protection
- Family grouping is provisional

## License

Internal / research use.

## Unattended scheduling (Windows)

```powershell
# Validate PowerShell syntax first
.\scripts\validate_powershell.ps1

# One-time setup after venv + migrations (also triggers a verification run)
.\scripts\install_windows_task.ps1

# Manual one-shot (same path as the scheduled task)
.\scripts\run_scheduled.ps1
$LASTEXITCODE

# Inspect logs
Get-Content .\data\logs\scheduled-wrapper.log -Tail 30
Get-Content .\data\logs\scheduled-python.log -Tail 50

# Status (task + DB + logs)
.\scripts\status_windows_task.ps1

# Seven-day stew report
python -m scripts.stew_report --days 7

# Remove task only (data retained)
.\scripts\remove_windows_task.ps1
```

Task name: `WatchClank-CasioJapan`  
Default interval: 90 minutes (`SCHEDULE_INTERVAL_MINUTES`).  
Logon model: **Interactive** (runs while the installing user is logged on; no stored credentials).  
SQLite path is forced absolute via `DATABASE_URL` in the wrapper so Task Scheduler and the dashboard share the same DB.

### Wrapper exit codes
| Code | Meaning |
|------|---------|
| 0 | SUCCESS, PARTIAL, ZERO_ITEMS, BLOCKED, SKIPPED_OVERLAP |
| 1 | FAILED |
| 2 | Setup / fatal exception |
| 3 | Migration / DB failure (installer) |

A persistent Casio 403 records `BLOCKED` and returns exit 0 so the schedule continues.

### Run statuses

| Status | Meaning |
|--------|---------|
| RUNNING | In progress |
| SUCCESS | Useful items, no hard failures |
| PARTIAL | Some items succeeded, some failed |
| FAILED | Could not complete meaningfully |
| ZERO_ITEMS | Source OK but nothing discovered |
| BLOCKED | Persistent 403 / bot protection |
| SKIPPED_OVERLAP | Another healthy run was active |

Application-level file + DB lock prevents concurrent runs. Stale RUNNING records older than `STALE_RUN_THRESHOLD_MINUTES` are recovered as FAILED.
