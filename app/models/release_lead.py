"""Announcement-first release leads (before full catalog enrichment)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class ReleaseLead(Base):
    __tablename__ = "release_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manufacturer: Mapped[str] = mapped_column(String(64), nullable=False, default="Casio")
    brand: Mapped[str] = mapped_column(String(64), nullable=False, default="Casio")
    collection: Mapped[str | None] = mapped_column(String(128), nullable=True)
    announcement_title: Mapped[str] = mapped_column(String(512), nullable=False)
    announcement_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    announcement_url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_references: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    product_urls: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    image_urls: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    snapshot_fetch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    completeness_score: Mapped[float] = mapped_column(Float, nullable=False, default=20.0)
    enrichment_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ANNOUNCEMENT_ONLY"
    )
    merge_key: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    merged_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    watch_ids: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SourceComponentState(Base):
    """Per-source operational state (backoff, last status)."""

    __tablename__ = "source_component_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_blocks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backoff_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
