"""Seiko Japan official retail-store collector (store.seikowatches.com).

See app/parsers/seiko_jp_products.py for the full reconnaissance writeup.
`/products.json` returns up to 250 products per request; standard Shopify
`?page=N` (1-indexed) paginates further, terminating when a page returns
zero raw products. Confirmed live 2026-08-14 from the Hetzner cloud
vantage point: pages 1-3 = 250 products each, page 4 = 209, page 5 = empty
-- 959 total products, every one a bare watch reference (no product_type
filtering needed -- see the parser module docstring).

No second HTTP request per product -- each listing page already carries
the full product record, same pattern as timex_products.py/
seiko_products.py/citizen_products.py.

Discovery-cap lesson applied from day one (2026-08-14 Hall of Shame
sprint found Timex/Citizen silently excluding most of their catalogues
under a positional max_items slice): this collector accepts
`known_product_urls` and prioritizes not-yet-seen URLs ahead of already-
known ones before applying the cap, exactly like the fix applied to
timex_products.py/citizen_products.py the same day -- never reintroducing
the bug into a brand-new source.
"""

from __future__ import annotations

import json

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "seiko_jp_products"
COLLECTOR_VERSION = "0.1.0"
REGION = "JP"
TRUST_SCORE = 95.0
LISTING_URL_TEMPLATE = "https://store.seikowatches.com/products.json?limit=250&page={page}"
PRODUCT_URL_TEMPLATE = "https://store.seikowatches.com/products/{handle}"
# Safety cap: pages, not products -- 250/page, bounds total candidates at
# 250 * cap even if the source ever reports far more than the confirmed
# ~959. Catalogue-collapse protection, not a realistic expectation.
MAX_PAGES = 20


class SeikoJapanProductsCollector:
    """Discover Seiko Japan retail-store watch products from the public
    Shopify products.json listing. No DB writes."""

    def discover_from_listing_json(self, payload: bytes | str) -> list[DiscoveredItem]:
        raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        items: list[DiscoveredItem] = []
        for p in data.get("products", []):
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

    def discover_all_pages(
        self, *, listing_pages: list[bytes] | None = None
    ) -> tuple[list[DiscoveredItem], list[FetchResult]]:
        """listing_pages: optional ordered list of page payloads for
        offline/fixture use, replacing live fetch_url calls."""
        all_items: list[DiscoveredItem] = []
        seen_refs: set[str] = set()
        fetches: list[FetchResult] = []

        page = 1
        while page <= MAX_PAGES:
            url = LISTING_URL_TEMPLATE.format(page=page)
            if listing_pages is not None:
                payload = listing_pages[page - 1] if page - 1 < len(listing_pages) else None
                fr = (
                    FetchResult(url=url, success=True, status_code=200, content_type="application/json", payload=payload)
                    if payload is not None
                    else FetchResult(url=url, success=False, error="no fixture page")
                )
            else:
                fr = fetch_url(url, accept_language="ja-JP,ja;q=0.9")
            fetches.append(fr)

            if not fr.success or not fr.payload:
                break

            items = self.discover_from_listing_json(fr.payload)
            raw_count = len(json.loads(fr.payload.decode("utf-8", errors="ignore")).get("products", []))
            if raw_count == 0:
                break

            for item in items:
                ref = item.reference_hint
                if not ref or ref in seen_refs:
                    continue
                seen_refs.add(ref)
                all_items.append(item)

            page += 1

        if page > MAX_PAGES:
            logger.warning("seiko_jp_products_pagination_capped", cap=MAX_PAGES)

        return all_items, fetches

    def run(
        self,
        *,
        max_items: int | None = 250,
        listing_pages: list[bytes] | None = None,
        known_product_urls: set[str] | None = None,
    ) -> CollectorRunResult:
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, region=REGION, trust_score=TRUST_SCORE
        )
        items, discovery_fetches = self.discover_all_pages(listing_pages=listing_pages)

        result.metadata["index_blocked"] = bool(discovery_fetches) and all(
            is_blocked_response(f.status_code, f.payload, f.error) for f in discovery_fetches
        )
        result.metadata["candidate_count"] = len(items)
        if not items and result.metadata["index_blocked"]:
            result.metadata["component_status"] = "BLOCKED"
            result.metadata["healthy"] = False
            result.fetched = discovery_fetches
            return result

        known = known_product_urls or set()
        if max_items is not None and known:
            new_items = [i for i in items if i.url not in known]
            known_items = [i for i in items if i.url in known]
            items = (new_items + known_items)[:max_items]
        elif max_items is not None:
            items = items[:max_items]
        result.discovered = items
        result.metadata["discovered_count"] = len(items)

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

        any_fetch_success = any(f.success for f in discovery_fetches)
        result.metadata["component_status"] = "SUCCESS" if items else ("ZERO_ITEMS" if any_fetch_success else "FAILED")
        result.metadata["healthy"] = result.metadata["component_status"] in ("SUCCESS", "PARTIAL")
        result.metadata["discovery_fetches"] = [
            {"url": f.url, "status": f.status_code, "success": f.success} for f in discovery_fetches
        ]
        return result
