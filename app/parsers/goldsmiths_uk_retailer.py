"""Parser for Goldsmiths UK Citizen product pages."""

from __future__ import annotations

import json
import re
from typing import Any

from app.normalization.references import safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

PARSER_ID = "goldsmiths_uk_retailer_ng_state"
PARSER_VERSION = "0.1.0"
EXPECTED_CURRENCY = "GBP"

_NG_STATE_RE = re.compile(
    r"<script[^>]+id=[\"']ng-state[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_JSON_SCRIPT_RE = re.compile(
    r"<script[^>]+type=[\"']application/json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_REFERENCE_RE = re.compile(r"^[A-Za-z]{2}\d{4}[-+_][A-Za-z0-9]{2,}$")


def _walk_product(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        manufacturer = str(value.get("manufacturer") or "").lower()
        if value.get("mpn") and manufacturer == "citizen":
            return value
        for child in value.values():
            found = _walk_product(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _walk_product(child)
            if found:
                return found
    return None


def _extract_product(html: str) -> dict[str, Any] | None:
    blocks = _NG_STATE_RE.findall(html) or _JSON_SCRIPT_RE.findall(html)
    for block in blocks:
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        product = _walk_product(data)
        if product:
            return product
    return None


def _price(product: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
    value = product.get("price")
    if isinstance(value, dict):
        currency = value.get("currencyIso") or value.get("currency")
        # Not `or`: a literal 0 price must not fall through to formattedValue.
        raw_price = value.get("value")
        if raw_price is None:
            raw_price = value.get("formattedValue")
    else:
        currency = product.get("currencyIso") or product.get("currency")
        raw_price = value
    if raw_price in (None, ""):
        return None, None, None
    try:
        parsed = float(str(raw_price).replace(",", "").replace("£", "").strip())
    except (TypeError, ValueError):
        return None, str(currency) if currency else None, "invalid_price"
    return parsed, str(currency).upper() if currency else None, None


def _availability(product: dict[str, Any]) -> str | None:
    status = str(product.get("stockLevelStatus") or product.get("stockStatus") or "").lower()
    if status in {"instock", "in_stock", "available", "lowstock", "low_stock", "limited"}:
        return "AVAILABLE"
    if status in {"outofstock", "out_of_stock", "soldout", "sold_out", "unavailable"}:
        return "SOLD_OUT"
    if product.get("purchasable") is True:
        return "AVAILABLE"
    if product.get("purchasable") is False:
        return "SOLD_OUT"
    return None


def parse_goldsmiths_uk_product_html(html: str | bytes, *, source_url: str = "") -> ParseResult:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    if not html or not html.strip():
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="empty html")

    product = _extract_product(html)
    if not product:
        return ParseResult(
            success=False,
            parser_id=PARSER_ID,
            parser_version=PARSER_VERSION,
            error="Citizen product object not found in Angular state",
        )

    reference = str(product.get("mpn") or "").strip()
    if not _REFERENCE_RE.fullmatch(reference):
        return ParseResult(
            success=False,
            parser_id=PARSER_ID,
            parser_version=PARSER_VERSION,
            error=f"invalid Citizen MPN shape: {reference!r}",
        )

    price, currency, price_error = _price(product)
    if price_error:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error=price_error)
    if price is not None and currency != EXPECTED_CURRENCY:
        if currency is None:
            error = "price present but currency missing for GB retailer evidence"
        else:
            error = f"unexpected currency for GB retailer evidence: {currency!r}"
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error=error)

    availability = _availability(product)
    confidence: dict[str, float] = {"reference": 1.0}
    warnings = ["third_party_retailer_evidence"]
    if price is not None:
        confidence["price"] = 0.95
    else:
        warnings.append("no_price_in_source")
    if availability:
        confidence["availability_status"] = 0.9
    else:
        warnings.append("no_supported_availability_in_source")

    watch = ParsedWatch(
        reference_raw=reference,
        manufacturer="Citizen",
        brand="Citizen",
        model_name=product.get("name") or product.get("baseProductName"),
        price=price,
        currency=currency,
        availability_status=availability,
        extra_specs={
            "retailer": "Goldsmiths",
            "retailer_mpn_source": "goldsmiths_ng_state",
            "purchasable": product.get("purchasable"),
            "stock_level_status": product.get("stockLevelStatus"),
        },
        field_confidence=confidence,
        parser_warnings=warnings,
        overall_confidence=safe_overall_confidence(confidence),
        source_url=source_url,
    )
    return ParseResult(success=True, parser_id=PARSER_ID, parser_version=PARSER_VERSION, watches=[watch])
