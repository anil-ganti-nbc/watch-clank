"""Seiko Japan official retail-store product parser (store.seikowatches.com).

Reconnaissance (live, 2026-08-14, from the actual Hetzner cloud vantage
point -- see ai/handoff/SEIKO_JP_COLLECTOR.md for the full writeup): this
host returns HTTP 200 for both `/products/{handle}.json` and the standard
Shopify collection listing `/products.json?limit=250&page=N`. It is **not**
geo-blocked from that vantage point, contrary to the working assumption
carried into the prior forensic sprint.

`store.seikowatches.com` is Seiko's own single-purpose Japan watch retail
site (vendor field on every product: "セイコーオンラインストア / Seiko
Online Store") -- unlike seikousa.com/citizenwatch.com, it sells nothing
but watches, so no `product_type` filtering is needed or reliable: real
live sampling found `product_type` is either empty string or "新製品" ("new
product") on every one of 959 real catalogue items, never a dedicated
"Watch" value. Every title observed is the bare reference itself (e.g.
"HBC008J"), and every variant's `sku` already equals the canonical
reference with no suffix garbage to strip (unlike Timex's Shopify
catalogue) -- so `normalize_seiko_reference`'s existing conservative
pass-through policy applies unchanged, no new normalization rule needed.

Unlike the per-product `.json` endpoint (which omits `available` entirely),
the collection-listing `/products.json` response includes a real
`variants[].available` boolean -- confirmed live: HCC011J observed as
`available: false` (genuinely sold out), HBC008J/HBC009J as `true`. This
parser is written against the collection-listing shape, matching
seiko_products.py's existing pattern of "hand each product dict to the
parser individually, no second HTTP request per product."

Currency: this store's prices are Japanese Yen. `variants[].price` is a
bare integer/string with no decimal point in the real capture (e.g.
155100 for HBC008J, matching the Notebookcheck-reported ¥155,100 exactly)
-- hardcoded here as a verified constant, not guessed, same discipline as
SEIKO_USA_CURRENCY.

Preorder signal: real tag `予約購入ボタン` ("reservation/preorder purchase
button") appears on genuinely preorder-stage listings (confirmed present
on the real HBC008J/HBC009J capture, both still-upcoming Alpinist GMT
preorders at capture time) -- surfaced into extra_specs as a boolean fact,
not inferred into availability_status (which stays driven only by the
`available` boolean, per this project's "never guess" policy).
"""

from __future__ import annotations

import json

from app.normalization.references import safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

PARSER_ID = "seiko_jp_products_json"
PARSER_VERSION = "0.1.0"

SEIKO_JP_CURRENCY = "JPY"
_PREORDER_TAG = "予約購入ボタン"


def parse_seiko_jp_product_json(payload: bytes | str | dict, *, source_url: str = "") -> ParseResult:
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

    variants = data.get("variants") or []
    if not variants:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="no variants")

    reference_raw = variants[0].get("sku") or data.get("title")
    if not reference_raw:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="no sku/title")

    field_confidence: dict[str, float] = {"reference": 0.9}
    price_raw = variants[0].get("price")
    price = float(price_raw) if price_raw not in (None, "") else None
    if price is not None:
        field_confidence["price"] = 0.85

    available = variants[0].get("available")
    availability_status = None
    if isinstance(available, bool):
        availability_status = "AVAILABLE" if available else "SOLD_OUT"
        field_confidence["availability_status"] = 0.9

    tags = data.get("tags") or []
    tags_text = " ".join(tags)
    is_limited = "限定" in tags_text or "限定" in (data.get("title") or "")
    is_preorder = _PREORDER_TAG in tags

    watch = ParsedWatch(
        reference_raw=str(reference_raw),
        manufacturer="Seiko",
        brand="Seiko",
        collection=None,
        model_name=data.get("title"),
        limited_edition=is_limited or None,
        price=price,
        currency=SEIKO_JP_CURRENCY if price is not None else None,
        availability_status=availability_status,
        extra_specs=(
            {"tags": tags, "preorder_tag_present": is_preorder, "published_at": data.get("published_at")}
            if (tags or data.get("published_at"))
            else {}
        ),
        field_confidence=field_confidence,
        parser_warnings=[] if price is not None else ["no_price_in_source"],
        overall_confidence=safe_overall_confidence(field_confidence),
        source_url=source_url,
    )
    return ParseResult(success=True, parser_id=PARSER_ID, parser_version=PARSER_VERSION, watches=[watch])
