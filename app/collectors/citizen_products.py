"""Citizen US product/catalogue collector (citizenwatch.com).

Sprint 3 discovery (still available as discover_from_collection_html /
DISCOVERY_COLLECTIONS below) scraped product links off a handful of small
collection pages — good for depth (individual product pages carry
inventory/availability) but poor for breadth (only ~8-50 references per
category, and each product needed its own HTTP fetch).

Sprint 4 adds discover_via_search, the primary run() path: citizenwatch.com's
collection pages are themselves backed by a server-rendered search/listing
API — the same React Query hydration JSON pattern as the individual product
page, but keyed by `"data":{"limit":...,"effectiveSearchMode"` instead of
`"data":{"brand":"CITIZEN"`. Each page of up to 48 "hits" already carries
almost the full product record (price, currency, collection, case material,
movement, water resistance, dial/band/crystal, intro date — everything
except inventory/availability, which only the individual product page has).
Paginating this via `?offset=N&limit=48` on two broad top-level categories
(mens: 348, womens: 182 total, confirmed live 2026-08-11) discovers the
large majority of the real US catalogue in ~12 lightweight requests total,
with ZERO additional per-product HTTP fetches — far more conservative on
the source than fetching hundreds of individual product pages, and the
right tradeoff for Sprint 4's "widen coverage" priority over Sprint 3's
per-product depth. See parse_citizen_search_hit for the availability
tradeoff this implies.

robots.txt does not disallow /collection/ or /product/ (only /search,
/cart, /archive, and a few account endpoints). Confirmed live 2026-08-11.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import component_status_from_fetches, fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "citizen_products"
COLLECTOR_VERSION = "0.1.0"
REGION = "US"
TRUST_SCORE = 95.0
BASE_URL = "https://citizenwatch.com"

# Legacy Sprint-3 seed list, kept for discover_from_collection_html (still
# usable for a depth pass with real availability data on a small sample).
DISCOVERY_COLLECTIONS = (
    "/us/en/collection/attesa",
    "/us/en/collection/tsuyosa",
    "/us/en/collection/collabs",
)

# Sprint 4 broad-discovery seeds: two top-level gender categories cover the
# large majority of the catalogue (confirmed totals live 2026-08-11: mens
# 348, womens 182) without needing to enumerate every sub-collection.
SEARCH_COLLECTIONS = ("mens", "womens")
SEARCH_PAGE_SIZE = 48
# Safety cap per collection so a catalogue-size anomaly (e.g. a search bug
# reporting an inflated total) cannot trigger unbounded pagination.
MAX_CANDIDATES_PER_COLLECTION = 600
# Safety cap on the per-run-new-item availability enrichment fetches added
# by the 2026-08-18 Citizen stale/OOS flood autopsy -- see run()'s
# docstring comment. A steady-state run typically has a few dozen new
# items at most; this caps the worst case defensively.
MAX_AVAILABILITY_ENRICHMENT_FETCHES = 60

_PRODUCT_HREF_RE = re.compile(r'href="(/us/en/product/[A-Za-z0-9-]+)"')
_SEARCH_ANCHOR_RE = re.compile(r'"data"\s*:\s*\{\s*"limit"\s*:\s*\d+\s*,\s*"effectiveSearchMode"')


def _extract_balanced_json(html: str, anchor_match: re.Match) -> dict | None:
    start = html.index("{", anchor_match.start())
    depth = 0
    i = start
    n = len(html)
    while i < n:
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        return None
    try:
        return json.loads(html[start : i + 1])
    except json.JSONDecodeError:
        return None


class CitizenProductsCollector:
    """Discover Citizen US products. No DB writes."""

    # --- Sprint 3 legacy path: per-product-page depth, small sample -------
    def discover_from_collection_html(self, html: str, base_url: str) -> list[DiscoveredItem]:
        seen: set[str] = set()
        items: list[DiscoveredItem] = []
        for m in _PRODUCT_HREF_RE.finditer(html):
            url = urljoin(base_url, m.group(1))
            if url in seen:
                continue
            seen.add(url)
            ref = url.rsplit("/", 1)[-1]
            items.append(DiscoveredItem(url=url, title=ref, reference_hint=ref, metadata={"source_region": REGION}))
        return items

    # --- Sprint 4 primary path: search-listing breadth ---------------------
    def parse_search_page(self, html: str) -> tuple[list[DiscoveredItem], int | None]:
        """Returns (items, total_reported_by_source). Each item's metadata
        carries a flattened product dict ready for parse_citizen_search_hit."""
        m = _SEARCH_ANCHOR_RE.search(html)
        if not m:
            return [], None
        data = _extract_balanced_json(html, m)
        if not data:
            return [], None
        total = data.get("total")
        items: list[DiscoveredItem] = []
        for hit in data.get("hits", []):
            rp = hit.get("representedProduct")
            if not rp or not rp.get("id"):
                continue
            merged = {**rp, "_hit_price": hit.get("price"), "_hit_currency": hit.get("currency")}
            items.append(
                DiscoveredItem(
                    url=urljoin(BASE_URL, f"/us/en/product/{rp['id']}"),
                    title=hit.get("productName"),
                    reference_hint=rp["id"],
                    metadata={"source_region": REGION, "product_dict": merged},
                )
            )
        return items, total if isinstance(total, int) else None

    def discover_via_search(
        self, *, search_pages: dict[str, list[bytes]] | None = None
    ) -> tuple[list[DiscoveredItem], list[FetchResult]]:
        """search_pages: optional {collection_slug: [page1_html, page2_html, ...]}
        for offline/fixture use, replacing live fetch_url calls."""
        all_items: list[DiscoveredItem] = []
        seen_refs: set[str] = set()
        fetches: list[FetchResult] = []

        for slug in SEARCH_COLLECTIONS:
            offset = 0
            fetched_this_collection = 0
            total: int | None = None
            page_index = 0
            while True:
                url = f"{BASE_URL}/us/en/collection/{slug}?offset={offset}&limit={SEARCH_PAGE_SIZE}"
                if search_pages is not None:
                    pages = search_pages.get(slug, [])
                    payload = pages[page_index] if page_index < len(pages) else None
                    fr = (
                        FetchResult(url=url, success=True, status_code=200, content_type="text/html", payload=payload)
                        if payload is not None
                        else FetchResult(url=url, success=False, error="no fixture page")
                    )
                else:
                    fr = fetch_url(url)
                fetches.append(fr)
                page_index += 1

                if not fr.success or not fr.payload:
                    break

                items, page_total = self.parse_search_page(fr.payload.decode("utf-8", errors="ignore"))
                if page_total is not None:
                    total = page_total
                if not items:
                    break

                for item in items:
                    ref = item.reference_hint
                    if ref in seen_refs:
                        continue
                    seen_refs.add(ref)
                    all_items.append(item)

                fetched_this_collection += len(items)
                offset += SEARCH_PAGE_SIZE
                if total is not None and offset >= total:
                    break  # authoritative: source told us how many there are
                if fetched_this_collection >= MAX_CANDIDATES_PER_COLLECTION:
                    logger.warning("citizen_search_pagination_capped", slug=slug, cap=MAX_CANDIDATES_PER_COLLECTION)
                    break
                if total is None and len(items) < SEARCH_PAGE_SIZE:
                    # No authoritative total available (unexpected page shape) —
                    # fall back to the short-page heuristic rather than looping
                    # on a source we can't otherwise bound.
                    break

        return all_items, fetches

    def run(
        self,
        *,
        max_items: int | None = 60,
        search_pages: dict[str, list[bytes]] | None = None,
        known_product_urls: set[str] | None = None,
        fetch_availability_for_new: bool = True,
        detail_pages: dict[str, bytes] | None = None,
    ) -> CollectorRunResult:
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, region=REGION, trust_score=TRUST_SCORE
        )
        items, discovery_fetches = self.discover_via_search(search_pages=search_pages)

        result.metadata["index_blocked"] = bool(discovery_fetches) and all(
            is_blocked_response(f.status_code, f.payload, f.error) for f in discovery_fetches
        )
        result.metadata["candidate_count"] = len(items)
        if not items and result.metadata["index_blocked"]:
            result.metadata["component_status"] = "BLOCKED"
            result.metadata["healthy"] = False
            result.fetched = discovery_fetches
            return result

        # See timex_products.py's run() for the full discovery-cap rationale:
        # the real US catalogue (~530 across mens+womens) already exceeds
        # the default 300-item processing budget, so a plain positional
        # slice can permanently starve a genuinely new SKU that happens to
        # sort past the cap. Known URLs are deprioritized, not dropped.
        known = known_product_urls or set()
        if max_items is not None and known:
            new_items = [i for i in items if i.url not in known]
            known_items = [i for i in items if i.url in known]
            items = (new_items + known_items)[:max_items]
        elif max_items is not None:
            items = items[:max_items]
        result.discovered = items
        result.metadata["discovered_count"] = len(items)

        # Availability enrichment (2026-08-18 Citizen stale/OOS flood
        # autopsy): the cheap search-hit path above never carries real
        # inventory data (see class docstring), so every first observation
        # of a Citizen product used to record availability_status=UNKNOWN,
        # indistinguishable from a genuinely current, in-stock launch. The
        # individual product page DOES carry real inventory (see
        # parse_citizen_product_html / _availability_from_inventory) --
        # this was already fetched for the small Sprint-3 depth path, just
        # never wired into the primary breadth path.
        #
        # Fix: for items not already known to this database -- i.e. exactly
        # the ones that will actually produce a NEW_REFERENCE event -- do
        # one extra real HTTP fetch of the product page and parse that
        # instead of the cheap repackaged search hit. This is bounded (only
        # genuinely-new items per run, never the full up-to-max_items
        # catalogue slice -- typically a handful to a few dozen, not
        # hundreds) and additive: on any fetch/parse failure it falls back
        # to the pre-existing cheap record rather than dropping the item,
        # so recall never regresses. Every already-known item (the large
        # majority of any steady-state run) is untouched -- same zero-extra-
        # fetch behavior as before. See parse_citizen_search_hit for how a
        # real HTML payload here is distinguished from the repackaged JSON.
        # Gated on max_items being bounded (a normal/steady-state run):
        # pipeline.py passes max_items=None for a force-baseline/first-run
        # sweep, where every item is "new" by definition (nothing has ever
        # been observed) and every resulting event is baseline-suppressed
        # anyway -- enriching hundreds of items nobody will ever see would
        # only load the source for zero editorial benefit. A hard cap is
        # kept regardless as defense in depth against known_product_urls
        # unexpectedly coming back empty on a bounded run.
        #
        # Offline/fixture mode (search_pages is not None, the same signal
        # discover_via_search already uses to avoid real network calls)
        # never issues a real fetch here either -- only `detail_pages`
        # (explicit {url: html_bytes} fixtures) can supply enrichment
        # payloads offline. This keeps every pre-existing test byte-for-byte
        # deterministic: passing search_pages alone continues to produce
        # zero real HTTP calls, exactly as before this change.
        known_urls = known_product_urls or set()
        new_urls: set[str] = set()
        capped_out = 0
        if fetch_availability_for_new and max_items is not None:
            all_new = {i.url for i in items if i.url not in known_urls}
            if len(all_new) > MAX_AVAILABILITY_ENRICHMENT_FETCHES:
                capped_out = len(all_new) - MAX_AVAILABILITY_ENRICHMENT_FETCHES
                new_urls = set(list(all_new)[:MAX_AVAILABILITY_ENRICHMENT_FETCHES])
            else:
                new_urls = all_new
        offline = search_pages is not None
        for item in items:
            if item.url in new_urls:
                if offline:
                    fixture_html = (detail_pages or {}).get(item.url)
                    detail_fr = (
                        FetchResult(url=item.url, success=True, status_code=200, content_type="text/html", payload=fixture_html)
                        if fixture_html is not None
                        else FetchResult(url=item.url, success=False, error="no detail fixture page")
                    )
                else:
                    detail_fr = fetch_url(item.url)
                if detail_fr.success and detail_fr.payload:
                    result.fetched.append(detail_fr)
                    continue
            product_dict = dict(item.metadata.get("product_dict") or {})
            # Availability provenance (2026-08-21): make WHY an observation
            # is UNKNOWN visible downstream instead of conflating "source
            # has no field" with "we could not enrich". Item 61+ of a cap
            # overflow and a failed detail fetch are different facts.
            if item.url in new_urls:
                product_dict["availability_provenance"] = "ENRICHMENT_FETCH_FAILED"
            elif capped_out and item.url not in known_urls:
                product_dict["availability_provenance"] = "NOT_ENRICHED_CAP"
            result.fetched.append(
                FetchResult(
                    url=item.url, success=True, status_code=200, content_type="application/json",
                    payload=json.dumps(product_dict).encode("utf-8"),
                )
            )

        if capped_out:
            result.metadata["availability_enrichment_capped_out"] = capped_out

        useful = sum(1 for f in result.fetched if f.success)
        status = component_status_from_fetches(
            discovery_fetches=discovery_fetches, item_fetches=result.fetched, useful_count=useful
        )
        if status == "ZERO_ITEMS" and items:
            status = "SUCCESS"
        if not items and any(f.success for f in discovery_fetches):
            status = "ZERO_ITEMS"
        result.metadata["component_status"] = status
        result.metadata["healthy"] = status in ("SUCCESS", "PARTIAL")
        result.metadata["discovery_fetches"] = [
            {"url": f.url, "status": f.status_code, "success": f.success} for f in discovery_fetches
        ]
        return result
