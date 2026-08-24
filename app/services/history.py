"""Explicit database history state (2026-08-24 Windows field-test repair).

The Windows deployment's active DB was created fresh by an export job, and
its entire catalogue registered as locally-unseen on the first run -- every
source behaved like a first-ever baseline even though the operator had
weeks of triage history elsewhere. Nothing in the app distinguished "this
database is one run old" from "this database has been watching for weeks",
so a fresh field-test DB silently presented itself as ordinary established
operation.

This module makes that state explicit and observable. Deliberately minimal:
a derived property of rows this schema already has (no migration), exposed
through the existing health snapshot rather than a new subsystem.

States:
- EMPTY        -- no successful collector run ever recorded on this DB.
                  Every source will be auto-baselined; nothing here is news.
- BASELINING   -- at least one successful run exists but fewer than
                  ``established_run_threshold`` successful runs for every
                  known collector; novelty evidence is still warming up and
                  FIRST_SEEN events from these early runs deserve extra
                  operator suspicion.
- ESTABLISHED  -- every known collector has accumulated at least the
                  threshold of successful runs; ordinary operation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CollectorRun
from app.services.health import KNOWN_COLLECTORS

# Successful non-baseline runs per collector after which its novelty
# evidence is considered seasoned. Deliberately small: two full catalogue
# passes are enough to prove the collector both works and has stable
# identity resolution. Expressed in runs (not days) so the meaning is the
# same for a 45-minute RSS lane and a 12-hour sitemap lane, matching
# zero_item_warning_streak's precedent.
ESTABLISHED_RUN_THRESHOLD = 2

HISTORY_STATES = ("EMPTY", "BASELINING", "ESTABLISHED")


def history_state(session: Session, *, now: datetime | None = None) -> str:
    """Classify this database's operational history state.

    Read-only and deterministic. Never raises on an empty database --
    EMPTY is a normal, expected state for a new deployment.
    """
    success_counts = dict(
        session.execute(
            select(CollectorRun.collector_id, func.count())
            .where(CollectorRun.status.in_(("SUCCESS", "PARTIAL")))
            .group_by(CollectorRun.collector_id)
        ).all()
    )

    if not any((success_counts.get(cid) or 0) > 0 for cid in KNOWN_COLLECTORS):
        return "EMPTY"

    missing = [
        cid
        for cid in KNOWN_COLLECTORS
        if (success_counts.get(cid) or 0) < ESTABLISHED_RUN_THRESHOLD
    ]
    return "BASELINING" if missing else "ESTABLISHED"


def history_state_detail(session: Session) -> dict:
    """Structured detail for /diagnostics: state plus which collectors are
    still short of the establishment threshold."""
    success_counts = dict(
        session.execute(
            select(CollectorRun.collector_id, func.count())
            .where(CollectorRun.status.in_(("SUCCESS", "PARTIAL")))
            .group_by(CollectorRun.collector_id)
        ).all()
    )
    state = history_state(session)
    below = {
        cid: int(success_counts.get(cid) or 0)
        for cid in KNOWN_COLLECTORS
        if (success_counts.get(cid) or 0) < ESTABLISHED_RUN_THRESHOLD
    }
    return {
        "state": state,
        "established_run_threshold": ESTABLISHED_RUN_THRESHOLD,
        "collectors_below_threshold": below,
        "checked_at": datetime.now(UTC).isoformat(),
    }
