"""Citizen US product-page parser (citizenwatch.com).

The page is server-side rendered React (Salesforce Commerce Cloud / Mobify
PWA Kit). The full product record — name, brand, price, promotional price,
inventory/availability, case/movement/dial/band specs, water resistance,
intro date — is embedded verbatim as JSON inside a React Query hydration
`<script>` block server-rendered into the HTML. No client-side JS execution,
no API call, and no anti-bot circumvention is needed: this is the same
static HTML httpx already fetches for every other collector in this
project. Confirmed live 2026-08-11 against citizenwatch.com/us/en/product/*.

Extraction approach: find the `"data":{"brand":"CITIZEN"` anchor and take a
brace-balanced JSON object from there — regex alone is unreliable against
nested JSON, but the exact anchor is stable (observed across every real
product page fetched during investigation).
"""

from __future__ import annotations

import json
import re

from app.core.logging import get_logger
from app.normalization.references import safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

logger = get_logger(__name__)

PARSER_ID = "citizen_products_html"
PARSER_VERSION = "0.1.0"

_ANCHOR_RE = re.compile(r'"data"\s*:\s*\{\s*"brand"\s*:\s*"CITIZEN"')

# c_waterResistance looks like "WR100/10Bar/333ft [...]" — first number after WR.
_WR_RE = re.compile(r"WR\s*(\d+)", re.IGNORECASE)
_LIMITED_RE = re.compile(r"\blimited[\s-]edition\b", re.IGNORECASE)


def _extract_product_json(html: str) -> dict | None:
    m = _ANCHOR_RE.search(html)
    if not m:
        return None
    # Advance to the opening brace of the object (right after "data":)
    start = html.index("{", m.start())
    depth = 0
    i = start
    n = len(html)
    while i < n:
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        return None  # unbalanced — do not guess, fail cleanly
    try:
        return json.loads(html[start : i + 1])
    except json.JSONDecodeError:
        return None


def _availability_from_inventory(inventory: dict | None) -> str | None:
    """Conservative mapping — UNKNOWN (None) unless the source explicitly
    states orderable/stock state. Never guess SOLD_OUT from absence."""
    if not isinstance(inventory, dict) or "orderable" not in inventory:
        return None
    if inventory.get("orderable") is True:
        return "AVAILABLE"
    if inventory.get("orderable") is False:
        return "SOLD_OUT"
    return None


def _water_resistance_m(text: str | None) -> int | None:
    if not text:
        return None
    m = _WR_RE.search(text)
    return int(m.group(1)) if m else None


def parse_citizen_product_html(html: str | bytes, *, source_url: str = "") -> ParseResult:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="ignore")
    if not html or not html.strip():
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="empty html")

    data = _extract_product_json(html)
    if not data:
        return ParseResult(
            success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION,
            error="product JSON block not found or malformed",
        )

    reference_raw = data.get("id")
    if not reference_raw:
        return ParseResult(
            success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION,
            error="no product id in extracted JSON",
        )

    field_confidence: dict[str, float] = {"reference": 1.0}
    name = data.get("name")
    if name:
        field_confidence["model_name"] = 0.95

    price = data.get("price")
    currency = data.get("currency")
    if price is not None and currency:
        field_confidence["price"] = 0.9

    availability_status = _availability_from_inventory(data.get("inventory"))
    if availability_status:
        field_confidence["availability_status"] = 0.9

    case_material = data.get("c_caseMaterial")
    movement = data.get("c_movement")
    water_resistance_m = _water_resistance_m(data.get("c_waterResistance"))
    long_desc = data.get("longDescription") or ""
    is_limited = bool(_LIMITED_RE.search(f"{name or ''} {long_desc}"))
    solar = "eco-drive" in (long_desc + (movement or "") + (data.get("c_movementDescription") or "")).lower() or None

    extra_specs = {
        k: v
        for k, v in {
            "dial_color": data.get("c_dialColor"),
            "band_material": data.get("c_bandMaterial"),
            "crystal_material_detail": data.get("c_crystalMaterial"),
            "case_width_mm": data.get("c_caseWidth"),
            "model_intro_date": data.get("c_modelIntroDate"),
            "market_status": data.get("c_marketStatus"),
            "promotional_price": data.get("c_originalPrice") if data.get("c_originalPrice") != str(price) else None,
        }.items()
        if v is not None
    }

    collection = data.get("c_PDCollection4") or data.get("c_PDCollection3") or data.get("c_PDCollection2")

    watch = ParsedWatch(
        reference_raw=str(reference_raw),
        manufacturer="Citizen",
        brand="Citizen",
        collection=collection,
        model_name=name,
        solar=solar,
        case_material=case_material,
        crystal=data.get("c_crystalMaterial"),
        water_resistance_m=water_resistance_m,
        limited_edition=is_limited or None,
        caliber_or_module=movement,
        price=float(price) if isinstance(price, (int, float)) else None,
        currency=currency,
        availability_status=availability_status,
        extra_specs=extra_specs,
        field_confidence=field_confidence,
        parser_warnings=[] if price is not None else ["no_price_in_source"],
        overall_confidence=safe_overall_confidence(field_confidence),
        source_url=source_url,
    )
    return ParseResult(success=True, parser_id=PARSER_ID, parser_version=PARSER_VERSION, watches=[watch])
