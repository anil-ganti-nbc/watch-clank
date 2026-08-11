"""Parse Citizen official product-news announcement pages.

Mirrors the structure of app/parsers/casio_news.py exactly (same
NewsParseResult/ExtractedReference shape) so it plugs into
PipelineService.process_news_announcement via its parse_fn parameter without
any pipeline changes beyond what generalization already added.

Source: https://www.citizenwatch-global.com/news/ — this page is already a
pure watch-product news feed (no corporate/financial noise observed), unlike
Casio's or Seiko's mixed corporate feeds, so no topic filtering is applied
at the parser level; filtering happens (if ever needed) at discovery time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

PARSER_ID = "citizen_news"
PARSER_VERSION = "0.1.0"

# Citizen reference format observed in live announcement bodies, e.g.
# "CC4107-80H". Two letters (movement/line family code) + 4 digits + dash +
# 2-4 alphanumeric case/dial code. Conservative: requires the digit block.
MODEL_RE = re.compile(r"\b([A-Z]{2}[0-9]{4}-[0-9A-Z]{2,4})\b")

KNOWN_COLLECTIONS = (
    "ATTESA", "PROMASTER", "TSUYOSA", "RAINELL", "EXCEED", "XC",
    "CITIZEN L", "ECO-DRIVE", "SATELLITE WAVE",
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
            return name.title() if name != "XC" else "XC"
    return None


def parse_citizen_news_html(html: str | bytes, *, source_url: str = "") -> NewsParseResult:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    if not html or not html.strip():
        return NewsParseResult(success=False, error="empty html", announcement_url=source_url)

    tree = HTMLParser(html)
    result = NewsParseResult(success=True, announcement_url=source_url)

    title_el = tree.css_first(".news_article_title")
    if title_el:
        result.title = title_el.text(separator=" ", strip=True)
    if not result.title:
        title_tag = tree.css_first("title")
        if title_tag:
            result.title = (title_tag.text(strip=True) or "").split("|")[0].strip()

    date_el = tree.css_first(".news_date")
    if date_el:
        result.publication_date = date_el.text(strip=True)

    og_img = tree.css_first('meta[property="og:image"]')
    if og_img and og_img.attributes.get("content"):
        result.image_urls.append(og_img.attributes["content"])
    for img in tree.css(".news_article img, #bar img"):
        src = img.attributes.get("src")
        if src and src not in result.image_urls:
            result.image_urls.append(src)

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
            ExtractedReference(
                raw=raw,
                normalized=norm,
                location="body_text",
                confidence=0.8,
            )
        )

    result.collection = _guess_collection(result.title or "")

    if not result.model_references:
        result.parser_warnings.append("no_model_reference_extracted")

    return result
