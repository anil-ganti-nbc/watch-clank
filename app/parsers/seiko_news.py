"""Parse Seiko Group Corporation news announcement pages (seiko.co.jp).

Mirrors app/parsers/casio_news.py's NewsParseResult/ExtractedReference shape.

Known limitation (documented, not silently ignored): seiko.co.jp/en/news/ is
Seiko Group Corporation's *corporate* press feed (covers Seiko Watch, Seiko
Clock, Seiko Instruments, cultural sponsorships, financial results, etc.),
not a watch-only feed. Discovery-time topic filtering (see
app/collectors/seiko_news.py::is_watch_announcement) is required and is
conservative by design — it will under-discover before it over-discovers.

The brand's actual watch-only news feed (seikowatches.com/global-en/news) is
a JS-rendered SPA backed by a REST API (`/v3/api/`) that was not reverse
engineered in this pass — see HANDOFF.md for that as a documented gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

PARSER_ID = "seiko_news"
PARSER_VERSION = "0.1.0"

# Seiko reference format, e.g. SPB123, SNE123, SSC123, SBDX, SLA
MODEL_RE = re.compile(r"\b(S[A-Z]{2}[0-9]{3}[A-Z0-9]{0,3})\b")

KNOWN_COLLECTIONS = (
    "PROSPEX", "PRESAGE", "ASTRON", "KING SEIKO", "GRAND SEIKO",
    "5 SPORTS", "SEIKO 5", "LUKIA", "CREDOR",
)


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


def _guess_collection(title: str) -> str | None:
    t = title.upper()
    for name in KNOWN_COLLECTIONS:
        if name in t:
            return name.title()
    return None


def parse_seiko_news_html(html: str | bytes, *, source_url: str = "") -> NewsParseResult:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    if not html or not html.strip():
        return NewsParseResult(success=False, error="empty html", announcement_url=source_url)

    tree = HTMLParser(html)
    result = NewsParseResult(success=True, announcement_url=source_url)

    h1 = tree.css_first("h1")
    result.title = h1.text(strip=True) if h1 else None
    if not result.title:
        title_tag = tree.css_first("title")
        if title_tag:
            result.title = (title_tag.text(strip=True) or "").split("|")[0].strip()

    date_el = tree.css_first("time") or tree.css_first(".c-newsList__date")
    if date_el:
        result.publication_date = date_el.attributes.get("datetime") or date_el.text(strip=True)

    og_img = tree.css_first('meta[property="og:image"]')
    if og_img and og_img.attributes.get("content"):
        result.image_urls.append(og_img.attributes["content"])

    body = tree.body.text(separator=" ") if tree.body else ""
    result.body_excerpt = body[:1500] if body else None

    seen_refs: set[str] = set()
    for m in MODEL_RE.finditer(body):
        raw = m.group(1)
        norm = raw.upper()
        if norm in seen_refs:
            continue
        seen_refs.add(norm)
        result.model_references.append(
            ExtractedReference(raw=raw, normalized=norm, location="body_text", confidence=0.75)
        )

    result.collection = _guess_collection(result.title or "")

    if not result.model_references:
        result.parser_warnings.append("no_model_reference_extracted")

    return result
