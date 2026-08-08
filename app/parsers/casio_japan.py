"""Casio Japan product page parser.

Stage 1: deterministic extraction from saved HTML snapshots.
Never performs network access.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from app.core.logging import get_logger
from app.normalization.references import normalize_casio_reference, safe_overall_confidence
from app.parsers.base import ParsedWatch, ParseResult

logger = get_logger(__name__)

PARSER_ID = "casio_japan_html"
PARSER_VERSION = "0.1.0"


def _text(node) -> str:
    if node is None:
        return ""
    return (node.text(strip=True) or "").strip()


def _first_text(tree: HTMLParser, selectors: list[str]) -> str | None:
    for sel in selectors:
        node = tree.css_first(sel)
        t = _text(node)
        if t:
            return t
    return None


def _extract_reference(tree: HTMLParser, html: str) -> str | None:
    """Extract model reference from multiple possible locations."""
    # Common patterns on Casio product pages
    candidates = [
        # Meta / structured
        _first_text(tree, ['meta[property="product:retailer_item_id"]', 'meta[name="sku"]']),
        # Title often contains the model
        _first_text(tree, ["title"]),
        # Product name / model number blocks
        _first_text(
            tree,
            [
                ".product-model",
                ".model-number",
                ".product-code",
                "[data-model]",
                ".pdp-model",
                "h1.product-name",
                "h1",
            ],
        ),
    ]

    # Regex fallback on full HTML for typical Casio refs
    # e.g. GA-2100-1A1JF, OCW-T2600RL-3AJR, PRW-61Y-3JF
    pattern = re.compile(
        r"\b([A-Z]{2,4}-[A-Z0-9]{2,8}(?:-[A-Z0-9]{1,6})?(?:[A-Z]{1,3})?)\b",
        re.IGNORECASE,
    )

    for c in candidates:
        if not c:
            continue
        # Clean common prefixes
        cleaned = re.sub(r"^(CASIO|G-SHOCK|EDIFICE|OCEANUS|PRO TREK|BABY-G)\s*[:\-]?\s*", "", c, flags=re.I)
        m = pattern.search(cleaned)
        if m:
            return m.group(1).upper()
        # Sometimes the whole string is the ref
        if re.match(r"^[A-Z0-9\-]{5,20}$", cleaned, re.I):
            return cleaned.upper()

    # Full page scan
    matches = pattern.findall(html)
    if matches:
        # Prefer the most "product-like" (longest or with more hyphens)
        matches = sorted(set(m.upper() for m in matches), key=lambda x: (-x.count("-"), -len(x)))
        return matches[0]

    return None


def _extract_bool_from_text(text: str, keywords: list[str]) -> bool | None:
    lower = text.lower()
    for kw in keywords:
        if kw in lower:
            return True
    return None


def _parse_water_resistance(text: str) -> int | None:
    # e.g. "200m", "20気圧", "20 BAR"
    m = re.search(r"(\d+)\s*(?:m|メートル|meter)", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(?:気圧|BAR|bar|ATM)", text, re.I)
    if m:
        # 1 bar ≈ 10 m
        return int(m.group(1)) * 10
    return None


def parse_casio_product_html(
    html: str | bytes,
    *,
    source_url: str | None = None,
) -> ParseResult:
    """Parse a single Casio product page HTML snapshot.

    Returns ParseResult with zero or one ParsedWatch.
    """
    warnings: list[str] = []

    if isinstance(html, bytes):
        try:
            html = html.decode("utf-8")
        except UnicodeDecodeError:
            try:
                html = html.decode("shift_jis")
            except UnicodeDecodeError:
                return ParseResult(
                    success=False,
                    parser_id=PARSER_ID,
                    parser_version=PARSER_VERSION,
                    error="Unable to decode HTML as UTF-8 or Shift_JIS",
                )

    if not html or not html.strip():
        return ParseResult(
            success=False,
            parser_id=PARSER_ID,
            parser_version=PARSER_VERSION,
            error="Empty HTML payload",
        )

    try:
        tree = HTMLParser(html)
    except Exception as exc:
        return ParseResult(
            success=False,
            parser_id=PARSER_ID,
            parser_version=PARSER_VERSION,
            error=f"HTML parse failure: {exc}",
        )

    reference_raw = _extract_reference(tree, html)
    if not reference_raw:
        return ParseResult(
            success=False,
            parser_id=PARSER_ID,
            parser_version=PARSER_VERSION,
            error="Essential product identity (reference) not found",
            warnings=["Could not locate a Casio model reference in the snapshot"],
        )

    # Normalize
    try:
        norm = normalize_casio_reference(reference_raw)
    except ValueError as exc:
        return ParseResult(
            success=False,
            parser_id=PARSER_ID,
            parser_version=PARSER_VERSION,
            error=str(exc),
        )

    field_conf: dict[str, float] = {"reference_raw": 95.0}
    if norm.warnings:
        warnings.extend(norm.warnings)
        field_conf["reference_raw"] = 80.0

    # Model name / title
    model_name = _first_text(
        tree,
        [
            "h1.product-name",
            "h1.pdp-title",
            ".product-title",
            "h1",
            "title",
        ],
    )
    if model_name:
        field_conf["model_name"] = 70.0
    else:
        model_name = reference_raw
        field_conf["model_name"] = 40.0
        warnings.append("Model name not found; falling back to reference")

    # Specs extraction (tolerant)
    full_text = tree.body.text(separator=" ") if tree.body else html

    solar = _extract_bool_from_text(full_text, ["ソーラー", "solar", "tough solar", "タフソーラー"])
    bluetooth = _extract_bool_from_text(full_text, ["bluetooth", "ブルートゥース", "スマートフォンリンク"])
    radio_sync = _extract_bool_from_text(
        full_text, ["電波", "radio controlled", "マルチバンド", "wave ceptor", "radio sync"]
    )
    gps = _extract_bool_from_text(full_text, ["gps", "ジーピーエス"])

    if solar is not None:
        field_conf["solar"] = 75.0
    if bluetooth is not None:
        field_conf["bluetooth"] = 75.0
    if radio_sync is not None:
        field_conf["radio_sync"] = 75.0
    if gps is not None:
        field_conf["gps"] = 75.0

    # Water resistance
    water_m = _parse_water_resistance(full_text)
    if water_m is not None:
        field_conf["water_resistance_m"] = 70.0

    # Case material
    case_material = None
    for kw, label in [
        ("ステンレス", "Stainless Steel"),
        ("ステンレススチール", "Stainless Steel"),
        ("カーボン", "Carbon"),
        ("樹脂", "Resin"),
        ("チタン", "Titanium"),
        ("titanium", "Titanium"),
        ("resin", "Resin"),
        ("stainless", "Stainless Steel"),
    ]:
        if kw.lower() in full_text.lower():
            case_material = label
            field_conf["case_material"] = 60.0
            break

    # Crystal
    crystal = None
    for kw, label in [
        ("サファイア", "Sapphire"),
        ("sapphire", "Sapphire"),
        ("ミネラル", "Mineral"),
        ("mineral", "Mineral"),
        ("ハードグラス", "Hard Glass"),
    ]:
        if kw.lower() in full_text.lower():
            crystal = label
            field_conf["crystal"] = 60.0
            break

    # Limited edition – only explicit phrases, never bare "limited"
    limited = None
    lower_full = full_text.lower()
    limited_phrases = ("限定モデル", "限定版", "limited edition", "limited model", "limited edition model")
    if any(ph in lower_full or ph in full_text for ph in limited_phrases):
        limited = True
        field_conf["limited_edition"] = 70.0
    elif "限定" in full_text and ("本" in full_text or "個" in full_text or "台" in full_text):
        # e.g. 500本限定
        limited = True
        field_conf["limited_edition"] = 65.0
        warnings.append("parser.warning.limited_inferred_from_quantity")

    # Price (JPY common)
    price = None
    currency = None
    price_m = re.search(r"[¥￥]\s*([\d,]+)", full_text)
    if price_m:
        try:
            price = float(price_m.group(1).replace(",", ""))
            currency = "JPY"
            field_conf["price"] = 80.0
        except ValueError:
            pass

    overall = safe_overall_confidence(field_conf)

    watch = ParsedWatch(
        reference_raw=norm.reference_raw,
        manufacturer=norm.manufacturer,
        brand=norm.brand,
        collection=norm.collection,
        model_name=model_name,
        solar=solar,
        bluetooth=bluetooth,
        radio_sync=radio_sync,
        gps=gps,
        case_material=case_material,
        crystal=crystal,
        water_resistance_m=water_m,
        limited_edition=limited,
        price=price,
        currency=currency,
        field_confidence=field_conf,
        parser_warnings=warnings,
        overall_confidence=overall,
        source_url=source_url,
        extra_specs={
            "family_candidate_key": norm.family_candidate_key,
            "reference_canonical": norm.reference_canonical,
        },
    )

    return ParseResult(
        success=True,
        parser_id=PARSER_ID,
        parser_version=PARSER_VERSION,
        watches=[watch],
        warnings=warnings,
    )
