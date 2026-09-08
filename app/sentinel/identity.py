"""Sentinel identity rules: extraction, normalization, global dedup keys.

Identity contract (per the Sentinel brief):
- The model/reference number is the strongest identity key when present.
- Cosmetic formatting differences must not create duplicates; genuinely
  different SKUs must never merge.
- Dedup is GLOBAL across regions: a Timex model first seen on Timex UK
  must not re-alert when Timex US publishes the same reference.
- No reliable reference -> canonicalized first-party product URL fallback,
  conservative enough that routine page movement cannot spam Discord.

Normalization reuses app.normalization.references verbatim (Casio JDM
allowlist; Citizen/Seiko/Timex conservative pass-throughs) -- the Sentinel
invents no brand rules of its own. The ONE Sentinel-specific rule is the
regional-suffix collapse below.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.models.sentinel import IDENTITY_TYPE_REFERENCE, IDENTITY_TYPE_URL
from app.normalization.references import (
    normalize_casio_reference,
    normalize_citizen_reference,
    normalize_seiko_reference,
    normalize_timex_reference,
)

# Regional suffixes observed in the wild that mark the SAME underlying SKU
# as a store-specific variant. Collapsed ONLY under the strict shape rule
# below; everything else is preserved (the Casio JDM-allowlist principle:
# suffix-stripping needs explicit evidence, never a generic regex).
# Evidence: timex_uk_products.py documents UK-suffixed SKUs (TW4B34400UK,
# TW2Y05900UK) as regional variants; live timex.co.uk data 2026-09-08
# shows TW5M74100UK alongside the US TW5M74100.
REGIONAL_SUFFIX_ALLOWLIST: frozenset[str] = frozenset({"UK"})

# Only unhyphenated alphanumeric SKUs (Timex-style TW5M74100UK) are ever
# suffix-collapsed. Hyphenated reference systems (Casio GA-2100-1A, Citizen
# AW1911-53A, Seiko SBXY105) are never touched by this rule.
_UNHYPHENATED_SKU_RE = re.compile(r"^[A-Za-z0-9]+$")

_NORMALIZERS = {
    "Casio": normalize_casio_reference,
    "Citizen": normalize_citizen_reference,
    "Seiko": normalize_seiko_reference,
    "Timex": normalize_timex_reference,
}


@dataclass(frozen=True)
class SentinelIdentityResult:
    identity_key: str
    identity_type: str  # REFERENCE | URL
    manufacturer: str
    reference_raw: str | None  # exactly as the source spelled it
    reference_canonical: str | None
    fallback_url: str | None


def collapse_regional_suffix(reference_raw: str) -> str:
    """Strip an allowlisted regional suffix from an unhyphenated SKU.

    TW5M74100UK -> TW5M74100. Requires ALL of: allowlisted suffix, SKU has
    no hyphens, and the character before the suffix is a digit (so a
    hypothetical product literally ending "...UKUK" or "SUAVEUK" is not
    mangled). Anything not matching is returned unchanged.
    """
    raw = reference_raw.strip()
    if not _UNHYPHENATED_SKU_RE.match(raw):
        return raw
    upper = raw.upper()
    if len(raw) < 4:
        return raw
    for suffix in REGIONAL_SUFFIX_ALLOWLIST:
        if upper.endswith(suffix) and raw[-len(suffix) - 1].isdigit():
            return raw[: -len(suffix)]
    return raw


def canonicalize_url(url: str) -> str:
    """Cosmetic-insensitive first-party product URL for fallback identity.

    Lowercases scheme/host, drops query/fragment (tracking params), strips
    one trailing slash. Path case is preserved on purpose: product paths on
    these storefronts are meaningful and stable, and over-normalizing URL
    fallbacks would risk merging genuinely different pages.
    """
    parts = urlsplit((url or "").strip())
    path = parts.path or "/"
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def build_identity(
    manufacturer: str,
    *,
    reference_raw: str | None,
    url: str,
) -> SentinelIdentityResult:
    """Compute the global identity for one candidate.

    Reference identity wins whenever a non-empty reference exists; the URL
    fallback exists so reference-less products still dedup instead of
    alerting on every poll.
    """
    manufacturer = (manufacturer or "").strip()
    cleaned_ref = (reference_raw or "").strip()
    if cleaned_ref:
        collapsed = collapse_regional_suffix(cleaned_ref)
        normalizer = _NORMALIZERS.get(manufacturer)
        if normalizer is not None:
            canonical = normalizer(collapsed).reference_canonical
        else:
            # Unknown manufacturer: conservative pass-through, no invention.
            canonical = collapsed.upper()
        return SentinelIdentityResult(
            identity_key=f"{manufacturer.lower()}:{canonical.lower()}",
            identity_type=IDENTITY_TYPE_REFERENCE,
            manufacturer=manufacturer,
            reference_raw=cleaned_ref,
            reference_canonical=canonical,
            fallback_url=None,
        )

    canonical_url = canonicalize_url(url)
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
    return SentinelIdentityResult(
        identity_key=f"{manufacturer.lower()}:url:{digest}",
        identity_type=IDENTITY_TYPE_URL,
        manufacturer=manufacturer,
        reference_raw=None,
        reference_canonical=None,
        fallback_url=canonical_url,
    )
