"""WATCH SIGHTING formatting + Discord delivery via the fleet's own lane.

Delivery follows every existing convention:
- DiscordNotifier (app/services/discord_notify.py) owns transport, the
  editorial/health lane split, retries, and the host authority boundary
  (editorial_notifications_enabled + webhook + instance label). The
  Sentinel adds no new webhook and no new transport code.
- DeliveryReceiptService owns durable, redacted delivery evidence with an
  idempotency key, so a retried sweep can never double-ping an already
  accepted sighting, and a failed ping is recorded, never silently lost.
- A delivery failure must never break collection: every failure mode
  collapses into an alert_state on the sighting row.
"""
from __future__ import annotations

import sqlalchemy.orm as orm

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.time import ensure_utc
from app.models.sentinel import ALERT_DISABLED, ALERT_FAILED, ALERT_SENT, SentinelSighting
from app.services.delivery_receipts import DeliveryReceiptService
from app.services.discord_notify import DiscordNotifier

logger = get_logger(__name__)

ENTITY_SENTINEL_SIGHTING = "SENTINEL_SIGHTING"
PURPOSE_SENTINEL_SIGHTING = "sentinel_sighting"


def format_sighting(sighting: SentinelSighting, *, display_name: str | None = None) -> str:
    """The compact alert contract. No article-worthiness language, no
    novelty claim -- first-seen only, by construction."""
    source_label = display_name or sighting.source
    lines = [
        "**WATCH SIGHTING**",
        f"Brand: {_brand_from_key(sighting.identity_key)}",
    ]
    if sighting.reference:
        lines.append(f"Model: {sighting.reference}")
    if sighting.title:
        lines.append(f"Title: {sighting.title}")
    lines.append(f"Source: {source_label}" + (f" ({sighting.region})" if sighting.region else ""))
    lines.append(f"Detected: {ensure_utc(sighting.detected_at).isoformat()}")
    if sighting.source_url:
        lines.append(f"URL: {sighting.source_url}")
    lines.append(f"Sighting ID: {sighting.id}")
    lines.append("")
    lines.append("Status: `UNENRICHED / FIRST-SEEN ONLY`")
    return "\n".join(lines)


def _brand_from_key(identity_key: str) -> str:
    return identity_key.split(":", 1)[0].replace("_", " ").title()


class SentinelAlerter:
    def __init__(
        self,
        session: orm.Session,
        settings: Settings | None = None,
        notifier: DiscordNotifier | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._notifier = notifier
        self.receipts = DeliveryReceiptService(session)

    @property
    def notifier(self) -> DiscordNotifier:
        if self._notifier is None:
            self._notifier = DiscordNotifier(self.settings)
        return self._notifier

    def send(self, sighting: SentinelSighting, *, display_name: str | None = None) -> str:
        """Deliver one sighting. Returns the durable alert_state.

        Idempotent: if a receipt already records provider acceptance for
        this sighting, it is never re-pinged. Every failure lands in the
        sighting row; nothing raises.
        """
        if self.receipts.already_delivered(
            ENTITY_SENTINEL_SIGHTING, sighting.id, PURPOSE_SENTINEL_SIGHTING
        ):
            return sighting.alert_state or ALERT_SENT

        if not self.notifier.editorial_enabled:
            self.receipts.record(
                entity_type=ENTITY_SENTINEL_SIGHTING,
                entity_id=sighting.id,
                purpose=PURPOSE_SENTINEL_SIGHTING,
                attempt=_disabled_attempt(),
            )
            self.session.flush()
            SentinelAlerter._set(sighting, ALERT_DISABLED, "editorial delivery disabled/unconfigured")
            return ALERT_DISABLED

        text = format_sighting(sighting, display_name=display_name)
        try:
            attempt = self.notifier.send_editorial_alert_detailed(text)
        except Exception as exc:  # belt and braces: notifier already catches, this cannot lose a sighting
            logger.warning("sentinel_alert_exception", sighting_id=sighting.id, error=str(exc))
            SentinelAlerter._set(sighting, ALERT_FAILED, str(exc)[:300])
            return ALERT_FAILED

        self.receipts.record(
            entity_type=ENTITY_SENTINEL_SIGHTING,
            entity_id=sighting.id,
            purpose=PURPOSE_SENTINEL_SIGHTING,
            attempt=attempt,
        )
        self.session.flush()
        if attempt.accepted:
            SentinelAlerter._set(sighting, ALERT_SENT)
            return ALERT_SENT
        SentinelAlerter._set(sighting, ALERT_FAILED, attempt.error_summary)
        return ALERT_FAILED

    @staticmethod
    def _set(sighting: SentinelSighting, state: str, error: str | None = None) -> None:
        sighting.alert_state = state
        sighting.alert_error = error


def _disabled_attempt():
    from app.services.discord_notify import DeliveryAttempt

    return DeliveryAttempt(accepted=False, error_summary="editorial_delivery_disabled")
