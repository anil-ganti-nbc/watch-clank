"""Bounded Citizen Germany catalogue collector.

Citizen Germany exposes a public product sitemap and server-rendered Product
JSON-LD. The first run fetches the finite sitemap catalogue for a silent
source baseline. Later runs receive persisted known URLs from the pipeline and
fetch only new canonical product URLs, avoiding repeated bulk detail crawls.
"""

from __future__ import annotations

import re

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import component_status_from_fetches, fetch_url, is_blocked_response

COLLECTOR_ID = "citizen_de_products"
COLLECTOR_VERSION = "0.1.0"
REGION = "DE"
TRUST_SCORE = 95.0
PRODUCT_SITEMAP_URL = "https://de.citizenwatch.eu/media/sitemap/sitemap-products-de.citizenwatch.eu.xml"
MAX_CANDIDATES = 600

_LOC_RE = re.compile(r"<loc>(https://de\.citizenwatch\.eu/de/p/[^<]+)</loc>", re.IGNORECASE)


class CitizenGermanyProductsCollector:
    """Discover canonical German product pages from the official sitemap."""

    def discover_from_sitemap_xml(self, payload: str | bytes) -> list[DiscoveredItem]:
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        for url in _LOC_RE.findall(text):
            if url in seen:
                continue
            seen.add(url)
            reference_hint = url.rstrip("/").rsplit("/", 1)[-1].upper()
            items.append(DiscoveredItem(url=url, title=reference_hint, reference_hint=reference_hint))
            if len(items) >= MAX_CANDIDATES:
                break
        return items

    def run(
        self,
        *,
        max_items: int | None = 100,
        sitemap_payload: bytes | None = None,
        known_product_urls: set[str] | None = None,
    ) -> CollectorRunResult:
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID,
            collector_version=COLLECTOR_VERSION,
            region=REGION,
            trust_score=TRUST_SCORE,
        )
        sitemap_fetch = (
            FetchResult(PRODUCT_SITEMAP_URL, True, 200, "application/xml", sitemap_payload)
            if sitemap_payload is not None
            else fetch_url(PRODUCT_SITEMAP_URL, accept_language="de-DE,de;q=0.9")
        )
        discovery_fetches = [sitemap_fetch]
        if not sitemap_fetch.success or not sitemap_fetch.payload:
            result.metadata["component_status"] = "BLOCKED" if is_blocked_response(
                sitemap_fetch.status_code, sitemap_fetch.payload, sitemap_fetch.error
            ) else "FAILED"
            result.metadata["healthy"] = False
            result.fetched = discovery_fetches
            return result

        discovered = self.discover_from_sitemap_xml(sitemap_fetch.payload)
        result.metadata["candidate_count"] = len(discovered)
        known = known_product_urls or set()
        pending = [item for item in discovered if item.url not in known]
        if max_items is not None:
            pending = pending[:max_items]
        result.discovered = pending
        result.metadata["discovered_count"] = len(pending)
        result.metadata["known_url_count"] = len(known)
        for item in pending:
            result.fetched.append(fetch_url(item.url, accept_language="de-DE,de;q=0.9"))

        status = component_status_from_fetches(
            discovery_fetches=discovery_fetches,
            item_fetches=result.fetched,
            useful_count=sum(1 for item in result.fetched if item.success),
        )
        result.metadata["component_status"] = status
        result.metadata["healthy"] = status in {"SUCCESS", "PARTIAL", "ZERO_ITEMS"}
        result.metadata["discovery_fetches"] = [
            {"url": sitemap_fetch.url, "status": sitemap_fetch.status_code, "success": sitemap_fetch.success}
        ]
        return result
