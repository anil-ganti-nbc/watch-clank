"""Experimental Goldsmiths UK Citizen retailer collector.

Goldsmiths exposes a JSON sitemap index whose child product sitemaps contain
large, unsorted URL lists. The collector always reads every child sitemap and
filters Citizen URLs before applying the detail-page budget. A budget applied
while walking the raw sitemap would starve Citizen products (the live spike
found them thousands of positions into a 50,000-URL child).

This is third-party retailer evidence, not first-party Citizen evidence. The
lane is deliberately experimental and its events are delivery-blocked by the
fleet maturity gate until an operator promotes it.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
from app.collectors.http_util import fetch_url, is_blocked_response

COLLECTOR_ID = "goldsmiths_uk_retailer"
COLLECTOR_VERSION = "0.1.0"
REGION = "GB"
TRUST_SCORE = 70.0
SOURCE_CLASS = "RETAILER"
EVIDENCE_GRADE = "THIRD_PARTY_RETAILER_LISTING"
AUTHORIZED_STOCKIST = True

SITEMAP_INDEX_URL = "https://www.goldsmiths.co.uk/sitemap.xml"
PRODUCT_SITEMAP_PREFIX = "https://www.goldsmiths.co.uk/sitemap/product-en-gbp-"
DETAIL_FETCH_CAP = 60
DEFAULT_DETAIL_BUDGET = 300

_REFERENCE_RE = re.compile(r"(?P<reference>[A-Za-z]{2}\d{4}[-+_][A-Za-z0-9]{2,})", re.IGNORECASE)


@dataclass(frozen=True)
class _Candidate:
    url: str
    reference_hint: str | None


def _as_bytes(value: bytes | str | dict | list) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value).encode("utf-8")


def _json_object(payload: bytes | str | dict) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _locs_from_xml(payload: bytes | str) -> list[str]:
    raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    locs: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() == "loc" and element.text:
            locs.append(html.unescape(element.text.strip()))
    return locs


def extract_reference_hint(url: str) -> str | None:
    """Extract a shape-validated hint without rewriting literal plus signs.

    The detail page's MPN remains the identity authority. This hint exists
    only for discovery diagnostics and traversal ordering.
    """
    match = _REFERENCE_RE.search(urlparse(url).path)
    if not match:
        return None
    return match.group("reference")


def _is_citizen_url(url: str) -> bool:
    path = urlparse(url).path.lstrip("/")
    return path.lower().startswith("citizen-")


def _child_urls(payload: bytes | str | dict) -> list[str]:
    data = _json_object(payload)
    if data is not None:
        entries = data.get("sitemap") or data.get("sitemaps") or []
        if isinstance(entries, list):
            values = []
            for entry in entries:
                if isinstance(entry, dict) and entry.get("loc"):
                    values.append(str(entry["loc"]))
                elif isinstance(entry, str):
                    values.append(entry)
            return list(dict.fromkeys(values))
    return list(dict.fromkeys(_locs_from_xml(payload if isinstance(payload, (bytes, str)) else _as_bytes(payload))))


def _fixture_value(mapping: dict[str, Any], key: str, url: str, fallback: bytes | str | dict | list | None = None):
    value = mapping.get(key)
    if isinstance(value, dict):
        return value.get(url, fallback)
    if isinstance(value, list):
        try:
            index = mapping.get("_" + key + "_index", 0)
            return value[index]
        except (IndexError, TypeError):
            return fallback
    return value if value is not None else fallback


class GoldsmithsUkRetailerCollector:
    """Full-sitemap discovery plus bounded Goldsmiths detail fetching."""

    def discover_candidates(self, index_payload: bytes | str | dict, child_payloads: dict[str, bytes | str | dict]) -> list[DiscoveredItem]:
        children = [
            url for url in _child_urls(index_payload)
            if url.startswith(PRODUCT_SITEMAP_PREFIX)
        ]
        seen: set[str] = set()
        candidates: list[DiscoveredItem] = []
        for child_url in children:
            payload = child_payloads.get(child_url)
            if payload is None:
                continue
            for url in _locs_from_xml(payload if isinstance(payload, (bytes, str)) else _as_bytes(payload)):
                if not _is_citizen_url(url) or url in seen:
                    continue
                seen.add(url)
                hint = extract_reference_hint(url)
                candidates.append(
                    DiscoveredItem(
                        url=url,
                        title=hint,
                        reference_hint=hint,
                        metadata={
                            "source_region": REGION,
                            "sitemap_child_url": child_url,
                            "reference_hint_source": "goldsmiths_url_shape" if hint else "detail_page_only",
                        },
                    )
                )
        return candidates

    def run(
        self,
        *,
        max_items: int | None = DEFAULT_DETAIL_BUDGET,
        sitemap_payload: object = None,
        known_product_urls: set[str] | None = None,
    ) -> CollectorRunResult:
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID,
            collector_version=COLLECTOR_VERSION,
            region=REGION,
            trust_score=TRUST_SCORE,
        )

        fixture = sitemap_payload if isinstance(sitemap_payload, dict) else None
        if fixture is not None and "index" in fixture:
            index_fetch = FetchResult(
                url=SITEMAP_INDEX_URL,
                success=True,
                status_code=200,
                content_type="application/json",
                payload=_as_bytes(fixture["index"]),
                metadata={"discovery_role": "sitemap_index"},
            )
            index_payload = fixture["index"]
            child_urls = [
                url for url in _child_urls(index_payload)
                if url.startswith(PRODUCT_SITEMAP_PREFIX)
            ]
            child_payloads = {
                url: _fixture_value(fixture, "children", url)
                for url in child_urls
            }
            child_fetches = [
                FetchResult(
                    url=url,
                    success=payload is not None,
                    status_code=200 if payload is not None else None,
                    content_type="application/xml" if payload is not None else None,
                    payload=_as_bytes(payload) if payload is not None else None,
                    error=None if payload is not None else "missing offline child sitemap fixture",
                    metadata={"discovery_role": "product_sitemap"},
                )
                for url, payload in child_payloads.items()
            ]
            child_payloads = {url: value for url, value in child_payloads.items() if value is not None}
        else:
            index_fetch = fetch_url(SITEMAP_INDEX_URL, accept_language="en-GB,en;q=0.9")
            index_payload = index_fetch.payload or b""
            child_fetches = []
            child_payloads = {}
            child_urls = [
                url for url in (_child_urls(index_payload) if index_fetch.success else [])
                if url.startswith(PRODUCT_SITEMAP_PREFIX)
            ]
            for child_url in child_urls:
                child_fetch = fetch_url(child_url, accept_language="en-GB,en;q=0.9")
                child_fetch.metadata["discovery_role"] = "product_sitemap"
                child_fetches.append(child_fetch)
                if child_fetch.success and child_fetch.payload:
                    child_payloads[child_url] = child_fetch.payload

        discovery_fetches = [index_fetch, *child_fetches]
        result.metadata.update(
            {
                "sitemap_index_url": SITEMAP_INDEX_URL,
                "sitemap_child_count": len(child_urls),
                "sitemap_urls_fetched": len(discovery_fetches),
                "lastmod_present": False,
                "detail_fetch_cap": DETAIL_FETCH_CAP,
                "source_class": SOURCE_CLASS,
                "evidence_grade": EVIDENCE_GRADE,
                "authorized_stockist": AUTHORIZED_STOCKIST,
            }
        )

        if not index_fetch.success or not index_payload:
            result.fetched = [index_fetch]
            result.metadata["component_status"] = "BLOCKED" if is_blocked_response(
                index_fetch.status_code, index_fetch.payload, index_fetch.error
            ) else "FAILED"
            result.metadata["healthy"] = False
            return result

        candidates = self.discover_candidates(index_payload, child_payloads)
        result.metadata["raw_sitemap_url_count"] = sum(
            len(_locs_from_xml(payload if isinstance(payload, (bytes, str)) else _as_bytes(payload)))
            for payload in child_payloads.values()
        )
        result.metadata["filtered_candidate_count"] = len(candidates)

        known = known_product_urls or set()
        new_items = [item for item in candidates if item.url not in known]
        known_items = [item for item in candidates if item.url in known]
        ordered = new_items + known_items if known else candidates
        detail_budget = (
            DETAIL_FETCH_CAP
            if max_items is None
            else max(0, min(max_items, DETAIL_FETCH_CAP))
        )
        pending = ordered[:detail_budget]
        result.discovered = pending
        result.metadata.update(
            {
                "candidate_count": len(candidates),
                "discovered_count": len(pending),
                "known_url_count": len(known),
                "detail_fetch_count": len(pending),
            }
        )

        for item in pending:
            if fixture is not None and isinstance(fixture.get("details"), dict):
                detail = fixture["details"].get(item.url)
                detail_fetch = FetchResult(
                    url=item.url,
                    success=detail is not None,
                    status_code=200 if detail is not None else None,
                    content_type="text/html" if detail is not None else None,
                    payload=_as_bytes(detail) if detail is not None else None,
                    error=None if detail is not None else "missing offline detail fixture",
                    metadata={
                        "reference_hint": item.reference_hint,
                        "discovery_role": "product_detail",
                    },
                )
            else:
                detail_fetch = fetch_url(item.url, accept_language="en-GB,en;q=0.9")
                detail_fetch.metadata.update(
                    {
                        "reference_hint": item.reference_hint,
                        "discovery_role": "product_detail",
                    }
                )
            result.fetched.append(detail_fetch)

        successful_details = sum(1 for fetch in result.fetched if fetch.success)
        child_failures = sum(1 for fetch in child_fetches if not fetch.success)
        if successful_details and (child_failures or successful_details < len(result.fetched)):
            status = "PARTIAL"
        elif successful_details:
            status = "SUCCESS"
        elif child_failures and not any(fetch.success for fetch in child_fetches):
            status = "BLOCKED" if all(
                is_blocked_response(fetch.status_code, fetch.payload, fetch.error)
                for fetch in child_fetches
            ) else "FAILED"
        elif not candidates or not result.fetched:
            status = "ZERO_ITEMS"
        else:
            status = "FAILED"
        result.metadata["component_status"] = status
        result.metadata["healthy"] = status in {"SUCCESS", "PARTIAL", "ZERO_ITEMS"}
        return result
