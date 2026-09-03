"""Persist redacted delivery evidence for every external alert (track F).

The rule this service exists to enforce: **transport acceptance is not
delivery**. Before this, an event carried `alerted=true` and nothing else,
so an alert that Discord accepted into a channel nobody reads was
indistinguishable from one that reached the operator. Every send now leaves
a durable receipt naming where it went (as a redacted alias), what the
provider said, and whether a message identity came back.

Nothing here may ever store a webhook URL or any other credential.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import DeliveryReceipt
from app.services.discord_notify import DeliveryAttempt

logger = get_logger(__name__)

ENTITY_EVENT = "EVENT"
ENTITY_SPECIALIST_LEAD = "SPECIALIST_LEAD"

# Purposes are distinct delivery intents for the same entity. A specialist
# lead legitimately delivers twice (early warning, then correlation
# follow-up); those must not dedup against each other.
PURPOSE_EDITORIAL_ALERT = "editorial_alert"
PURPOSE_LEAD_EARLY_WARNING = "lead_early_warning"
PURPOSE_LEAD_CORRELATION = "lead_correlation"


def idempotency_key(entity_type: str, entity_id: str | int, purpose: str) -> str:
    return f"{entity_type}:{entity_id}:{purpose}"


class DeliveryReceiptService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find(self, entity_type: str, entity_id: str | int, purpose: str) -> DeliveryReceipt | None:
        return (
            self.session.query(DeliveryReceipt)
            .filter_by(idempotency_key=idempotency_key(entity_type, entity_id, purpose))
            .first()
        )

    def already_delivered(self, entity_type: str, entity_id: str | int, purpose: str) -> bool:
        """True when this exact alert already reached the provider.

        Discord has no server-side idempotency for webhook posts, so this is
        the only thing standing between a re-processed event and a duplicate
        ping. Deliberately conservative: only a provider-accepted state
        counts, so a previous FAILED attempt is still allowed to retry.
        """
        receipt = self.find(entity_type, entity_id, purpose)
        return receipt is not None and receipt.lifecycle_state in (
            "PROVIDER_ACCEPTED",
            "PROVIDER_IDENTIFIED",
            "VERIFIED_VISIBLE",
        )

    def record(
        self,
        *,
        entity_type: str,
        entity_id: str | int,
        purpose: str,
        attempt: DeliveryAttempt,
        provider: str = "discord",
    ) -> DeliveryReceipt:
        """Upsert the receipt for one (entity, purpose) delivery intent."""
        now = datetime.now(UTC)
        receipt = self.find(entity_type, entity_id, purpose)
        if receipt is None:
            receipt = DeliveryReceipt(
                entity_type=entity_type,
                entity_id=str(entity_id),
                purpose=purpose,
                idempotency_key=idempotency_key(entity_type, entity_id, purpose),
                provider=provider,
                created_at=now,
                updated_at=now,
                attempt_count=0,
            )
            self.session.add(receipt)

        receipt.lifecycle_state = attempt.lifecycle_state
        receipt.provider_status = attempt.provider_status
        receipt.destination_alias = attempt.destination_alias or receipt.destination_alias
        receipt.attempt_count = (receipt.attempt_count or 0) + max(attempt.attempt_count, 1)
        receipt.last_attempt_at = now
        receipt.updated_at = now
        if receipt.first_attempt_at is None:
            receipt.first_attempt_at = now
        # Never let a later, weaker attempt erase a message identity we
        # already proved -- that identity is the strongest evidence held.
        if attempt.provider_message_id:
            receipt.provider_message_id = attempt.provider_message_id
        if attempt.provider_channel_id:
            receipt.provider_channel_id = attempt.provider_channel_id
        receipt.error_summary = attempt.error_summary

        self.session.flush()
        logger.info(
            "delivery_receipt_recorded",
            entity_type=entity_type,
            entity_id=str(entity_id),
            purpose=purpose,
            lifecycle_state=receipt.lifecycle_state,
            provider_status=receipt.provider_status,
            has_message_id=bool(receipt.provider_message_id),
            destination_alias=receipt.destination_alias,
        )
        return receipt

    def delivery_extra(self, receipt: DeliveryReceipt) -> dict:
        """The Event.extra["delivery"] payload for this receipt.

        Keeps the pre-existing "state" key (so dashboards, QC surfaces and
        STD-UI-COM-011 conformance keep working) and adds the evidence that
        was missing. "sent" is retained as the accepted-state label rather
        than renamed, to avoid rewriting the meaning of historical rows.
        """
        return {
            "state": "sent" if receipt.lifecycle_state != "FAILED" else "failed",
            "lifecycle_state": receipt.lifecycle_state,
            "attempted_at": (receipt.last_attempt_at or datetime.now(UTC)).isoformat(),
            "receipt_id": receipt.id,
            "destination_alias": receipt.destination_alias,
            "provider_status": receipt.provider_status,
            "provider_message_id": receipt.provider_message_id,
            "provider_channel_id": receipt.provider_channel_id,
            "attempt_count": receipt.attempt_count,
        }
