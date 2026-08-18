"""Human editorial QC review of an Event.

A Review is HUMAN EDITORIAL FEEDBACK ABOUT ONE INTELLIGENCE ITEM UNDER A
PARTICULAR EVIDENCE STATE -- not a mutation of the Event, not a permanent
verdict on the underlying Watch/reference. See
ai/handoff/HUMAN_QC_FEEDBACK_CONTRACT.md for the full contract this model
exists to support.

EVENT != REVIEW. OBSERVATION != REVIEW. WATCH != REVIEW.

One Event may carry at most one live Review (uq_event_review_event_id) --
a second submission for the same event_id is a *correction*, not a new
review: it overwrites disposition in place and appends the prior verdict
to review_metadata.correction_history, rather than creating a second row.
This keeps "has this exact item already been triaged" a simple existence
check while still leaving an audit trail for corrections.

A later Event for the same watch/reference is a **different event_id** --
it has no Review row of its own until independently reviewed, so a past
verdict here never silently suppresses future evidence (see Phase 7 of the
citizen-flood-autopsy brief: "a later Event for the same reference must
NOT automatically inherit permanent dismissal").
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

DISPOSITIONS = frozenset({"USEFUL", "NOT_USEFUL", "FALSE_POSITIVE", "OUT_OF_STOCK"})


class EventReview(Base):
    """One operator's current verdict on one Event."""

    __tablename__ = "event_reviews"
    __table_args__ = (
        CheckConstraint(
            "disposition IN ('USEFUL', 'NOT_USEFUL', 'FALSE_POSITIVE', 'OUT_OF_STOCK')",
            name="ck_event_review_disposition",
        ),
        UniqueConstraint("event_id", name="uq_event_review_event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL (not CASCADE): if a Watch is ever removed, the Review --
    # historical evidence of what an operator decided -- must survive.
    watch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("watches.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Denormalized snapshot of the reviewed item's identity at review time.
    # Deliberate, not accidental duplication: a later pipeline run could in
    # principle correct the canonical Watch/Event row underneath a Review;
    # this snapshot preserves what the operator actually looked at.
    manufacturer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference_canonical: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_collector_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    disposition: Mapped[str] = mapped_column(String(32), nullable=False)

    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # The evidence's own timestamp (SourceObservation.observed_at at review
    # time) -- distinct from reviewed_at, which is when the human acted.
    evidence_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    availability_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Corrections append {previous_disposition, previous_reviewed_at,
    # corrected_at} here rather than opening a second table -- see module
    # docstring.
    review_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    event: Mapped[Event] = relationship("Event", viewonly=True)


from app.models.pipeline import Event  # noqa: E402
