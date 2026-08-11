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
"""

from __future__ import annotations

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DISCORD_MESSAGE_LIMIT = 2000


class DiscordNotifier:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def editorial_enabled(self) -> bool:
        return bool(self.settings.discord_editorial_webhook_url)

    @property
    def health_enabled(self) -> bool:
        return bool(self.settings.discord_health_webhook_url)

    def _post(self, webhook_url: str, content: str) -> bool:
        try:
            if len(content) > DISCORD_MESSAGE_LIMIT:
                content = content[: DISCORD_MESSAGE_LIMIT - 20] + "\n... (truncated)"
            resp = httpx.post(webhook_url, json={"content": content}, timeout=10.0)
            if resp.status_code >= 300:
                logger.warning("discord_post_failed", status=resp.status_code, body=resp.text[:300])
                return False
            return True
        except Exception as exc:  # never let Discord break the pipeline
            logger.warning("discord_post_exception", error=str(exc))
            return False

    def send_editorial_alert(self, text: str) -> bool:
        """Send a pre-formatted editorial alert (see editorial.format_alert).
        No-op (returns False) if no webhook is configured — never raises."""
        if not self.editorial_enabled:
            return False
        return self._post(self.settings.discord_editorial_webhook_url, text)

    def send_health_alert(self, text: str) -> bool:
        """Send a short operational/health notice (source down, stuck run,
        etc.) — deliberately separate from editorial alerts."""
        if not self.health_enabled:
            return False
        return self._post(self.settings.discord_health_webhook_url, text)
