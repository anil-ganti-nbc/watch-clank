"""Durable, non-fabricated soak/promotion evidence."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base

class QualificationEvidence(Base):
    __tablename__ = "qualification_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collector_id: Mapped[str] = mapped_column(String(64), index=True)
    epoch_id: Mapped[str] = mapped_column(String(64), index=True)
    provenance: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    change_identity: Mapped[str | None] = mapped_column(String(128))
    reset_reason: Mapped[str | None] = mapped_column(Text)
    intervention_treatment: Mapped[str | None] = mapped_column(String(32))
    eligibility_gate: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    qualification_gate: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    execution_id: Mapped[int | None] = mapped_column(ForeignKey("collector_runs.id", ondelete="SET NULL"), index=True)
    material_identity: Mapped[str | None] = mapped_column(String(256))
    # A reset is a transition record.  Keep the prior state with the event,
    # rather than requiring a later lookup of mutable history to reconstruct it.
    prior_material_identity: Mapped[str | None] = mapped_column(String(256))
    prior_epoch_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str | None] = mapped_column(String(32))
