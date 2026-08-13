"""Collector runs, pipeline ledger, and event foundations."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.watch import Watch


class CollectorRun(Base):
    """Operational summary for each collector execution."""

    __tablename__ = "collector_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED', 'ZERO_ITEMS', 'BLOCKED', 'SKIPPED_OVERLAP')",
            name="ck_run_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    collector_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    collector_version: Mapped[str] = mapped_column(String(32), nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")

    discovered_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    parsed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    new_watch_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    observation_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    warning_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Sprint 7 epoch/baseline tracking -- see app/models/epoch.py and
    # app/services/epoch.py. Nullable/default-False so pre-Epoch-1 rows
    # (archived, not migrated forward) remain valid.
    epoch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("operational_epochs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    ledger_entries: Mapped[list["PipelineLedger"]] = relationship(
        "PipelineLedger",
        back_populates="run",
    )


class PipelineLedger(Base):
    """Append-only ledger tracking pipeline stages for a correlation_id."""

    __tablename__ = "pipeline_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("collector_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    stage: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)

    input_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    collector_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scoring_rule_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[Optional["CollectorRun"]] = relationship(
        "CollectorRun", back_populates="ledger_entries"
    )


class Event(Base):
    """Foundational multi-watch editorial event (Stage 1: schema only)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    story_score: Mapped[float | None] = mapped_column(nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(nullable=True)
    data_completeness_score: Mapped[float | None] = mapped_column(nullable=True)
    scoring_rule_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    watches: Mapped[list["EventWatch"]] = relationship(
        "EventWatch", back_populates="event", cascade="all, delete-orphan"
    )


class EventWatch(Base):
    """Association of watches to editorial events."""

    __tablename__ = "event_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    watch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("watches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    event: Mapped["Event"] = relationship("Event", back_populates="watches")
    # Web catch-up sprint: Recent Intelligence needs to render manufacturer/
    # reference for each Event without a second query per row. The watch_id
    # FK already existed; this is a read-only ORM convenience relationship,
    # no migration needed. One-directional on purpose -- Watch doesn't need
    # a back-reference to every event it's ever been part of for anything
    # this app currently does.
    watch: Mapped["Watch"] = relationship("Watch", viewonly=True)
