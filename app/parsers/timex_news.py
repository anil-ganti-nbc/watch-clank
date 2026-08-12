"""Parse Timex's official Atom blog feed (timex.com/blogs/the-timex-blog.atom).

Confirmed live 2026-08-11: this is genuinely a product-announcement feed,
not a generic lifestyle blog -- real recent entries include "New For Fall:
The E Line Returns In 30mm And 38mm", "Q Timex Marbella: A Bolder Sense Of
Time", and a real collaboration ("Todd Snyder x Timex Marlin Mesh"). It is
Timex's own first-party Shopify blog syndication (standard, publicly
documented Shopify feature, not a workaround), functionally the closest
Timex equivalent to Casio's/Citizen's/Seiko's official news feeds.

Mirrors app/parsers/seiko_news.py's NewsParseResult shape so it plugs into
PipelineService.process_news_announcement unchanged. Unlike Casio/Citizen/
Seiko's collectors (index page + one detail-page fetch per item), the Atom
feed already carries each entry's full content -- no second HTTP request
per article, same "no extra fetch per item" discipline as the product-json
collectors.

Deliberately does NOT store full article bodies -- only title, canonical
URL, publication timestamp, and a short plain-text excerpt from the
entry's own content (already present in the single feed fetch).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PARSER_ID = "timex_news_atom"
PARSER_VERSION = "0.1.0"

# Timex SKU pattern, e.g. "TW6A01000", "TW2V73700" -- "TW" + alnum body.
# Real blog prose rarely spells out the exact SKU (marketing copy uses
# product/collection names, not part numbers) -- an honest expectation,
# not a parser bug, same as Plus9Time's mostly-empty extraction (Sprint 7).
MODEL_RE = re.compile(r"\b(TW[0-9A-Z]{6,10})\b")

_TAG_STRIP_RE = re.compile(r"<[^>]+>")


@dataclass
class ExtractedReference:
    raw: str
    normalized: str
    location: str
    confidence: float
    warning: str | None = None


@dataclass
class NewsParseResult:
    success: bool
    title: str | None = None
    publication_date: str | None = None
    announcement_url: str | None = None
    collection: str | None = None
    model_references: list[ExtractedReference] = field(default_factory=list)
    product_urls: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    body_excerpt: str | None = None
    source_language: str = "en"
    is_watch_related: bool = True
    parser_warnings: list[str] = field(default_factory=list)
    error: str | None = None


def _clean_excerpt(html: str, max_len: int = 400) -> str:
    text = _TAG_STRIP_RE.sub(" ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def parse_timex_news_entry(payload: bytes | str, *, source_url: str = "") -> NewsParseResult:
    """payload is a single JSON-encoded feed-entry dict (see
    app/collectors/timex_news.py) -- not the raw Atom feed itself."""
    import json

    raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
    if not raw or not raw.strip():
        return NewsParseResult(success=False, error="empty payload", announcement_url=source_url)
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError as exc:
        return NewsParseResult(success=False, error=f"invalid JSON: {exc}", announcement_url=source_url)

    title = entry.get("title")
    if not title:
        return NewsParseResult(success=False, error="no title", announcement_url=source_url)

    result = NewsParseResult(success=True, announcement_url=source_url, title=title)
    result.publication_date = entry.get("published")

    content = entry.get("content") or ""
    result.body_excerpt = _clean_excerpt(content) if content else None

    blob = f"{title} {content}"
    seen_refs: set[str] = set()
    for m in MODEL_RE.finditer(blob):
        raw_ref = m.group(1)
        norm = raw_ref.upper()
        if norm in seen_refs:
            continue
        seen_refs.add(norm)
        result.model_references.append(
            ExtractedReference(raw=raw_ref, normalized=norm, location="content_text", confidence=0.75)
        )

    if not result.model_references:
        result.parser_warnings.append("no_model_reference_extracted")

    return result
