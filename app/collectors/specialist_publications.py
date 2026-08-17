"""Bounded public-RSS/Atom collectors for approved Layer B publications.

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
    # "rss2" (default) or "atom" -- see app/parsers/rss_common.py. Great G-
    # Shock World's platform (livedoor Blog) does not expose RSS 2.0 at
    # all, only RSS 1.0/RDF and Atom; Atom was chosen as the more standard
    # shape likely to recur for future non-English sources.
    feed_format: str = "rss2"
    # Only items carrying this exact <category> (case-insensitive) are
    # kept -- see app/parsers/specialist_publications.py's module
    # docstring. None (every source before Gear Patrol) means no
    # category gate, preserving existing behavior exactly.
    required_category: str | None = None


PUBLICATION_SOURCES: dict[str, PublicationSource] = {
    "monochrome": PublicationSource("monochrome", "monochrome_rss", "https://monochrome-watches.com/feed/"),
    "deployant": PublicationSource("deployant", "deployant_rss", "https://deployant.com/feed/"),
    "fratello": PublicationSource("fratello", "fratello_rss", "https://www.fratellowatches.com/feed/"),
    "watchtime": PublicationSource("watchtime", "watchtime_rss", "https://www.watchtime.com/feed/rss"),
    "great_gshock_world": PublicationSource(
        "great_gshock_world", "great_gshock_world_atom", "https://gshockjp.blog.jp/atom.xml", feed_format="atom"
    ),
    # Gear Patrol's dedicated /watches/feed/ and /sitemap.xml both return
    # HTTP 403 (Cloudflare); only the site-wide /feed/ is accessible.
    # required_category="Watches" is load-bearing here -- see
    # ai/handoff/SPECIALIST_SOURCE_GEAR_PATROL.md.
    "gear_patrol": PublicationSource(
        "gear_patrol", "gear_patrol_rss", "https://www.gearpatrol.com/feed/", required_category="Watches"
    ),
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
                content_type="application/atom+xml" if self.source.feed_format == "atom" else "application/rss+xml",
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
