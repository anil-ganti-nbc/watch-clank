"""Parse Casio official product-news announcement pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

PARSER_ID = "casio_news"
PARSER_VERSION = "0.1.0"

# Conservative model reference: family letters + digits, optional colorway suffix
MODEL_RE = re.compile(
    r"\b("
    r"(?:GA|GW|GM|GMW|GBD|GBDH|GWR|GBA|GMA|GMD|GME|GSH|GST|GTS|"
    r"DW|AW|AE|AWG|AWG-M|"
    r"EF|EFB|EFV|EFK|EQB|EQW|EQS|ECB|"
    r"OCW|OCIS|"
    r"PRG|PRW|PRT|"
    r"MRG|MR-G|MTG|MT-G|"
    r"BSA|BGA|MSG|SHB|SHE"
    r")[-]?[A-Z0-9]{2,20}"
    r")\b",
    re.IGNORECASE,
)

# Reject obvious non-models
REJECT_REFS = re.compile(r"^(ISO|HTTP|HTML|CASIO|G-SHOCK|EDIFICE|OCEANUS|PROTREK)$", re.I)

PRODUCT_LINK_RE = re.compile(r"/watches/.+/product\.([A-Z0-9\-]+)/?", re.I)


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


def _norm_ref(raw: str) -> str:
    return raw.strip().upper().replace("_", "-")


def _guess_collection(title: str, refs: list[str]) -> str | None:
    t = title.upper()
    if "G-SHOCK" in t or "GSHOCK" in t or "MR-G" in t or "MRG" in t or "MT-G" in t or "MTG" in t:
        if "MR-G" in t or "MRG" in t:
            return "MR-G"
        if "MT-G" in t or "MTG" in t:
            return "MT-G"
        return "G-SHOCK"
    if "EDIFICE" in t:
        return "EDIFICE"
    if "OCEANUS" in t:
        return "OCEANUS"
    if "PRO TREK" in t or "PROTREK" in t:
        return "PRO TREK"
    if "BABY-G" in t:
        return "BABY-G"
    # from refs
    for r in refs:
        u = r.upper()
        if u.startswith(("GA", "GW", "GM", "DW", "GWR", "GBD", "GBA")):
            return "G-SHOCK"
        if u.startswith(("EF", "EQ")):
            return "EDIFICE"
        if u.startswith("OC"):
            return "OCEANUS"
        if u.startswith("PR"):
            return "PRO TREK"
        if u.startswith("MR"):
            return "MR-G"
        if u.startswith("MT"):
            return "MT-G"
    return None


def parse_casio_news_html(html: str | bytes, *, source_url: str = "") -> NewsParseResult:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    if not html or not html.strip():
        return NewsParseResult(success=False, error="empty html", announcement_url=source_url)

    tree = HTMLParser(html)
    result = NewsParseResult(success=True, announcement_url=source_url)

    h1 = tree.css_first("h1")
    result.title = h1.text(strip=True) if h1 else None
    if not result.title:
        og = tree.css_first('meta[property="og:title"]')
        if og:
            result.title = (og.attributes.get("content") or "").split("|")[0].strip()

    # date: time element or visible date near title
    time_el = tree.css_first("time")
    if time_el:
        result.publication_date = time_el.attributes.get("datetime") or time_el.text(strip=True)
    if not result.publication_date:
        # try meta
        for sel in ['meta[name="pubdate"]', 'meta[property="article:published_time"]']:
            m = tree.css_first(sel)
            if m and m.attributes.get("content"):
                result.publication_date = m.attributes.get("content")
                break

    og_img = tree.css_first('meta[property="og:image"]')
    if og_img and og_img.attributes.get("content"):
        result.image_urls.append(og_img.attributes["content"])

    # Product specification links (high confidence)
    seen_refs: set[str] = set()
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        m = PRODUCT_LINK_RE.search(href)
        if m:
            full = urljoin(source_url or "https://www.casio.com/", href)
            if full not in result.product_urls:
                result.product_urls.append(full)
            raw = m.group(1)
            norm = _norm_ref(raw)
            if norm not in seen_refs and not REJECT_REFS.match(norm):
                seen_refs.add(norm)
                result.model_references.append(
                    ExtractedReference(
                        raw=raw,
                        normalized=norm,
                        location="product_link",
                        confidence=0.95,
                    )
                )

    # Headings and bold labels
    for sel in ["h1", "h2", "h3", "figcaption", "caption"]:
        for node in tree.css(sel):
            text = node.text(strip=True) or ""
            for m in MODEL_RE.finditer(text):
                raw = m.group(1)
                norm = _norm_ref(raw)
                if norm in seen_refs or REJECT_REFS.match(norm) or len(norm) < 5:
                    continue
                # require digit
                if not re.search(r"\d", norm):
                    continue
                seen_refs.add(norm)
                result.model_references.append(
                    ExtractedReference(
                        raw=raw,
                        normalized=norm,
                        location=sel,
                        confidence=0.75,
                    )
                )

    # Image alt text
    for img in tree.css("img"):
        alt = img.attributes.get("alt") or ""
        src = img.attributes.get("src") or img.attributes.get("data-src")
        if (
            src
            and src.startswith("http")
            and src not in result.image_urls
            and any(k in src.lower() for k in ("efk", "gshock", "g-shock", "edifice", "release", "press"))
        ):
            result.image_urls.append(src)
        for m in MODEL_RE.finditer(alt):
            raw = m.group(1)
            norm = _norm_ref(raw)
            if norm in seen_refs or not re.search(r"\d", norm) or REJECT_REFS.match(norm):
                continue
            seen_refs.add(norm)
            result.model_references.append(
                ExtractedReference(
                    raw=raw,
                    normalized=norm,
                    location="img_alt",
                    confidence=0.6,
                    warning="from alt text",
                )
            )

    body = tree.body.text(separator=" ") if tree.body else ""
    result.body_excerpt = body[:1500] if body else None
    refs_norm = [r.normalized for r in result.model_references]
    result.collection = _guess_collection(result.title or "", refs_norm)

    if not result.model_references:
        result.parser_warnings.append("no_model_reference_extracted")

    return result
