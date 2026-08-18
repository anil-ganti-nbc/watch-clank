"""Human QC review of a SpecialistLead (Layer B early-warning intelligence).

Sibling to `app.models.review.EventReview`, deliberately NOT the same
table -- Event and SpecialistLead have always been separate tables in
this codebase (see specialist_lead.py's own module docstring for why:
Layer A official evidence must never become indistinguishable from Layer
B third-party leads). Extending that same separation to reviews keeps
that boundary intact while reusing every other part of the QC system:
the same `app/services/qc.py` module, the same disposition-persistence/
correction-audit-trail pattern, the same `/qc/history` page and cursor-
pagination approach, the same field-test mutation-boundary allowance.

EVENT != REVIEW extends here too: SPECIALIST_LEAD != REVIEW. A review is
human editorial feedback about one lead under one evidence state, never a
mutation of the lead and never a permanent verdict on the underlying
reference -- see ai/handoff/HUMAN_QC_FEEDBACK_CONTRACT.md, which this
model follows exactly except for the disposition vocabulary (DUPLICATE
in place of OUT_OF_STOCK -- editorial leads can be genuine repeats of
already-seen coverage; they are never "in/out of stock").
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

LEAD_DISPOSITIONS = frozenset({"USEFUL", "NOT_USEFUL", "DUPLICATE", "FALSE_POSITIVE"})


class SpecialistLeadReview(Base):
    """One operator's current verdict on one SpecialistLead."""

    __tablename__ = "specialist_lead_reviews"
    __table_args__ = (
        CheckConstraint(
            "disposition IN ('USEFUL', 'NOT_USEFUL', 'DUPLICATE', 'FALSE_POSITIVE')",
            name="ck_specialist_lead_review_disposition",
        ),
        UniqueConstraint("specialist_lead_id", name="uq_specialist_lead_review_lead_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    specialist_lead_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialist_leads.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Denormalized snapshot at review time -- see EventReview's identical
    # rationale: a later pipeline run could in principle touch the
    # canonical SpecialistLead row underneath a review; this snapshot
    # preserves what the operator actually judged.
    lead_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lead_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_authority_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collector_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("collector_runs.id", ondelete="SET NULL"), nullable=True
    )

    disposition: Mapped[str] = mapped_column(String(32), nullable=False)

    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Corrections append {previous_disposition, previous_reviewed_at,
    # corrected_at} here -- same audit-trail convention as EventReview,
    # rather than a second history table.
    review_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Set True the first time this row's disposition is corrected -- see
    # EventReview's identical field/rationale (2026-08-19 QC History
    # correction UX addendum). Never reset back to False.
    is_corrected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    lead: Mapped[SpecialistLead] = relationship("SpecialistLead", viewonly=True)


from app.models.specialist_lead import SpecialistLead  # noqa: E402
