"""Timex official Atom blog feed collector (timex.com).

See app/parsers/timex_news.py for source rationale. Standard Shopify blog
Atom syndication, confirmed live -- no anti-bot concerns, single GET for
the whole feed (each entry already carries its full content, so no
per-item detail-page fetch is needed, unlike Casio/Citizen/Seiko's news
collectors).
"""

from __future__ import annotations

import json
from xml.etree import ElementTree

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "timex_news"
COLLECTOR_VERSION = "0.1.0"
REGION = "US"
TRUST_SCORE = 90.0
FEED_URL = "https://www.timex.com/blogs/the-timex-blog.atom"

_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


class TimexNewsCollector:
    """Discover Timex official blog entries from the public Atom feed. No DB writes."""

    def discover_from_feed(self, xml_bytes: bytes, *, max_items: int | None = 15) -> list[DiscoveredItem]:
        try:
            root = ElementTree.fromstring(xml_bytes.lstrip())
        except ElementTree.ParseError:
            return []

        entries = root.findall("a:entry", _ATOM_NS)
        if max_items is not None:
            entries = entries[:max_items]

        items: list[DiscoveredItem] = []
        for entry in entries:
            title_el = entry.find("a:title", _ATOM_NS)
            link_el = entry.find("a:link", _ATOM_NS)
            if title_el is None or link_el is None:
                continue
            url = link_el.attrib.get("href")
            if not url:
                continue
            title = title_el.text or ""
            published_el = entry.find("a:published", _ATOM_NS)
            content_el = entry.find("a:content", _ATOM_NS)

            entry_dict = {
                "title": title,
                "published": published_el.text if published_el is not None else None,
                "content": content_el.text if content_el is not None else None,
            }
            items.append(
                DiscoveredItem(
                    url=url,
                    title=title[:300] or None,
                    reference_hint=None,
                    metadata={"source_region": REGION, "source_language": "en", "entry_json": entry_dict},
                )
            )
        return items

    def run(self, *, max_items: int | None = 15, index_html: bytes | None = None) -> CollectorRunResult:
        """`index_html` is a misnomer inherited from run_brand_news_pipeline's
        shared call shape (`collector.run(max_items=..., index_html=...)`,
        reused across every brand regardless of the discovery payload's real
        content type) -- here it carries the raw Atom feed bytes, not HTML."""
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, region=REGION, trust_score=TRUST_SCORE
        )
        if index_html is not None:
            fr = FetchResult(url=FEED_URL, success=True, status_code=200, content_type="application/atom+xml", payload=index_html)
        else:
            fr = fetch_url(FEED_URL)

        result.metadata["index_blocked"] = is_blocked_response(fr.status_code, fr.payload, fr.error)
        if not fr.success or not fr.payload:
            status = "BLOCKED" if result.metadata["index_blocked"] else "FAILED"
            result.metadata["component_status"] = status
            result.metadata["healthy"] = False
            result.fetched = [fr]
            return result

        items = self.discover_from_feed(fr.payload, max_items=max_items)
        result.discovered = items
        result.metadata["discovered_count"] = len(items)

        for item in items:
            entry_json = item.metadata.get("entry_json")
            result.fetched.append(
                FetchResult(
                    url=item.url, success=True, status_code=200,
                    content_type="application/json", payload=json.dumps(entry_json).encode("utf-8"),
                )
            )

        result.metadata["component_status"] = "SUCCESS" if items else "ZERO_ITEMS"
        result.metadata["healthy"] = True
        result.metadata["discovery_fetches"] = [{"url": FEED_URL, "status": fr.status_code, "success": fr.success}]
        return result
