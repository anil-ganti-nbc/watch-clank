"""One-command health/status snapshot.

Reused by two callers that must never diverge in what "healthy" means:
scripts/status.py (cloud/CLI, no GUI available there) and, per Sprint 6's
Windows Control Centre, the GUI's RUN HEALTH CHECK button. Deliberately
read-only -- never mutates state, never recovers stale runs itself (that
remains RunLockService.recover_stale_runs's job, called from the normal
pipeline path).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.time import ensure_utc
from app.db.schema_check import SchemaStatus, check_schema
from app.models import CollectorRun, Event, SourceObservation, SpecialistLead, Watch

# Every collector_id this project schedules today. Kept as a plain list
# (not derived from the DB) so a source with zero runs ever still shows as
# NEVER_RUN instead of silently not appearing.
KNOWN_COLLECTORS = [
    "casio_multi",
    "casio_uk_sitemap",
    "casio_europe_sitemap",
    "casio_jp_sitemap",
    "citizen_news",
    "citizen_products",
    # citizen_de_products retired 2026-08-17 (owner directive: proved too
    # noisy/problematic to keep relying on) -- see
    # ai/handoff/RETIREMENT_CITIZEN_DE.md. Deliberately removed from this
    # list, not just disabled by a flag: this list is the single source of
    # truth SAFE_COLLECTOR_IDS/all_controls()/render_units.py all derive
    # from, so removal here is what actually keeps it out of "RUN ALL SAFE
    # COLLECTORS", the dashboard, health checks, and any future systemd
    # unit regeneration -- it cannot silently reappear by being rebuilt
    # from a stale default. The collector/parser code and its existing
    # tests are left intact (historical correctness, not deleted) -- only
    # its production reachability is removed. Hetzner's already-deployed
    # citizen_de_products systemd timer is UNCHANGED by this commit (not
    # touched, per the standing Hetzner freeze) and will keep running
    # there until a future, separately-authorized redeploy.
    "seiko_jp_news",
    "seiko_products",
    "seiko_jp_products",
    "casioblog_rss",
    "gcentral_rss",
    "plus9time_rss",
    "monochrome_rss",
    "deployant_rss",
    "fratello_rss",
    "watchtime_rss",
    "great_gshock_world_atom",
    "gear_patrol_rss",
    "timex_products",
    "timex_news",
]

SUCCESS_STATUSES = {"SUCCESS", "PARTIAL", "ZERO_ITEMS"}

# Expected scheduled cadence per collector, minutes -- matches the Windows
# Task Scheduler intervals (install_windows_task.ps1,
# install_windows_experimental_tasks.ps1, install_windows_casioblog_task.ps1)
# and the systemd timer definitions. Used only for the heartbeat check below:
# a source with no successful run within 3x its expected cadence is flagged
# WARNING even if its most recent run technically succeeded a long time ago.
EXPECTED_CADENCE_MINUTES = {
    "casio_multi": 90,
    "casio_uk_sitemap": 720,
    "casio_europe_sitemap": 720,
    "casio_jp_sitemap": 360,  # fresh daily lastmod: JP is the earliest official surface, check twice as often
    "citizen_news": 90,
    "citizen_products": 360,
    "seiko_jp_news": 90,
    "seiko_products": 360,
    "seiko_jp_products": 360,
    "casioblog_rss": 45,
    "gcentral_rss": 45,
    "plus9time_rss": 360,
    "monochrome_rss": 45,
    "deployant_rss": 90,
    "fratello_rss": 45,
    "watchtime_rss": 90,
    "great_gshock_world_atom": 45,
    "gear_patrol_rss": 90,  # matches deployant's tier-3 cadence, not the 45min tier-2 sources
    "timex_news": 90,
    "timex_products": 360,
}


@dataclass
class SourceHealth:
    collector_id: str
    state: str  # HEALTHY | WARNING | FAILED | NEVER_RUN
    last_success_at: str | None
    last_failure_at: str | None
    last_item_count: int | None
    heartbeat_overdue: bool = False


@dataclass
class HealthSnapshot:
    generated_at: str
    schema: SchemaStatus
    db_integrity_ok: bool
    db_integrity_detail: str
    total_watches: int
    latest_observation_at: str | None
    latest_event_at: str | None
    latest_specialist_lead_at: str | None
    sources: list[SourceHealth] = field(default_factory=list)
    active_locks: list[str] = field(default_factory=list)
    stale_running_count: int = 0


def _source_health(session: Session, collector_id: str) -> SourceHealth:
    runs = (
        session.query(CollectorRun)
        .filter(CollectorRun.collector_id == collector_id)
        .order_by(CollectorRun.started_at.desc())
        .limit(10)
        .all()
    )
    if not runs:
        return SourceHealth(collector_id, "NEVER_RUN", None, None, None)

    last_success = next((r for r in runs if r.status in SUCCESS_STATUSES), None)
    last_failure = next((r for r in runs if r.status == "FAILED"), None)
    most_recent = runs[0]

    if most_recent.status in SUCCESS_STATUSES:
        state = "HEALTHY"
        # A source can be "successfully" empty forever: ZERO_ITEMS counts as
        # a success status, so a silently broken feed/parser (real case:
        # monochrome_rss, 20 consecutive ZERO_ITEMS runs in the field-test
        # DB while the same source worked elsewhere) reads as HEALTHY.
        # Repeated empty runs from a source that historically produces
        # content must become operator-visible. The streak threshold is an
        # explicit, documented setting (zero_item_warning_streak, default 3)
        # rather than a buried magic number; it is deliberately expressed in
        # RUNS not hours so it means the same thing for a 45-minute RSS lane
        # and a 12-hour sitemap lane. Found by the 2026-08-21 hostile
        # architecture audit; re-verified still present after Phase 0
        # remediation (bf87c7d does not touch this path).
        zero_item_streak = 0
        for r in runs:
            if r.status == "ZERO_ITEMS" and (r.discovered_count or 0) == 0:
                zero_item_streak += 1
            else:
                break
        if zero_item_streak >= get_settings().zero_item_warning_streak:
            state = "WARNING"
    elif most_recent.status == "SKIPPED_OVERLAP":
        state = "WARNING"
    else:
        # Most recent run failed -- WARNING if an older run in this window
        # still succeeded (transient), FAILED if every recent run failed.
        state = "WARNING" if last_success else "FAILED"

    heartbeat_overdue = False
    cadence = EXPECTED_CADENCE_MINUTES.get(collector_id)
    if cadence and last_success:
        overdue_cutoff = datetime.now(UTC) - timedelta(minutes=cadence * 3)
        if ensure_utc(last_success.started_at) < overdue_cutoff:
            heartbeat_overdue = True
            if state == "HEALTHY":
                state = "WARNING"

    return SourceHealth(
        collector_id=collector_id,
        state=state,
        last_success_at=ensure_utc(last_success.started_at).isoformat() if last_success else None,
        last_failure_at=ensure_utc(last_failure.started_at).isoformat() if last_failure else None,
        last_item_count=most_recent.discovered_count,
        heartbeat_overdue=heartbeat_overdue,
    )


def get_health_snapshot(session: Session, settings: Settings, *, engine: Engine | None = None) -> HealthSnapshot:
    schema = check_schema(engine) if engine is not None else SchemaStatus("UNKNOWN", None, False)

    integrity_ok, integrity_detail = True, "not checked (no engine provided)"
    if engine is not None:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA quick_check")).scalar()
            integrity_ok = result == "ok"
            integrity_detail = str(result)

    total_watches = session.query(func.count(Watch.id)).scalar() or 0

    latest_obs = session.query(SourceObservation).order_by(SourceObservation.observed_at.desc()).first()
    latest_event = session.query(Event).order_by(Event.created_at.desc()).first()
    latest_lead = session.query(SpecialistLead).order_by(SpecialistLead.discovered_at.desc()).first()

    sources = [_source_health(session, cid) for cid in KNOWN_COLLECTORS]
    stale_cutoff = datetime.now(UTC) - timedelta(minutes=settings.stale_run_threshold_minutes)

    running = session.query(CollectorRun).filter(CollectorRun.status == "RUNNING").all()
    stale_running_count = sum(1 for r in running if ensure_utc(r.started_at) < stale_cutoff)

    active_locks: list[str] = []
    lock_dir = settings.resolved_lock_path.parent
    if lock_dir.exists():
        active_locks = sorted(p.name for p in lock_dir.glob("*.lock"))

    return HealthSnapshot(
        generated_at=datetime.now(UTC).isoformat(),
        schema=schema,
        db_integrity_ok=integrity_ok,
        db_integrity_detail=integrity_detail,
        total_watches=total_watches,
        latest_observation_at=ensure_utc(latest_obs.observed_at).isoformat() if latest_obs else None,
        latest_event_at=ensure_utc(latest_event.created_at).isoformat() if latest_event else None,
        latest_specialist_lead_at=(
            ensure_utc(latest_lead.discovered_at).isoformat() if latest_lead else None
        ),
        sources=sources,
        active_locks=active_locks,
        stale_running_count=stale_running_count,
    )
