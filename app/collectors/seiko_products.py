"""Seiko USA product/catalogue collector (seikousa.com).

See app/parsers/seiko_products.py for the first-party verification and the
public-Shopify-JSON rationale. One fetch of the standard
`/collections/all/products.json` endpoint returns many products at once;
this collector filters to product_type=="Wrist Watches" and hands each
product's JSON slice to the parser individually — no second HTTP request
per product (more conservative than re-fetching each product page).
"""

from __future__ import annotations

import json

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "seiko_products"
COLLECTOR_VERSION = "0.1.0"
REGION = "US"
TRUST_SCORE = 90.0
LISTING_URL = "https://seikousa.com/collections/all/products.json?limit=250"
PRODUCT_URL_TEMPLATE = "https://seikousa.com/products/{handle}"


class SeikoProductsCollector:
    """Discover Seiko US wrist-watch products from the public Shopify
    products.json listing. No DB writes."""

    def discover_from_listing_json(self, payload: bytes | str) -> list[DiscoveredItem]:
        raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        items: list[DiscoveredItem] = []
        for p in data.get("products", []):
            if p.get("product_type") != "Wrist Watches":
                continue
            handle = p.get("handle")
            if not handle:
                continue
            items.append(
                DiscoveredItem(
                    url=PRODUCT_URL_TEMPLATE.format(handle=handle),
                    title=p.get("title"),
                    reference_hint=(p.get("variants") or [{}])[0].get("sku"),
                    metadata={"source_region": REGION, "product_json": p},
                )
            )
        return items

    def run(self, *, max_items: int | None = 10, listing_json: bytes | None = None) -> CollectorRunResult:
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, region=REGION, trust_score=TRUST_SCORE
        )
        if listing_json is not None:
            listing_fr = FetchResult(
                url=LISTING_URL, success=True, status_code=200, content_type="application/json", payload=listing_json
            )
        else:
            listing_fr = fetch_url(LISTING_URL)
        result.metadata["index_blocked"] = is_blocked_response(listing_fr.status_code, listing_fr.payload, listing_fr.error)

        if not listing_fr.success or not listing_fr.payload:
            result.metadata["component_status"] = "BLOCKED" if result.metadata["index_blocked"] else "FAILED"
            result.metadata["healthy"] = False
            result.fetched = [listing_fr]
            return result

        items = self.discover_from_listing_json(listing_fr.payload)
        if max_items is not None:
            items = items[:max_items]
        result.discovered = items
        result.metadata["discovered_count"] = len(items)

        # No second HTTP fetch per product — the listing already carries
        # the full product record. Each "fetch" is a synthetic FetchResult
        # wrapping that product's own JSON slice, with the real product URL
        # preserved for evidence/traceability.
        for item in items:
            product_json = item.metadata.get("product_json")
            result.fetched.append(
                FetchResult(
                    url=item.url,
                    success=True,
                    status_code=200,
                    content_type="application/json",
                    payload=json.dumps(product_json).encode("utf-8"),
                )
            )

        result.metadata["component_status"] = "SUCCESS" if items else ("ZERO_ITEMS" if listing_fr.success else "FAILED")
        result.metadata["healthy"] = result.metadata["component_status"] in ("SUCCESS", "PARTIAL")
        result.metadata["discovery_fetches"] = [
            {"url": listing_fr.url, "status": listing_fr.status_code, "success": listing_fr.success}
        ]
        return result
