"""Snapshot blob (content-addressed payload) and fetch (per-URL observation) models.

Separation of concerns:
- SnapshotBlob: one row per unique payload bytes (deduplicated by content_hash).
- SnapshotFetch: one row per network fetch or fixture ingestion, preserving
  the exact source_url, collector identity, and HTTP metadata even when
  multiple URLs return identical bytes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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


class SnapshotBlob(Base):
    """Content-addressed payload storage. Identical bytes share one blob."""

    __tablename__ = "snapshot_blobs"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_snapshot_blob_content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filepath: Mapped[str] = mapped_column(Text, nullable=False)
    compression_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1", server_default="1"
    )
    encoding: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    fetches: Mapped[list[SnapshotFetch]] = relationship(
        "SnapshotFetch",
        back_populates="blob",
    )


class SnapshotFetch(Base):
    """One fetch (or fixture ingestion) of a URL. Always preserves source metadata."""

    __tablename__ = "snapshot_fetches"
    __table_args__ = ()

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    blob_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("snapshot_blobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    source_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    collector_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    collector_version: Mapped[str] = mapped_column(String(32), nullable=False)

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    blob: Mapped[SnapshotBlob] = relationship("SnapshotBlob", back_populates="fetches")
    observations: Mapped[list[SourceObservation]] = relationship(
        "SourceObservation",
        back_populates="fetch",
    )


from app.models.observation import SourceObservation  # noqa: E402
