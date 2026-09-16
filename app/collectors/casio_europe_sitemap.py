"""Casio Europe (mainland) regional sitemap-delta collector (www.casio.com/europe).

2026-08-17 Hall-of-Shame post-repair autopsy: the existing `casio_uk_sitemap`
collector (see that module for the full original reconnaissance and its
"honest limitation" design notes, all of which apply identically here)
covers the UK only. A real specimen -- Casio GBA-950 (colourways
GBA-950-1A/-2A/-7A/-7A2/-9A3) -- was found live on `www.casio.com/europe/
sitemap.xml` and `www.casio.com/de/sitemap.xml` but is **absent** from the
UK sitemap entirely: a genuine, evidence-confirmed EU-mainland regional
coverage gap, not a UK one. This is the same technically-proven pattern as
UK, just a different regional site: `www.casio.com/europe/watches/.../
product.<REF>/` product pages are Cloudflare-blocked (HTTP 403, confirmed
live), but `www.casio.com/europe/sitemap.xml` returns HTTP 200 and is
listed in `www.casio.com/robots.txt` with no crawler-specific disallow.
`/europe/` was chosen over a single-country sitemap (e.g. `/de/`) because
it is the broader, genuinely pan-EU-mainland listing Casio itself
publishes -- avoiding building N per-country collectors for what is one
already-consolidated regional catalogue.

Same honest limitation as UK: the sitemap carries only a canonical URL and
a `<lastmod>` timestamp -- no price, no currency, no availability. This
collector therefore only ever produces a `SourceObservation` with
`price=None`/`currency=None`/`availability_status=None`, and can only ever
produce `NEW_REFERENCE` or `NEW_REGION` (never a price/availability
transition) for the same reasons documented in `casio_uk_sitemap.py`.
"""

from __future__ import annotations

import re

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import component_status_from_fetches, fetch_url, is_blocked_response

COLLECTOR_ID = "casio_europe_sitemap"
COLLECTOR_VERSION = "0.1.0"
REGION = "EU"
TRUST_SCORE = 70.0  # matches casio_uk_sitemap: no price/availability evidence, see module docstring
EUROPE_SITEMAP_URL = "https://www.casio.com/europe/sitemap.xml"
MAX_CANDIDATES = 3000

_URL_BLOCK_RE = re.compile(r"<url>\s*<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>\s*</url>", re.IGNORECASE)
_REFERENCE_RE = re.compile(r"product\.([A-Za-z0-9-]+?)/?$")


class CasioEuropeSitemapCollector:
    """Discover canonical Casio Europe product URLs + lastmod from the
    official sitemap only -- never fetches the (Cloudflare-blocked) product
    pages themselves. Structurally identical to CasioUKSitemapCollector;
    kept as a separate module rather than a parameterized shared class to
    match this project's existing one-file-per-region convention (see
    citizen_products.py / citizen_de_products.py)."""

    def discover_from_sitemap_xml(self, payload: str | bytes) -> list[DiscoveredItem]:
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        for url, lastmod in _URL_BLOCK_RE.findall(text):
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
            FetchResult(EUROPE_SITEMAP_URL, True, 200, "application/xml", sitemap_payload)
            if sitemap_payload is not None
            else fetch_url(EUROPE_SITEMAP_URL, accept_language="en-GB,en;q=0.9")
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
        # Track E.2 / D.5: report why a candidate was not processed this
        # run, so a later "was it in the feed?" question is answerable.
        # The MAX_CANDIDATES ceiling is reported separately from the
        # per-run budget: it caps what is even CONSIDERED, not just what is
        # processed this cycle.
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
