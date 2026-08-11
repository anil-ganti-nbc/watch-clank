"""Layer B source authority registry.

A plain Python dict, not a database table — Phase 13 of the sprint brief
only asks that the data model not *prevent* future source-performance
analytics, not that we build that analytics now. Tier/type per source_id
lives here so it can be revised as evidence accumulates without a
migration; SpecialistLead rows snapshot the tier at discovery time (so
historical leads keep the tier that was true when found, and a later
registry change doesn't rewrite history).

Tier meaning (Phase 5):
  1 - official OEM evidence (not used by this registry; Layer A's own path)
  2 - highly reliable specialist/retailer source with demonstrated history
  3 - credible specialist/social source requiring verification
  4 - community signal / unverified lead

A Geesgshock leak must never look like a Casio press release, and a
retailer listing must never look like an official global launch — every
alert/render function must echo source_type and tier, never omit them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceProfile:
    source_id: str
    source_type: str
    tier: int
    account_or_domain: str
    display_name: str


# Populated from this sprint's research (see docs/specialist_source_research.md
# for the full evidence trail behind each entry).
SOURCE_REGISTRY: dict[str, SourceProfile] = {
    "casioblog": SourceProfile(
        source_id="casioblog",
        source_type="SPECIALIST_BLOG",
        tier=2,
        account_or_domain="casioblog.com",
        display_name="CASIOBLOG",
    ),
    "geesgshock_manual": SourceProfile(
        source_id="geesgshock_manual",
        source_type="SOCIAL_LEAKER",
        tier=3,
        account_or_domain="@geesgshock",
        display_name="Geesgshock (Instagram, manually ingested)",
    ),
    "neel_jp_retailer": SourceProfile(
        source_id="neel_jp_retailer",
        source_type="RETAILER_EARLY_LISTING",
        tier=3,
        account_or_domain="neel.co.jp",
        display_name="NEEL (Japan authorized retailer)",
    ),
    "great_gshock_world": SourceProfile(
        source_id="great_gshock_world",
        source_type="SPECIALIST_BLOG",
        tier=2,
        account_or_domain="gshockjp.blog.jp",
        display_name="Great G-Shock World",
    ),
    "oracle_of_time": SourceProfile(
        source_id="oracle_of_time",
        source_type="SPECIALIST_PUBLICATION",
        tier=3,
        account_or_domain="oracleoftime.com",
        display_name="Oracle Time",
    ),
}


def get_source_profile(source_id: str) -> SourceProfile:
    """Fail loudly on an unregistered source_id rather than silently
    defaulting to a tier — every source that can create a SpecialistLead
    must be a deliberate, reviewed registry entry."""
    if source_id not in SOURCE_REGISTRY:
        raise KeyError(f"unregistered specialist source_id: {source_id!r} — add it to SOURCE_REGISTRY first")
    return SOURCE_REGISTRY[source_id]
