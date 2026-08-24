"""Parser for the sitemap-delta collector family.

Mirrors parse_casio_uk_sitemap_item: a sitemap item carries only a
canonical URL-derived reference and a `<lastmod>` timestamp -- never price,
currency, or availability. Every ParsedWatch from this parser has those
three fields as None, deliberately, rather than guessed.
"""

from __future__ import annotations

import json

from app.normalization.references import safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

PARSER_ID = "sitemap_family_item"
PARSER_VERSION = "0.1.0"


def parse_sitemap_family_item(payload: bytes | str | dict, *, source_url: str = "") -> ParseResult:
    if isinstance(payload, dict):
        data = payload
    else:
        raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        if not raw or not raw.strip():
            return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="empty payload")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error=f"invalid JSON: {exc}")

    reference_raw = data.get("reference")
    if not reference_raw:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="no reference")

    watch = ParsedWatch(
        reference_raw=str(reference_raw),
        manufacturer=data.get("manufacturer", "Unknown"),
        brand=data.get("manufacturer", "Unknown"),
        extra_specs={"lastmod": data.get("lastmod")} if data.get("lastmod") else {},
        field_confidence={"reference": 0.7},  # URL-derived, not a labeled model-number field -- see module docstring
        parser_warnings=["no_price_availability_data_source_is_sitemap_only"],
        overall_confidence=safe_overall_confidence({"reference": 0.7}),
        source_url=source_url,
    )
    return ParseResult(success=True, parser_id=PARSER_ID, parser_version=PARSER_VERSION, watches=[watch])
