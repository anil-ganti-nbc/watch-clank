"""FastAPI application entrypoint for Watch Clank dashboard and API."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import get_db
from app.models import (
    CollectorRun,
    PipelineLedger,
    ReleaseLead,
    SourceComponentState,
    SourceObservation,
    Watch,
)

setup_logging()
logger = get_logger(__name__)
settings = get_settings()

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)
templates = Jinja2Templates(env=_jinja_env)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema must come from Alembic migrations only. Do not create_all here.
    logger.info("app_started", database=settings.resolved_database_url)
    yield
    logger.info("app_stopped")


app = FastAPI(
    title="Watch Clank",
    description="Editorial intelligence for analog watch releases – Stage 1",
    version="0.1.0",
    lifespan=lifespan,
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    from datetime import UTC, datetime, timedelta

    from app.core.config import get_settings
    from app.services.run_lock import RunLockService

    settings = get_settings()
    latest_run = db.scalar(select(CollectorRun).order_by(desc(CollectorRun.started_at)).limit(1))
    total_watches = db.scalar(select(func.count()).select_from(Watch)) or 0
    total_observations = db.scalar(select(func.count()).select_from(SourceObservation)) or 0
    total_leads = db.scalar(select(func.count()).select_from(ReleaseLead)) or 0
    recent_leads = db.scalars(select(ReleaseLead).order_by(desc(ReleaseLead.created_at)).limit(15)).all()
    source_states = db.scalars(select(SourceComponentState).order_by(SourceComponentState.source_id)).all()


    recent_watches = db.scalars(
        select(Watch).order_by(desc(Watch.created_at)).limit(10)
    ).all()
    recent_observations = db.scalars(
        select(SourceObservation)
        .options(joinedload(SourceObservation.watch))
        .order_by(desc(SourceObservation.observed_at))
        .limit(10)
    ).all()
    recent_ledger = db.scalars(
        select(PipelineLedger).order_by(desc(PipelineLedger.created_at)).limit(20)
    ).all()
    failed_runs = db.scalars(
        select(CollectorRun)
        .where(CollectorRun.status.in_(["FAILED", "PARTIAL", "ZERO_ITEMS", "BLOCKED"]))
        .order_by(desc(CollectorRun.started_at))
        .limit(5)
    ).all()

    # Operations section (DB-derived)
    cutoff_24h = datetime.now(UTC) - timedelta(hours=24)
    runs_24h = db.scalars(
        select(CollectorRun).where(CollectorRun.started_at >= cutoff_24h)
    ).all()
    counts_24h = {}
    for r in runs_24h:
        counts_24h[r.status] = counts_24h.get(r.status, 0) + 1

    latest_success = db.scalar(
        select(CollectorRun)
        .where(CollectorRun.status == "SUCCESS")
        .order_by(desc(CollectorRun.started_at))
        .limit(1)
    )
    latest_blocked = db.scalar(
        select(CollectorRun)
        .where(CollectorRun.status == "BLOCKED")
        .order_by(desc(CollectorRun.started_at))
        .limit(1)
    )
    latest_failure = db.scalar(
        select(CollectorRun)
        .where(CollectorRun.status.in_(["FAILED", "BLOCKED"]))
        .order_by(desc(CollectorRun.started_at))
        .limit(1)
    )
    active_run = db.scalar(
        select(CollectorRun)
        .where(CollectorRun.status == "RUNNING")
        .order_by(desc(CollectorRun.started_at))
        .limit(1)
    )
    lock_svc = RunLockService(db, settings)
    is_locked = lock_svc.is_locked()

    # Next expected run (derived from last start + interval)
    next_expected = None
    if latest_run and latest_run.started_at:
        started = latest_run.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        next_expected = started + timedelta(minutes=settings.schedule_interval_minutes)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "latest_run": latest_run,
            "total_watches": total_watches,
            "total_observations": total_observations,
            "total_leads": total_leads,
            "recent_leads": recent_leads,
            "source_states": source_states,
            "recent_watches": recent_watches,
            "recent_observations": recent_observations,
            "recent_ledger": recent_ledger,
            "failed_runs": failed_runs,
            "ops": {
                "schedule_interval_minutes": settings.schedule_interval_minutes,
                "latest_run": latest_run,
                "latest_success": latest_success,
                "latest_blocked": latest_blocked,
                "latest_failure": latest_failure,
                "active_run": active_run,
                "is_locked": is_locked,
                "next_expected_run": next_expected,
                "next_expected_label": "derived from last run + interval (not Task Scheduler)",
                "counts_24h": counts_24h,
                "runs_24h_total": len(runs_24h),
            },
        },
    )


@app.get("/runs", response_class=HTMLResponse)
def run_history(request: Request, db: Session = Depends(get_db)):
    runs = db.scalars(select(CollectorRun).order_by(desc(CollectorRun.started_at)).limit(50)).all()
    return templates.TemplateResponse(
        request,
        "runs.html",
        {"runs": runs},
    )


@app.get("/watches/{watch_id}", response_class=HTMLResponse)
def watch_detail(watch_id: int, request: Request, db: Session = Depends(get_db)):
    watch = db.get(Watch, watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")

    observations = db.scalars(
        select(SourceObservation)
        .where(SourceObservation.watch_id == watch_id)
        .order_by(desc(SourceObservation.observed_at))
    ).all()

    ledger = db.scalars(
        select(PipelineLedger)
        .where(PipelineLedger.entity_id == str(watch_id))
        .order_by(PipelineLedger.created_at)
        .limit(50)
    ).all()

    return templates.TemplateResponse(
        request,
        "watch_detail.html",
        {
            "watch": watch,
            "observations": observations,
            "ledger": ledger,
        },
    )


@app.get("/correlation/{correlation_id}", response_class=HTMLResponse)
def correlation_timeline(correlation_id: str, request: Request, db: Session = Depends(get_db)):
    entries = db.scalars(
        select(PipelineLedger)
        .where(PipelineLedger.correlation_id == correlation_id)
        .order_by(PipelineLedger.created_at)
    ).all()
    if not entries:
        raise HTTPException(status_code=404, detail="Correlation not found")

    stage_order = [
        "discovery",
        "fetch",
        "snapshot_storage",
        "parsing",
        "normalization",
        "identity_resolution",
        "observation_creation",
        "family_candidate_assignment",
        "pipeline",
    ]

    def sort_key(e: PipelineLedger):
        try:
            idx = stage_order.index(e.stage)
        except ValueError:
            idx = 99
        return (idx, e.created_at)

    sorted_entries = sorted(entries, key=sort_key)

    return templates.TemplateResponse(
        request,
        "correlation.html",
        {
            "correlation_id": correlation_id,
            "entries": sorted_entries,
        },
    )


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Real dependency checks. Returns non-200 when unhealthy."""

    from fastapi.responses import JSONResponse
    from sqlalchemy import text
    issues = []
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        issues.append(f"database: {exc}")
    # Required tables
    for table in ("watches", "snapshot_blobs", "snapshot_fetches", "source_observations", "collector_runs"):
        try:
            db.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        except Exception:
            issues.append(f"missing_table:{table}")
    # Snapshot root
    try:
        root = settings.resolved_snapshot_root
        if not root.exists() or not root.is_dir():
            issues.append("snapshot_root_inaccessible")
    except Exception as exc:
        issues.append(f"snapshot_root: {exc}")

    body = {
        "status": "ok" if not issues else "unhealthy",
        "service": "watch-clank",
        "stage": 1,
        "issues": issues,
    }
    code = 200 if not issues else 503
    return JSONResponse(content=body, status_code=code)
