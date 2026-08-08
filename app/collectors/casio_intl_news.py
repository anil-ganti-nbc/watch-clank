"""Casio International product-news collector (accessible official source).

Primary entry: https://www.casio.com/intl/news/
Filters to watch-related product announcements; suppresses corporate/education/etc.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import component_status_from_fetches, fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "casio_intl_news"
COLLECTOR_VERSION = "0.1.0"
REGION = "INTL"
TRUST_SCORE = 95.0
NEWS_INDEX_URL = "https://www.casio.com/intl/news/"

# Title/tag keywords that indicate a watch product announcement
WATCH_POSITIVE = re.compile(
    r"\b(G-SHOCK|GSHOCK|EDIFICE|OCEANUS|PRO\s*TREK|PROTREK|MR-G|MRG|MT-G|MTG|"
    r"BABY-G|BABYG|SHEEN|CASIO\s+WATCH|wristwatch|watch(?:es)?)\b",
    re.IGNORECASE,
)
# Strong negatives (non-watch product lines / corporate)
WATCH_NEGATIVE = re.compile(
    r"\b(calculator|education|EDU-Port|Moflin|keyboard|piano|Privia|Celviano|"
    r"projector|camera|uterocervical|MEXT|corporate|rename(?:s|d)?\s+company|"
    r"ambassador|metaverse|Roblox|MOU\b|scientific)\b",
    re.IGNORECASE,
)
# URL slug tokens that look like model families
MODEL_SLUG_RE = re.compile(
    r"(?:^|/)((?:efk|efb|eqb|eqw|gwr|gmw|ga|gw|dw|gbd|gbdh|gmg|mrg|mtg|ocw|"
    r"prg|prw|bsa|bga|msg|shb)[-_]?[a-z0-9\-]+)",
    re.IGNORECASE,
)


def is_watch_announcement(title: str, tag: str = "", href: str = "") -> bool:
    blob = f"{title} {tag} {href}"
    if WATCH_NEGATIVE.search(blob) and not WATCH_POSITIVE.search(title):
        return False
    if WATCH_POSITIVE.search(blob):
        return True
    return bool(MODEL_SLUG_RE.search(href) and "Product" in tag)


class CasioIntlNewsCollector:
    """Discover and fetch Casio International watch product news. No DB writes."""

    def discover_index(self, html: str, base_url: str = NEWS_INDEX_URL) -> list[DiscoveredItem]:
        tree = HTMLParser(html)
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        rows = tree.css(".cmp-newslist__item") or tree.css(".cmp-newslist-item")
        for row in rows:
            link = row.css_first("a.cmp-newslist__link") or row.css_first("a")
            if not link:
                continue
            href = link.attributes.get("href") or ""
            if not href or href.startswith("javascript:"):
                continue
            url = urljoin(base_url, href)
            if url in seen:
                continue
            title_el = row.css_first(".cmp-newslist-item__title") or link
            title = (title_el.text(strip=True) if title_el else "") or ""
            # strip leading date fragments often glued into title text
            date_el = row.css_first(".cmp-newslist-item__date")
            date_text = date_el.text(strip=True) if date_el else ""
            if date_text and title.startswith(date_text):
                title = title[len(date_text):].strip()
            tag_el = row.css_first(".cmp-newslist-item__tag")
            tag = tag_el.text(strip=True) if tag_el else ""
            if not is_watch_announcement(title, tag, href):
                continue
            seen.add(url)
            thumb = row.css_first("img")
            image = None
            if thumb:
                image = thumb.attributes.get("src") or thumb.attributes.get("data-src")
                if image:
                    image = urljoin(base_url, image)
            items.append(
                DiscoveredItem(
                    url=url,
                    title=title[:300] or None,
                    reference_hint=None,
                    metadata={
                        "publication_date_text": date_text,
                        "tag": tag,
                        "image_url": image,
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

        # Fetch announcement bodies
        for item in items:
            fr = fetch_url(item.url)
            result.fetched.append(fr)
            if not fr.success:
                result.errors.append(f"{item.url}: {fr.error}")

        # include index fetch in metadata only; item fetches are result.fetched
        useful = sum(1 for f in result.fetched if f.success)
        status = component_status_from_fetches(
            discovery_fetches=discovery_fetches,
            item_fetches=result.fetched,
            useful_count=useful if useful else (1 if items and index_fr.success else 0),
        )
        # If index OK and we listed items, even without detail fetches we have discoveries
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
