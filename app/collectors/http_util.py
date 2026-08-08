"""Shared HTTP helpers for collectors."""

from __future__ import annotations

import random
import time

import httpx

from app.collectors.base import FetchResult
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def is_blocked_response(status_code: int | None, body: bytes | str | None = None, error: str | None = None) -> bool:
    """True when upstream explicitly denied automated access."""
    if status_code in (401, 403):
        return True
    err = (error or "").lower()
    if "access denied" in err or "http 403" in err or "http 401" in err:
        return True
    if body:
        sample = body[:800].decode("utf-8", errors="ignore").lower() if isinstance(body, (bytes, bytearray)) else str(body)[:800].lower()
        if "access denied" in sample or "akamai" in sample and "denied" in sample:
            return True
    return False


def fetch_url(
    url: str,
    *,
    accept_language: str = "en-US,en;q=0.9",
    max_retries: int | None = None,
) -> FetchResult:
    """GET a URL with conservative retries. Does not raise for HTTP errors."""
    settings = get_settings()
    retries = settings.collector_max_retries if max_retries is None else max_retries
    last_error: str | None = None
    for attempt in range(retries + 1):
        started = time.perf_counter()
        try:
            with httpx.Client(
                headers={
                    "User-Agent": settings.collector_user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": accept_language,
                },
                timeout=settings.collector_timeout_seconds,
                follow_redirects=True,
            ) as client:
                resp = client.get(url)
            elapsed = int((time.perf_counter() - started) * 1000)
            blocked = is_blocked_response(resp.status_code, resp.content)
            if blocked:
                return FetchResult(
                    url=url,
                    success=False,
                    status_code=resp.status_code,
                    content_type=resp.headers.get("content-type"),
                    payload=resp.content[:4096] if resp.content else None,
                    error=f"HTTP {resp.status_code}",
                    elapsed_ms=elapsed,
                )
            if resp.status_code >= 400:
                return FetchResult(
                    url=url,
                    success=False,
                    status_code=resp.status_code,
                    content_type=resp.headers.get("content-type"),
                    payload=None,
                    error=f"HTTP {resp.status_code}",
                    elapsed_ms=elapsed,
                )
            return FetchResult(
                url=str(resp.url),
                success=True,
                status_code=resp.status_code,
                content_type=resp.headers.get("content-type"),
                payload=resp.content,
                error=None,
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            last_error = str(exc)
            logger.warning("http_fetch_failed", url=url, attempt=attempt, error=last_error)
            if attempt < retries:
                delay = settings.collector_backoff_base * (2**attempt)
                if settings.collector_jitter:
                    delay *= 0.5 + random.random()
                time.sleep(min(delay, 8.0))
    return FetchResult(url=url, success=False, error=last_error or "fetch failed")


def component_status_from_fetches(
    *,
    discovery_fetches: list[FetchResult],
    item_fetches: list[FetchResult],
    useful_count: int,
) -> str:
    """Derive a single-source status from discovery/item fetches and useful item count."""
    disc_blocked = all(
        is_blocked_response(f.status_code, f.payload, f.error) for f in discovery_fetches
    ) if discovery_fetches else False
    item_blocked = False
    if item_fetches:
        successes = sum(1 for f in item_fetches if f.success)
        blocked = sum(1 for f in item_fetches if is_blocked_response(f.status_code, f.payload, f.error))
        item_blocked = successes == 0 and blocked > 0 and blocked >= max(1, len(item_fetches) // 2)

    if disc_blocked and not item_fetches:
        return "BLOCKED"
    if item_blocked and useful_count == 0:
        return "BLOCKED"
    if useful_count > 0:
        failures = sum(1 for f in item_fetches if not f.success)
        if failures:
            return "PARTIAL"
        return "SUCCESS"
    # successful responses but nothing useful
    if discovery_fetches and any(f.success for f in discovery_fetches) and useful_count == 0:
        return "ZERO_ITEMS"
    if not discovery_fetches and not item_fetches:
        return "FAILED"
    return "FAILED"
