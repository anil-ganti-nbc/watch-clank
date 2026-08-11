"""Citizen US product/catalogue collector (citizenwatch.com).

Discovery: a small, fixed set of collection listing pages
(citizenwatch.com/us/en/collection/<slug>) that link directly to product
pages at /us/en/product/<reference>. robots.txt does not disallow either
path (only /search, /cart, /archive, and a few account endpoints are
disallowed). Confirmed live 2026-08-11.

Kept deliberately small and fixed rather than crawling every collection —
this is a discovery seed list, not a full-catalogue crawler, consistent
with the project's "boring, deterministic" philosophy and this sprint's
"do not build retailer scraping" scope boundary. Extend conservatively.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import component_status_from_fetches, fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "citizen_products"
COLLECTOR_VERSION = "0.1.0"
REGION = "US"
TRUST_SCORE = 95.0
BASE_URL = "https://citizenwatch.com"

# Seed collection pages. A mix of recognisable families (Attesa, Tsuyosa)
# and collaboration lines (Collabs) — matches the editorial patterns this
# project cares about (see app/services/editorial.py RECOGNIZABLE_FAMILIES).
DISCOVERY_COLLECTIONS = (
    "/us/en/collection/attesa",
    "/us/en/collection/tsuyosa",
    "/us/en/collection/collabs",
)

_PRODUCT_HREF_RE = re.compile(r'href="(/us/en/product/[A-Za-z0-9-]+)"')


class CitizenProductsCollector:
    """Discover and fetch Citizen US product pages. No DB writes."""

    def discover_from_collection_html(self, html: str, base_url: str) -> list[DiscoveredItem]:
        seen: set[str] = set()
        items: list[DiscoveredItem] = []
        for m in _PRODUCT_HREF_RE.finditer(html):
            url = urljoin(base_url, m.group(1))
            if url in seen:
                continue
            seen.add(url)
            ref = url.rsplit("/", 1)[-1]
            items.append(
                DiscoveredItem(
                    url=url,
                    title=ref,
                    reference_hint=ref,
                    metadata={"source_region": REGION},
                )
            )
        return items

    def run(
        self, *, max_items: int | None = 10, collection_html: dict[str, bytes] | None = None
    ) -> CollectorRunResult:
        """collection_html: optional {collection_path: html_bytes} for offline/fixture use."""
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, region=REGION, trust_score=TRUST_SCORE
        )
        discovery_fetches: list[FetchResult] = []
        items: list[DiscoveredItem] = []
        seen_urls: set[str] = set()

        for path in DISCOVERY_COLLECTIONS:
            url = urljoin(BASE_URL, path)
            if collection_html is not None:
                payload = collection_html.get(path)
                fr = (
                    FetchResult(url=url, success=True, status_code=200, content_type="text/html", payload=payload)
                    if payload is not None
                    else FetchResult(url=url, success=False, error="no fixture provided")
                )
            else:
                fr = fetch_url(url)
            discovery_fetches.append(fr)
            if not fr.success or not fr.payload:
                continue
            for item in self.discover_from_collection_html(fr.payload.decode("utf-8", errors="ignore"), url):
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                items.append(item)

        result.metadata["index_blocked"] = bool(discovery_fetches) and all(
            is_blocked_response(f.status_code, f.payload, f.error) for f in discovery_fetches
        )
        if not items and result.metadata["index_blocked"]:
            result.metadata["component_status"] = "BLOCKED"
            result.metadata["healthy"] = False
            result.fetched = discovery_fetches
            return result

        if max_items is not None:
            items = items[:max_items]
        result.discovered = items
        result.metadata["discovered_count"] = len(items)

        for item in items:
            fr = fetch_url(item.url)
            result.fetched.append(fr)
            if not fr.success:
                result.errors.append(f"{item.url}: {fr.error}")

        useful = sum(1 for f in result.fetched if f.success)
        status = component_status_from_fetches(
            discovery_fetches=discovery_fetches, item_fetches=result.fetched, useful_count=useful
        )
        if status == "ZERO_ITEMS" and items:
            status = "SUCCESS"
        if not items and any(f.success for f in discovery_fetches):
            status = "ZERO_ITEMS"
        result.metadata["component_status"] = status
        result.metadata["healthy"] = status in ("SUCCESS", "PARTIAL")
        result.metadata["discovery_fetches"] = [
            {"url": f.url, "status": f.status_code, "success": f.success} for f in discovery_fetches
        ]
        return result
