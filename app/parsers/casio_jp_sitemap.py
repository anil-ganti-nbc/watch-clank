"""Parser for the Casio Japan sitemap-delta collector.

See app/collectors/casio_jp_sitemap.py for the reconnaissance and the honest
limitation this parser shares with casio_uk_sitemap: the sitemap carries
only a canonical URL, a reference (extracted from the URL path, e.g.
`product.GBA-950-2A/` -> `GBA-950-2A`), and a `<lastmod>` timestamp -- never
price, currency, or availability. The lastmod is stored in extra_specs as
WEAK recency evidence only; it is deliberately NOT mapped to published_at,
because a sitemap lastmod means "the crawler-visible file changed", not
"this product launched" -- exactly the distinction the 2026-08-19 incident
doc draws for bulk-touchable source timestamps.
"""

from __future__ import annotations

import json

from app.normalization.references import safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

PARSER_ID = "casio_jp_sitemap_item"
PARSER_VERSION = "0.1.0"


def parse_casio_jp_sitemap_item(payload: bytes | str | dict, *, source_url: str = "") -> ParseResult:
    if isinstance(payload, dict):
        data = payload
    else:
        raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        if not raw or not raw.strip():
            return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="empty payload")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return ParseResult(
                success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error=f"invalid JSON: {exc}"
            )

    reference_raw = data.get("reference")
    if not reference_raw:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="no reference")

    extra = {}
    if data.get("lastmod"):
        extra["lastmod"] = data.get("lastmod")

    watch = ParsedWatch(
        reference_raw=str(reference_raw),
        manufacturer="Casio",
        brand="Casio",
        extra_specs=extra,
        field_confidence={"reference": 0.7},  # URL-derived, not a labeled model-number field
        parser_warnings=["no_price_availability_data_source_is_sitemap_only", "jp_region_first_party_surface"],
        overall_confidence=safe_overall_confidence({"reference": 0.7}),
        source_url=source_url,
    )
    return ParseResult(success=True, parser_id=PARSER_ID, parser_version=PARSER_VERSION, watches=[watch])
