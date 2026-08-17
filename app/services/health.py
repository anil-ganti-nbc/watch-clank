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

from app.core.config import Settings
from app.core.time import ensure_utc
from app.db.schema_check import SchemaStatus, check_schema
from app.models import CollectorRun, Event, SourceObservation, SpecialistLead, Watch

# Every collector_id this project schedules today. Kept as a plain list
# (not derived from the DB) so a source with zero runs ever still shows as
# NEVER_RUN instead of silently not appearing.
KNOWN_COLLECTORS = [
    "casio_multi",
    "casio_uk_sitemap",
    "citizen_news",
    "citizen_products",
    "citizen_de_products",
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
    "citizen_news": 90,
    "citizen_products": 360,
    "citizen_de_products": 720,
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
