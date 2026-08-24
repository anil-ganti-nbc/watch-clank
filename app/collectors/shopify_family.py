"""Reusable Shopify catalogue collector family.

Extracted from the proven timex_products.py / seiko_products.py /
seiko_jp_products.py pattern (three bespoke copies of one loop).

Family contract — identical to all existing Watch Clank collectors:

- Network discovery only; never touches the database.
- Shopify public `products.json` pagination, terminating on an empty page,
  bounded by MAX_PAGES for catalogue-collapse safety.
- `product_type` filter excludes straps/giftsets/service plans.
- Reference identity = first variant's SKU (the store's canonical model
  number); deduped across pages by SKU.
- New-first traversal with known-URL deprioritization (2026-08-24
  slice-starvation repair): unseen URLs are processed before known ones;
  known URLs are deprioritized, never dropped.
- Honest component statuses: BLOCKED / FAILED / ZERO_ITEMS / SUCCESS.

Malformed listing pages are tolerated as empty (pagination stops) — a bad
page must not crash a run, and must not fabricate candidates.

Brand-specific concerns stay OUT of this family:
- freshness interpretation (`published_at` handling) lives in the pipeline;
- reference normalization lives in app/normalization;
- collection/family semantics live in parsers.

Timex regression fixtures ride on this family so the prior Shopify
freshness defects (bulk-touch timestamps, future-dated publishes) cannot
resurrect through new consumers.
"""

from __future__ import annotations

import json

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import fetch_url, is_blocked_response


class ShopifyCatalogueConfig:
    """Configuration contract for one Shopify catalogue collector."""

    def __init__(
        self,
        *,
        collector_id: str,
        region: str,
        listing_url_template: str,
        product_url_template: str,
        trust_score: float,
        product_type: str = "Watch",
        max_pages: int = 20,
        currency_note: str | None = None,
    ) -> None:
        self.collector_id = collector_id
        self.region = region
        self.listing_url_template = listing_url_template
        self.product_url_template = product_url_template
        self.trust_score = trust_score
        self.product_type = product_type
        self.max_pages = max_pages
        self.currency_note = currency_note


class ShopifyCatalogueCollector:
    """Generic Shopify products.json catalogue collector driven by config."""

    def __init__(self, config: ShopifyCatalogueConfig) -> None:
        self._config = config

    def discover_from_listing_json(self, payload: bytes | str) -> list[DiscoveredItem]:
        cfg = self._config
        raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []  # malformed page tolerated as empty; pagination stops
        items: list[DiscoveredItem] = []
        for p in data.get("products", []):
            if p.get("product_type") != cfg.product_type:
                continue
            handle = p.get("handle")
            if not handle:
                continue
            items.append(
                DiscoveredItem(
                    url=cfg.product_url_template.format(handle=handle),
                    title=p.get("title"),
                    reference_hint=(p.get("variants") or [{}])[0].get("sku"),
                    metadata={"source_region": cfg.region, "product_json": p},
                )
            )
        return items

    def run(
        self,
        *,
        max_items: int | None = 250,
        listing_pages: list[bytes] | None = None,
        known_product_urls: set[str] | None = None,
    ) -> CollectorRunResult:
        cfg = self._config
        result = CollectorRunResult(
            collector_id=cfg.collector_id, collector_version="0.1.0", region=cfg.region,
            trust_score=cfg.trust_score,
        )
        items: list[DiscoveredItem] = []
        seen_refs: set[str] = set()
        fetches: list[FetchResult] = []

        page = 1
        while page <= cfg.max_pages:
            url = cfg.listing_url_template.format(page=page)
            if listing_pages is not None:
                payload = listing_pages[page - 1] if page - 1 < len(listing_pages) else None
                fr = (
                    FetchResult(url=url, success=True, status_code=200, content_type="application/json", payload=payload)
                    if payload is not None
                    else FetchResult(url=url, success=False, error="no fixture page")
                )
            else:
                fr = fetch_url(url)
            fetches.append(fr)  # 2026-08-25 fix: record EVERY discovery fetch so
            # status classification sees the real fetch outcome (a successful
            # empty page must read ZERO_ITEMS, never FAILED).

            if not fr.success or not fr.payload:
                break
            discovered_page = self.discover_from_listing_json(fr.payload)
            if not _is_valid_json(fr.payload):
                break
            raw_count = len(json.loads(fr.payload.decode("utf-8", errors="ignore")).get("products", []))
            if raw_count == 0:
                break

            for item in discovered_page:
                ref = item.reference_hint
                if not ref or ref in seen_refs:
                    continue
                seen_refs.add(ref)
                items.append(item)

            page += 1

        if page > cfg.max_pages:
            from app.core.logging import get_logger

            get_logger(__name__).warning("shopify_family_pagination_capped", cap=cfg.max_pages, collector=cfg.collector_id)

        result.metadata["index_blocked"] = bool(fetches) and all(
            is_blocked_response(f.status_code, f.payload, f.error) for f in fetches
        )
        result.metadata["candidate_count"] = len(items)
        if not items and result.metadata["index_blocked"]:
            result.metadata["component_status"] = "BLOCKED"
            result.metadata["healthy"] = False
            result.fetched = fetches
            return result

        # New-first traversal (slice-starvation repair): unseen URLs before
        # known ones; known deprioritized, never dropped.
        known = known_product_urls or set()
        if max_items is not None and known:
            new_items = [i for i in items if i.url not in known]
            known_items = [i for i in items if i.url in known]
            items = (new_items + known_items)[:max_items]
        elif max_items is not None:
            items = items[:max_items]

        result.discovered = items
        result.metadata["discovered_count"] = len(items)
        # 2026-08-25 initial-fill signal: did THIS run's slice re-serve any
        # already-known URL? A pure-unseen slice means the first catalogue
        # pass has not yet wrapped (initial-fill suppression may apply);
        # any known URL in the slice proves the pass has wrapped.
        result.metadata["slice_is_pure_unseen"] = (
            known_product_urls is not None and len(items) > 0
            and all(i.url not in known_product_urls for i in items)
        )

        # Honest status: SUCCESS requires a genuinely successful discovery
        # fetch; an empty-but-200 catalogue is ZERO_ITEMS; every fetch failed
        # (non-blocked) is FAILED.
        any_fetch_success = any(f.success for f in fetches)
        if items:
            result.metadata["component_status"] = "SUCCESS"
        elif any_fetch_success:
            result.metadata["component_status"] = "ZERO_ITEMS"
        else:
            result.metadata["component_status"] = "FAILED"
        result.metadata["healthy"] = result.metadata["component_status"] in ("SUCCESS", "PARTIAL")

        # Per-item synthetic fetches flow through the standard pipeline path.
        for item in items:
            product_json = item.metadata.get("product_json")
            result.fetched.append(
                FetchResult(
                    url=item.url,
                    success=True,
                    status_code=200,
                    content_type="application/json",
                    payload=json.dumps(product_json).encode("utf-8") if product_json else b"{}",
                )
            )
        return result


def _is_valid_json(payload: bytes | str) -> bool:
    raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
    try:
        json.loads(raw)
        return True
    except json.JSONDecodeError:
        return False
