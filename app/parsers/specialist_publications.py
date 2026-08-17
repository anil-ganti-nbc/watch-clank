"""Deterministic RSS/Atom parser for approved multi-brand publications.

Only feed title, categories, publication date, canonical URL and the
shared short description excerpt enter the candidate.  A reference must
match one of the explicitly supported brand formats; no language-model
inference or article-body scraping is used.

re.ASCII on every pattern below (added for Great G-Shock World, see
ai/handoff/SPECIALIST_SOURCE_GREAT_G_SHOCK_WORLD.md): Python's default
Unicode-aware \\b treats CJK ideographs as "word" characters, so an ASCII
brand/reference token directly adjacent to Japanese text with no
separating space or punctuation (e.g. "G-SHOCK秋冬予想", a
real title from this source) silently fails to match at all -- verified
empirically against the real GCW-B5000 article this source was onboarded
for. re.ASCII restores standard boundary behavior (ASCII word chars vs.
everything else) and is a no-op for the four purely-English sources that
predate it.

Two additions for Gear Patrol (see
ai/handoff/SPECIALIST_SOURCE_GEAR_PATROL.md), both general-purpose, not
specimen-specific:

- `required_category`: unlike the dedicated watch publications that
  predate it, Gear Patrol's only accessible feed is site-wide (its
  watch-specific feed and sitemap both return HTTP 403; the site-wide
  `/feed/` does not). ~79% of its real items are non-watch (motorcycles,
  audio, footwear, outdoors, cars, deals). Brand-keyword matching alone
  is not enough: a "Deals" roundup mentioning exactly one tracked brand
  by name (e.g. "...Seiko Sports Watches...") would otherwise pass the
  existing brand filter despite being commerce/deals content, not
  editorial -- verified against a real captured example. Gear Patrol's
  own `<category>Watches</category>` tag reliably distinguishes the two
  (that same "Deals" example is tagged `Deals`, never `Watches`).
  `required_category`, when set, is checked before brand matching even
  runs; `None` (the default) preserves every existing source's behavior
  exactly.
- The canonical URL is now part of the blob used for reference
  extraction (previously title + categories + description only): Gear
  Patrol's real headlines are editorial prose ("Timex Wrenches Its
  Heritage Waterbury Watch into a Historic Racing Chronograph") that
  never states the model number, which appears only in the URL slug
  (`.../timex-waterbury-heritage-chronograph-tw2y93300/`) -- verified
  empirically against the real specimen article before this was added.
  Benefits every source equally; URLs are already-trusted first-party
  data, not scraped free text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.parsers.rss_common import parse_atom_feed, parse_rss_feed

PARSER_ID = "specialist_publication_rss"
PARSER_VERSION = "0.2.0"

_BRAND_TERMS = (
    ("Casio", re.compile(r"\b(?:casio|g-shock)\b", re.IGNORECASE | re.ASCII)),
    ("Seiko", re.compile(r"\bseiko\b", re.IGNORECASE | re.ASCII)),
    ("Citizen", re.compile(r"\bcitizen\b", re.IGNORECASE | re.ASCII)),
    ("Timex", re.compile(r"\btimex\b", re.IGNORECASE | re.ASCII)),
)
_REFERENCE_PATTERNS: dict[str, re.Pattern[str]] = {
    # Require a digit, preventing ordinary title words from becoming a
    # model. GCW added alongside the existing G-prefixed families for the
    # real full-carbon GCW-B5000 square -- a genuine Casio reference
    # prefix, not a specimen-specific special case.
    "Casio": re.compile(r"\b((?:GA|GW|GM|GMW|GCW|GBD|GBDH|GBX|GWR|GBA|GMA|GMD|GME|GSH|GST|EFK|EF|OCW|PRG|PRW|MRG|MTG|BGA|MSG)[-]?(?=[A-Z0-9-]*\d)[A-Z0-9-]{3,24})\b", re.IGNORECASE | re.ASCII),
    "Seiko": re.compile(r"\b((?:S[A-Z]{2,3}[0-9]{3,4}|H(?:CC|BC|DB)[A-Z0-9]{3,5}))\b", re.IGNORECASE | re.ASCII),
    "Citizen": re.compile(r"\b([A-Z]{2}[0-9]{4}-[0-9A-Z]{2,4})\b", re.IGNORECASE | re.ASCII),
    "Timex": re.compile(r"\b(TW[A-Z0-9]{6,})\b", re.IGNORECASE | re.ASCII),
}
_LIMITED_RE = re.compile(r"\b(?:limited edition|limited to|limited-run)\b", re.IGNORECASE)
_COLLAB_RE = re.compile(r"\b(?:collaboration|collab| x )\b", re.IGNORECASE)


@dataclass
class PublicationLeadCandidate:
    title: str
    url: str
    published_at: str | None
    claim_text: str | None
    brand: str | None
    reference_candidates: list[str]
    is_limited_edition: bool
    is_collaboration: bool


@dataclass
class PublicationFeedParseResult:
    success: bool
    items: list[PublicationLeadCandidate] = field(default_factory=list)
    error: str | None = None


def _brand_from_blob(blob: str) -> str | None:
    matches = [brand for brand, pattern in _BRAND_TERMS if pattern.search(blob)]
    return matches[0] if len(matches) == 1 else None


def _canonicalize_url(url: str) -> str:
    """Strip query string/fragment so tracking params (utm_*, etc.) never
    let the same article dedup as two different source_url values.
    Trailing slash preserved as-is -- Gear Patrol and every existing
    source already emit it consistently; normalizing it away would risk
    the opposite problem (two genuinely different paths colliding)."""
    return url.split("?", 1)[0].split("#", 1)[0]


def parse_specialist_publication_feed(
    xml_bytes: bytes | str,
    *,
    max_items: int | None = 20,
    feed_format: str = "rss2",
    required_category: str | None = None,
) -> PublicationFeedParseResult:
    raw_result = (
        parse_atom_feed(xml_bytes, max_items=max_items)
        if feed_format == "atom"
        else parse_rss_feed(xml_bytes, max_items=max_items)
    )
    if not raw_result.success:
        return PublicationFeedParseResult(success=False, error=raw_result.error)

    items: list[PublicationLeadCandidate] = []
    for raw in raw_result.items:
        if required_category is not None:
            cats_lower = {c.strip().lower() for c in raw.categories if c}
            if required_category.lower() not in cats_lower:
                continue

        url = _canonicalize_url(raw.url)
        blob = f"{raw.title} {' '.join(raw.categories)} {raw.claim_text or ''}"
        brand = _brand_from_blob(blob)
        # A general-publication article outside the four approved brands is
        # irrelevant to Watch Clank and must not become an undifferentiated lead.
        if brand is None:
            continue
        # URL included for reference extraction only (see module docstring):
        # some sources put the model number only in the slug, never in the
        # human-readable title/description.
        refs = sorted(
            {match.group(1).upper() for match in _REFERENCE_PATTERNS[brand].finditer(f"{blob} {url}")}
        )
        items.append(
            PublicationLeadCandidate(
                title=raw.title,
                url=url,
                published_at=raw.published_at,
                claim_text=raw.claim_text,
                brand=brand,
                reference_candidates=refs,
                is_limited_edition=bool(_LIMITED_RE.search(blob)),
                is_collaboration=bool(_COLLAB_RE.search(blob)),
            )
        )
    return PublicationFeedParseResult(success=True, items=items)
