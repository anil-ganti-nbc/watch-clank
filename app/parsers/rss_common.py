"""Shared RSS2.0/Atom parsing core for specialist-source feeds.

Extracted from app/parsers/casioblog.py's proven implementation (kept
untouched to avoid any risk to its existing tests) so new sources
(G-Central, Plus9Time) don't duplicate the same XML-shape handling.
Deliberately reads only title/link/pubDate/category/description --
NEVER content:encoded or any other full-body field. This is the boundary
that keeps every specialist collector from copying full article bodies.

parse_atom_feed (added for Great G-Shock World, see
ai/handoff/SPECIALIST_SOURCE_GREAT_G_SHOCK_WORLD.md) is a second, general
entrypoint for Atom-syndicated sources -- livedoor Blog (the platform
Great G-Shock World and several other Japanese watch blogs run on) does
not expose an RSS 2.0 <channel><item> feed at all, only RSS 1.0/RDF and
Atom. Atom was chosen over RDF here as the more standard, more likely to
recur shape for future non-English sources. Same discipline as
parse_rss_feed: title/link/published-time/category/summary only, never
<content>.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_DC_SUBJECT = "{http://purl.org/dc/elements/1.1/}subject"


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


def parse_atom_feed(xml_bytes: bytes | str, *, max_items: int | None = 20) -> RawFeedParseResult:
    """Generic, defensive Atom (0.3 or 1.0) <feed><entry> parse. Namespace
    is read from the root element itself rather than hardcoded, so this
    works for either Atom version without guessing which one a given
    source uses. Never touches <content>. Skips any entry missing a title
    or an alternate-HTML link rather than guessing.

    published_at prefers <issued>/<published> (the entry's true first-
    publish time) over <modified>/<updated> (last-edited time) --
    deliberately not falling back to the latter, matching this codebase's
    existing discipline of never treating a different timestamp concept as
    if it were publication time (see app/services/freshness.py)."""
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("utf-8")
    if not xml_bytes or not xml_bytes.strip():
        return RawFeedParseResult(success=False, error="empty feed")
    xml_bytes = xml_bytes.lstrip()
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        return RawFeedParseResult(success=False, error=f"XML parse error: {exc}")

    if "}" not in root.tag or not root.tag.endswith("}feed"):
        return RawFeedParseResult(success=False, error=f"not an Atom <feed> root (got {root.tag!r})")
    ns = root.tag.split("}")[0][1:]  # the URI inside {...}

    def q(tag: str) -> str:
        return f"{{{ns}}}{tag}"

    raw_entries = root.findall(q("entry"))
    if max_items is not None:
        raw_entries = raw_entries[:max_items]

    items: list[RawFeedItem] = []
    for entry in raw_entries:
        title_el = entry.find(q("title"))
        if title_el is None or not (title_el.text or "").strip():
            continue
        url = None
        for link_el in entry.findall(q("link")):
            rel = link_el.get("rel") or "alternate"
            if rel == "alternate" and link_el.get("href"):
                url = link_el.get("href").strip()
                break
        if not url:
            continue
        title = title_el.text.strip()

        published_at = None
        for date_tag in ("issued", "published"):
            date_el = entry.find(q(date_tag))
            if date_el is not None and date_el.text:
                try:
                    published_at = datetime.fromisoformat(date_el.text.strip()).isoformat()
                except ValueError:
                    published_at = None
                break

        categories = [c.get("term") for c in entry.findall(q("category")) if c.get("term")]
        dc_subject = entry.find(_DC_SUBJECT)
        if dc_subject is not None and dc_subject.text:
            categories.append(dc_subject.text.strip())

        summary_el = entry.find(q("summary"))
        claim_text = clean_excerpt(summary_el.text, 400) if summary_el is not None and summary_el.text else None

        creator = None
        author_el = entry.find(q("author"))
        if author_el is not None:
            name_el = author_el.find(q("name"))
            if name_el is not None and name_el.text:
                creator = name_el.text.strip()

        items.append(
            RawFeedItem(
                title=title, url=url, published_at=published_at,
                categories=categories, claim_text=claim_text, author=creator,
            )
        )

    return RawFeedParseResult(success=True, items=items)
