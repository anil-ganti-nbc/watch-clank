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
    Event,
    PipelineLedger,
    ReleaseLead,
    SourceObservation,
    SpecialistLead,
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


def _instance_context() -> dict:
    """Instance label + notification authority, computed fresh per request
    (never cached at import time) so a .env change takes effect on reload
    without restarting the process. Available in every template via
    base.html's header -- see Phase 1 of the web catch-up sprint."""
    from app.services.discord_notify import DiscordNotifier

    current_settings = get_settings()
    notifier = DiscordNotifier(current_settings)
    label = (current_settings.watch_clank_instance or "").strip()
    return {
        "instance_label": label or "UNLABELED",
        "instance_configured": bool(label),
        "notification_authority": notifier.notification_authority(),
    }


_jinja_env.globals["instance"] = _instance_context


def _humantime(value) -> str:
    """Render any timestamp as '12 Aug 2026, 07:12 UTC' -- never a bare ISO
    string. Always labeled with the zone abbreviation (Phase 7: a display
    timezone must be labeled, never implicit). Falls back to raw str() on
    anything that isn't a real timestamp rather than raising inside a
    template, since a render failure here must never break the whole page.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.core.time import ensure_utc

    if value is None:
        return "—"
    try:
        dt = value
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
        dt = ensure_utc(dt)
        tz_name = get_settings().display_timezone
        local = dt.astimezone(ZoneInfo(tz_name))
        # %Z gives an abbreviation for named zones (UTC, IST has none from
        # zoneinfo directly for Asia/Kolkata -- fall back to a GMT offset
        # label in that case so it's still explicit, never silently omitted).
        tzlabel = local.strftime("%Z") or local.strftime("GMT%z")
        return local.strftime("%d %b %Y, %H:%M ") + tzlabel
    except Exception:
        return str(value)


def _relative_time(value) -> str:
    """'3 min ago' / '2 days ago' -- for at-a-glance freshness, always
    paired with humantime in the UI, never shown alone (Phase 7 wants exact
    labeled timestamps, not just relative ones)."""
    from datetime import datetime

    from app.core.time import ensure_utc, utc_now

    if value is None:
        return ""
    try:
        dt = value
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
        dt = ensure_utc(dt)
        delta = utc_now() - dt
        seconds = delta.total_seconds()
        if seconds < 0:
            return "in the future"
        if seconds < 90:
            return "just now"
        minutes = seconds / 60
        if minutes < 60:
            return f"{int(minutes)} min ago"
        hours = minutes / 60
        if hours < 24:
            return f"{int(hours)} hr ago"
        days = hours / 24
        return f"{int(days)} day{'s' if int(days) != 1 else ''} ago"
    except Exception:
        return ""


_jinja_env.filters["humantime"] = _humantime
_jinja_env.filters["relative"] = _relative_time


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
def overview(request: Request, db: Session = Depends(get_db)):
    """Overview: "did Watch Clank find anything, and are collectors healthy"
    at a glance. Built on the same app.services.health.get_health_snapshot()
    the Windows Control Centre and scripts/status.py use, so this page can
    never disagree with either about what "healthy" means (Phase 3/7 of the
    web catch-up sprint -- the old dashboard queried SourceComponentState
    directly instead, a separate, divergent source of truth)."""
    from app.db.session import get_engine
    from app.services.health import get_health_snapshot

    settings = get_settings()
    snapshot = get_health_snapshot(db, settings, engine=get_engine())

    total_watches = db.scalar(select(func.count()).select_from(Watch)) or 0
    total_observations = db.scalar(select(func.count()).select_from(SourceObservation)) or 0
    total_release_leads = db.scalar(select(func.count()).select_from(ReleaseLead)) or 0
    total_specialist_leads = db.scalar(select(func.count()).select_from(SpecialistLead)) or 0
    total_events = db.scalar(select(func.count()).select_from(Event)) or 0

    # Manufacturer breakdown -- derived from whatever's actually in the DB,
    # never a hardcoded brand list (Phase 3: Orient Star must not appear
    # until it's a real active collector with real rows).
    manufacturer_rows = db.execute(
        select(Watch.manufacturer, func.count()).group_by(Watch.manufacturer).order_by(desc(func.count()))
    ).all()

    # "Genuinely fresh intelligence" -- FRESH specialist leads only, the
    # exact concept Sprint 8 exists to protect (never STALE_PUBLICATION/
    # BASELINE masquerading as current). Official Events don't carry
    # editorial_freshness (see freshness.py's docstring: they don't need
    # it), so every Event counts as current by construction.
    fresh_specialist_count = db.scalar(
        select(func.count()).select_from(SpecialistLead).where(SpecialistLead.editorial_freshness == "FRESH")
    ) or 0
    fresh_intelligence_count = fresh_specialist_count + total_events

    healthy_sources = [s for s in snapshot.sources if s.state == "HEALTHY"]
    degraded_sources = [s for s in snapshot.sources if s.state in ("WARNING", "FAILED")]
    never_run_sources = [s for s in snapshot.sources if s.state == "NEVER_RUN"]

    latest_success = db.scalar(
        select(CollectorRun)
        .where(CollectorRun.status == "SUCCESS")
        .order_by(desc(CollectorRun.started_at))
        .limit(1)
    )
    latest_failure = db.scalar(
        select(CollectorRun)
        .where(CollectorRun.status.in_(["FAILED", "BLOCKED"]))
        .order_by(desc(CollectorRun.started_at))
        .limit(1)
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active_nav": "overview",
            "snapshot": snapshot,
            "total_watches": total_watches,
            "total_observations": total_observations,
            "total_release_leads": total_release_leads,
            "total_specialist_leads": total_specialist_leads,
            "total_events": total_events,
            "fresh_intelligence_count": fresh_intelligence_count,
            "manufacturer_rows": manufacturer_rows,
            "healthy_sources": healthy_sources,
            "degraded_sources": degraded_sources,
            "never_run_sources": never_run_sources,
            "latest_success": latest_success,
            "latest_failure": latest_failure,
        },
    )


@app.get("/intelligence", response_class=HTMLResponse)
def recent_intelligence(request: Request, show: str = "current", db: Session = Depends(get_db)):
    """The most important page: "did Watch Clank find anything worth
    writing?" Two genuinely different evidence classes, kept visually and
    structurally separate per the sprint brief:

    - OFFICIAL EVENTS (Event model) -- only ever created for a real
      transition after a healthy baseline; every non-availability event is
      always current by construction (app.services.editorial's own
      reasoning -- Events don't carry an independent publication timestamp
      the way a blog article does), availability events are filtered
      through the same editorial-eligibility read-side check the rest of
      the app already uses.
    - EARLY-WARNING / SPECIALIST LEADS (SpecialistLead model) -- filtered to
      editorial_freshness == FRESH by default. This is the exact protection
      Sprint 8 exists for (ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md):
      discovery novelty is not editorial freshness, and historical/baseline
      evidence must never masquerade as breaking news. ?show=historical
      reveals STALE_PUBLICATION/BASELINE/UNKNOWN_TIMESTAMP/MANUAL_UNDATED
      leads explicitly -- never mixed into the default view.
    """
    from app.models import EventWatch
    from app.services.editorial import event_row_is_editorially_eligible

    settings = get_settings()
    show_historical = show == "historical"

    all_events = db.scalars(
        select(Event)
        .options(joinedload(Event.watches).joinedload(EventWatch.watch))
        .order_by(desc(Event.created_at))
        .limit(200)
    ).unique().all()
    current_events = [
        e
        for e in all_events
        if event_row_is_editorially_eligible(
            event_type=e.event_type,
            story_score=e.story_score,
            extra=e.extra,
            availability_min_score=settings.availability_editorial_min_score,
        )
    ][:40]

    if show_historical:
        specialist_leads = db.scalars(
            select(SpecialistLead).order_by(desc(SpecialistLead.discovered_at)).limit(60)
        ).all()
        historical_suppressed_count = 0
    else:
        specialist_leads = db.scalars(
            select(SpecialistLead)
            .where(SpecialistLead.editorial_freshness == "FRESH")
            .order_by(desc(func.coalesce(SpecialistLead.published_at, SpecialistLead.discovered_at)))
            .limit(60)
        ).all()
        historical_suppressed_count = db.scalar(
            select(func.count())
            .select_from(SpecialistLead)
            .where(SpecialistLead.editorial_freshness != "FRESH")
        ) or 0

    return templates.TemplateResponse(
        request,
        "intelligence.html",
        {
            "active_nav": "intelligence",
            "current_events": current_events,
            "specialist_leads": specialist_leads,
            "show_historical": show_historical,
            "historical_suppressed_count": historical_suppressed_count,
        },
    )


def _require_loopback(request: Request) -> None:
    """Phase 11 (web security): this app has no authentication at all.
    RUN NOW and RUN ALL SAFE COLLECTORS execute real subprocesses -- a
    read-only dashboard and one that can trigger execution are materially
    different security surfaces. mac/dashboard already binds 127.0.0.1 only,
    but this is defense-in-depth: refuse the mutation regardless of how the
    app happens to be bound, so a future APP_HOST change can't silently turn
    this into an unauthenticated remote-execution endpoint.
    """
    client_host = request.client.host if request.client else None
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=403,
            detail="Collector execution is restricted to localhost. This app has no authentication.",
        )


@app.get("/operations", response_class=HTMLResponse)
def operations(
    request: Request,
    ran: str | None = None,
    result: str | None = None,
    ran_all: str | None = None,
    ok_count: int | None = None,
    total: int | None = None,
    db: Session = Depends(get_db),
):
    from app.db.session import get_engine
    from app.services.collector_registry import all_controls
    from app.services.health import get_health_snapshot
    from app.services.run_lock import RunLockService

    settings = get_settings()
    snapshot = get_health_snapshot(db, settings, engine=get_engine())
    health_by_id = {s.collector_id: s for s in snapshot.sources}

    lock_svc = RunLockService(db, settings)
    is_locked = lock_svc.is_locked()

    rows = []
    for control in all_controls():
        rows.append({"control": control, "health": health_by_id.get(control.collector_id)})

    return templates.TemplateResponse(
        request,
        "operations.html",
        {
            "active_nav": "operations",
            "rows": rows,
            "is_locked": is_locked,
            "is_loopback": (request.client.host if request.client else None) in ("127.0.0.1", "::1", "localhost"),
            "ran": ran,
            "ran_result": result,
            "ran_all": ran_all,
            "ok_count": ok_count,
            "total": total,
        },
    )


def _run_collector_subprocess(cli_args: tuple[str, ...], timeout_seconds: int = 180) -> dict:
    """Shell out to the exact same scripts/run_pipeline.py entry point Task
    Scheduler/systemd use -- never duplicate collector/pipeline logic in the
    web layer. RunLockService (inside the pipeline) is what actually
    prevents overlap; this just reports what happened."""
    import subprocess
    import sys

    settings = get_settings()
    cmd = [sys.executable, "-m", "scripts.run_pipeline", "--live", *cli_args]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(settings.project_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout_tail": "", "stderr_tail": f"timed out after {timeout_seconds}s"}


@app.post("/operations/run/{collector_id}")
def run_collector_now(collector_id: str, request: Request):
    """RUN NOW for a single collector. Localhost-only (Phase 11)."""
    from app.services.collector_registry import get_control

    _require_loopback(request)
    try:
        control = get_control(collector_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown collector_id: {collector_id!r}")

    result = _run_collector_subprocess(control.cli_args)
    logger.info("web_run_now", collector_id=collector_id, ok=result["ok"], returncode=result["returncode"])
    from fastapi.responses import RedirectResponse

    status_flag = "ok" if result["ok"] else "fail"
    return RedirectResponse(url=f"/operations?ran={collector_id}&result={status_flag}", status_code=303)


@app.post("/operations/run-all-safe")
def run_all_safe_collectors(request: Request):
    """RUN ALL SAFE COLLECTORS -- every collector in
    app.services.collector_registry.SAFE_COLLECTOR_IDS, sequentially (not
    parallel: RunLockService is per-collector, but running 15 real network
    collectors concurrently from a single web request has no operational
    justification and makes failure attribution harder). Localhost-only.
    """
    from app.services.collector_registry import SAFE_COLLECTOR_IDS, get_control

    _require_loopback(request)
    results = {}
    for collector_id in SAFE_COLLECTOR_IDS:
        control = get_control(collector_id)
        results[collector_id] = _run_collector_subprocess(control.cli_args)
        logger.info(
            "web_run_all_safe_step",
            collector_id=collector_id,
            ok=results[collector_id]["ok"],
        )

    ok_count = sum(1 for r in results.values() if r["ok"])
    from fastapi.responses import RedirectResponse

    return RedirectResponse(
        url=f"/operations?ran_all=1&ok_count={ok_count}&total={len(results)}", status_code=303
    )


@app.get("/runs", response_class=HTMLResponse)
def run_history(
    request: Request,
    source: str | None = None,
    status: str | None = None,
    layer: str | None = None,
    db: Session = Depends(get_db),
):
    from app.services.collector_registry import all_controls

    controls = all_controls()
    controls_by_id = {c.collector_id: c for c in controls}

    query = select(CollectorRun)
    if source:
        query = query.where(CollectorRun.collector_id == source)
    if status:
        query = query.where(CollectorRun.status == status)
    if layer in ("OFFICIAL", "SPECIALIST"):
        ids = [c.collector_id for c in controls if c.layer == layer]
        query = query.where(CollectorRun.collector_id.in_(ids))

    runs = db.scalars(query.order_by(desc(CollectorRun.started_at)).limit(100)).all()
    all_statuses = ["RUNNING", "SUCCESS", "PARTIAL", "FAILED", "ZERO_ITEMS", "BLOCKED", "SKIPPED_OVERLAP"]

    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "active_nav": "runs",
            "runs": runs,
            "controls_by_id": controls_by_id,
            "all_controls": controls,
            "all_statuses": all_statuses,
            "filter_source": source,
            "filter_status": status,
            "filter_layer": layer,
        },
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


# Real Windows task names (from HANDOFF.md -- this mapping cannot be derived
# from anything else, it documents a genuinely separate system). Only ever
# used to attempt a live `schtasks /query` when actually running on Windows;
# every other platform (this Mac, Hetzner/Linux) never touches this and
# relies solely on the DERIVED view below. UNVERIFIED: written from
# documentation, never run against a real Windows Task Scheduler in this
# session -- see the sprint's final report.
_WINDOWS_TASK_NAMES = {
    "casio_multi": "WatchClank-CasioJapan",
    "citizen_news": "WatchClank-CitizenNews",
    "citizen_products": "WatchClank-CitizenProducts",
    "seiko_jp_news": "WatchClank-SeikoNews",
    "seiko_products": "WatchClank-SeikoProducts",
    "casioblog_rss": "WatchClank-Casioblog",
    "gcentral_rss": "WatchClank-GCentral",
    "plus9time_rss": "WatchClank-Plus9Time",
    "timex_news": "WatchClank-TimexNews",
    "timex_products": "WatchClank-TimexProducts",
}


def _query_windows_task(task_name: str) -> dict | None:
    """Best-effort real schtasks query. Returns None on any failure or on a
    non-Windows host -- callers must treat None as "fall back to derived,"
    never as an error to surface. UNVERIFIED against a real Windows host."""
    import platform
    import subprocess

    if platform.system() != "Windows":
        return None
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/fo", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        info: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                info[key.strip()] = value.strip()
        return {
            "status": info.get("Status") or info.get("Scheduled Task State"),
            "next_run": info.get("Next Run Time"),
            "last_run": info.get("Last Run Time"),
        }
    except Exception:
        return None


@app.get("/scheduler", response_class=HTMLResponse)
def scheduler(request: Request, db: Session = Depends(get_db)):
    """Phase 9: scheduler visibility appropriate to the actual host, never
    one platform's scheduler pretending to be another's. The DERIVED section
    (last run + expected cadence -> next expected run) is always shown and
    always honestly labeled. A live Windows Task Scheduler section is added
    only when actually running on Windows and the query succeeds -- Hetzner/
    Linux has no equivalent live section at all, by design, not omission."""
    import platform
    from datetime import timedelta

    from app.services.collector_registry import all_controls
    from app.services.health import EXPECTED_CADENCE_MINUTES

    is_windows = platform.system() == "Windows"
    rows = []
    for control in all_controls():
        last_run = db.scalar(
            select(CollectorRun)
            .where(CollectorRun.collector_id == control.collector_id)
            .order_by(desc(CollectorRun.started_at))
            .limit(1)
        )
        cadence = EXPECTED_CADENCE_MINUTES.get(control.collector_id)
        next_expected = None
        if last_run and last_run.started_at and cadence:
            from app.core.time import ensure_utc

            next_expected = ensure_utc(last_run.started_at) + timedelta(minutes=cadence)

        windows_task_name = _WINDOWS_TASK_NAMES.get(control.collector_id)
        windows_live = _query_windows_task(windows_task_name) if (is_windows and windows_task_name) else None

        rows.append(
            {
                "control": control,
                "cadence_minutes": cadence,
                "last_run": last_run,
                "next_expected": next_expected,
                "windows_task_name": windows_task_name,
                "windows_live": windows_live,
            }
        )

    return templates.TemplateResponse(
        request,
        "scheduler.html",
        {
            "active_nav": "scheduler",
            "rows": rows,
            "is_windows": is_windows,
            "scheduler_source": "Windows Task Scheduler (live)" if is_windows else "none on this host (Hetzner/Linux uses cron/systemd, not queried live here)",
        },
    )


@app.get("/diagnostics", response_class=HTMLResponse)
def diagnostics(request: Request, db: Session = Depends(get_db)):
    """Health/Diagnostics -- Phase 7 (human-readable timestamps, per-source
    state/heartbeat/lock/failure-reason) and Phase 10 (Discord configuration
    visibility -- safe booleans only, never a webhook URL) folded into one
    page, matching the Windows Control Centre's own tab grouping."""
    from app.db.session import get_engine
    from app.services.discord_notify import DiscordNotifier
    from app.services.health import get_health_snapshot
    from app.services.run_lock import RunLockService

    settings = get_settings()
    snapshot = get_health_snapshot(db, settings, engine=get_engine())
    notifier = DiscordNotifier(settings)

    # Recent failure reason where available -- the most recent FAILED/BLOCKED
    # run's own summary, not a fabricated explanation.
    failure_reasons = {}
    for s in snapshot.sources:
        if s.state in ("WARNING", "FAILED"):
            last_bad_run = db.scalar(
                select(CollectorRun)
                .where(CollectorRun.collector_id == s.collector_id)
                .where(CollectorRun.status.in_(["FAILED", "BLOCKED"]))
                .order_by(desc(CollectorRun.started_at))
                .limit(1)
            )
            if last_bad_run:
                meta = last_bad_run.summary_metadata or {}
                failure_reasons[s.collector_id] = (
                    meta.get("error") or meta.get("reason") or f"status={last_bad_run.status}, see run #{last_bad_run.id}"
                )

    lock_svc = RunLockService(db, settings)
    is_locked = lock_svc.is_locked()

    return templates.TemplateResponse(
        request,
        "diagnostics.html",
        {
            "active_nav": "diagnostics",
            "snapshot": snapshot,
            "failure_reasons": failure_reasons,
            "is_locked": is_locked,
            "display_timezone": settings.display_timezone,
            "discord": {
                "editorial_enabled": notifier.editorial_enabled,
                "editorial_configured": bool(settings.discord_editorial_webhook_url),
                "editorial_notifications_enabled_flag": settings.editorial_notifications_enabled,
                "health_configured": bool(settings.discord_health_webhook_url),
                "authority": notifier.notification_authority(),
            },
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
