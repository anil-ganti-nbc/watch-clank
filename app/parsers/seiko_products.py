"""Seiko USA product parser (seikousa.com).

seikousa.com is operated by Seiko Watch of America LLC (confirmed live via
its own Terms of Service page, 2026-08-11: "This website is operated by
Seiko Watch of America LLC") — Seiko's official US importer/distributor
entity, the same corporate relationship Citizen Watch America has to
citizenwatch.com. First-party, not a third-party retailer, despite its
marketing copy ("Shop authentic Seiko watches") reading ambiguously at
first glance — verified before use, not assumed.

The store runs on Shopify, which by default publicly exposes
`/collections/all/products.json` — a standard, publicly-documented Shopify
storefront feature (not an API being reverse-engineered or a protection
being bypassed; any Shopify store works this way unless deliberately
disabled). One HTTP fetch returns full product records: title, handle,
vendor, product_type, tags, and per-variant sku/price/availability.

Unlike Citizen (one HTML page per product), this parser takes a single
product's JSON object as payload — the collector fetches the listing once
and hands each product dict to this parser individually, avoiding a
separate request per product (more conservative on the source).

Currency: this store's active currency is confirmed "USD" via its own
`Shopify.currency = {"active":"USD",...}` page state (2026-08-11), so it is
hardcoded here as a verified constant, not guessed — see SEIKO_USA_CURRENCY.
"""

from __future__ import annotations

import json

from app.normalization.references import safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

PARSER_ID = "seiko_products_json"
PARSER_VERSION = "0.1.0"

SEIKO_USA_CURRENCY = "USD"


def parse_seiko_product_json(payload: bytes | str | dict, *, source_url: str = "") -> ParseResult:
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

    if data.get("product_type") != "Wrist Watches":
        return ParseResult(
            success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION,
            error=f"not a wrist watch product (product_type={data.get('product_type')!r})",
        )

    variants = data.get("variants") or []
    if not variants:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="no variants")

    reference_raw = variants[0].get("sku") or data.get("title")
    if not reference_raw:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="no sku/title")

    field_confidence: dict[str, float] = {"reference": 0.9}  # SKU, not a dedicated model-number field
    price_str = variants[0].get("price")
    price = float(price_str) if price_str not in (None, "") else None
    if price is not None:
        field_confidence["price"] = 0.85

    available = variants[0].get("available")
    availability_status = None
    if isinstance(available, bool):
        availability_status = "AVAILABLE" if available else "SOLD_OUT"
        field_confidence["availability_status"] = 0.9

    is_limited = "limited" in " ".join(data.get("tags") or []).lower() or "limited" in (data.get("title") or "").lower()

    watch = ParsedWatch(
        reference_raw=str(reference_raw),
        manufacturer="Seiko",
        brand="Seiko",
        collection=None,
        model_name=data.get("title"),
        limited_edition=is_limited or None,
        price=price,
        currency=SEIKO_USA_CURRENCY if price is not None else None,
        availability_status=availability_status,
        extra_specs={"tags": data.get("tags")} if data.get("tags") else {},
        field_confidence=field_confidence,
        parser_warnings=[] if price is not None else ["no_price_in_source"],
        overall_confidence=safe_overall_confidence(field_confidence),
        source_url=source_url,
    )
    return ParseResult(success=True, parser_id=PARSER_ID, parser_version=PARSER_VERSION, watches=[watch])
