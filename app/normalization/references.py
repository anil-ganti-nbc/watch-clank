"""Reference normalization for Casio, Citizen, and Seiko watches.

Rules:
1. Always preserve the exact source reference as reference_raw.
2. Remove regional suffixes only through an explicit, brand-specific allowlist.
3. Do not blindly strip trailing letters with generic regex.
4. Do not use chained removesuffix() logic that can remove unintended characters.
5. Do not create the family key by taking only the string before the first hyphen.
6. Family grouping remains provisional in Stage 1.
7. A brand only gets suffix-stripping rules once real evidence shows which
   suffixes are safe to collapse (as Casio's JDM allowlist required). Until
   then its normalizer must be a conservative pass-through: canonical == raw.
   This is why normalize_citizen_reference / normalize_seiko_reference below
   do not strip anything yet — see their docstrings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Explicit JDM (Japan Domestic Market) suffix allowlist.
# Only these exact suffixes are removed to form the canonical reference.
# Unknown suffixes are preserved.
JDM_SUFFIX_ALLOWLIST: frozenset[str] = frozenset(
    {
        "JF",  # Japan
        "JR",  # Japan (some lines)
        "JA",  # Japan analog?
        "JB",
        "JC",
        "JD",
        "JE",
        "JG",
        "JH",
        "JI",
        "JJ",
        "JK",
        "JL",
        "JM",
        "JN",
        "JO",
        "JP",
        "JQ",
        "JS",
        "JT",
        "JU",
        "JV",
        "JW",
        "JX",
        "JY",
        "JZ",
    }
)


@dataclass(frozen=True)
class NormalizedReference:
    reference_raw: str
    reference_canonical: str
    family_candidate_key: str
    manufacturer: str
    brand: str
    collection: str | None
    warnings: list[str]


def _detect_brand_and_collection(raw: str) -> tuple[str, str | None]:
    """Heuristic brand/collection detection from model prefix.

    Conservative: only well-known Casio prefixes.
    """
    upper = raw.upper()
    # G-Shock families
    if upper.startswith(("GA-", "GW-", "GG-", "GP-", "GST-", "GM-", "GBD-", "GBX-", "GBA-", "GMA-", "GMD-", "GMW-", "MRG-", "MTG-", "GWF-", "GWN-", "GWR-", "GGB-", "GPR-", "GPS-", "GR-", "GLS-", "GLX-", "GD-", "G-")):
        # Master of G
        if any(upper.startswith(p) for p in ("GWF-", "GWN-", "GWR-", "GGB-", "GPR-", "GPS-", "GG-", "GWG-", "GWD-")):
            return "G-Shock", "Master of G"
        # Full Metal / Premium
        if upper.startswith(("GMW-", "GM-", "GST-", "MTG-", "MRG-")):
            return "G-Shock", "Full Metal / Premium"
        return "G-Shock", None

    # Edifice
    if upper.startswith(("EQB-", "EQS-", "EFV-", "EFR-", "ECB-", "EQW-", "EF-", "EDB-")):
        return "Casio", "Edifice"

    # Oceanus
    if upper.startswith(("OCW-", "OCS-", "OC-")):
        return "Casio", "Oceanus"

    # Pro Trek
    if upper.startswith(("PRG-", "PRW-", "PRT-", "PRJ-", "PRO-")):
        return "Casio", "Pro Trek"

    # Baby-G
    if upper.startswith(("BA-", "BGA-", "BGD-", "BLG-", "MSG-", "BSA-", "BCO-", "BG-")):
        return "Baby-G", None

    # Default Casio
    return "Casio", None


def normalize_casio_reference(
    reference_raw: str,
    *,
    manufacturer: str = "Casio",
    brand_hint: str | None = None,
    collection_hint: str | None = None,
) -> NormalizedReference:
    """Normalize a Casio reference according to Stage 1 rules.

    Example:
        GA-2100-1A1JF -> canonical GA-2100-1A1, family casio_gshock_ga_2100
    """
    warnings: list[str] = []
    raw = (reference_raw or "").strip()
    if not raw:
        raise ValueError("reference_raw must not be empty")

    # Detect brand / collection
    detected_brand, detected_collection = _detect_brand_and_collection(raw)
    brand = brand_hint or detected_brand
    collection = collection_hint or detected_collection

    # Canonical: remove only exact allowlisted JDM suffixes
    canonical = raw
    upper_raw = raw.upper()
    # Check last 2 characters if they form an allowlisted suffix and are preceded by a digit or letter pattern typical of Casio
    if len(raw) >= 3:
        possible_suffix = upper_raw[-2:]
        if possible_suffix in JDM_SUFFIX_ALLOWLIST:
            # Ensure it looks like a regional suffix (often after a digit or color code)
            # We remove it only if the character before is not part of a longer unknown token.
            # Simple rule: if the suffix is in allowlist, strip it.
            candidate = raw[:-2]
            # Avoid stripping if it would leave a trailing hyphen or empty
            if candidate and not candidate.endswith("-"):
                canonical = candidate
            else:
                # edge case: keep original
                warnings.append(f"Suffix {possible_suffix} looked like JDM but removal left trailing hyphen; preserved")
        else:
            # Unknown suffix – preserve fully
            if re.search(r"[A-Z]{2}$", upper_raw) and possible_suffix not in JDM_SUFFIX_ALLOWLIST:
                warnings.append(f"Unknown trailing suffix '{possible_suffix}' preserved in canonical")

    # Family candidate key: manufacturer_brand_base
    # Base is the series portion before color/variant codes, but NOT simply before first hyphen.
    # For GA-2100-1A1 -> ga_2100
    # Heuristic: take the first two hyphen-separated segments when they look like series + number.
    parts = canonical.upper().replace("_", "-").split("-")
    if len(parts) >= 2:
        # e.g. GA, 2100, 1A1 -> ga_2100
        series = parts[0].lower()
        number = parts[1].lower()
        # Keep only alphanumeric for the key
        series = re.sub(r"[^a-z0-9]", "", series)
        number = re.sub(r"[^a-z0-9]", "", number)
        family_base = f"{series}_{number}"
    elif len(parts) == 1:
        family_base = re.sub(r"[^a-z0-9]", "", parts[0].lower())
    else:
        family_base = re.sub(r"[^a-z0-9]", "", canonical.lower())

    brand_slug = re.sub(r"[^a-z0-9]", "", brand.lower())
    family_key = f"casio_{brand_slug}_{family_base}"

    return NormalizedReference(
        reference_raw=raw,
        reference_canonical=canonical,
        family_candidate_key=family_key,
        manufacturer=manufacturer,
        brand=brand,
        collection=collection,
        warnings=warnings,
    )


def normalize_citizen_reference(
    reference_raw: str,
    *,
    manufacturer: str = "Citizen",
    brand_hint: str | None = None,
    collection_hint: str | None = None,
) -> NormalizedReference:
    """Normalize a Citizen reference conservatively.

    Citizen reference suffixes (movement/case/dial variants such as -80H,
    -52A) can encode meaningful differences (bracelet vs strap, dial finish,
    limited-edition markers) rather than purely regional packaging. Unlike
    Casio's JDM allowlist, there is not yet evidence any Citizen suffix is
    safe to strip. Canonical == raw until brand-specific evidence justifies
    otherwise (see app/normalization/references.py module docstring policy).
    """
    raw = (reference_raw or "").strip()
    if not raw:
        raise ValueError("reference_raw must not be empty")
    brand = brand_hint or "Citizen"
    parts = raw.upper().split("-")
    base = re.sub(r"[^a-z0-9]", "", parts[0].lower()) if parts else re.sub(r"[^a-z0-9]", "", raw.lower())
    brand_slug = re.sub(r"[^a-z0-9]", "", brand.lower())
    family_key = f"citizen_{brand_slug}_{base}"
    return NormalizedReference(
        reference_raw=raw,
        reference_canonical=raw,
        family_candidate_key=family_key,
        manufacturer=manufacturer,
        brand=brand,
        collection=collection_hint,
        warnings=["no suffix normalization applied: no validated Citizen suffix rules yet"],
    )


def normalize_seiko_reference(
    reference_raw: str,
    *,
    manufacturer: str = "Seiko",
    brand_hint: str | None = None,
    collection_hint: str | None = None,
) -> NormalizedReference:
    """Normalize a Seiko reference conservatively.

    Same conservative policy as Citizen: Seiko caliber/case codes (e.g.
    SPB, SNE, SSC prefixes with numeric suffixes) are preserved verbatim.
    Canonical == raw until brand-specific evidence justifies stripping.
    """
    raw = (reference_raw or "").strip()
    if not raw:
        raise ValueError("reference_raw must not be empty")
    brand = brand_hint or "Seiko"
    parts = raw.upper().split("-")
    base = re.sub(r"[^a-z0-9]", "", parts[0].lower()) if parts else re.sub(r"[^a-z0-9]", "", raw.lower())
    brand_slug = re.sub(r"[^a-z0-9]", "", brand.lower())
    family_key = f"seiko_{brand_slug}_{base}"
    return NormalizedReference(
        reference_raw=raw,
        reference_canonical=raw,
        family_candidate_key=family_key,
        manufacturer=manufacturer,
        brand=brand,
        collection=collection_hint,
        warnings=["no suffix normalization applied: no validated Seiko suffix rules yet"],
    )


def normalize_timex_reference(
    reference_raw: str,
    *,
    manufacturer: str = "Timex",
    brand_hint: str | None = None,
    collection_hint: str | None = None,
) -> NormalizedReference:
    """Normalize a Timex reference conservatively.

    Timex SKUs (e.g. "TW6A01000VQ") have no hyphen and no validated
    suffix-stripping rule (the trailing letters can encode strap/case
    color variants). Same conservative policy as Citizen/Seiko: canonical
    == raw until brand-specific evidence justifies otherwise. Uniqueness
    is scoped by (manufacturer, brand, reference_canonical) regardless
    (see app/models/watch.py), so this format never collides with Casio/
    Citizen/Seiko references even though it shares no prefix convention
    with any of them.
    """
    raw = (reference_raw or "").strip()
    if not raw:
        raise ValueError("reference_raw must not be empty")
    brand = brand_hint or "Timex"
    base = re.sub(r"[^a-z0-9]", "", raw.lower())
    brand_slug = re.sub(r"[^a-z0-9]", "", brand.lower())
    family_key = f"timex_{brand_slug}_{base}"
    return NormalizedReference(
        reference_raw=raw,
        reference_canonical=raw,
        family_candidate_key=family_key,
        manufacturer=manufacturer,
        brand=brand,
        collection=collection_hint,
        warnings=["no suffix normalization applied: no validated Timex suffix rules yet"],
    )


def safe_overall_confidence(field_confidence: dict[str, float] | None) -> float:
    """Compute overall confidence safely; never divide by zero.

    If field_confidence is empty or None, return a conservative default of 50.0.
    """
    if not field_confidence:
        return 50.0
    values = [v for v in field_confidence.values() if isinstance(v, (int, float))]
    if not values:
        return 50.0
    return round(sum(values) / len(values), 2)
