"""Reusable sitemap-delta collector family.

Extracted from the proven casio_uk_sitemap.py pattern (2026-08-14 UK
signal-path research) so brand N+1 becomes configuration, not architecture.

The family contract (identical to every existing Watch Clank collector):

- Collectors do NETWORK DISCOVERY ONLY -- they never touch the database.
- One sitemap fetch per run; each discovered item becomes a synthetic
  FetchResult carrying {reference, lastmod} so it flows through the same
  process_fetch_result path as full product collectors.
- Honest limitation preserved: a bare sitemap carries no price, no
  currency, no availability. Those fields stay None -- never guessed.
- New-first traversal with known-URL deprioritization (slice-starvation
  repair, 2026-08-24): unseen URLs are processed before known ones.
- component_status BLOCKED / FAILED / ZERO_ITEMS / SUCCESS via the shared
  http_util helper, so source-health semantics stay uniform fleet-wide.

A brand supplies configuration only:
    collector_id, region, sitemap_url, url_pattern (regex with one group =
    reference), reference_transform (e.g. upper()), trust_score.

The Tissot collector is the first consumer of this family; subsequent
brands (Longines regional stores, Seiko JP catalogue surfaces, etc.)
should add entries here rather than new bespoke collectors.
"""

from __future__ import annotations

import re

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import component_status_from_fetches, fetch_url, is_blocked_response


class SitemapDeltaConfig:
    """Configuration contract for one sitemap-delta collector instance."""

    def __init__(
        self,
        *,
        collector_id: str,
        region: str,
        sitemap_url: str,
        reference_pattern: re.Pattern[str],
        trust_score: float = 70.0,
        max_candidates: int = 3000,
        accept_language: str | None = None,
    ) -> None:
        self.collector_id = collector_id
        self.region = region
        self.sitemap_url = sitemap_url
        self.reference_pattern = reference_pattern
        self.trust_score = trust_score
        self.max_candidates = max_candidates
        self.accept_language = accept_language


class SitemapDeltaCollector:
    """Generic sitemap-delta collector driven entirely by SitemapDeltaConfig."""

    def __init__(self, config: SitemapDeltaConfig, *, version: str = "0.1.0") -> None:
        self._config = config
        self.COLLECTOR_ID = config.collector_id
        self.REGION = config.region
        COLLECTOR_VERSION = version  # noqa: F841 -- parity with sibling modules

    _URL_BLOCK_RE = re.compile(
        r"<url>\s*<loc>([^<]+)</loc>(?:\s*<lastmod>([^<]*)</lastmod>)?", re.IGNORECASE
    )

    def discover_from_sitemap_xml(self, payload: str | bytes) -> list[DiscoveredItem]:
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        for url, lastmod in self._URL_BLOCK_RE.findall(text):
            m = self._config.reference_pattern.search(url)
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
                    metadata={"source_region": self._config.region, "lastmod": lastmod or ""},
                )
            )
            if len(items) >= self._config.max_candidates:
                break
        return items

    def run(
        self,
        *,
        max_items: int | None = 300,
        sitemap_payload: bytes | None = None,
        known_product_urls: set[str] | None = None,
    ) -> CollectorRunResult:
        cfg = self._config
        result = CollectorRunResult(
            collector_id=cfg.collector_id,
            collector_version="0.1.0",
            region=cfg.region,
            trust_score=cfg.trust_score,
        )
        sitemap_fetch = (
            FetchResult(cfg.sitemap_url, True, 200, "application/xml", sitemap_payload)
            if sitemap_payload is not None
            else fetch_url(cfg.sitemap_url, accept_language=cfg.accept_language or "en-US,en;q=0.9")
        )
        discovery_fetches = [sitemap_fetch]
        if not sitemap_fetch.success or not sitemap_fetch.payload:
            result.metadata["component_status"] = (
                "BLOCKED"
                if is_blocked_response(sitemap_fetch.status_code, sitemap_fetch.payload, sitemap_fetch.error)
                else "FAILED"
            )
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
            "max_candidates": self._config.max_candidates,
            "truncated_at_max_candidates": len(discovered) >= self._config.max_candidates,
            "deferred_reason": "per_run_item_budget" if deferred else None,
            "deferred_sample": [i.reference_hint for i in deferred[:20]],
        }

        # No per-item fetch: the sitemap itself is the entire evidence base.
        # Each synthetic fetch carries {reference, lastmod} through the same
        # process_fetch_result path as every other product collector.
        for item in pending:
            result.fetched.append(
                FetchResult(
                    url=item.url,
                    success=True,
                    status_code=200,
                    content_type="application/json",
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
        result.metadata["healthy"] = status in ("SUCCESS", "PARTIAL")
        return result
