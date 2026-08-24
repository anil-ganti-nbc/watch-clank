"""Timex UK product parser (timex.co.uk, Shopify storefront).

Same record shape as Timex US (parse_timex_products) with one material
difference: currency is GBP (UK storefront), verified against the store's
own Shopify currency state during live recon 2026-08-25.

Inherited honesty rules carried over unchanged:
- published_at is preserved as provenance in extra_specs only;
- no freshness/launch interpretation happens here (pipeline's job);
- price is taken from the variant JSON as-is; availability from the
  variant `available` flag; anything absent stays None.
"""

from __future__ import annotations

import json

from app.normalization.references import safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

PARSER_ID = "timex_uk_products_json"
PARSER_VERSION = "0.1.0"

TIMEX_UK_CURRENCY = "GBP"
TIMEX_PRODUCT_TYPE = "Watch"


def parse_timex_uk_product_json(payload: bytes | str | dict, *, source_url: str = "") -> ParseResult:
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

    if data.get("product_type") != TIMEX_PRODUCT_TYPE:
        return ParseResult(
            success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION,
            error=f"not a watch product (product_type={data.get('product_type')!r})",
        )

    variants = data.get("variants") or []
    if not variants:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="no variants")

    reference_raw = variants[0].get("sku") or data.get("title")
    if not reference_raw:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="no sku/title")

    field_confidence: dict[str, float] = {"reference": 0.9}
    price_str = variants[0].get("price")
    price = float(price_str) if price_str not in (None, "") else None
    if price is not None:
        field_confidence["price"] = 0.85

    available = variants[0].get("available")
    availability_status = None
    if isinstance(available, bool):
        availability_status = "AVAILABLE" if available else "SOLD_OUT"
        field_confidence["availability_status"] = 0.9

    tags = data.get("tags") or []
    tags_blob = " ".join(tags).lower()
    is_limited = "limited" in tags_blob or "limited" in (data.get("title") or "").lower()

    ygroup = next((t for t in tags if t.startswith("YGroup_")), None)

    published_at = data.get("published_at")

    watch = ParsedWatch(
        reference_raw=str(reference_raw),
        manufacturer="Timex",
        brand="Timex",
        collection=ygroup,
        model_name=data.get("title"),
        limited_edition=is_limited or None,
        price=price,
        currency=TIMEX_UK_CURRENCY if price is not None else None,
        availability_status=availability_status,
        extra_specs={"tags": tags, "published_at": published_at} if (tags or published_at) else {},
        field_confidence=field_confidence,
        parser_warnings=[] if price is not None else ["no_price_in_source"],
        overall_confidence=safe_overall_confidence(field_confidence),
        source_url=source_url,
    )
    return ParseResult(success=True, parser_id=PARSER_ID, parser_version=PARSER_VERSION, watches=[watch])
