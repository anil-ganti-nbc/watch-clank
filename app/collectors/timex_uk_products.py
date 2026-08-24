"""Timex UK regional catalogue collector (timex.co.uk).

Second consumer of the Shopify catalogue family; the first *regional*
consumer, so its primary editorial product is NEW_REGION-class evidence on
SKUs Timex US already surfaced — never a new-product identity (US + UK SKU
equality resolves to one canonical Watch via the existing
(manufacturer, brand, reference_canonical) identity).

Live reconnaissance 2026-08-25:
- https://timex.co.uk/products.json?limit=250&page=1 → 247 watches on
  page one alone (product_type == "Watch"); UK-suffixed SKUs observed
  (e.g. TW4B34400UK, TW2Y05900UK) alongside US-shared SKUs.
- Currency: GBP store (UK storefront); price taken from variant JSON as-is.

Inherited Timex rules that stay binding through this collector:
- published_at is provenance, never a trusted launch date;
- future-dated publishes cannot strengthen novelty (freshness.py gate);
- bulk-touch clustering is batch-complete and order-independent
  (pipeline batch staging, 2026-08-24);
- regional presence does not create a new product identity.
"""

from __future__ import annotations

from app.collectors.shopify_family import ShopifyCatalogueCollector, ShopifyCatalogueConfig

COLLECTOR_ID = "timex_uk_products"
REGION = "UK"
TRUST_SCORE = 90.0
LISTING_URL_TEMPLATE = "https://timex.co.uk/products.json?limit=250&page={page}"
PRODUCT_URL_TEMPLATE = "https://timex.co.uk/products/{handle}"
MAX_PAGES = 20


class TimexUkProductsCollector(ShopifyCatalogueCollector):
    def __init__(self) -> None:
        super().__init__(
            ShopifyCatalogueConfig(
                collector_id=COLLECTOR_ID,
                region=REGION,
                listing_url_template=LISTING_URL_TEMPLATE,
                product_url_template=PRODUCT_URL_TEMPLATE,
                trust_score=TRUST_SCORE,
                max_pages=MAX_PAGES,
            )
        )
