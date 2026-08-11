"""Seiko Group Corporation news collector (accessible official source).

Primary entry: https://www.seiko.co.jp/en/news/
This is Seiko Group Corporation's *corporate* press feed — it covers Seiko
Watch alongside Seiko Clock, Seiko Instruments, cultural sponsorships,
financial results, and other non-watch topics (confirmed by live sample on
2026-08-11: items included "Seiko Time & Jazz", a RIKEN biomanufacturing
partnership, and a clock-tower replica unveiling alongside real watch press
releases). A topic filter is therefore required, mirroring
app/collectors/casio_intl_news.py's is_watch_announcement.

Known gap (see HANDOFF.md): the watch-brand-specific feed at
seikowatches.com/global-en/news is a JS-rendered SPA backed by a REST API
that was not reverse engineered in this pass.

EXPERIMENTAL: not wired into the production Casio scheduled pipeline. See
PipelineService.run_brand_news_pipeline.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import component_status_from_fetches, fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "seiko_jp_news"
COLLECTOR_VERSION = "0.1.0"
REGION = "JP"
TRUST_SCORE = 90.0
NEWS_INDEX_URL = "https://www.seiko.co.jp/en/news/"

WATCH_POSITIVE = re.compile(
    r"\b(watch(?:es)?|Seiko\s+5|Prospex|Presage|Astron|King\s+Seiko|Grand\s+Seiko|"
    r"Lukia|Credor|caliber|calibre|wristwatch|movement|dial|chronograph)\b",
    re.IGNORECASE,
)
WATCH_NEGATIVE = re.compile(
    r"\b(clock\s+tower|jazz|biomanufacturing|RIKEN|financial\s+results?|earnings|"
    r"museum|sponsorship|shareholder|dividend|subsidiary|joint\s+venture)\b",
    re.IGNORECASE,
)


def is_watch_announcement(title: str, category: str = "") -> bool:
    blob = f"{title} {category}"
    if WATCH_NEGATIVE.search(blob) and not WATCH_POSITIVE.search(title):
        return False
    return bool(WATCH_POSITIVE.search(blob))


class SeikoNewsCollector:
    """Discover and fetch Seiko (JP corporate feed) watch-relevant news. No DB writes."""

    def discover_index(self, html: str, base_url: str = NEWS_INDEX_URL) -> list[DiscoveredItem]:
        tree = HTMLParser(html)
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        for row in tree.css(".c-newsList__item"):
            link = row.css_first("a.c-newsList__link") or row.css_first("a")
            if not link:
                continue
            href = link.attributes.get("href") or ""
            if not href or href.startswith("javascript:"):
                continue
            url = urljoin(base_url, href)
            if url in seen:
                continue
            date_el = row.css_first(".c-newsList__date")
            date_text = date_el.text(strip=True) if date_el else ""
            cat_els = row.css(".c-newsList__category")
            category = " ".join(c.text(strip=True) for c in cat_els) if cat_els else ""
            title_el = row.css_first(".c-newsList__txt")
            title = title_el.text(strip=True) if title_el else ""
            if not title:
                continue
            if not is_watch_announcement(title, category):
                continue
            seen.add(url)
            items.append(
                DiscoveredItem(
                    url=url,
                    title=title[:300] or None,
                    reference_hint=None,
                    metadata={
                        "publication_date_text": date_text,
                        "category": category,
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
