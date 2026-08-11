"""Operational epoch lifecycle -- see app/models/epoch.py for the concept.

Deliberately explicit, not auto-created on first access: an operational
DB with no epoch row is a configuration problem worth surfacing loudly
(scripts/epoch.py's `start` subcommand is the only thing that creates one),
not something to silently paper over.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import OperationalEpoch

logger = get_logger(__name__)


def get_active_epoch(session: Session) -> OperationalEpoch | None:
    """The most recently started epoch, or None if none exists yet."""
    return session.query(OperationalEpoch).order_by(OperationalEpoch.id.desc()).first()


def is_baseline_active(session: Session) -> bool:
    """True only between baseline_start() and baseline_complete() on the
    active epoch. False (normal operation) if there's no active epoch,
    baseline was never started, or it already completed."""
    epoch = get_active_epoch(session)
    if epoch is None:
        return False
    return epoch.baseline_started_at is not None and epoch.baseline_completed_at is None


def start_epoch(session: Session, *, name: str, notes: str | None = None) -> OperationalEpoch:
    """Create a new epoch. Refuses if one already exists with this name --
    call sites should check get_active_epoch() first if they want
    idempotent "ensure epoch 1 exists" behavior."""
    existing = session.query(OperationalEpoch).filter_by(name=name).one_or_none()
    if existing is not None:
        raise ValueError(f"epoch '{name}' already exists (id={existing.id})")
    epoch = OperationalEpoch(name=name, started_at=datetime.now(UTC), notes=notes)
    session.add(epoch)
    session.commit()
    logger.info("epoch_started", epoch_id=epoch.id, name=name)
    return epoch


def start_baseline(session: Session, epoch: OperationalEpoch) -> OperationalEpoch:
    if epoch.baseline_started_at is not None:
        raise ValueError(f"epoch '{epoch.name}' baseline already started at {epoch.baseline_started_at}")
    epoch.baseline_started_at = datetime.now(UTC)
    session.commit()
    logger.info("epoch_baseline_started", epoch_id=epoch.id, name=epoch.name)
    return epoch


def complete_baseline(session: Session, epoch: OperationalEpoch) -> OperationalEpoch:
    if epoch.baseline_started_at is None:
        raise ValueError(f"epoch '{epoch.name}' baseline was never started")
    if epoch.baseline_completed_at is not None:
        raise ValueError(f"epoch '{epoch.name}' baseline already completed at {epoch.baseline_completed_at}")
    epoch.baseline_completed_at = datetime.now(UTC)
    session.commit()
    logger.info("epoch_baseline_completed", epoch_id=epoch.id, name=epoch.name)
    return epoch
