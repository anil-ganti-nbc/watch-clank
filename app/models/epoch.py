"""Operational epoch tracking (Sprint 7 / "Epoch 1 Reset").

The smallest mechanism needed to distinguish "unknown to Clank because
Clank just started watching" from "genuinely new to the world". A fresh
operational database starts exactly one OperationalEpoch row (Epoch 1).
While that epoch's baseline_started_at is set and baseline_completed_at is
still null, every collector run happening is a BASELINE run: it still
creates real Watch/SourceObservation/SpecialistLead rows (so correlation
and lead-time math have real data to work with later), but it must never
create Event rows (an Event is an editorial claim of "this changed" —
during baseline nothing has "changed", we're just discovering what
already existed) and must never send a Discord notification. Once
baseline_completed_at is set, normal event/notification rules resume.

Deliberately NOT a general "epoch history" model with switching/rollback
semantics -- this project runs one epoch at a time per database. Multiple
rows would only exist if a future session ever ran a second reset; the
"active" epoch is always simply the most recently started one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OperationalEpoch(Base):
    __tablename__ = "operational_epochs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baseline_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
