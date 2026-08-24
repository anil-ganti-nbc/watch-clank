"""Generic reference normalization for sitemap-family brands.

Same conservative policy as normalize_timex_reference: canonical == raw
until brand-specific evidence justifies suffix-stripping. Uniqueness is
scoped by (manufacturer, brand, reference_canonical) regardless (see
app/models/watch.py).
"""

from __future__ import annotations

import re

from app.normalization.references import NormalizedReference


def normalize_generic_reference(
    reference_raw: str,
    *,
    manufacturer: str = "Unknown",
    brand_hint: str | None = None,
    collection_hint: str | None = None,
) -> NormalizedReference:
    raw = (reference_raw or "").strip()
    if not raw:
        raise ValueError("reference_raw must not be empty")
    brand = brand_hint or manufacturer
    base = re.sub(r"[^a-z0-9]", "", raw.lower())
    brand_slug = re.sub(r"[^a-z0-9]", "", (brand or "").lower())
    family_key = f"{brand_slug}_{base}" if brand_slug else f"generic_{base}"
    return NormalizedReference(
        reference_raw=raw,
        reference_canonical=raw,
        family_candidate_key=family_key,
        manufacturer=manufacturer,
        brand=brand,
        collection=collection_hint,
        warnings=[],
    )
