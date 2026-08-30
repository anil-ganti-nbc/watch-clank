"""FastAPI application entrypoint for Watch Clank dashboard and API."""

import ipaddress
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel
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
    SnapshotFetch,
    SourceObservation,
    SpecialistLead,
    Watch,
)
from app.models.review import DISPOSITIONS
from app.models.specialist_lead import LEAD_TYPES
from app.models.specialist_lead_review import LEAD_DISPOSITIONS
from app.services import qc as qc_service
from app.services.editorial import VALID_EVENT_TYPES

setup_logging()
logger = get_logger(__name__)
settings = get_settings()

# QC filter-dropdown choices -- sourced from the same enums the rest of the
# app already treats as canonical (app.services.editorial, app.models.review)
# rather than re-typed here, so a future event type/disposition can't
# silently drift out of sync with the filter UI.
_EVENT_TYPE_CHOICES = sorted(VALID_EVENT_TYPES)
_DISPOSITIONS_ORDERED = sorted(DISPOSITIONS)
_LEAD_TYPE_CHOICES = sorted(LEAD_TYPES)
_LEAD_DISPOSITIONS_ORDERED = sorted(LEAD_DISPOSITIONS)

_RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
TEMPLATES_DIR = _RESOURCE_ROOT / "app" / "templates"
STATIC_DIR = _RESOURCE_ROOT / "app" / "static"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)
templates = Jinja2Templates(env=_jinja_env)


def _loopback(value: str | None) -> bool:
    if not value:
        return False
    value = value.strip().strip("[]")
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value.lower() == "localhost"


def _instance_context() -> dict:
    """Instance label + notification authority, computed fresh per request
    (never cached at import time) so a .env change takes effect on reload
    without restarting the process. Available in every template via
    base.html's header -- see Phase 1 of the web catch-up sprint."""
    from app.local_operator import mutation_authority
    from app.services.discord_notify import DiscordNotifier

    current_settings = get_settings()
    notifier = DiscordNotifier(current_settings)
    label = (current_settings.watch_clank_instance or "").strip()
    return {
        "instance_label": label or "UNLABELED",
        "instance_configured": bool(label),
        "notification_authority": notifier.notification_authority(),
        "mutation_authority": mutation_authority(app),
        "field_test": os.getenv("WATCH_CLANK_FIELD_TEST") == "1",
        "version": app.version,
        "channel": os.getenv("WATCH_CLANK_RELEASE_CHANNEL", "production"),
        "revision": os.getenv("WATCH_CLANK_BUILD_REVISION", "local development build"),
        "state_root": os.getenv("WATCH_CLANK_STATE_ROOT", "default server paths"),
    }


_jinja_env.globals["instance"] = _instance_context


_IST_ZONE = "Asia/Kolkata"


def _humantime(value) -> str:
    """Render any timestamp as '12 Aug 2026, 07:12 UTC (12:42 IST)' -- never
    a bare ISO string. Always labeled with the zone abbreviation (Phase 7: a
    display timezone must be labeled, never implicit). The IST value in
    brackets is a fixed second reading alongside whatever display_timezone
    renders as the primary time -- not a config option, since the ask was
    simply "keep the primary display, also show IST". No DST adjustment is
    needed for either side: UTC has none by definition, and India abolished
    DST decades ago, so IST is a flat UTC+5:30 year-round. Suppressed when
    the primary display timezone already IS Asia/Kolkata, to avoid a
    redundant "IST (12:42 IST)". Falls back to raw str() on anything that
    isn't a real timestamp rather than raising inside a template, since a
    render failure here must never break the whole page.
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
        rendered = local.strftime("%d %b %Y, %H:%M ") + tzlabel
        if tz_name != _IST_ZONE:
            ist = dt.astimezone(ZoneInfo(_IST_ZONE))
            rendered += ist.strftime(" (%H:%M IST)")
        return rendered
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


@app.middleware("http")
async def phase0_dashboard_containment(request: Request, call_next):
    """Deny remote reads and every unauthenticated mutation.

    This remains effective even if someone bypasses the supported launcher and
    tells Uvicorn to listen on a wildcard address.
    """
    network_authorizer = getattr(request.app.state, "phase0_network_authorizer", None)
    client_host = request.client.host if request.client else None
    host_header = request.headers.get("host", "").rsplit(":", 1)[0]
    network_ok = (
        bool(network_authorizer(client_host, host_header))
        if network_authorizer is not None
        else _loopback(client_host) and _loopback(host_header)
    )
    if not network_ok:
        return HTMLResponse("Dashboard access is restricted to loopback.", status_code=403)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        mutation_authorizer = getattr(request.app.state, "phase0_mutation_authorizer", None)
        if mutation_authorizer is None or not mutation_authorizer(request):
            return HTMLResponse(
                "Dashboard mutations are disabled; no authenticated profile exists.",
                status_code=403,
            )
    return await call_next(request)


@app.middleware("http")
async def field_test_mutation_boundary(request: Request, call_next):
    """Allow only explicit local collection writes -- and, since the human
    QC sprint, local editorial review writes -- in field-test mode. A QC
    review never triggers a collector run, never calls out to Discord, and
    never leaves this machine, so it belongs in the same "local-only, no
    delivery" category as /operations/run/*, not the blocked category.
    RUN ALL SAFE COLLECTORS is the same category too -- every job it starts
    still goes through _local_collection's field-test branch (real
    subprocesses, but writing only to this Mac's isolated DB, Discord env
    vars already stripped by launcher.py) -- it just does that for every
    registered collector sequentially instead of one at a time."""
    field_test_collection = request.method == "POST" and (
        request.url.path.startswith("/operations/run/") or request.url.path == "/operations/run-all-safe"
    )
    field_test_review = request.method == "POST" and (
        request.url.path.startswith("/api/qc/review/") or request.url.path.startswith("/api/qc/lead-review/")
    )
    if (
        os.getenv("WATCH_CLANK_FIELD_TEST") == "1"
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and not field_test_collection
        and not field_test_review
    ):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"detail": "Watch Clank FIELD TEST permits local collection only."},
            status_code=403,
        )
    response = await call_next(request)
    if os.getenv("WATCH_CLANK_FIELD_TEST") == "1":
        response.headers["X-Watch-Clank-Mode"] = "FIELD TEST / LOCAL COLLECTION / DELIVERY DISABLED"
    return response

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
    from app.services.collector_registry import all_controls
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
            "collector_controls": all_controls(),
            "field_test": os.getenv("WATCH_CLANK_FIELD_TEST") == "1",
        },
    )


@app.get("/intelligence", response_class=HTMLResponse)
def recent_intelligence(
    request: Request,
    show: str = "current",
    manufacturer: str | None = None,
    event_type: str | None = None,
    region: str | None = None,
    run_id: int | None = None,
    include_deprioritized: bool = False,
    lead_manufacturer: str | None = None,
    lead_type: str | None = None,
    lead_region: str | None = None,
    lead_run_id: int | None = None,
    db: Session = Depends(get_db),
):
    """The most important page: "did Watch Clank find anything worth
    writing?" Two genuinely different evidence classes, kept visually and
    structurally separate per the sprint brief:

    - OFFICIAL EVENTS (Event model) -- only ever created for a real
      transition after a healthy baseline; every non-availability event is
      always current by construction (app.services.editorial's own
      reasoning -- Events don't carry an independent publication timestamp
      the way a blog article does), availability events are filtered
      through the same editorial-eligibility read-side check the rest of
      the app already uses. Since the 2026-08-18 human-QC sprint, this
      section IS the QC active queue: it only ever shows editorially
      eligible Events with no EventReview yet (app.services.qc), true
      cursor-paginated beyond the old fixed top-40/200 cap that caused the
      Citizen flood autopsy's "additional entries beyond the viewport"
      problem, and a reviewed Event disappears from this view the moment
      it is triaged (see /api/qc/queue, /api/qc/review/{event_id}).
    - EARLY-WARNING / SPECIALIST LEADS (SpecialistLead model) -- filtered to
      editorial_freshness == FRESH by default. This is the exact protection
      Sprint 8 exists for (ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md):
      discovery novelty is not editorial freshness, and historical/baseline
      evidence must never masquerade as breaking news. ?show=historical
      reveals STALE_PUBLICATION/BASELINE/UNKNOWN_TIMESTAMP/MANUAL_UNDATED
      leads explicitly -- never mixed into the default view, and (matching
      "preserve current historical behaviour") is NOT QC-integrated: the
      QC queue below only ever replaces the *current* (FRESH) list.
      Since the 2026-08-19 QC + classifier hardening pass, the *current*
      view is a second QC active queue exactly like Official Events above
      -- unreviewed only, cursor-paginated, reviewed-count badges, own
      filter bar (`lead_manufacturer`/`lead_type`/`lead_region`/
      `lead_run_id` -- deliberately separate query params from the Events
      filter bar above them, since "event_type" and "lead_type" are
      different vocabularies and one dropdown controlling both would
      silently break whichever table wasn't the one the operator meant).
      See app.services.qc's SpecialistLead functions,
      /api/qc/lead-queue, /api/qc/lead-review/{lead_id}.

    The LISTING column (2026-08-17 production-reset sprint) reuses the
    Watch's own existing `observations` relationship as its source of
    truth for a manufacturer product-page link -- SourceObservation rows
    are, by construction, only ever written by official/manufacturer-
    facing collectors (product AND news lanes), never by specialist/
    editorial sources (those write to SpecialistLead.source_url instead,
    a structurally separate table/column already excluded from this
    query). No new URL storage, no manufacturer-specific URL builder --
    see intelligence.html for the "most recent observation" selection.
    """
    show_historical = show == "historical"

    qc_filters = qc_service.QueueFilters(
        manufacturer=manufacturer or None,
        event_type=event_type or None,
        region=region or None,
        run_id=run_id,
        include_deprioritized=include_deprioritized,
    )
    current_events = qc_service.fetch_queue_page(db, qc_filters, limit=qc_service.DEFAULT_PAGE_SIZE)
    unreviewed_total = qc_service.unreviewed_count(db, qc_filters)
    reviewed_today = qc_service.reviewed_today_count(db)
    # 2026-08-26 truthfulness: expose the individual/bulk split so hundreds of
    # emergency bulk rejections never again read as item-level editorial work.
    reviewed_today_split = qc_service.reviewed_today_breakdown(db)
    next_cursor = current_events[-1].id if len(current_events) == qc_service.DEFAULT_PAGE_SIZE else None

    manufacturer_choices = [
        m for (m,) in db.execute(select(Watch.manufacturer).distinct().order_by(Watch.manufacturer)).all()
    ]

    lead_qc_filters = qc_service.QueueFilters(
        manufacturer=lead_manufacturer or None,
        event_type=lead_type or None,
        region=lead_region or None,
        run_id=lead_run_id,
    )

    if show_historical:
        specialist_leads = db.scalars(
            select(SpecialistLead).order_by(desc(SpecialistLead.discovered_at)).limit(60)
        ).all()
        historical_suppressed_count = 0
        lead_unreviewed_total = lead_reviewed_today = 0
        lead_next_cursor = None
    else:
        specialist_leads = qc_service.fetch_lead_queue_page(db, lead_qc_filters, limit=qc_service.DEFAULT_PAGE_SIZE)
        historical_suppressed_count = db.scalar(
            select(func.count())
            .select_from(SpecialistLead)
            .where(SpecialistLead.editorial_freshness != "FRESH")
        ) or 0
        lead_unreviewed_total = qc_service.unreviewed_lead_count(db, lead_qc_filters)
        lead_reviewed_today = qc_service.reviewed_leads_today_count(db)
        lead_next_cursor = (
            specialist_leads[-1].id if len(specialist_leads) == qc_service.DEFAULT_PAGE_SIZE else None
        )

    lead_manufacturer_choices = [
        m for (m,) in db.execute(select(SpecialistLead.manufacturer).distinct().order_by(SpecialistLead.manufacturer)).all() if m
    ]

    return templates.TemplateResponse(
        request,
        "intelligence.html",
        {
            "active_nav": "intelligence",
            "current_events": current_events,
            "specialist_leads": specialist_leads,
            "show_historical": show_historical,
            "historical_suppressed_count": historical_suppressed_count,
            "unreviewed_total": unreviewed_total,
            "reviewed_today": reviewed_today,
            "reviewed_today_split": reviewed_today_split,
            "next_cursor": next_cursor,
            "qc_filters": {
                "manufacturer": manufacturer or "",
                "event_type": event_type or "",
                "region": region or "",
                "run_id": run_id or "",
            },
            "manufacturer_choices": manufacturer_choices,
            "event_type_choices": _EVENT_TYPE_CHOICES,
            "dispositions": _DISPOSITIONS_ORDERED,
            "lead_unreviewed_total": lead_unreviewed_total,
            "lead_reviewed_today": lead_reviewed_today,
            "lead_next_cursor": lead_next_cursor,
            "lead_qc_filters": {
                "manufacturer": lead_manufacturer or "",
                "lead_type": lead_type or "",
                "region": lead_region or "",
                "run_id": lead_run_id or "",
            },
            "lead_manufacturer_choices": lead_manufacturer_choices,
            "lead_type_choices": _LEAD_TYPE_CHOICES,
            "lead_dispositions": _LEAD_DISPOSITIONS_ORDERED,
        },
    )


def _event_delivery_view(e: Event) -> dict:
    """Delivery outcome for one Event row (STD-UI-COM-011): the pipeline
    records extra["delivery"] = {state, reason?, attempted_at?}; rows from
    before that field existed are reported via their legacy alerted flag."""
    extra = e.extra or {}
    d = extra.get("delivery") or {}
    attempted_human = _humantime(d["attempted_at"]) if d.get("attempted_at") else None
    if d:
        return {"state": d.get("state"), "reason": d.get("reason"), "attempted_human": attempted_human}
    return {"state": None, "reason": None, "attempted_human": None, "legacy_alerted": bool(extra.get("alerted"))}


def _event_to_qc_dict(e: Event) -> dict:
    w = e.watches[0].watch if e.watches else None
    latest_obs = None
    if w and w.observations:
        latest_obs = max(w.observations, key=lambda o: o.observed_at)
    return {
        "event_id": e.id,
        "created_at_human": _humantime(e.created_at),
        "created_at_relative": _relative_time(e.created_at),
        "manufacturer": w.manufacturer if w else None,
        "watch_id": w.id if w else None,
        "reference": w.reference_raw if w else None,
        "listing_url": latest_obs.source_url if latest_obs else None,
        "event_type": e.event_type,
        "region": (e.extra or {}).get("region"),
        "score": round(e.story_score) if e.story_score is not None else None,
        "delivery": _event_delivery_view(e),
    }


@app.get("/api/qc/queue")
def qc_queue_api(
    manufacturer: str | None = None,
    event_type: str | None = None,
    region: str | None = None,
    run_id: int | None = None,
    before_id: int | None = None,
    limit: int = qc_service.DEFAULT_PAGE_SIZE,
    include_deprioritized: bool = False,
    db: Session = Depends(get_db),
):
    """Next page of the QC active queue (unreviewed, editorially eligible
    Events), for the "Load more" button and for pulling in one replacement
    row after a review action -- see Phase 10 of
    ai/handoff/HUMAN_QC_FEEDBACK_CONTRACT.md ("entire-run/entire-queue
    access").

    include_deprioritized=true (2026-08-24 QC-memory repair) reveals events
    the pipeline flagged human_qc_deprioritized -- repeat weak events for
    references the operator already rejected. Annotation, not deletion."""
    filters = qc_service.QueueFilters(
        manufacturer=manufacturer or None,
        event_type=event_type or None,
        region=region or None,
        run_id=run_id,
        include_deprioritized=include_deprioritized,
    )
    limit = max(1, min(limit, 100))
    events = qc_service.fetch_queue_page(db, filters, before_id=before_id, limit=limit)
    return {
        "items": [_event_to_qc_dict(e) for e in events],
        "next_cursor": events[-1].id if len(events) == limit else None,
        "unreviewed_count": qc_service.unreviewed_count(db, filters),
        "reviewed_today_count": qc_service.reviewed_today_count(db),
    }


class ReviewSubmission(BaseModel):
    disposition: str
    reason: str | None = None
    # 2026-08-26 review provenance: how was this verdict applied? "individual"
    # (default, one-at-a-time editorial decision) or "bulk" (operator mass
    # triage under queue pressure). Orthogonal audit metadata recorded in
    # EventReview.review_metadata — the human disposition vocabulary is
    # unchanged. Reviewed-today analytics can separate deliberate item-level
    # review from emergency bulk rejection via this field.
    mode: str | None = None  # "individual" | "bulk"


@app.post("/api/qc/review/{event_id}")
def qc_submit_review(event_id: int, request: Request, payload: ReviewSubmission, db: Session = Depends(get_db)):
    """Persist (or correct) one human editorial verdict. Never deletes the
    Event, its Watch, or any observation/provenance -- see
    app.services.qc.submit_review and
    ai/handoff/HUMAN_QC_FEEDBACK_CONTRACT.md."""
    _require_loopback(request)
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    try:
        review = qc_service.submit_review(
            db, event=event, disposition=payload.disposition, reason=payload.reason,
            mode=payload.mode,
        )
    except qc_service.InvalidDispositionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    empty_filters = qc_service.QueueFilters()
    return {
        "ok": True,
        "event_id": event_id,
        "disposition": review.disposition,
        "unreviewed_count": qc_service.unreviewed_count(db, empty_filters),
        "reviewed_today_count": qc_service.reviewed_today_count(db),
    }


def _lead_to_qc_dict(lead: SpecialistLead) -> dict:
    published_or_discovered = lead.published_at or lead.discovered_at
    if lead.notified_at:
        delivery = {"state": "sent", "human": _humantime(lead.notified_at)}
    else:
        delivery = {"state": lead.delivery_state, "human": None}
    return {
        "lead_id": lead.id,
        "when_human": _humantime(published_or_discovered),
        "when_relative": _relative_time(published_or_discovered),
        # STD-UI-COM-010: the row states which semantic role the time plays
        # instead of leaving it to a dual-meaning column header.
        "when_role": "published" if lead.published_at else "discovered",
        "manufacturer": lead.manufacturer,
        "title": lead.title,
        "source_url": lead.source_url,
        "lead_type": lead.lead_type,
        "source_id": lead.source_id,
        "source_authority_tier": lead.source_authority_tier,
        "confidence": round(lead.confidence) if lead.confidence is not None else None,
        "delivery": delivery,
    }


class LeadReviewSubmission(BaseModel):
    disposition: str
    reason: str | None = None


@app.get("/api/qc/lead-queue")
def qc_lead_queue_api(
    manufacturer: str | None = None,
    lead_type: str | None = None,
    region: str | None = None,
    run_id: int | None = None,
    before_id: int | None = None,
    limit: int = qc_service.DEFAULT_PAGE_SIZE,
    db: Session = Depends(get_db),
):
    """Next page of the Specialist-lead QC active queue -- same "Load
    more"/"reveal next after review" contract as /api/qc/queue, scoped to
    SpecialistLead instead of Event."""
    filters = qc_service.QueueFilters(
        manufacturer=manufacturer or None,
        event_type=lead_type or None,
        region=region or None,
        run_id=run_id,
    )
    limit = max(1, min(limit, 100))
    leads = qc_service.fetch_lead_queue_page(db, filters, before_id=before_id, limit=limit)
    return {
        "items": [_lead_to_qc_dict(lead) for lead in leads],
        "next_cursor": leads[-1].id if len(leads) == limit else None,
        "unreviewed_count": qc_service.unreviewed_lead_count(db, filters),
        "reviewed_today_count": qc_service.reviewed_leads_today_count(db),
    }


@app.post("/api/qc/lead-review/{lead_id}")
def qc_submit_lead_review(
    lead_id: int, request: Request, payload: LeadReviewSubmission, db: Session = Depends(get_db)
):
    """Persist (or correct) one human editorial verdict on a Specialist
    lead. Never deletes the SpecialistLead or any of its fields -- see
    app.services.qc.submit_lead_review."""
    _require_loopback(request)
    lead = db.get(SpecialistLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="specialist lead not found")
    try:
        review = qc_service.submit_lead_review(db, lead=lead, disposition=payload.disposition, reason=payload.reason)
    except qc_service.InvalidDispositionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    empty_filters = qc_service.QueueFilters()
    return {
        "ok": True,
        "lead_id": lead_id,
        "disposition": review.disposition,
        "unreviewed_count": qc_service.unreviewed_lead_count(db, empty_filters),
        "reviewed_today_count": qc_service.reviewed_leads_today_count(db),
    }


def _review_to_dict(r) -> dict:
    e = r.event
    return {
        "review_id": r.id,
        "event_id": r.event_id,
        "manufacturer": r.manufacturer,
        "reference": r.reference_canonical,
        "watch_id": r.watch_id,
        "disposition": r.disposition,
        "reviewed_at_human": _humantime(r.reviewed_at),
        "reviewed_at_relative": _relative_time(r.reviewed_at),
        "event_type": r.event_type,
        "region": r.region,
        "availability_status": r.availability_status,
        "listing_url": r.provenance_url,
        "discovered_at_human": _humantime(e.created_at) if e else None,
        "corrected": bool((r.review_metadata or {}).get("correction_history")),
        "is_corrected": r.is_corrected,
    }


@app.get("/qc/history", response_class=HTMLResponse)
def qc_history(
    request: Request,
    manufacturer: str | None = None,
    event_type: str | None = None,
    region: str | None = None,
    disposition: str | None = None,
    run_id: int | None = None,
    include_corrected: bool = False,
    lead_manufacturer: str | None = None,
    lead_type: str | None = None,
    lead_region: str | None = None,
    lead_disposition: str | None = None,
    lead_run_id: int | None = None,
    lead_include_corrected: bool = False,
    db: Session = Depends(get_db),
):
    """Archived/reviewed QC entries -- Phase 11 of
    ai/handoff/HUMAN_QC_FEEDBACK_CONTRACT.md. Corrections happen from here
    by re-submitting a different disposition through the same
    /api/qc/review/{event_id} endpoint the active queue uses. Since the
    2026-08-19 QC + classifier hardening pass, also shows Specialist-lead
    reviews in their own section with their own filter bar/correction
    control, reusing the same page/pattern rather than a parallel one.

    2026-08-19 correction UX addendum: the default view for BOTH sections
    excludes already-corrected reviews (QC History is a workable
    correction queue, not an immutable dump) -- `include_corrected`/
    `lead_include_corrected` reveal them again. Nothing is ever deleted;
    see qc_service.fetch_history_page's docstring."""
    filters = qc_service.QueueFilters(
        manufacturer=manufacturer or None,
        event_type=event_type or None,
        region=region or None,
        run_id=run_id,
    )
    reviews = qc_service.fetch_history_page(
        db, filters, disposition=disposition or None, include_corrected=include_corrected
    )
    next_cursor = reviews[-1].id if len(reviews) == qc_service.DEFAULT_PAGE_SIZE else None
    manufacturer_choices = [
        m for (m,) in db.execute(select(Watch.manufacturer).distinct().order_by(Watch.manufacturer)).all()
    ]

    lead_filters = qc_service.QueueFilters(
        manufacturer=lead_manufacturer or None,
        event_type=lead_type or None,
        region=lead_region or None,
        run_id=lead_run_id,
    )
    lead_reviews = qc_service.fetch_lead_history_page(
        db, lead_filters, disposition=lead_disposition or None, include_corrected=lead_include_corrected
    )
    lead_next_cursor = lead_reviews[-1].id if len(lead_reviews) == qc_service.DEFAULT_PAGE_SIZE else None
    lead_manufacturer_choices = [
        m for (m,) in db.execute(select(SpecialistLead.manufacturer).distinct().order_by(SpecialistLead.manufacturer)).all() if m
    ]

    return templates.TemplateResponse(
        request,
        "qc_history.html",
        {
            "active_nav": "qc_history",
            "reviews": reviews,
            "next_cursor": next_cursor,
            "include_corrected": include_corrected,
            "qc_filters": {
                "manufacturer": manufacturer or "",
                "event_type": event_type or "",
                "region": region or "",
                "disposition": disposition or "",
                "run_id": run_id or "",
            },
            "manufacturer_choices": manufacturer_choices,
            "event_type_choices": _EVENT_TYPE_CHOICES,
            "dispositions": _DISPOSITIONS_ORDERED,
            "lead_reviews": lead_reviews,
            "lead_next_cursor": lead_next_cursor,
            "lead_include_corrected": lead_include_corrected,
            "lead_qc_filters": {
                "manufacturer": lead_manufacturer or "",
                "lead_type": lead_type or "",
                "region": lead_region or "",
                "disposition": lead_disposition or "",
                "run_id": lead_run_id or "",
            },
            "lead_manufacturer_choices": lead_manufacturer_choices,
            "lead_type_choices": _LEAD_TYPE_CHOICES,
            "lead_dispositions": _LEAD_DISPOSITIONS_ORDERED,
        },
    )


@app.get("/api/qc/history")
def qc_history_api(
    manufacturer: str | None = None,
    event_type: str | None = None,
    region: str | None = None,
    disposition: str | None = None,
    run_id: int | None = None,
    include_corrected: bool = False,
    before_id: int | None = None,
    limit: int = qc_service.DEFAULT_PAGE_SIZE,
    db: Session = Depends(get_db),
):
    filters = qc_service.QueueFilters(
        manufacturer=manufacturer or None,
        event_type=event_type or None,
        region=region or None,
        run_id=run_id,
    )
    limit = max(1, min(limit, 100))
    reviews = qc_service.fetch_history_page(
        db, filters, disposition=disposition or None, include_corrected=include_corrected,
        before_id=before_id, limit=limit,
    )
    return {
        "items": [_review_to_dict(r) for r in reviews],
        "next_cursor": reviews[-1].id if len(reviews) == limit else None,
    }


def _lead_review_to_dict(r) -> dict:
    lead = r.lead
    return {
        "review_id": r.id,
        "lead_id": r.specialist_lead_id,
        "manufacturer": r.manufacturer,
        "title": r.lead_title,
        "source_url": r.source_url,
        "source_id": r.source_id,
        "disposition": r.disposition,
        "reviewed_at_human": _humantime(r.reviewed_at),
        "reviewed_at_relative": _relative_time(r.reviewed_at),
        "lead_type": r.lead_type,
        "region": r.region,
        "discovered_at_human": _humantime(lead.discovered_at) if lead else None,
        "corrected": bool((r.review_metadata or {}).get("correction_history")),
        "is_corrected": r.is_corrected,
    }


@app.get("/api/qc/lead-history")
def qc_lead_history_api(
    manufacturer: str | None = None,
    lead_type: str | None = None,
    region: str | None = None,
    disposition: str | None = None,
    run_id: int | None = None,
    include_corrected: bool = False,
    before_id: int | None = None,
    limit: int = qc_service.DEFAULT_PAGE_SIZE,
    db: Session = Depends(get_db),
):
    filters = qc_service.QueueFilters(
        manufacturer=manufacturer or None,
        event_type=lead_type or None,
        region=region or None,
        run_id=run_id,
    )
    limit = max(1, min(limit, 100))
    reviews = qc_service.fetch_lead_history_page(
        db, filters, disposition=disposition or None, include_corrected=include_corrected,
        before_id=before_id, limit=limit,
    )
    return {
        "items": [_lead_review_to_dict(r) for r in reviews],
        "next_cursor": reviews[-1].id if len(reviews) == limit else None,
    }


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
    from app.services.collector_registry import SAFE_COLLECTOR_IDS, all_controls
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
            "safe_collector_ids": SAFE_COLLECTOR_IDS,
            "is_locked": is_locked,
            "is_loopback": (request.client.host if request.client else None) in ("127.0.0.1", "::1", "localhost"),
            "ran": ran,
            "ran_result": result,
            "ran_all": ran_all,
            "ok_count": ok_count,
            "total": total,
            "field_test": os.getenv("WATCH_CLANK_FIELD_TEST") == "1",
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
    frozen = bool(getattr(sys, "frozen", False))
    cmd = [sys.executable, "--collector-worker", "--live", *cli_args] if frozen else [sys.executable, "-m", "scripts.run_pipeline", "--live", *cli_args]
    try:
        result = subprocess.run(
            cmd,
            cwd=None if frozen else str(settings.project_root),
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


class _LocalCollectionController:
    """Runs field-test collection in a background thread -- either one
    collector (RUN NOW / COLLECT) or every registered collector
    sequentially (RUN ALL SAFE COLLECTORS). Both modes share one lock/state
    so they can never run concurrently with each other -- mirroring, at the
    web layer, the mutual exclusion RunLockService already guarantees at
    the pipeline layer, and giving the UI a single source of truth for
    "is local collection busy right now" regardless of which button
    started it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict = {"status": "IDLE", "running": False}

    def snapshot(self) -> dict:
        with self._lock:
            state = dict(self._state)
        started = state.pop("started_monotonic", None)
        if state.get("running") and started:
            state["elapsed_seconds"] = round(time.monotonic() - started, 1)
        return state

    def start(self, collector_id: str, cli_args: tuple[str, ...]) -> bool:
        with self._lock:
            if self._state.get("running"):
                return False
            self._state = {
                "status": "RUNNING", "running": True, "mode": "single", "collector_id": collector_id,
                "started_at": datetime.now(UTC).isoformat(), "started_monotonic": time.monotonic(),
            }
        threading.Thread(target=self._run, args=(collector_id, cli_args), name="watch-local-collection", daemon=True).start()
        return True

    def start_all(self, jobs: list[tuple[str, tuple[str, ...]]]) -> bool:
        with self._lock:
            if self._state.get("running"):
                return False
            self._state = {
                "status": "RUNNING", "running": True, "mode": "batch",
                "total": len(jobs), "completed": 0, "current_collector_id": None, "results": {},
                "started_at": datetime.now(UTC).isoformat(), "started_monotonic": time.monotonic(),
            }
        threading.Thread(target=self._run_all, args=(jobs,), name="watch-local-collection-all", daemon=True).start()
        return True

    def _latest_run_detail(self, collector_id: str, ok: bool, result: dict) -> dict:
        from app.db.session import session_scope

        with session_scope() as session:
            latest = session.scalar(
                select(CollectorRun).where(CollectorRun.collector_id == collector_id).order_by(desc(CollectorRun.started_at)).limit(1)
            )
            detail = None if latest is None else {
                "run_id": latest.id, "status": latest.status, "discovered": latest.discovered_count,
                "parsed": latest.parsed_count, "new_watches": latest.new_watch_count,
                "observations": latest.observation_count,
                "events": len((latest.summary_metadata or {}).get("events", [])) if isinstance((latest.summary_metadata or {}).get("events"), list) else (latest.summary_metadata or {}).get("events_created"),
                "warnings": latest.warning_count, "failures": latest.failure_count,
                "summary": latest.summary_metadata or {},
            }
        return {
            "status": detail["status"] if detail else ("FAILED" if not ok else "COMPLETED"),
            "result": detail,
            "error": result["stderr_tail"][-1000:] if not ok else None,
            "output": result["stdout_tail"][-1000:],
        }

    def _run(self, collector_id: str, cli_args: tuple[str, ...]) -> None:
        result = _run_collector_subprocess(cli_args, timeout_seconds=900)
        detail = self._latest_run_detail(collector_id, result["ok"], result)
        with self._lock:
            self._state.update({"running": False, **detail})

    def _run_all(self, jobs: list[tuple[str, tuple[str, ...]]]) -> None:
        for collector_id, cli_args in jobs:
            with self._lock:
                self._state["current_collector_id"] = collector_id
            result = _run_collector_subprocess(cli_args, timeout_seconds=900)
            detail = self._latest_run_detail(collector_id, result["ok"], result)
            logger.info("field_test_run_all_step", collector_id=collector_id, ok=result["ok"])
            with self._lock:
                self._state["results"][collector_id] = detail
                self._state["completed"] += 1
        with self._lock:
            ok_count = sum(1 for r in self._state["results"].values() if r["status"] in ("SUCCESS", "PARTIAL", "ZERO_ITEMS", "COMPLETED"))
            self._state.update({
                "running": False, "status": "COMPLETED", "current_collector_id": None, "ok_count": ok_count,
            })


_local_collection = _LocalCollectionController()


@app.get("/operations/status")
def local_collection_status():
    return _local_collection.snapshot()


@app.post("/operations/run/{collector_id}")
def run_collector_now(collector_id: str, request: Request):
    """RUN NOW for a single collector. Localhost-only (Phase 11)."""
    from app.services.collector_registry import get_control

    _require_loopback(request)
    try:
        control = get_control(collector_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown collector_id: {collector_id!r}")

    if os.getenv("WATCH_CLANK_FIELD_TEST") == "1":
        from fastapi.responses import JSONResponse

        if not _local_collection.start(collector_id, control.cli_args):
            return JSONResponse(_local_collection.snapshot(), status_code=409)
        return JSONResponse(_local_collection.snapshot(), status_code=202)

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

    SAFE_COLLECTOR_IDS excludes EXPERIMENTAL-maturity collectors
    (WATCH_SOAK_CONTRACT.md) by default (2026-08-27 operator closeout
    decision) -- they remain individually runnable via RUN NOW/COLLECT
    above, just not swept into this bulk action. See the comment above
    SAFE_COLLECTOR_IDS's definition for the config-driven re-enable.

    Field-test mode runs the exact same sequential job list through
    _local_collection's background-thread batch mode instead of blocking
    the request for the full duration -- matching the async, polled UX
    the single-collector RUN NOW/COLLECT path already uses there, and
    writing to this Mac's isolated local database only, same as every
    other field-test collection action.
    """
    from app.services.collector_registry import SAFE_COLLECTOR_IDS, get_control

    _require_loopback(request)
    jobs = [(collector_id, get_control(collector_id).cli_args) for collector_id in SAFE_COLLECTOR_IDS]

    if os.getenv("WATCH_CLANK_FIELD_TEST") == "1":
        from fastapi.responses import JSONResponse

        if not _local_collection.start_all(jobs):
            return JSONResponse(_local_collection.snapshot(), status_code=409)
        return JSONResponse(_local_collection.snapshot(), status_code=202)

    results = {}
    for collector_id, cli_args in jobs:
        results[collector_id] = _run_collector_subprocess(cli_args)
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


# Canonical pipeline-stage ordering, shared by /correlation/{id} and
# /runs/{run_id} so both surfaces present ledger stages in the same order
# (STD-UI-COM-009: stage detail must be reachable and coherent).
_PIPELINE_STAGE_ORDER = [
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


def _ledger_sort_key(e):
    try:
        idx = _PIPELINE_STAGE_ORDER.index(e.stage)
    except ValueError:
        idx = 99
    return (idx, e.created_at)


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


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: int, request: Request, db: Session = Depends(get_db)):
    """STD-UI-COM-009 remediation (2026-08-31): the primary run surface
    links each run here, so the pipeline-stage detail the backend already
    tracks in PipelineLedger is visibly indicated and directly reachable —
    per-run ledger entries, stage-ordered, grouped per correlation id with
    links into the existing /correlation/{id} timeline view."""
    run = db.get(CollectorRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    entries = db.scalars(
        select(PipelineLedger)
        .where(PipelineLedger.run_id == run_id)
        .order_by(PipelineLedger.created_at)
    ).all()
    sorted_entries = sorted(entries, key=_ledger_sort_key)

    by_correlation: dict[str, list] = {}
    for entry in sorted_entries:
        by_correlation.setdefault(entry.correlation_id, []).append(entry)

    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "active_nav": "runs",
            "run": run,
            "correlations": by_correlation,
            "stage_entry_count": len(sorted_entries),
        },
    )


@app.get("/evidence", response_class=HTMLResponse)
def evidence_index(request: Request, db: Session = Depends(get_db)):
    fetches = db.scalars(
        select(SnapshotFetch)
        .options(joinedload(SnapshotFetch.blob))
        .order_by(desc(SnapshotFetch.fetched_at))
        .limit(100)
    ).all()
    return templates.TemplateResponse(request, "evidence.html", {"active_nav": "evidence", "fetches": fetches})


@app.get("/evidence/{fetch_id}", response_class=HTMLResponse)
def evidence_detail(fetch_id: int, request: Request, db: Session = Depends(get_db)):
    from app.services.snapshot_storage import SnapshotStorageService

    fetch = db.scalar(
        select(SnapshotFetch).options(joinedload(SnapshotFetch.blob)).where(SnapshotFetch.id == fetch_id)
    )
    if not fetch:
        raise HTTPException(status_code=404, detail="Evidence not found")
    try:
        payload = SnapshotStorageService().read(fetch.blob.filepath, fetch.blob.compression_type)
        preview = payload.decode(fetch.blob.encoding or "utf-8", errors="replace")[:20000]
    except Exception as error:
        preview = f"Evidence preview unavailable: {error}"
    return templates.TemplateResponse(
        request, "evidence_detail.html", {"active_nav": "evidence", "fetch": fetch, "preview": preview}
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

    sorted_entries = sorted(entries, key=_ledger_sort_key)

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


@app.get("/api/runtime")
def runtime_provenance():
    """Non-secret build/state provenance for operator verification."""
    from app.local_operator import mutation_authority

    field_test = os.getenv("WATCH_CLANK_FIELD_TEST") == "1"
    authority = mutation_authority(app)
    return {
        "service": "Watch Clank",
        "version": app.version,
        "mode": "FIELD TEST" if field_test else "default",
        "channel": os.getenv("WATCH_CLANK_RELEASE_CHANNEL", "production"),
        "revision": os.getenv("WATCH_CLANK_BUILD_REVISION", "local development build"),
        "state_root": os.getenv("WATCH_CLANK_STATE_ROOT", "default server paths"),
        # Phase 0 truth-in-provenance: report what this instance can
        # actually do, derived from the installed authority rather than a
        # hardcoded False that went stale when containment landed.
        "mutation_authority": authority,
        "read_only": authority == "NONE",
        "local_collection": field_test,
        "external_delivery": False if field_test else "configured by server settings",
    }
