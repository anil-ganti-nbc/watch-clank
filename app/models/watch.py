"""Watch, family, and related models."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Watch(Base, TimestampMixin):
    """Canonical watch entity.

    Uniqueness strategy:
    - Primary identity is (manufacturer, brand, reference_canonical).
    - reference_raw is preserved exactly as observed.
    - family_candidate_key is provisional grouping.
    """

    __tablename__ = "watches"
    __table_args__ = (
        UniqueConstraint(
            "manufacturer",
            "brand",
            "reference_canonical",
            name="uq_watch_manufacturer_brand_canonical",
        ),
        CheckConstraint(
            "water_resistance_m IS NULL OR water_resistance_m >= 0",
            name="ck_water_resistance_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    manufacturer: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    brand: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    collection: Mapped[str | None] = mapped_column(String(128), nullable=True)

    model_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    reference_raw: Mapped[str] = mapped_column(String(128), nullable=False)
    reference_canonical: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    family_candidate_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True, index=True
    )

    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    movement_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    caliber_or_module: Mapped[str | None] = mapped_column(String(64), nullable=True)

    solar: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    bluetooth: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    radio_sync: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gps: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    case_material: Mapped[str | None] = mapped_column(String(128), nullable=True)
    crystal: Mapped[str | None] = mapped_column(String(128), nullable=True)
    water_resistance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limited_edition: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    extra_specs: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Relationships
    observations: Mapped[list["SourceObservation"]] = relationship(
        "SourceObservation",
        back_populates="watch",
        cascade="all, delete-orphan",
    )
    memberships: Mapped[list["FamilyMembership"]] = relationship(
        "FamilyMembership",
        back_populates="watch",
        cascade="all, delete-orphan",
    )


class WatchFamily(Base, TimestampMixin):
    """Provisional or confirmed family grouping."""

    __tablename__ = "watch_families"
    __table_args__ = (
        UniqueConstraint("manufacturer", "brand", "family_key", name="uq_family_key"),
        CheckConstraint(
            "status IN ('PROVISIONAL', 'RULE_MATCHED', 'CONFIRMED', 'MANUAL_REVIEW')",
            name="ck_family_status",
        ),
        CheckConstraint(
            "grouping_confidence IS NULL OR (grouping_confidence >= 0 AND grouping_confidence <= 100)",
            name="ck_family_confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    manufacturer: Mapped[str] = mapped_column(String(64), nullable=False)
    brand: Mapped[str] = mapped_column(String(64), nullable=False)
    family_key: Mapped[str] = mapped_column(String(256), nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PROVISIONAL", server_default="PROVISIONAL"
    )
    grouping_rule: Mapped[str | None] = mapped_column(String(128), nullable=True)
    grouping_rule_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    grouping_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    fields_compared: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    memberships: Mapped[list["FamilyMembership"]] = relationship(
        "FamilyMembership",
        back_populates="family",
        cascade="all, delete-orphan",
    )


class FamilyMembership(Base):
    """Association between watches and families with history metadata."""

    __tablename__ = "family_memberships"
    __table_args__ = (
        UniqueConstraint("watch_id", "family_id", name="uq_watch_family"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("watches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("watch_families.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    assignment_rule: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")

    watch: Mapped["Watch"] = relationship("Watch", back_populates="memberships")
    family: Mapped["WatchFamily"] = relationship("WatchFamily", back_populates="memberships")


# Forward refs for type checking
from app.models.observation import SourceObservation  # noqa: E402
