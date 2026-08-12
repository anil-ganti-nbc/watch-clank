"""Deterministic RSS parser for approved multi-brand publications.

Only RSS title, categories, publication date, canonical URL and the shared
short description excerpt enter the candidate.  A reference must match one
of the explicitly supported brand formats; no language-model inference or
article-body scraping is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.parsers.rss_common import parse_rss_feed

PARSER_ID = "specialist_publication_rss"
PARSER_VERSION = "0.1.0"

_BRAND_TERMS = (
    ("Casio", re.compile(r"\b(?:casio|g-shock)\b", re.IGNORECASE)),
    ("Seiko", re.compile(r"\bseiko\b", re.IGNORECASE)),
    ("Citizen", re.compile(r"\bcitizen\b", re.IGNORECASE)),
    ("Timex", re.compile(r"\btimex\b", re.IGNORECASE)),
)
_REFERENCE_PATTERNS: dict[str, re.Pattern[str]] = {
    # Require a digit, preventing ordinary title words from becoming a model.
    "Casio": re.compile(r"\b((?:GA|GW|GM|GMW|GBD|GBDH|GBX|GWR|GBA|GMA|GMD|GME|GSH|GST|EFK|EF|OCW|PRG|PRW|MRG|MTG|BGA|MSG)[-]?(?=[A-Z0-9-]*\d)[A-Z0-9-]{3,24})\b", re.IGNORECASE),
    "Seiko": re.compile(r"\b((?:S[A-Z]{2,3}[0-9]{3,4}|H(?:CC|BC|DB)[A-Z0-9]{3,5}))\b", re.IGNORECASE),
    "Citizen": re.compile(r"\b([A-Z]{2}[0-9]{4}-[0-9A-Z]{2,4})\b", re.IGNORECASE),
    "Timex": re.compile(r"\b(TW[A-Z0-9]{6,})\b", re.IGNORECASE),
}
_LIMITED_RE = re.compile(r"\b(?:limited edition|limited to|limited-run)\b", re.IGNORECASE)
_COLLAB_RE = re.compile(r"\b(?:collaboration|collab| x )\b", re.IGNORECASE)


@dataclass
class PublicationLeadCandidate:
    title: str
    url: str
    published_at: str | None
    claim_text: str | None
    brand: str | None
    reference_candidates: list[str]
    is_limited_edition: bool
    is_collaboration: bool


@dataclass
class PublicationFeedParseResult:
    success: bool
    items: list[PublicationLeadCandidate] = field(default_factory=list)
    error: str | None = None


def _brand_from_blob(blob: str) -> str | None:
    matches = [brand for brand, pattern in _BRAND_TERMS if pattern.search(blob)]
    return matches[0] if len(matches) == 1 else None


def parse_specialist_publication_feed(xml_bytes: bytes | str, *, max_items: int | None = 20) -> PublicationFeedParseResult:
    raw_result = parse_rss_feed(xml_bytes, max_items=max_items)
    if not raw_result.success:
        return PublicationFeedParseResult(success=False, error=raw_result.error)

    items: list[PublicationLeadCandidate] = []
    for raw in raw_result.items:
        blob = f"{raw.title} {' '.join(raw.categories)} {raw.claim_text or ''}"
        brand = _brand_from_blob(blob)
        # A general-publication article outside the four approved brands is
        # irrelevant to Watch Clank and must not become an undifferentiated lead.
        if brand is None:
            continue
        refs = sorted({match.group(1).upper() for match in _REFERENCE_PATTERNS[brand].finditer(blob)})
        items.append(
            PublicationLeadCandidate(
                title=raw.title,
                url=raw.url,
                published_at=raw.published_at,
                claim_text=raw.claim_text,
                brand=brand,
                reference_candidates=refs,
                is_limited_edition=bool(_LIMITED_RE.search(blob)),
                is_collaboration=bool(_COLLAB_RE.search(blob)),
            )
        )
    return PublicationFeedParseResult(success=True, items=items)
