"""Durable, redacted evidence that an external delivery actually happened.

Track F remediation (Citizen JY8144-50E, event 442, 2026-09-01): the system
recorded `alerted=true` / `delivery.state=sent` purely because an HTTP POST
returned a status below 300. It kept no destination, no provider response,
no message identity and no retry state, so "the operator saw no alert" could
not be distinguished from "the message was delivered to a channel nobody was
watching" or "the message was delivered and buried". Transport acceptance is
not delivery evidence.

Secrets discipline: `destination_alias` is a redacted, stable fingerprint of
the webhook (see app.services.delivery_receipts.destination_alias), never the
webhook URL itself. Nothing in this table may contain a credential.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base

# Lifecycle, deliberately ordered from least to most proven. The old boolean
# collapsed every one of these into "sent".
#   QUEUED               a delivery was decided on but not yet attempted
#   ATTEMPTED            a request was issued; outcome not yet known
#   PROVIDER_ACCEPTED    provider returned success but gave us no message id
#   PROVIDER_IDENTIFIED  provider returned a durable message id (strongest
#                        evidence available without reading the channel back)
#   VERIFIED_VISIBLE     independently confirmed present in the destination;
#                        reserved for a future read-back/canary check and set
#                        by nothing today, so it can never be silently faked
#   FAILED               provider rejected, or the attempt errored out
DELIVERY_LIFECYCLE_STATES: tuple[str, ...] = (
    "QUEUED",
    "ATTEMPTED",
    "PROVIDER_ACCEPTED",
    "PROVIDER_IDENTIFIED",
    "VERIFIED_VISIBLE",
    "FAILED",
)

# States that mean "the provider took it, but nobody has proven a human could
# see it". These are exactly what the reconciliation report surfaces.
UNVERIFIED_ACCEPTED_STATES: tuple[str, ...] = ("PROVIDER_ACCEPTED", "PROVIDER_IDENTIFIED")


class DeliveryReceipt(Base):
    """One row per (entity, delivery purpose) — not one per attempt.

    Retries update this row and increment `attempt_count` rather than
    appending, so `idempotency_key` stays the natural dedup guard: a second
    delivery of the same alert for the same entity can be recognised and
    refused instead of double-posting.
    """

    __tablename__ = "delivery_receipts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_delivery_receipt_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Mirrors PipelineLedger's addressing rather than a hard FK, because both
    # Events and SpecialistLeads deliver through the same notifier.
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)

    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)

    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="discord")
    # Redacted fingerprint, never a URL. See delivery_receipts.destination_alias.
    destination_alias: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED", index=True)

    provider_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Truncated and provider-supplied only; never echo request content here.
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
