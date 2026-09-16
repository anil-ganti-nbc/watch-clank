"""Horology Sentinel persistence: known-identity store, sightings, source state.

The Sentinel is a deliberately lightweight first-stage tripwire (see
ai/handoff/SENTINEL_RUNBOOK.md): it detects previously unseen watch
identities on first-party sources and pings Discord immediately. It never
creates Watch/Event/SpecialistLead rows -- enrichment and editorial
judgement remain Horology Clank's job. These three tables are the entire
Sentinel footprint; everything is additive and independent of the main
pipeline tables except the (optional, SET NULL) run correlation FKs.

identity semantics (global across regions by contract):
- identity_key is THE dedup key. A reference sighting is
  "<manufacturer>:<reference_canonical>" -- the same reference from Timex US
  and Timex UK is one identity. A no-reference fallback is
  "<manufacturer>:url:<sha256 prefix of the canonicalized first-party URL>".
- admitted_via records how an identity entered the store: BASELINE rows were
  imported silently (first successful poll of a source, or an explicit
  baseline sweep) and are never alerted; SIGHTING rows were admitted live and
  have a matching SentinelSighting row.
"""
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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

IDENTITY_TYPE_REFERENCE = "REFERENCE"
IDENTITY_TYPE_URL = "URL"
ADMITTED_VIA_BASELINE = "BASELINE"
ADMITTED_VIA_SIGHTING = "SIGHTING"

ALERT_SENT = "SENT"
ALERT_FAILED = "FAILED"
ALERT_DISABLED = "DISABLED"
ALERT_CAPPED = "CAPPED"


class SentinelIdentity(Base):
    """One known watch identity in the Sentinel's global dedup store."""

    __tablename__ = "sentinel_identities"
    __table_args__ = (
        UniqueConstraint("identity_key", name="uq_sentinel_identity_key"),
        CheckConstraint(
            "identity_type IN ('REFERENCE', 'URL')",
            name="ck_sentinel_identity_type",
        ),
        CheckConstraint(
            "admitted_via IN ('BASELINE', 'SIGHTING')",
            name="ck_sentinel_admitted_via",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identity_key: Mapped[str] = mapped_column(String(320), nullable=False)

    identity_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=IDENTITY_TYPE_REFERENCE
    )
    manufacturer: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Preserved exactly as the source spelled it (regional suffix included).
    reference_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Post-normalization, post-regional-suffix-collapse. Null for URL identities.
    reference_canonical: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Canonicalized first-party product URL (identity of record for URL-type).
    fallback_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    title: Mapped[str | None] = mapped_column(String(512), nullable=True)

    admitted_via: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=ADMITTED_VIA_SIGHTING
    )

    first_source: Mapped[str] = mapped_column(String(64), nullable=False)
    first_region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Refreshed on re-observation only past a staleness threshold
    # (settings.sentinel_last_seen_refresh_hours) so a 15-minute poll never
    # turns into a full-table write churn.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_source: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SentinelSighting(Base):
    """One live first-seen detection, with its own correlation ID.

    The row id doubles as the Sighting ID quoted in the Discord alert so
    later Horology Clank enrichment can correlate back to the original
    tripwire. alert_state is the durable answer to "did the ping go out?",
    backed by a delivery_receipts row (entity_type SENTINEL_SIGHTING).
    """

    __tablename__ = "sentinel_sightings"
    __table_args__ = (
        CheckConstraint(
            "alert_state IN ('SENT', 'FAILED', 'DISABLED', 'CAPPED')",
            name="ck_sentinel_alert_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sentinel_identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    identity_key: Mapped[str] = mapped_column(String(320), nullable=False, index=True)

    source: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    alert_state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=ALERT_SENT
    )
    alert_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("collector_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class SentinelSourceState(Base):
    """Per-source poll bookkeeping and the baseline arming latch.

    armed_at IS NULL means "this source has never completed a successful
    poll" -- its first success is a silent per-source baseline (the
    _auto_baseline_for_first_run pattern), so a brand-new or previously
    broken source can never replay its whole catalogue into Discord.
    """

    __tablename__ = "sentinel_source_state"
    __table_args__ = (
        UniqueConstraint("source", name="uq_sentinel_source_state_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)

    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
