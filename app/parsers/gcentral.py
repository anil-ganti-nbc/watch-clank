"""Parser for G-Central's RSS feed (g-central.com/feed/).

G-Central is an independent G-Shock fan site (not affiliated with Casio),
real and confirmed live (RSS 2.0, WordPress, hourly updatePeriod).
Explicitly Layer B (early-warning/specialist), never first-party. Covers
regional releases, collaborations, limited editions, and restock/
availability -- exactly the coverage gaps CASIOBLOG doesn't fill.

Same discipline as casioblog.py: title/canonical URL/pubDate/short excerpt/
categories/reference candidates only. Never content:encoded, never a full
article body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.parsers.rss_common import parse_rss_feed

PARSER_ID = "gcentral_rss"
PARSER_VERSION = "0.1.0"

# Same Casio family-prefix approach as casioblog.py's _REF_RE (kept in sync
# manually rather than importing across modules, since each source's real
# title conventions may drift independently over time).
# A trailing lookahead requires at least one digit in the suffix -- without
# it, plain-English words starting with a family prefix (e.g. "GAme" in
# "...new obby GAME and virtual items") false-positive as a reference.
# Found live during this sprint's isolated validation against the real feed
# (title: "G-Shock is now on Roblox with new obby game and virtual items").
_REF_RE = re.compile(
    r"\b((?:GA|GW|GM|GMW|GBD|GBDH|GWR|GBA|GMA|GMD|GME|GSH|GST|GTS|DW|AW|AE|"
    r"EF|EFB|EFV|EFK|EQB|EQW|EQS|ECB|OCW|OCIS|PRG|PRW|PRJ|PRT|"
    r"MRG|MR-G|MTG|MT-G|BSA|BGA|MSG|SHB|SHE)[-]?(?=[A-Z0-9]*[0-9])[A-Z0-9]{2,20})\b",
    re.IGNORECASE,
)
_RESTOCK_RE = re.compile(r"\b(restock|back in stock|sold out)\b", re.IGNORECASE)
_COLLAB_RE = re.compile(r"\b(collab|collaboration|x G-Shock|x Casio)\b", re.IGNORECASE)


@dataclass
class GCentralLeadCandidate:
    title: str
    url: str
    published_at: str | None
    categories: list[str]
    claim_text: str | None
    reference_candidates: list[str]
    is_restock_or_availability: bool
    is_collaboration: bool


@dataclass
class GCentralFeedParseResult:
    success: bool
    items: list[GCentralLeadCandidate] = field(default_factory=list)
    error: str | None = None


def parse_gcentral_feed(xml_bytes: bytes | str, *, max_items: int | None = 20) -> GCentralFeedParseResult:
    raw_result = parse_rss_feed(xml_bytes, max_items=max_items)
    if not raw_result.success:
        return GCentralFeedParseResult(success=False, error=raw_result.error)

    items = []
    for raw in raw_result.items:
        blob = f"{raw.title} {' '.join(raw.categories)}"
        refs = sorted({m.group(1).upper() for m in _REF_RE.finditer(blob)})
        items.append(
            GCentralLeadCandidate(
                title=raw.title,
                url=raw.url,
                published_at=raw.published_at,
                categories=raw.categories,
                claim_text=raw.claim_text,
                reference_candidates=refs,
                is_restock_or_availability=bool(_RESTOCK_RE.search(raw.title)),
                is_collaboration=bool(_COLLAB_RE.search(blob)),
            )
        )
    return GCentralFeedParseResult(success=True, items=items)
