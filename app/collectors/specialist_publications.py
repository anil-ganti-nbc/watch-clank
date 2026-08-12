"""Bounded public-RSS collectors for approved Layer B publications.

Each source is one GET of its published syndication endpoint.  No article
pages, archives, commerce endpoints, or anti-bot bypasses are involved.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.collectors.base import CollectorRunResult, FetchResult
from app.collectors.http_util import fetch_url, is_blocked_response


@dataclass(frozen=True)
class PublicationSource:
    source_id: str
    collector_id: str
    feed_url: str


PUBLICATION_SOURCES: dict[str, PublicationSource] = {
    "monochrome": PublicationSource("monochrome", "monochrome_rss", "https://monochrome-watches.com/feed/"),
    "deployant": PublicationSource("deployant", "deployant_rss", "https://deployant.com/feed/"),
    "fratello": PublicationSource("fratello", "fratello_rss", "https://www.fratellowatches.com/feed/"),
    "watchtime": PublicationSource("watchtime", "watchtime_rss", "https://www.watchtime.com/feed/rss"),
}


class SpecialistPublicationCollector:
    """Fetch exactly one approved publication feed; never writes the DB."""

    collector_version = "0.1.0"

    def __init__(self, source_id: str) -> None:
        self.source = PUBLICATION_SOURCES[source_id]

    def run(self, *, feed_xml: bytes | None = None) -> CollectorRunResult:
        result = CollectorRunResult(
            collector_id=self.source.collector_id,
            collector_version=self.collector_version,
            region="GLOBAL",
            trust_score=60.0,
        )
        fr = (
            FetchResult(
                url=self.source.feed_url,
                success=True,
                status_code=200,
                content_type="application/rss+xml",
                payload=feed_xml,
            )
            if feed_xml is not None
            else fetch_url(self.source.feed_url)
        )
        result.fetched = [fr]
        result.metadata["index_blocked"] = is_blocked_response(fr.status_code, fr.payload, fr.error)
        if not fr.success or not fr.payload:
            result.metadata["component_status"] = "BLOCKED" if result.metadata["index_blocked"] else "FAILED"
            result.metadata["healthy"] = False
            return result
        result.metadata["component_status"] = "SUCCESS"
        result.metadata["healthy"] = True
        return result
