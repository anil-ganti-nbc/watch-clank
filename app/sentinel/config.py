"""Declarative Sentinel source registry.

Brand/source N+1 is a config entry here, not new code: pick an adapter,
point it at the first-party discovery surface, and set the cadence tier.
The runner, identity store, alerting, and registry wiring are all
source-agnostic.

Source selection rules (2026-09-08 reconnaissance, Hetzner vantage):
- Shopify surfaces MUST use the plain unscoped /products.json endpoint.
  Its default order is newest-first (live-verified: timex.com,
  timex.co.uk, and seikousa.com all returned newest publications at the
  top). The collection-scoped /collections/all/products.json variant is
  NOT reliably ordered (seikousa served a mixed manual order; sort_by=
  created-descending was silently ignored on two of three stores) and
  must not be swapped in casually.
- Casio sitemaps reuse the casio_uk/casio_jp collectors' own discovery
  methods (discovery_kind), so URL->reference extraction, option/strap
  exclusions, and sitemap URLs cannot drift from the proven collectors.
  No Cloudflare/Akamai-blocked product page is ever fetched.
- citizenwatch.eu is the only Citizen surface that is simultaneously
  first-party and machine-readable from this vantage; citizenwatch.co.uk
  is robots-disallowed (ClaudeBot) + Cloudflare-blocked and must never be
  added. The EU lane is valid Sentinel input because dedup is global --
  region=EU is metadata, never a UK/US commercialisation claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FAST_TIER_MINUTES = 15


@dataclass(frozen=True)
class SentinelSourceConfig:
    """One monitored first-party source. Frozen: registry entries are
    configuration, not mutable runtime state (per-poll state lives in
    sentinel_source_state)."""

    name: str
    manufacturer: str
    region: str
    display_name: str
    adapter: str  # "shopify_products" | "sitemap"
    # Cadence tier: fast-tier brands poll every sweep (the 15-minute
    # timer); heavier surfaces set a larger cadence_minutes and the runner
    # skips them until due. One timer serves every tier.
    cadence_minutes: int = FAST_TIER_MINUTES
    enabled: bool = True

    # -- shopify_products adapter -----------------------------------------
    # Plain unscoped /products.json endpoint, newest-first (module docstring).
    listing_url_template: str | None = None  # needs {page}
    product_url_template: str | None = None  # needs {handle}
    product_type_filter: str | None = None  # e.g. "Watch"; None = keep all

    # -- sitemap adapter ----------------------------------------------------
    sitemap_url: str | None = None
    # Which discovery implementation extracts candidates from the document.
    # "casio_uk"/"casio_jp" delegate to those collectors' proven
    # discover_from_sitemap_xml (exact URL/reference/option rules);
    # "generic" uses the shared sitemap_family regex machinery with
    # reference_pattern below. index_child_pattern enables one level of
    # <sitemapindex> following (fetch first child whose <loc> matches).
    discovery_kind: str = "generic"
    reference_pattern: re.Pattern[str] | None = None
    index_child_pattern: re.Pattern[str] | None = None

    # Safety ceiling on candidates considered per poll. Full documents are
    # always parsed pre-budget (the Goldsmiths slice-starvation lesson);
    # the cap only guards against a pathological feed.
    max_candidates: int = 5000

    notes: str = ""
    extra: dict = field(default_factory=dict)


from app.collectors.casio_jp_sitemap import (  # noqa: E402
    JP_WATCHES_SITEMAP_URL as _CASIO_JP_SITEMAP_URL,
)
from app.collectors.casio_uk_sitemap import (  # noqa: E402
    UK_SITEMAP_URL as _CASIO_UK_SITEMAP_URL,
)

_CITIZEN_EU_REFERENCE_RE = re.compile(r"p/([A-Za-z0-9][A-Za-z0-9-]*?)/?$")

SENTINEL_SOURCES: dict[str, SentinelSourceConfig] = {
    "timex_us": SentinelSourceConfig(
        name="timex_us",
        manufacturer="Timex",
        region="US",
        display_name="Timex US",
        adapter="shopify_products",
        listing_url_template="https://www.timex.com/products.json?limit=250&page={page}",
        product_url_template="https://www.timex.com/products/{handle}",
        product_type_filter="Watch",
        notes="Default /products.json order live-verified newest-first 2026-09-08.",
    ),
    "timex_uk": SentinelSourceConfig(
        name="timex_uk",
        manufacturer="Timex",
        region="UK",
        display_name="Timex UK",
        adapter="shopify_products",
        listing_url_template="https://timex.co.uk/products.json?limit=250&page={page}",
        product_url_template="https://timex.co.uk/products/{handle}",
        product_type_filter="Watch",
        notes="UK-suffixed SKUs (e.g. TW5M74100UK) collapse onto the base "
        "identity via identity.py's regional-suffix allowlist, so a Timex "
        "UK listing never re-alerts a model Timex US already sighted.",
    ),
    "seiko_us": SentinelSourceConfig(
        name="seiko_us",
        manufacturer="Seiko",
        region="US",
        display_name="Seiko US",
        adapter="shopify_products",
        # Unscoped endpoint: the /collections/all variant is NOT newest-first
        # (live-verified 2026-09-08: served manual/best-selling order).
        listing_url_template="https://seikousa.com/products.json?limit=250&page={page}",
        product_url_template="https://seikousa.com/products/{handle}",
        # Live evidence 2026-09-09 (Hetzner): seikousa product_type is
        # "Wrist Watches" (208 of 250 on page 1; Clocks/Straps excluded) —
        # NOT "Watch" like the Timex stores.
        product_type_filter="Wrist Watches",
        notes="Page 1 only, like every Shopify lane here (page-2+ is a "
        "documented limitation; ordering evidence lives in the docstring).",
    ),
    "casio_uk_sitemap": SentinelSourceConfig(
        name="casio_uk_sitemap",
        manufacturer="Casio",
        region="UK",
        display_name="Casio UK",
        adapter="sitemap",
        sitemap_url=_CASIO_UK_SITEMAP_URL,
        discovery_kind="casio_uk",
        notes="Same published sitemap the casio_uk_sitemap collector uses.",
    ),
    "casio_jp_sitemap": SentinelSourceConfig(
        name="casio_jp_sitemap",
        manufacturer="Casio",
        region="JP",
        display_name="Casio JP",
        adapter="sitemap",
        sitemap_url=_CASIO_JP_SITEMAP_URL,
        discovery_kind="casio_jp",
        cadence_minutes=60,
        max_candidates=25000,
        notes="watches.xml is ~17.8k URLs: full-document parse, on a "
        "60-minute cadence so the 15-minute sweep stays cheap.",
    ),
    "citizen_eu_sitemap": SentinelSourceConfig(
        name="citizen_eu_sitemap",
        manufacturer="Citizen",
        region="EU",
        display_name="Citizen EU",
        adapter="sitemap",
        sitemap_url="https://citizenwatch.eu/media/sitemap/sitemap-products-citizenwatch.eu.xml",
        discovery_kind="generic",
        reference_pattern=_CITIZEN_EU_REFERENCE_RE,
        notes="First-party EUR/EU catalogue. Metadata region=EU is evidence "
        "of nothing beyond 'listed on the EU storefront'.",
    ),
}


def enabled_sources() -> list[SentinelSourceConfig]:
    return [s for s in SENTINEL_SOURCES.values() if s.enabled]
