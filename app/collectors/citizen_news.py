"""Citizen global product-news collector (accessible official source).

Primary entry: https://www.citizenwatch-global.com/news/
This page is Citizen's own watch-brand news feed — live probing (2026-08-11)
found it is 100% watch product announcements (Tsuyosa, Attesa, Promaster,
Rainell, etc.) with no corporate/financial noise, unlike Casio's or Seiko's
mixed corporate feeds. No topic filtering is applied here as a result; if
that changes, add an is_watch_announcement-style filter as Casio's collector
does.

EXPERIMENTAL: not wired into the production Casio scheduled pipeline. See
PipelineService.run_brand_news_pipeline.
"""

from __future__ import annotations

from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import component_status_from_fetches, fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "citizen_news"
COLLECTOR_VERSION = "0.1.0"
REGION = "GLOBAL"
TRUST_SCORE = 95.0
NEWS_INDEX_URL = "https://www.citizenwatch-global.com/news/"


class CitizenNewsCollector:
    """Discover and fetch Citizen global watch news. No DB writes."""

    def discover_index(self, html: str, base_url: str = NEWS_INDEX_URL) -> list[DiscoveredItem]:
        tree = HTMLParser(html)
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        for row in tree.css(".news_main_item"):
            link = row.css_first("a.news_main_itemLink") or row.css_first("a")
            if not link:
                continue
            href = link.attributes.get("href") or ""
            if not href or href.startswith("javascript:"):
                continue
            url = urljoin(base_url, href)
            if url in seen:
                continue
            date_el = row.css_first(".news_date")
            date_text = date_el.text(strip=True) if date_el else ""
            title_el = row.css_first(".news_main_detail")
            title = title_el.text(strip=True) if title_el else ""
            if not title:
                continue
            seen.add(url)
            items.append(
                DiscoveredItem(
                    url=url,
                    title=title[:300] or None,
                    reference_hint=None,
                    metadata={
                        "publication_date_text": date_text,
                        "source_language": "en",
                        "source_region": REGION,
                    },
                )
            )
        return items

    def run(self, *, max_items: int | None = 15, index_html: bytes | None = None) -> CollectorRunResult:
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID,
            collector_version=COLLECTOR_VERSION,
            region=REGION,
            trust_score=TRUST_SCORE,
        )
        discovery_fetches: list[FetchResult] = []

        if index_html is not None:
            index_fr = FetchResult(
                url=NEWS_INDEX_URL,
                success=True,
                status_code=200,
                content_type="text/html",
                payload=index_html,
            )
        else:
            index_fr = fetch_url(NEWS_INDEX_URL)
        discovery_fetches.append(index_fr)
        result.metadata["index_status"] = index_fr.status_code
        result.metadata["index_blocked"] = is_blocked_response(
            index_fr.status_code, index_fr.payload, index_fr.error
        )

        if not index_fr.success or not index_fr.payload:
            status = "BLOCKED" if result.metadata["index_blocked"] else "FAILED"
            result.metadata["component_status"] = status
            result.metadata["healthy"] = False
            if result.metadata["index_blocked"]:
                result.errors.append(f"index blocked: {index_fr.error}")
            else:
                result.errors.append(f"index fetch failed: {index_fr.error}")
            result.fetched = discovery_fetches
            return result

        items = self.discover_index(index_fr.payload.decode("utf-8", errors="ignore"))
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
            discovery_fetches=discovery_fetches,
            item_fetches=result.fetched,
            useful_count=useful if useful else (1 if items and index_fr.success else 0),
        )
        if status == "ZERO_ITEMS" and items:
            status = "SUCCESS"
        if not items and index_fr.success:
            status = "ZERO_ITEMS"
        result.metadata["component_status"] = status
        result.metadata["healthy"] = status in ("SUCCESS", "PARTIAL")
        result.metadata["discovery_fetches"] = [
            {"url": f.url, "status": f.status_code, "success": f.success} for f in discovery_fetches
        ]
        return result
