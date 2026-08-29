"""Parser for operator-attested UK purchasing evidence.

This is intentionally a parser for a small, explicit JSON envelope rather
than a second web collector. It records what an operator saw and when; it
does not promote a candidate or emit an event by itself.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from app.normalization.references import safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

PARSER_ID = "manual_uk_evidence_json"
PARSER_VERSION = "0.1.0"
EXPECTED_CURRENCY = "GBP"
_REFERENCE_RE = re.compile(r"^[A-Za-z]{2}\d{4}[-+_][A-Za-z0-9]{2,}$")
_AVAILABILITY = {"AVAILABLE", "SOLD_OUT", "UNKNOWN"}


def parse_manual_uk_evidence(payload: bytes | str | dict, *, source_url: str = "") -> ParseResult:
    if isinstance(payload, dict):
        data = payload
    else:
        raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            return ParseResult(
                success=False,
                parser_id=PARSER_ID,
                parser_version=PARSER_VERSION,
                error=f"invalid manual evidence JSON: {exc}",
            )
    if not isinstance(data, dict):
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="manual evidence must be an object")

    reference = str(data.get("reference") or "").strip()
    submitter = str(data.get("submitter") or "").strip()
    captured_at = str(data.get("captured_at") or "").strip()
    evidence_url = str(data.get("source_url") or source_url or "").strip()
    if not _REFERENCE_RE.fullmatch(reference):
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error=f"invalid Citizen reference shape: {reference!r}")
    if not submitter or not evidence_url:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="submitter and source_url are required")
    if data.get("operator_confirmed") is not True:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="operator_confirmed must be true")
    try:
        captured = datetime.fromisoformat(captured_at)
    except ValueError:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="captured_at must be ISO 8601")
    if captured.tzinfo is None:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="captured_at must include a timezone")

    raw_price = data.get("price")
    price = None
    if raw_price not in (None, ""):
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="price must be numeric")
        if price < 0:
            return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="price must be non-negative")
    currency = str(data.get("currency") or EXPECTED_CURRENCY).upper()
    if currency != EXPECTED_CURRENCY:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error="manual UK evidence currency must be GBP")

    availability = str(data.get("availability") or "UNKNOWN").upper()
    if availability not in _AVAILABILITY:
        return ParseResult(success=False, parser_id=PARSER_ID, parser_version=PARSER_VERSION, error=f"unsupported availability: {availability!r}")
    availability_status = None if availability == "UNKNOWN" else availability

    confidence = {"reference": 1.0}
    if price is not None:
        confidence["price"] = 0.95
    if availability_status:
        confidence["availability_status"] = 0.95
    warnings = ["manual_operator_attestation", "third_party_or_first_party_url_requires_human_review"]
    if price is None:
        warnings.append("no_price_in_manual_evidence")
    if availability_status is None:
        warnings.append("availability_unknown_in_manual_evidence")

    watch = ParsedWatch(
        reference_raw=reference,
        manufacturer="Citizen",
        brand="Citizen",
        model_name=data.get("model_name"),
        price=price,
        currency=currency if price is not None else None,
        availability_status=availability_status,
        extra_specs={
            "manual_evidence": {
                "submitter": submitter,
                "captured_at": captured.isoformat(),
                "operator_confirmed": True,
                "source_url": evidence_url,
            }
        },
        field_confidence=confidence,
        parser_warnings=warnings,
        overall_confidence=safe_overall_confidence(confidence),
        source_url=evidence_url,
    )
    return ParseResult(success=True, parser_id=PARSER_ID, parser_version=PARSER_VERSION, watches=[watch])
