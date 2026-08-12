"""Citizen Germany product-page parser.

The public German Citizen store emits a schema.org Product JSON-LD block on
each canonical product page. It is first-party, server-rendered evidence for
the exact reference, EUR price, and an explicit stock state.
"""

from __future__ import annotations

import json
import re

from app.normalization.references import safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

PARSER_ID = "citizen_de_products_jsonld"
PARSER_VERSION = "0.1.0"

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _product_jsonld(html: str) -> dict | None:
    for raw in _JSONLD_RE.findall(html):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        values = data if isinstance(data, list) else [data]
        for value in values:
            if isinstance(value, dict) and value.get("@type") == "Product":
                return value
    return None


def _availability(value: str | None) -> str | None:
    if not value:
        return None
    state = value.rsplit("/", 1)[-1].upper()
    if state == "INSTOCK":
        return "AVAILABLE"
    if state in {"OUTOFSTOCK", "SOLDOUT"}:
        return "SOLD_OUT"
    return None


def parse_citizen_de_product_html(html: str | bytes, *, source_url: str = "") -> ParseResult:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    if not html or not html.strip():
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="empty html")

    product = _product_jsonld(html)
    if not product or not product.get("sku"):
        return ParseResult(
            success=False,
            parser_id=PARSER_ID,
            parser_version=PARSER_VERSION,
            error="Product JSON-LD with SKU not found",
        )

    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    offers = offers if isinstance(offers, dict) else {}
    raw_price = offers.get("price")
    try:
        price = float(raw_price) if raw_price is not None else None
    except (TypeError, ValueError):
        price = None
    currency = offers.get("priceCurrency") if price is not None else None
    availability = _availability(offers.get("availability"))

    confidence = {"reference": 1.0}
    if price is not None and currency:
        confidence["price"] = 0.98
    if availability:
        confidence["availability_status"] = 0.98
    warnings: list[str] = []
    if price is None or not currency:
        warnings.append("no_price_in_product_jsonld")
    if availability is None:
        warnings.append("no_supported_availability_in_product_jsonld")

    watch = ParsedWatch(
        reference_raw=str(product["sku"]),
        manufacturer="Citizen",
        brand="Citizen",
        model_name=product.get("name"),
        price=price,
        currency=currency,
        availability_status=availability,
        extra_specs={
            key: value
            for key, value in {"gtin13": product.get("gtin13"), "category": product.get("category")}.items()
            if value
        },
        field_confidence=confidence,
        parser_warnings=warnings,
        overall_confidence=safe_overall_confidence(confidence),
        source_url=source_url or offers.get("url") or "",
    )
    return ParseResult(success=True, parser_id=PARSER_ID, parser_version=PARSER_VERSION, watches=[watch])
