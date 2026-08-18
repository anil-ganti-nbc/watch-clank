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

2026-08-19 manufacturer-attribution hardening (Watch Clank QC + classifier
hardening pass). Real production data surfaced a Gear Patrol article about
a Boldr x Windup Watch Shop collaboration classified with
manufacturer="Seiko" -- the article's description mentions the watch uses
a "Seiko NH35" movement, and the old `_brand_from_blob` scanned title +
categories + description as one flat bag of text with no notion of
"subject" vs. "incidental component/comparison mention". Two independent
fixes, both general-purpose (verified against every pre-existing fixture,
none of which regress):

1. Title-first hierarchy: a brand mentioned in the TITLE is authoritative
   and short-circuits before the description is even considered -- this
   alone fixes "Timex article that mentions Seiko only for a price
   comparison in the body" (the title-only scan never sees the
   comparison brand at all).
2. Context-aware incidental-mention suppression (`_INCIDENTAL_CONTEXT_RE`,
   `_brand_candidates`): a brand token is excluded from candidacy if a
   movement/caliber/comparison trigger word appears in a small window
   around it -- "Seiko NH35 movement" is filtered out project-wide
   (title or body), not just for the Boldr specimen.

Neither Boldr nor Windup Watch Shop is one of Watch Clank's four tracked
manufacturers -- the article was never going to become a normal
brand-scoped lead. `required_category`-gated sources (currently only Gear
Patrol) are explicitly designed to admit any watch brand, not just the
tracked four (see the class docstring above), so for a *detected
collaboration* on such a source, `_collab_pair_from_title` extracts the
two named participants directly from the title's own "X and/x Y" seam,
independent of the tracked-brand vocabulary. This is deliberately scoped
to collaborations on multi-brand sources only -- it does not turn every
Gear Patrol article into a free-text brand-name generator, only ones the
article itself already flags as a named collaboration.
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

# Movement supplier, caliber, and comparison language -- a brand token
# found near one of these is an incidental mention, not the article's
# subject manufacturer. Window-based (checked around each individual
# match, not blob-wide) so a genuinely-subject brand elsewhere in the
# same text is unaffected.
_INCIDENTAL_CONTEXT_RE = re.compile(
    r"\b(?:movement|caliber|calibre|module|powered by|compared? (?:to|with)|comparable(?: to)?|"
    r"\bvs\.?\b|versus|rival(?:s|ed)?|alternative(?: to)?|similar(?: to)?|cheaper than|"
    r"instead of|rather than|like the|reminiscent of)\b",
    re.IGNORECASE | re.ASCII,
)
_INCIDENTAL_WINDOW = 45

_COLLAB_SPLIT_RE = re.compile(r"\s+(?:and|x|×)\s+", re.IGNORECASE | re.ASCII)
_LEADING_ENTITY_RE = re.compile(r"^(?:The\s+)?([A-Z][\w&]*(?:\s+[A-Z][\w&]*){0,2})", re.ASCII)


def _brand_candidates(text: str) -> set[str]:
    """Tracked-brand tokens in `text` that are NOT sitting next to
    incidental (movement/caliber/comparison) language -- see module
    docstring's 2026-08-19 entry."""
    found: set[str] = set()
    for brand, pattern in _BRAND_TERMS:
        for m in pattern.finditer(text):
            start, end = max(0, m.start() - _INCIDENTAL_WINDOW), min(len(text), m.end() + _INCIDENTAL_WINDOW)
            if _INCIDENTAL_CONTEXT_RE.search(text[start:end]):
                continue
            found.add(brand)
    return found


def _collab_pair_from_title(title: str) -> tuple[str, str] | None:
    """For a title shaped like "X and Y('s) ..." or "X x Y ...", return
    (primary, collaborator) -- deterministic leading-entity extraction,
    no brand-vocabulary restriction (a multi-brand source's collaboration
    can legitimately name a brand Watch Clank doesn't otherwise track,
    e.g. "Boldr"). Returns None if the title doesn't have this shape."""
    parts = _COLLAB_SPLIT_RE.split(title, maxsplit=1)
    if len(parts) != 2:
        return None
    left, right = parts
    m1 = _LEADING_ENTITY_RE.match(left.strip())
    m2 = _LEADING_ENTITY_RE.match(right.strip())
    if not m1 or not m2:
        return None
    return m1.group(1).strip(), m2.group(1).strip()


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
    collaborator: str | None = None


@dataclass
class PublicationFeedParseResult:
    success: bool
    items: list[PublicationLeadCandidate] = field(default_factory=list)
    error: str | None = None


def _brand_from_blob(blob: str) -> str | None:
    """Legacy whole-blob scan, kept for direct callers/tests that still
    want the original behavior. `parse_specialist_publication_feed` itself
    uses the title-first hierarchy below instead."""
    matches = [brand for brand, pattern in _BRAND_TERMS if pattern.search(blob)]
    return matches[0] if len(matches) == 1 else None


def _determine_brand(*, title: str, rest: str) -> str | None:
    """Entity hierarchy: title first (a brand mentioned in the headline is
    the subject; an incidentally-mentioned comparison/movement-supplier
    brand there is already excluded by `_brand_candidates`), then the
    combined title+categories+description blob as a fallback for sources
    that don't restate the brand in the headline. Ambiguous (0 or >1
    tracked-brand candidates) at both levels returns None -- unchanged
    from the original single-match-only discipline."""
    title_candidates = _brand_candidates(title)
    if len(title_candidates) == 1:
        return next(iter(title_candidates))
    full_candidates = _brand_candidates(f"{title} {rest}")
    if len(full_candidates) == 1:
        return next(iter(full_candidates))
    return None


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
        rest = f"{' '.join(raw.categories)} {raw.claim_text or ''}"
        blob = f"{raw.title} {rest}"
        is_collaboration = bool(_COLLAB_RE.search(blob))

        brand = _determine_brand(title=raw.title, rest=rest)
        collaborator: str | None = None
        # A detected collaboration on a multi-brand source (required_category
        # set -- currently only Gear Patrol, see class docstring) may name a
        # brand Watch Clank doesn't otherwise track (e.g. "Boldr"). Extract
        # the two named parties from the title's own structure rather than
        # falling back to whatever tracked brand happens to be mentioned
        # incidentally (the original Boldr/Seiko-NH35-movement bug).
        if brand is None and is_collaboration and required_category is not None:
            pair = _collab_pair_from_title(raw.title)
            if pair is not None:
                brand, collaborator = pair

        # A general-publication article with no determinable subject brand
        # (tracked or, for multi-brand sources, collaboration-named) is
        # irrelevant to Watch Clank and must not become an undifferentiated
        # lead.
        if brand is None:
            continue
        # URL included for reference extraction only (see module docstring):
        # some sources put the model number only in the slug, never in the
        # human-readable title/description. Only the four tracked brands
        # have a reference pattern -- a collaboration-extracted brand
        # outside that vocabulary (e.g. "Boldr") has no known reference
        # shape, so it correctly yields no candidates rather than guessing.
        refs = (
            sorted({match.group(1).upper() for match in _REFERENCE_PATTERNS[brand].finditer(f"{blob} {url}")})
            if brand in _REFERENCE_PATTERNS
            else []
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
                is_collaboration=is_collaboration,
                collaborator=collaborator,
            )
        )
    return PublicationFeedParseResult(success=True, items=items)
