"""Sentinel discovery adapters: cheap first-party feed -> Candidate list.

Contract:
- One poll = ONE discovery request (plus at most one index-child fetch for
  sitemaps). No product-page fetches: a sitemap/JSON feed must identify a
  candidate on its own, exactly like the sitemap_family collectors.
- Adapters never touch the database and never decide novelty -- they only
  surface (reference?, title?, url) tuples for the identity stage.
- Every adapter accepts an injectable fetch_fn(url) -> FetchResult so tests
  run fully offline; production uses app.collectors.http_util.fetch_url,
  inheriting fleet retries/backoff/blocked-detection.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from app.collectors.base import FetchResult
from app.collectors.casio_jp_sitemap import CasioJPSitemapCollector
from app.collectors.casio_uk_sitemap import CasioUKSitemapCollector
from app.collectors.http_util import fetch_url, is_blocked_response
from app.collectors.sitemap_family import SitemapDeltaCollector, SitemapDeltaConfig
from app.core.logging import get_logger
from app.sentinel.config import SentinelSourceConfig

logger = get_logger(__name__)

FetchFn = Callable[[str], FetchResult]


@dataclass(frozen=True)
class Candidate:
    """One discovered product candidate, pre-identity."""

    source: str
    manufacturer: str
    region: str
    url: str
    reference_raw: str | None = None
    title: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class PollOutcome:
    candidates: list[Candidate]
    status: str  # SUCCESS | ZERO_ITEMS | BLOCKED | FAILED
    error: str | None = None
    request_count: int = 0


def default_fetch_fn(url: str) -> FetchResult:
    return fetch_url(url)


class ShopifyProductsAdapter:
    """Plain unscoped /products.json, page 1 only (newest-first, see config)."""

    def poll(self, config: SentinelSourceConfig, fetch_fn: FetchFn) -> PollOutcome:
        assert config.listing_url_template and config.product_url_template
        url = config.listing_url_template.format(page=1)
        fr = fetch_fn(url)
        if not fr.success or fr.payload is None:
            status = "BLOCKED" if is_blocked_response(fr.status_code, fr.payload, fr.error) else "FAILED"
            return PollOutcome([], status=status, error=fr.error or f"HTTP {fr.status_code}", request_count=1)
        try:
            data = json.loads(fr.payload)
            products = data.get("products", [])
        except (ValueError, TypeError) as exc:
            return PollOutcome([], status="FAILED", error=f"unparseable products.json: {exc}", request_count=1)

        candidates: list[Candidate] = []
        for product in products:
            if not isinstance(product, dict):
                continue
            if config.product_type_filter and product.get("product_type") != config.product_type_filter:
                continue
            handle = product.get("handle")
            if not handle:
                continue
            sku = _first_variant_sku(product)
            candidates.append(
                Candidate(
                    source=config.name,
                    manufacturer=config.manufacturer,
                    region=config.region,
                    url=config.product_url_template.format(handle=handle),
                    reference_raw=sku,
                    title=_clean_title(product.get("title")),
                    metadata={"published_at": product.get("published_at")},
                )
            )
            if len(candidates) >= config.max_candidates:
                break

        status = "SUCCESS" if candidates else "ZERO_ITEMS"
        return PollOutcome(candidates, status=status, request_count=1)


class SitemapAdapter:
    """One sitemap document (plus at most one index-child fetch).

    Reference extraction is delegated per discovery_kind so brand rules
    live in exactly one place: casio_uk/casio_jp reuse those collectors'
    own discover_from_sitemap_xml (including JP's option/strap URL
    exclusions); "generic" reuses the shared sitemap_family machinery.
    """

    _INDEX_LOC_RE = re.compile(r"<loc>\s*([^<]+)\s*</loc>", re.IGNORECASE)

    def poll(self, config: SentinelSourceConfig, fetch_fn: FetchFn) -> PollOutcome:
        assert config.sitemap_url
        requests = 0
        fr = fetch_fn(config.sitemap_url)
        requests += 1
        if not fr.success or fr.payload is None:
            status = "BLOCKED" if is_blocked_response(fr.status_code, fr.payload, fr.error) else "FAILED"
            return PollOutcome([], status=status, error=fr.error or f"HTTP {fr.status_code}", request_count=requests)

        text = fr.payload.decode("utf-8", errors="ignore")
        if "<sitemapindex" in text.lower():
            child = self._pick_index_child(text, config.index_child_pattern)
            if child is None:
                return PollOutcome(
                    [], status="FAILED", error="sitemap index without a matching child", request_count=requests
                )
            fr = fetch_fn(child)
            requests += 1
            if not fr.success or fr.payload is None:
                status = "BLOCKED" if is_blocked_response(fr.status_code, fr.payload, fr.error) else "FAILED"
                return PollOutcome([], status=status, error=fr.error or f"HTTP {fr.status_code}", request_count=requests)
            text = fr.payload.decode("utf-8", errors="ignore")

        discovered = self._discover(config, text)
        candidates: list[Candidate] = []
        for item in discovered:
            reference = item.reference_hint
            candidates.append(
                Candidate(
                    source=config.name,
                    manufacturer=config.manufacturer,
                    region=config.region,
                    url=item.url,
                    reference_raw=reference,
                    # Sitemaps genuinely carry no title -- it stays None and
                    # the alert omits the line rather than inventing one.
                    title=None,
                    metadata={"lastmod": item.metadata.get("lastmod", "")},
                )
            )
            if len(candidates) >= config.max_candidates:
                logger.warning("sentinel_candidate_cap_reached", source=config.name, cap=config.max_candidates)
                break

        status = "SUCCESS" if candidates else "ZERO_ITEMS"
        return PollOutcome(candidates, status=status, request_count=requests)

    def _discover(self, config: SentinelSourceConfig, text: str):
        if config.discovery_kind == "casio_uk":
            return CasioUKSitemapCollector().discover_from_sitemap_xml(text)
        if config.discovery_kind == "casio_jp":
            return CasioJPSitemapCollector().discover_from_sitemap_xml(text)
        # generic: shared sitemap_family machinery with this source's pattern
        if config.reference_pattern is None:
            raise ValueError(f"source {config.name}: generic sitemap discovery needs reference_pattern")
        collector = SitemapDeltaCollector(
            SitemapDeltaConfig(
                collector_id=f"sentinel_{config.name}",
                region=config.region,
                sitemap_url=config.sitemap_url or "",
                reference_pattern=config.reference_pattern,
                max_candidates=config.max_candidates,
            )
        )
        return collector.discover_from_sitemap_xml(text)

    def _pick_index_child(self, index_text: str, pattern) -> str | None:
        for loc in self._INDEX_LOC_RE.findall(index_text):
            if pattern is None or pattern.search(loc):
                return loc.strip()
        return None


_ADAPTORS = {
    "shopify_products": ShopifyProductsAdapter(),
    "sitemap": SitemapAdapter(),
}


def poll_source(config: SentinelSourceConfig, fetch_fn: FetchFn | None = None) -> PollOutcome:
    adapter = _ADAPTORS.get(config.adapter)
    if adapter is None:
        return PollOutcome([], status="FAILED", error=f"unknown adapter {config.adapter!r}")
    return adapter.poll(config, fetch_fn or default_fetch_fn)


def _first_variant_sku(product: dict) -> str | None:
    variants = product.get("variants") or []
    for variant in variants:
        if isinstance(variant, dict):
            sku = (variant.get("sku") or "").strip()
            if sku:
                return sku
    return None


def _clean_title(title: object) -> str | None:
    if not isinstance(title, str):
        return None
    stripped = " ".join(title.split())
    return stripped[:512] or None
