"""Source observation model."""

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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SourceObservation(Base):
    """One successful parse of a product page/source item.

    Reprocessing the same source creates a new observation, not a new watch.
    """

    __tablename__ = "source_observations"
    __table_args__ = (
        CheckConstraint(
            "source_trust_score >= 0 AND source_trust_score <= 100",
            name="ck_source_trust_range",
        ),
        CheckConstraint(
            "overall_confidence >= 0 AND overall_confidence <= 100",
            name="ck_overall_confidence_range",
        ),
        CheckConstraint(
            "price IS NULL OR price >= 0",
            name="ck_price_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    watch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("watches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fetch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("snapshot_fetches.id", ondelete="SET NULL"), nullable=True, index=True
    )

    collector_id: Mapped[str] = mapped_column(String(64), nullable=False)
    collector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)

    region: Mapped[str] = mapped_column(String(16), nullable=False, default="JP")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    availability_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    source_trust_score: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    field_confidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    parser_warnings: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    # Sprint 7 epoch/baseline tracking -- see app/models/epoch.py.
    epoch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("operational_epochs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    watch: Mapped[Watch] = relationship("Watch", back_populates="observations")
    fetch: Mapped[SnapshotFetch | None] = relationship(
        "SnapshotFetch", back_populates="observations"
    )


from app.models.snapshot import SnapshotFetch  # noqa: E402
from app.models.watch import Watch  # noqa: E402
