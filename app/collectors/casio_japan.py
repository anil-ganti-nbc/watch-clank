"""Casio Japan official collector.

Discovery strategy (Stage 1):
- Primary: product listing / new products pages on www.casio.com/jp/
- Fallback: known high-value product page patterns for smoke testing
- Prefers static HTML extraction via selectolax
- No Playwright unless static access fails (documented)

Note: As of 2026-08 the official domain applies aggressive bot protection
(Akamai). Live runs from restricted environments may receive 403/Access Denied.
The collector handles non-200 responses gracefully and records them.
"""

from __future__ import annotations

import random
import re
import time
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import is_blocked_response
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "casio_japan"
COLLECTOR_VERSION = "0.2.0"
REGION = "JP"
TRUST_SCORE = 100.0

# Known discovery entry points (official)
DISCOVERY_URLS = [
    "https://www.casio.com/jp/watches/",
    "https://www.casio.com/jp/watches/gshock/",
    "https://www.casio.com/jp/watches/edifice/",
    "https://www.casio.com/jp/watches/oceanus/",
    "https://www.casio.com/jp/watches/protrek/",
]

# Product URL pattern used for filtering discovered links
PRODUCT_PATH_RE = re.compile(
    r"/jp/watches/.+/product\.[A-Z0-9\-]+\.html",
    re.IGNORECASE,
)


class CasioJapanCollector:
    """Network discovery + fetch only. Never writes to the database."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.user_agent = self.settings.collector_user_agent
        self.timeout = self.settings.collector_timeout_seconds
        self.max_retries = self.settings.collector_max_retries
        self.backoff_base = self.settings.collector_backoff_base
        self.jitter = self.settings.collector_jitter

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en;q=0.9",
            },
            timeout=self.timeout,
            follow_redirects=True,
        )

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self.backoff_base * (2**attempt)
        if self.jitter:
            delay *= 0.5 + random.random()
        time.sleep(min(delay, 30.0))

    def fetch_url(self, url: str) -> FetchResult:
        """Fetch a single URL with retries and bounded backoff."""
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            start = time.perf_counter()
            try:
                with self._client() as client:
                    resp = client.get(url)
                elapsed = int((time.perf_counter() - start) * 1000)

                content_type = resp.headers.get("content-type", "").split(";")[0].strip()
                if resp.status_code != 200:
                    return FetchResult(
                        url=url,
                        success=False,
                        status_code=resp.status_code,
                        content_type=content_type,
                        error=f"HTTP {resp.status_code}",
                        elapsed_ms=elapsed,
                    )

                # Basic content-type validation
                if content_type and not any(
                    t in content_type for t in ("text/html", "application/xhtml", "text/plain", "application/json")
                ):
                    return FetchResult(
                        url=url,
                        success=False,
                        status_code=resp.status_code,
                        content_type=content_type,
                        error=f"Unexpected content-type: {content_type}",
                        elapsed_ms=elapsed,
                    )

                payload = resp.content
                max_bytes = self.settings.snapshot_max_payload_bytes
                if len(payload) > max_bytes:
                    return FetchResult(
                        url=url,
                        success=False,
                        status_code=resp.status_code,
                        content_type=content_type,
                        error=f"Payload exceeds maximum size ({len(payload)} > {max_bytes})",
                        elapsed_ms=elapsed,
                    )

                return FetchResult(
                    url=url,
                    success=True,
                    status_code=resp.status_code,
                    content_type=content_type or "text/html",
                    payload=payload,
                    elapsed_ms=elapsed,
                )
            except httpx.TimeoutException as exc:
                last_error = f"Timeout: {exc}"
            except httpx.HTTPError as exc:
                last_error = f"HTTP error: {exc}"
            except Exception as exc:
                last_error = f"Unexpected: {exc}"

            if attempt < self.max_retries:
                logger.warning(
                    "fetch_retry",
                    url=url,
                    attempt=attempt + 1,
                    error=last_error,
                )
                self._sleep_backoff(attempt)

        return FetchResult(
            url=url,
            success=False,
            error=last_error or "Unknown fetch failure",
        )

    def discover(self, discovery_urls: list[str] | None = None) -> list[DiscoveredItem]:
        """Discover product URLs from listing pages.

        One malformed page does not abort the full discovery.
        Duplicate URLs are suppressed.
        """
        urls = discovery_urls or DISCOVERY_URLS
        seen: set[str] = set()
        discovered: list[DiscoveredItem] = []
        discovery_fetches: list[dict] = []

        for d_url in urls:
            result = self.fetch_url(d_url)
            discovery_fetches.append(
                {
                    "url": d_url,
                    "status": result.status_code,
                    "success": result.success,
                    "blocked": is_blocked_response(result.status_code, result.payload, result.error),
                    "error": result.error,
                }
            )
            if not result.success or not result.payload:
                logger.warning(
                    "discovery_fetch_failed",
                    url=d_url,
                    error=result.error,
                    status=result.status_code,
                )
                continue

            try:
                html = result.payload.decode("utf-8", errors="replace")
                tree = HTMLParser(html)
                for a in tree.css("a[href]"):
                    href = a.attributes.get("href", "")
                    if not href:
                        continue
                    full = urljoin(d_url, href)
                    # Normalize
                    parsed = urlparse(full)
                    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    if clean in seen:
                        continue
                    if PRODUCT_PATH_RE.search(parsed.path) or "/product." in parsed.path.lower():
                        seen.add(clean)
                        title = (a.text(strip=True) or "")[:200]
                        discovered.append(
                            DiscoveredItem(
                                url=clean,
                                title=title or None,
                                metadata={"discovered_from": d_url},
                            )
                        )
            except Exception as exc:
                logger.warning("discovery_parse_failed", url=d_url, error=str(exc))
                continue

        self._last_discovery_fetches = discovery_fetches
        return discovered

    def run(
        self,
        *,
        discovery_urls: list[str] | None = None,
        max_items: int | None = 20,
        known_product_urls: list[str] | None = None,
    ) -> CollectorRunResult:
        """Execute a full discovery + fetch cycle. No database writes."""
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID,
            collector_version=COLLECTOR_VERSION,
            region=REGION,
            trust_score=TRUST_SCORE,
        )

        # Discovery
        items = self.discover(discovery_urls)
        result.metadata["discovery_fetches"] = getattr(self, "_last_discovery_fetches", [])
        if known_product_urls:
            for u in known_product_urls:
                if u not in {i.url for i in items}:
                    items.append(DiscoveredItem(url=u, metadata={"source": "known"}))

        # Deduplicate already handled in discover
        if max_items is not None:
            items = items[:max_items]

        result.discovered = items
        result.metadata["discovered_count"] = len(items)
        result.metadata["role"] = "catalog_enrichment"

        # Discovery-level block detection (no product URLs found and discoveries failed)
        disc_meta = result.metadata.get("discovery_fetches") or []
        if not items and disc_meta:
            blocked = all(
                (d.get("status") in (401, 403)) or d.get("blocked") for d in disc_meta
            )
            if blocked:
                result.metadata["component_status"] = "BLOCKED"
                result.metadata["healthy"] = False
                result.errors.append("catalog discovery blocked by upstream (Akamai/403)")
                return result
            result.metadata["component_status"] = "ZERO_ITEMS"
            result.metadata["healthy"] = False
            result.warnings.append("Zero items discovered from successful discovery responses")
            return result

        if not items:
            result.metadata["component_status"] = "FAILED"
            result.metadata["healthy"] = False
            result.warnings.append("Zero items discovered; no discovery metadata")
            return result

        # Fetch each product page
        for item in items:
            fr = self.fetch_url(item.url)
            result.fetched.append(fr)
            if not fr.success:
                result.errors.append(f"{item.url}: {fr.error}")

        success_count = sum(1 for f in result.fetched if f.success)
        blocked_count = sum(
            1 for f in result.fetched if is_blocked_response(f.status_code, f.payload, f.error)
        )
        result.metadata["fetched_success"] = success_count
        result.metadata["fetched_total"] = len(result.fetched)
        if success_count == 0 and blocked_count > 0:
            result.metadata["component_status"] = "BLOCKED"
            result.metadata["healthy"] = False
        elif success_count > 0:
            result.metadata["component_status"] = "SUCCESS" if blocked_count == 0 else "PARTIAL"
            result.metadata["healthy"] = True
        else:
            result.metadata["component_status"] = "FAILED"
            result.metadata["healthy"] = False

        return result
