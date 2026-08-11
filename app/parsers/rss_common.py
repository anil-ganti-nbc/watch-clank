"""Shared RSS2.0 parsing core for specialist-source feeds.

Extracted from app/parsers/casioblog.py's proven implementation (kept
untouched to avoid any risk to its existing tests) so new sources
(G-Central, Plus9Time) don't duplicate the same XML-shape handling.
Deliberately reads only title/link/pubDate/category/description --
NEVER content:encoded or any other full-body field. This is the boundary
that keeps every specialist collector from copying full article bodies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"


@dataclass
class RawFeedItem:
    title: str
    url: str
    published_at: str | None
    categories: list[str]
    claim_text: str | None
    author: str | None = None


@dataclass
class RawFeedParseResult:
    success: bool
    items: list[RawFeedItem] = field(default_factory=list)
    error: str | None = None


def clean_excerpt(html: str, max_len: int = 400) -> str:
    text = _TAG_STRIP_RE.sub(" ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def parse_rss_feed(xml_bytes: bytes | str, *, max_items: int | None = 20) -> RawFeedParseResult:
    """Generic, defensive RSS2.0 <channel><item> parse. Never touches
    content:encoded. Skips any item missing a title or canonical link
    rather than guessing."""
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("utf-8")
    if not xml_bytes or not xml_bytes.strip():
        return RawFeedParseResult(success=False, error="empty feed")
    # Some real feeds (observed on casioblog.com) emit leading whitespace
    # before the XML declaration, which ElementTree rejects per spec
    # strictness even though real feed readers tolerate it.
    xml_bytes = xml_bytes.lstrip()
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        return RawFeedParseResult(success=False, error=f"XML parse error: {exc}")

    channel = root.find("channel")
    if channel is None:
        return RawFeedParseResult(success=False, error="no <channel> element -- unexpected feed shape")

    raw_items = channel.findall("item")
    if max_items is not None:
        raw_items = raw_items[:max_items]

    items: list[RawFeedItem] = []
    for item in raw_items:
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None or not (link_el.text or "").strip():
            continue
        title = (title_el.text or "").strip()
        url = link_el.text.strip()

        pub_el = item.find("pubDate")
        published_at = None
        if pub_el is not None and pub_el.text:
            try:
                published_at = parsedate_to_datetime(pub_el.text).isoformat()
            except (TypeError, ValueError):
                published_at = None

        categories = [c.text.strip() for c in item.findall("category") if c.text]

        desc_el = item.find("description")
        claim_text = clean_excerpt(desc_el.text) if desc_el is not None and desc_el.text else None

        creator = None
        dc_creator = item.find(_DC_CREATOR)
        if dc_creator is not None and dc_creator.text:
            creator = dc_creator.text.strip()

        items.append(
            RawFeedItem(
                title=title, url=url, published_at=published_at,
                categories=categories, claim_text=claim_text, author=creator,
            )
        )

    return RawFeedParseResult(success=True, items=items)
