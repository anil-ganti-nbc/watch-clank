"""Parser for CASIOBLOG's RSS feed (casioblog.com/en/feed/).

CASIOBLOG is a specialist enthusiast blog with a demonstrated history of
early/accurate Casio watch leaks and rumors (confirmed via real
Notebookcheck article citations — see docs/specialist_source_research.md).
It is explicitly Layer B (early-warning), never treated as first-party.

Deliberately does NOT store full article bodies — only title, canonical
URL, publication timestamp, a short plain-text excerpt (from the RSS
<description>, which WordPress already truncates), category tags, and any
Casio reference candidates found in the title/categories. This is a lead,
not a copy of the article.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

PARSER_ID = "casioblog_rss"
PARSER_VERSION = "0.1.0"

# Casio reference patterns, reused conservatively from the family prefixes
# already validated in app/parsers/casio_news.py — title/category text only.
_REF_RE = re.compile(
    r"\b((?:GA|GW|GM|GMW|GBD|GBDH|GWR|GBA|GMA|GMD|GME|GSH|GST|GTS|DW|AW|AE|"
    r"EF|EFB|EFV|EFK|EQB|EQW|EQS|ECB|OCW|OCIS|PRG|PRW|PRT|"
    r"MRG|MR-G|MTG|MT-G|BSA|BGA|MSG|SHB|SHE)[-]?[A-Z0-9]{2,20})\b",
    re.IGNORECASE,
)
_RUMOR_RE = re.compile(r"\[\s*rumors?\s*\]", re.IGNORECASE)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


@dataclass
class SpecialistLeadCandidate:
    title: str
    url: str
    published_at: str | None  # ISO string, or None if unparseable
    categories: list[str]
    claim_text: str | None
    reference_candidates: list[str]
    is_rumor_tagged: bool
    author: str | None = None


@dataclass
class FeedParseResult:
    success: bool
    items: list[SpecialistLeadCandidate] = field(default_factory=list)
    error: str | None = None


def _clean_excerpt(html: str, max_len: int = 400) -> str:
    text = _TAG_STRIP_RE.sub(" ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    # Drop the boilerplate "Read more" / cross-post footer WordPress appends.
    text = re.split(r"Read more|Сообщение\s", text)[0].strip()
    return text[:max_len]


def parse_casioblog_feed(xml_bytes: bytes | str, *, max_items: int | None = 20) -> FeedParseResult:
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("utf-8")
    if not xml_bytes or not xml_bytes.strip():
        return FeedParseResult(success=False, error="empty feed")
    # The real feed has been observed to emit leading whitespace before the
    # XML declaration, which ElementTree rejects per spec strictness even
    # though every real browser/feed reader tolerates it. Strip leading
    # whitespace rather than fail on a harmless server quirk.
    xml_bytes = xml_bytes.lstrip()
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        return FeedParseResult(success=False, error=f"XML parse error: {exc}")

    channel = root.find("channel")
    if channel is None:
        return FeedParseResult(success=False, error="no <channel> element — unexpected feed shape")

    items: list[SpecialistLeadCandidate] = []
    raw_items = channel.findall("item")
    if max_items is not None:
        raw_items = raw_items[:max_items]

    for item in raw_items:
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None or not (link_el.text or "").strip():
            continue  # cannot identify this item without a canonical URL — skip, don't guess
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
        claim_text = _clean_excerpt(desc_el.text) if desc_el is not None and desc_el.text else None

        creator = None
        dc_creator = item.find("{http://purl.org/dc/elements/1.1/}creator")
        if dc_creator is not None and dc_creator.text:
            creator = dc_creator.text.strip()

        blob = f"{title} {' '.join(categories)}"
        refs = sorted({m.group(1).upper() for m in _REF_RE.finditer(blob)})

        items.append(
            SpecialistLeadCandidate(
                title=title,
                url=url,
                published_at=published_at,
                categories=categories,
                claim_text=claim_text,
                reference_candidates=refs,
                is_rumor_tagged=bool(_RUMOR_RE.search(title)),
                author=creator,
            )
        )

    return FeedParseResult(success=True, items=items)
