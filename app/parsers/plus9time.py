"""Parser for Plus9Time's RSS feed (plus9time.com/blog?format=rss).

Plus9Time is a real, active Japanese-watch-industry publication (Squarespace-
hosted, confirmed live) covering Seiko, Grand Seiko, and Citizen -- catalog
scans, patents, trademark filings, and periodic "all H1/H2 announcements"
roundups. Honest finding from this sprint's research: most of its real
content is historical/archival rather than early-warning leads (vintage
catalog scans, patent filings) -- it rarely names a specific unreleased
current-production reference before an official listing does. It is still
implemented because it is real, cheap (RSS), safe, and Seiko/Citizen
coverage is otherwise thin in Watch Clank's specialist network; reference
extraction will legitimately return empty for most items, which is
expected and handled the same way CASIOBLOG handles a no-reference item
(LEAKED_IMAGE-style fallback lead_type), not treated as a parser bug.

Same discipline as casioblog.py/gcentral.py: title/canonical URL/pubDate/
short excerpt/categories/reference candidates only -- never content:encoded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.parsers.rss_common import parse_rss_feed

PARSER_ID = "plus9time_rss"
PARSER_VERSION = "0.1.0"

# Seiko: letter-prefix + 4-6 alnum, no hyphen (e.g. SBGA211, SPB143, SSK001).
# Citizen: 2 letters + 4 digits + hyphen + 2-4 alnum (e.g. CC4107-80H) --
# same pattern already validated in app/parsers/citizen_news.py.
_SEIKO_RE = re.compile(r"\b(S[A-Z]{2,3}[0-9]{3,4})\b")
_CITIZEN_RE = re.compile(r"\b([A-Z]{2}[0-9]{4}-[0-9A-Z]{2,4})\b")


@dataclass
class Plus9TimeLeadCandidate:
    title: str
    url: str
    published_at: str | None
    categories: list[str]
    claim_text: str | None
    reference_candidates: list[str]
    brand_guess: str | None  # "Seiko" | "Citizen" | None -- from category text only, never invented


@dataclass
class Plus9TimeFeedParseResult:
    success: bool
    items: list[Plus9TimeLeadCandidate] = field(default_factory=list)
    error: str | None = None


def _guess_brand(categories: list[str], title: str) -> str | None:
    blob = f"{title} {' '.join(categories)}".lower()
    has_seiko = "seiko" in blob
    has_citizen = "citizen" in blob
    if has_seiko and not has_citizen:
        return "Seiko"
    if has_citizen and not has_seiko:
        return "Citizen"
    return None  # ambiguous or neither -- never guess


def parse_plus9time_feed(xml_bytes: bytes | str, *, max_items: int | None = 20) -> Plus9TimeFeedParseResult:
    raw_result = parse_rss_feed(xml_bytes, max_items=max_items)
    if not raw_result.success:
        return Plus9TimeFeedParseResult(success=False, error=raw_result.error)

    items = []
    for raw in raw_result.items:
        blob = f"{raw.title} {' '.join(raw.categories)}"
        refs = sorted(
            {m.group(1).upper() for m in _SEIKO_RE.finditer(blob)}
            | {m.group(1).upper() for m in _CITIZEN_RE.finditer(blob)}
        )
        items.append(
            Plus9TimeLeadCandidate(
                title=raw.title,
                url=raw.url,
                published_at=raw.published_at,
                categories=raw.categories,
                claim_text=raw.claim_text,
                reference_candidates=refs,
                brand_guess=_guess_brand(raw.categories, raw.title),
            )
        )
    return Plus9TimeFeedParseResult(success=True, items=items)
