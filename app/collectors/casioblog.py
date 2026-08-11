"""CASIOBLOG RSS collector (Layer B / early-warning, EXPERIMENTAL).

See app/parsers/casioblog.py for source rationale and the "no full article
bodies" policy. RSS is a standard, publicly-intended syndication mechanism
— no anti-bot concerns, no scraping judgment calls, same conservative
single-GET pattern as every other collector in this project.
"""

from __future__ import annotations

from app.collectors.base import CollectorRunResult, FetchResult
from app.collectors.http_util import fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "casioblog_rss"
COLLECTOR_VERSION = "0.1.0"
REGION = "GLOBAL"
TRUST_SCORE = 60.0  # specialist source — see source_registry tier, not first-party
FEED_URL = "https://casioblog.com/en/feed/"


class CasioblogCollector:
    """Fetch the CASIOBLOG RSS feed. No DB writes, no per-item fetches —
    the feed already carries everything this lead needs."""

    def run(self, *, feed_xml: bytes | None = None) -> CollectorRunResult:
        result = CollectorRunResult(
            collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, region=REGION, trust_score=TRUST_SCORE
        )
        if feed_xml is not None:
            fr = FetchResult(url=FEED_URL, success=True, status_code=200, content_type="application/rss+xml", payload=feed_xml)
        else:
            fr = fetch_url(FEED_URL)

        result.fetched = [fr]
        result.metadata["index_blocked"] = is_blocked_response(fr.status_code, fr.payload, fr.error)
        if not fr.success or not fr.payload:
            result.metadata["component_status"] = "BLOCKED" if result.metadata["index_blocked"] else "FAILED"
            result.metadata["healthy"] = False
            return result

        result.metadata["component_status"] = "SUCCESS"
        result.metadata["healthy"] = True
        return result
