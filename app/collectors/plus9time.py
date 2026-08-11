"""Plus9Time RSS collector (Layer B / early-warning, EXPERIMENTAL).

See app/parsers/plus9time.py for source rationale. Standard public RSS,
same conservative single-GET pattern as every other feed collector here.
"""

from __future__ import annotations

from app.collectors.base import CollectorRunResult, FetchResult
from app.collectors.http_util import fetch_url, is_blocked_response
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTOR_ID = "plus9time_rss"
COLLECTOR_VERSION = "0.1.0"
REGION = "GLOBAL"
TRUST_SCORE = 55.0
FEED_URL = "https://www.plus9time.com/blog?format=rss"


class Plus9TimeCollector:
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
