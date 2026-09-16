"""Casio Japan sitemap-delta collector (www.casio.com/jp).

Closes the single largest live coverage gap found by the 2026-08-21 hostile
audit: Casio's JP product pages (www.casio.com/jp/watches/...) are
Akamai-blocked (HTTP 403, confirmed live from this macOS vantage point and
repeatedly in production casio_multi runs -- not attempted to bypass), which
left the 2,207-watch Casio catalogue with ZERO transition capability in
Japan, the brand's home market and earliest release surface.

**The JP watches sitemap is not blocked** (live-verified 2026-08-21):
`https://www.casio.com/jp/sitemap/watches.xml` returns HTTP 200 with
~17,800 URLs; it is published via the robots.txt-listed sitemap index
(`https://www.casio.com/jp/sitemap.xml`, itself listed as
`Sitemap: https://www.casio.com/jp/sitemap.xml` in robots.txt); robots.txt
has no /jp disallow and no AI-crawler-specific restrictions. Unlike the UK
and Europe sitemaps -- whose `<lastmod>` values were verified weeks-to-
months stale -- the JP watches sitemap carries lastmod timestamps updated
DAILY (entries dated the day before each probe observed).

URL shapes handled:
    /jp/watches/{gshock|casio|babyg|edifice|oceanus|protrek|sheen}/product.<REF>/
Deliberately EXCLUDED:
    /jp/watches/options/option.<REF>/   strap/bracelet options -- the
                                        accessory class, not watches
    /jp/watches/casio/product.PAIR_.../ two-watch marketing bundles --
                                        not a single reference

Honest limitation, identical to casio_uk_sitemap: the sitemap carries only
a canonical URL and `<lastmod>` -- no price, currency, or availability.
This collector can therefore produce NEW_REFERENCE (first sighting) or
NEW_REGION (known reference, first JP observation) and never
PRICE_CHANGE/SOLD_OUT/RESTOCK. The fresh daily lastmod is stored in
extra_specs as weak recency evidence; it is NOT a publication timestamp.
"""

from __future__ import annotations

import re

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import component_status_from_fetches, fetch_url, is_blocked_response

COLLECTOR_ID = "casio_jp_sitemap"
COLLECTOR_VERSION = "0.1.0"
REGION = "JP"
TRUST_SCORE = 70.0  # same evidence class as casio_uk_sitemap: URL+lastmod only
JP_WATCHES_SITEMAP_URL = "https://www.casio.com/jp/sitemap/watches.xml"
MAX_CANDIDATES = 3000

_URL_BLOCK_RE = re.compile(r"<url>\s*<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>\s*)?</url>", re.IGNORECASE)
_REFERENCE_RE = re.compile(r"/watches/[a-z]+/product\.([A-Za-z0-9-]+?)/?$", re.IGNORECASE)
_OPTION_RE = re.compile(r"/options/option\.", re.IGNORECASE)
_PAIR_RE = re.compile(r"product\.PAIR_", re.IGNORECASE)


class CasioJPSitemapCollector:
    """Discover canonical Casio Japan watch URLs + lastmod from the official
    sitemap only -- never fetches the (Akamai-blocked) product pages."""

    def discover_from_sitemap_xml(self, payload: str | bytes) -> list[DiscoveredItem]:
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        for url, lastmod in _URL_BLOCK_RE.findall(text):
            if _OPTION_RE.search(url) or _PAIR_RE.search(url):
                continue  # straps/options and marketing bundles are not watch references
            m = _REFERENCE_RE.search(url)
            if not m:
                continue
            reference = m.group(1).upper()
            if reference in seen:
                continue
            seen.add(reference)
            items.append(
                DiscoveredItem(
                    url=url,
                    title=reference,
                    reference_hint=reference,
                    metadata={"source_region": REGION, "lastmod": lastmod},
                )
            )
            if len(items) >= MAX_CANDIDATES:
                break
        return items

    def run(
        self,
        *,
        max_items: int | None = 300,
        sitemap_payload: bytes | None = None,
        known_product_urls: set[str] | None = None,
    ) -> CollectorRunResult:
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, region=REGION, trust_score=TRUST_SCORE
        )
        sitemap_fetch = (
            FetchResult(JP_WATCHES_SITEMAP_URL, True, 200, "application/xml", sitemap_payload)
            if sitemap_payload is not None
            else fetch_url(JP_WATCHES_SITEMAP_URL)
        )
        discovery_fetches = [sitemap_fetch]
        if not sitemap_fetch.success or not sitemap_fetch.payload:
            result.metadata["component_status"] = "BLOCKED" if is_blocked_response(
                sitemap_fetch.status_code, sitemap_fetch.payload, sitemap_fetch.error
            ) else "FAILED"
            result.metadata["healthy"] = False
            result.fetched = discovery_fetches
            return result

        # Track E: retain the document this run actually selected from.
        result.discovery_payloads = [sitemap_fetch]

        discovered = self.discover_from_sitemap_xml(sitemap_fetch.payload)
        result.metadata["candidate_count"] = len(discovered)

        known = known_product_urls or set()
        new_items = [i for i in discovered if i.url not in known]
        known_items = [i for i in discovered if i.url in known]
        pending = (new_items + known_items) if known else discovered
        deferred = pending[max_items:] if max_items is not None else []
        if max_items is not None:
            pending = pending[:max_items]
        result.discovered = pending
        result.metadata["discovered_count"] = len(pending)
        result.metadata["known_url_count"] = len(known)
        # Track E.2 / D.5: why a candidate did NOT get processed this run.
        # Reported, never silently dropped -- the pipeline writes it to the
        # ledger. MAX_CANDIDATES truncation is called out separately from
        # the per-run budget because it is a HARD ceiling on what is even
        # considered: this sitemap carries ~17,800 URLs against a 3,000
        # candidate cap, so ~14,800 are invisible to every run regardless of
        # budget or prioritization.
        result.metadata["selection"] = {
            "policy": "unseen_first" if known else "document_order",
            "candidate_count": len(discovered),
            "selected_count": len(pending),
            "deferred_count": len(deferred),
            "max_items": max_items,
            "max_candidates": MAX_CANDIDATES,
            "truncated_at_max_candidates": len(discovered) >= MAX_CANDIDATES,
            "deferred_reason": "per_run_item_budget" if deferred else None,
            "deferred_sample": [i.reference_hint for i in deferred[:20]],
        }

        # No per-item fetch: the sitemap itself is the entire evidence base
        # (see module docstring). Each "fetch" is a synthetic wrapper around
        # the discovered item's own url+lastmod so it flows through the same
        # process_fetch_result path as every other product collector.
        for item in pending:
            result.fetched.append(
                FetchResult(
                    url=item.url, success=True, status_code=200, content_type="application/json",
                    payload=(
                        f'{{"reference": "{item.reference_hint}", "lastmod": "{item.metadata["lastmod"]}"}}'
                    ).encode(),
                )
            )

        status = component_status_from_fetches(
            discovery_fetches=discovery_fetches,
            item_fetches=result.fetched,
            useful_count=sum(1 for f in result.fetched if f.success),
        )
        result.metadata["component_status"] = status
        result.metadata["healthy"] = status in {"SUCCESS", "PARTIAL", "ZERO_ITEMS"}
        result.metadata["discovery_fetches"] = [
            {"url": sitemap_fetch.url, "status": sitemap_fetch.status_code, "success": sitemap_fetch.success}
        ]
        return result
