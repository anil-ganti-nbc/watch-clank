"""Conservative Discord alert delivery.

Design constraints:
- Secrets (webhook URLs) come from Settings, which reads env/.env only —
  never hardcode or commit one.
- A Discord failure (network error, bad webhook, rate limit) must NEVER
  propagate into collector/pipeline/DB code. Every public method catches
  everything and logs; callers can always ignore the return value safely.
- EDITORIAL alerts (a scored Event worth a journalist's attention) and
  HEALTH/OPS alerts (source failures, stuck runs) go to separate webhooks
  and must never be mixed.
- No article prose. This only ever sends the deterministic alert text
  produced by app.services.editorial.format_alert (or a short health line).

Track F (2026-09-03): this module used to answer one question — "did an HTTP
POST return below 300?" — and throw the response away. That could not
distinguish a delivered-and-buried alert from one sent to a channel nobody
watches, which is exactly the ambiguity that made Citizen JY8144-50E
(event 442) unexplainable. Posts now use Discord's `?wait=true`, which
returns the created message object (id + channel_id) instead of a bare 204,
and every attempt is reported as a structured DeliveryAttempt. The bool
returning methods are unchanged for existing callers.
"""

from __future__ import annotations

import hashlib

import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DISCORD_MESSAGE_LIMIT = 2000

# Bounded: worst case two backoff sleeps, so a rate-limited or flapping
# Discord can add ~a few seconds to a run and never more. Collection must
# never be held hostage to notification transport.
MAX_DELIVERY_ATTEMPTS = 3
MAX_RETRY_SLEEP_SECONDS = 5.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def destination_alias(webhook_url: str | None, *, lane: str) -> str | None:
    """A stable, redacted fingerprint of a webhook — never the URL itself.

    Lets an operator answer "did these two alerts go to the same place?" and
    "did the destination change on 2026-09-01?" without any secret ever
    reaching the database, a log line, or a report. Truncated SHA-256 is
    one-way; the alias cannot be turned back into a usable webhook.
    """
    if not webhook_url:
        return None
    digest = hashlib.sha256(webhook_url.encode("utf-8")).hexdigest()[:12]
    return f"{lane}:{digest}"


@dataclass(frozen=True)
class DeliveryAttempt:
    """Structured outcome of one logical delivery (including its retries)."""

    accepted: bool
    provider_status: int | None = None
    provider_message_id: str | None = None
    provider_channel_id: str | None = None
    destination_alias: str | None = None
    attempt_count: int = 0
    error_summary: str | None = None

    @property
    def lifecycle_state(self) -> str:
        """Map a transport outcome onto the durable lifecycle vocabulary.

        VERIFIED_VISIBLE is deliberately unreachable from here: no transport
        response proves a human can see the message, and this layer must
        never be able to claim that it does.
        """
        if not self.accepted:
            return "FAILED"
        if self.provider_message_id:
            return "PROVIDER_IDENTIFIED"
        return "PROVIDER_ACCEPTED"


def _with_wait(webhook_url: str) -> str:
    """Ask Discord to return the created message instead of a bare 204."""
    parts = urlsplit(webhook_url)
    query = parts.query
    if "wait=" in query:
        return webhook_url
    query = f"{query}&wait=true" if query else "wait=true"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _retry_after_seconds(resp: httpx.Response) -> float:
    for raw in (resp.headers.get("retry-after"), resp.headers.get("x-ratelimit-reset-after")):
        try:
            if raw is not None:
                return min(float(raw), MAX_RETRY_SLEEP_SECONDS)
        except (TypeError, ValueError):
            continue
    try:
        body = resp.json()
        if isinstance(body, dict) and "retry_after" in body:
            return min(float(body["retry_after"]), MAX_RETRY_SLEEP_SECONDS)
    except Exception:  # noqa: BLE001 -- body may be empty/non-JSON; not an error here
        pass
    return 1.0


class DiscordNotifier:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # Last structured editorial outcome, so callers that use the
        # long-standing boolean API still get access to the full evidence
        # without changing their call shape (and so a test double that only
        # implements the boolean method degrades honestly to weaker
        # evidence instead of fabricating a provider response).
        self.last_editorial_attempt: DeliveryAttempt | None = None

    @property
    def editorial_enabled(self) -> bool:
        # This is the single-host editorial authority boundary. When Hetzner
        # becomes authoritative, Windows sets the flag false and every
        # official and specialist editorial call site becomes a safe no-op.
        # Health/ops delivery deliberately remains host-local.
        return self.settings.editorial_notifications_enabled and bool(self.settings.discord_editorial_webhook_url)

    @property
    def health_enabled(self) -> bool:
        return bool(self.settings.discord_health_webhook_url)

    def editorial_destination_alias(self) -> str | None:
        return destination_alias(self.settings.discord_editorial_webhook_url, lane="editorial")

    def _post_detailed(self, webhook_url: str, content: str, *, lane: str) -> DeliveryAttempt:
        alias = destination_alias(webhook_url, lane=lane)
        if len(content) > DISCORD_MESSAGE_LIMIT:
            content = content[: DISCORD_MESSAGE_LIMIT - 20] + "\n... (truncated)"
        url = _with_wait(webhook_url)

        last_status: int | None = None
        last_error: str | None = None
        for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
            try:
                resp = httpx.post(url, json={"content": content}, timeout=10.0)
                last_status = resp.status_code
                if resp.status_code < 300:
                    message_id = channel_id = None
                    try:
                        body = resp.json()
                        if isinstance(body, dict):
                            message_id = str(body["id"]) if body.get("id") is not None else None
                            channel_id = (
                                str(body["channel_id"]) if body.get("channel_id") is not None else None
                            )
                    except Exception:  # noqa: BLE001
                        # The status code alone decides acceptance. A 204, an
                        # empty body, a non-JSON payload or a response object
                        # that cannot be parsed at all is only ever a loss of
                        # OPTIONAL extra evidence (PROVIDER_ACCEPTED instead
                        # of PROVIDER_IDENTIFIED) -- it must never downgrade a
                        # genuine provider acceptance into a false FAILED,
                        # which would both misreport delivery and trigger
                        # pointless retries against a provider that already
                        # took the message.
                        pass
                    return DeliveryAttempt(
                        accepted=True,
                        provider_status=resp.status_code,
                        provider_message_id=message_id,
                        provider_channel_id=channel_id,
                        destination_alias=alias,
                        attempt_count=attempt,
                    )

                last_error = resp.text[:300]
                logger.warning("discord_post_failed", status=resp.status_code, body=last_error, attempt=attempt)
                if resp.status_code not in _RETRYABLE_STATUSES or attempt == MAX_DELIVERY_ATTEMPTS:
                    break
                time.sleep(_retry_after_seconds(resp))
            except Exception as exc:  # never let Discord break the pipeline
                last_error = str(exc)[:300]
                logger.warning("discord_post_exception", error=last_error, attempt=attempt)
                if attempt == MAX_DELIVERY_ATTEMPTS:
                    break
                time.sleep(min(float(attempt), MAX_RETRY_SLEEP_SECONDS))

        return DeliveryAttempt(
            accepted=False,
            provider_status=last_status,
            destination_alias=alias,
            attempt_count=MAX_DELIVERY_ATTEMPTS,
            error_summary=last_error,
        )

    def _post(self, webhook_url: str, content: str) -> bool:
        """Backward-compatible boolean wrapper (pre-track-F signature)."""
        return self._post_detailed(webhook_url, content, lane="editorial").accepted

    def send_editorial_alert_detailed(self, text: str) -> DeliveryAttempt:
        """Send an editorial alert and report structured delivery evidence.

        A disabled/unconfigured notifier is NOT a transport failure — it is
        a deliberate authority boundary — so it returns accepted=False with
        an explicit reason rather than a fabricated status code.
        """
        if not self.editorial_enabled:
            attempt = DeliveryAttempt(accepted=False, error_summary="editorial_delivery_disabled")
        else:
            attempt = self._post_detailed(self.settings.discord_editorial_webhook_url, text, lane="editorial")
        self.last_editorial_attempt = attempt
        return attempt

    def send_editorial_alert(self, text: str) -> bool:
        """Send a pre-formatted editorial alert (see editorial.format_alert).
        No-op (returns False) if no webhook is configured — never raises."""
        return self.send_editorial_alert_detailed(text).accepted

    def send_health_alert(self, text: str) -> bool:
        """Send a short operational/health notice (source down, stuck run,
        etc.) — deliberately separate from editorial alerts."""
        if not self.health_enabled:
            return False
        return self._post_detailed(self.settings.discord_health_webhook_url, text, lane="health").accepted

    def notification_authority(self) -> str:
        """WINDOWS / HETZNER / UNLABELED / NONE — never a raw webhook URL.

        Reports which *labeled instance* (see Settings.watch_clank_instance)
        would actually send an editorial alert right now, so an operator can
        see the authority boundary without either host ever exposing a
        secret. NONE means this instance sends no editorial alerts at all
        (disabled, or no webhook configured) — the safe/expected state for
        every instance except the one true authority. UNLABELED is a
        deliberately loud distinct state: this instance WOULD send editorial
        alerts but has no WATCH_CLANK_INSTANCE set, so its authority can't be
        confirmed — never silently folded into NONE, since that would hide a
        real misconfiguration risk (an unlabeled host that's actually live).
        """
        if not self.editorial_enabled:
            return "NONE"
        label = (self.settings.watch_clank_instance or "").strip().upper()
        if "HETZNER" in label:
            return "HETZNER"
        if "WINDOWS" in label:
            return "WINDOWS"
        return "UNLABELED"
