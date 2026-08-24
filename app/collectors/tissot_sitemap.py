"""Tissot regional sitemap-delta collector (www.tissotwatches.com).

First consumer of the reusable sitemap_family (see that module's docstring
for the family contract and honest limitations).

Live reconnaissance 2026-08-25 (scripts/probe_tissot_*.py):
- Platform: Salesforce Commerce Cloud; multi-locale sitemap index at
  https://www.tissotwatches.com/sitemap.xml with per-locale product
  sitemaps (en-us, en-en, en-au, es-mx, fr-ch, ...).
- Product URLs carry the bare reference in the path:
  https://www.tissotwatches.com/en-us/T41118316.html -- the SKU is the
  Tissot model number itself, so reference identity needs no page fetch.
- Product JSON-LD confirmed live: sku/mpn == URL slug,
  priceCurrency/price/availability present. NOT fetched by this collector
  (sitemap-only evidence base, same honest limitation as casio_uk_sitemap;
  a JSON-LD StructuredProductCollector is a separate future lane).
- Live probe: en-us sitemap_0.xml = 2467 URLs / 648 distinct SKUs.
- No bot friction observed on sitemap endpoints (product pages untested --
  not fetched by design).

Reference identity note: Tissot references are globally unique per model
variant (the T-number encodes family/case/bracelet); cross-locale
duplicates of one SKU are one Watch with multiple regional observations,
matching the canonical (manufacturer, brand, reference_canonical) identity
-- exactly the NEW_REGION mechanism's intended input.
"""

from __future__ import annotations

import re

from app.collectors.sitemap_family import SitemapDeltaCollector, SitemapDeltaConfig

COLLECTOR_ID = "tissot_sitemap"
REGION = "US"
TRUST_SCORE = 70.0
SITEMAP_URL = "https://www.tissotwatches.com/en-us/sitemap_0.xml"
MAX_CANDIDATES = 3000

# https://www.tissotwatches.com/en-us/T41118316.html -> "T41118316"
_REFERENCE_RE = re.compile(r"tissotwatches\.com/[a-z]{2}-[a-z]{2}/([A-Za-z0-9]+)\.html")


class TissotSitemapCollector(SitemapDeltaCollector):
    def __init__(self) -> None:
        super().__init__(
            SitemapDeltaConfig(
                collector_id=COLLECTOR_ID,
                region=REGION,
                sitemap_url=SITEMAP_URL,
                reference_pattern=_REFERENCE_RE,
                trust_score=TRUST_SCORE,
                max_candidates=MAX_CANDIDATES,
            )
        )
