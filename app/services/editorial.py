"""Deterministic, explainable change-intelligence and editorial scoring.

Design constraints (see HANDOFF.md sprint briefs):
- No black-box classifier. Every score has a stored list of concrete reasons.
- A baseline observation (first time we see a reference) is evidence, not
  automatically "news" on its own — NEW_REFERENCE still gets a score, but the
  reasons are explicit about what is and is not known.
- Never claim a fact (price comparison, spec change) the caller did not
  supply evidence for. Absence of evidence means that dimension contributes
  zero score and is not asserted as a reason.
- SOLD_OUT/UNAVAILABLE must never be inferred from source failure, zero
  results, redirects, or parse errors — only from a genuinely successful
  before/after observation pair. See classify_price_availability_transition.

Sprint 2 policy change (recall, not precision, for the experimental lane):
Watch Clank has a human verification layer — a plausible-but-uninteresting
alert costs a journalist 30 seconds. A confidently wrong one (fabricated
price/spec/sell-out claim) costs trust. So this module is tuned to not
under-score genuine new evidence, while keeping every "bad false positive"
category (parser errors, fabricated comparisons, unsupported claims)
structurally impossible: those dimensions simply contribute nothing without
real evidence, never a guess.

This module is pure logic: it takes already-fetched facts and returns
(event_type, score, reasons). It does not query the database or the network.
Callers (app/services/pipeline.py) are responsible for gathering evidence and
persisting the result as an Event/EventWatch row.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCORING_RULE_VERSION = "0.2.0"

# Families with strong public recognisability. Conservative, editable list —
# extend only with families that have demonstrated audience recognition, not
# merely "exists". Matched case-insensitively against collection/family text.
RECOGNIZABLE_FAMILIES = frozenset(
    {
        "g-shock",
        "gshock",
        "baby-g",
        "edifice",
        "pro trek",
        "protrek",
        "oceanus",
        "prospex",
        "presage",
        "astron",
        "king seiko",
        "grand seiko",
        "5 sports",
        "seiko 5",
        "promaster",
        "attesa",
        "tsuyosa",
    }
)

VALID_EVENT_TYPES = frozenset(
    {
        "NEW_REFERENCE",
        "NEW_REGION",
        "PRICE_CHANGE",
        "AVAILABILITY_CHANGE",
        "SOLD_OUT",
        "RESTOCK",
    }
)


@dataclass
class EventEvidence:
    """Facts available about a single watch/lead transition. All optional
    except event_type — leave fields None/empty when there is no evidence;
    do not guess."""

    event_type: str
    manufacturer: str
    brand: str
    collection: str | None = None
    region: str | None = None
    is_first_party: bool = True
    prior_regions: frozenset[str] = field(default_factory=frozenset)
    reference_raw: str | None = None

    # Price/availability evidence (Phase 3). None means "no evidence", never
    # "zero"/"unavailable" — see classify_price_availability_transition for
    # how these get populated from real observation pairs.
    price: float | None = None
    currency: str | None = None
    prior_price: float | None = None
    prior_currency: str | None = None
    price_delta_pct: float | None = None  # signed, e.g. -20.0 = 20% cheaper
    availability_status: str | None = None

    # Product-character evidence (Phase 5 recall tuning). Only set these when
    # the source text genuinely supports them — do not infer.
    is_limited_edition: bool | None = None
    limited_edition_quantity: int | None = None
    is_collaboration: bool | None = None
    unusual_material: str | None = None  # e.g. "titanium", "sapphire" if source says so


@dataclass
class ScoredEvent:
    event_type: str
    score: float
    confidence: str  # HIGH / MEDIUM / LOW
    reasons: list[str]
    scoring_rule_version: str = SCORING_RULE_VERSION


def _is_recognizable(collection: str | None) -> bool:
    if not collection:
        return False
    return collection.strip().lower() in RECOGNIZABLE_FAMILIES


def classify_price_availability_transition(
    *,
    prior_price: float | None,
    prior_currency: str | None,
    prior_availability: str | None,
    prior_region: str | None,
    prior_source_healthy: bool,
    new_price: float | None,
    new_currency: str | None,
    new_availability: str | None,
    new_region: str | None,
    new_source_healthy: bool,
) -> tuple[str | None, list[str]]:
    """Classify a PRICE_CHANGE/AVAILABILITY_CHANGE/SOLD_OUT/RESTOCK transition
    between two observations of the SAME watch in the SAME region.

    Returns (event_type_or_None, reasons). Deliberately conservative:
    - Never returns SOLD_OUT/AVAILABILITY_CHANGE if either observation came
      from an unhealthy source (failed fetch, blocked, parser error) — a
      missing/failed fetch is UNKNOWN, never evidence of unavailability.
    - Never compares prices across different currencies (no conversion layer
      exists yet, per this sprint's explicit scope).
    - Never compares observations from different regions (different market,
      not a real transition).
    - Returns None (no event) when there is nothing new to report — a
      routine repeat observation is silent, not "AVAILABILITY_CHANGE: same".
    """
    reasons: list[str] = []

    if not prior_source_healthy or not new_source_healthy:
        reasons.append(
            "UNKNOWN: at least one observation came from an unhealthy/failed source fetch — "
            "no price/availability transition can be claimed from this pair"
        )
        return None, reasons

    if prior_region != new_region:
        reasons.append(
            f"prior observation region ({prior_region!r}) differs from new region "
            f"({new_region!r}) — not a valid before/after comparison"
        )
        return None, reasons

    # Availability transition (checked before price so SOLD_OUT/RESTOCK win)
    if prior_availability and new_availability and prior_availability != new_availability:
        prior_avail = prior_availability.upper()
        new_avail = new_availability.upper()
        if new_avail in ("SOLD_OUT", "UNAVAILABLE") and prior_avail == "AVAILABLE":
            reasons.append(f"availability changed AVAILABLE -> {new_availability} in {new_region}")
            return "SOLD_OUT", reasons
        if prior_avail in ("SOLD_OUT", "UNAVAILABLE") and new_avail == "AVAILABLE":
            reasons.append(f"availability changed {prior_availability} -> AVAILABLE in {new_region}")
            return "RESTOCK", reasons
        reasons.append(f"availability changed {prior_availability} -> {new_availability} in {new_region}")
        return "AVAILABILITY_CHANGE", reasons

    if (
        prior_price is not None
        and new_price is not None
        and prior_currency
        and new_currency
        and prior_currency == new_currency
        and prior_price != new_price
    ):
        delta_pct = round((new_price - prior_price) / prior_price * 100, 1) if prior_price else None
        reasons.append(
            f"price changed {prior_price} {prior_currency} -> {new_price} {new_currency} "
            f"in {new_region} ({delta_pct:+.1f}%)" if delta_pct is not None
            else f"price changed {prior_price} {prior_currency} -> {new_price} {new_currency} in {new_region}"
        )
        return "PRICE_CHANGE", reasons

    if prior_currency and new_currency and prior_currency != new_currency:
        reasons.append(
            f"UNKNOWN: prior currency ({prior_currency}) differs from new currency "
            f"({new_currency}) — no conversion layer, price not compared"
        )

    return None, reasons


def score_event(evidence: EventEvidence) -> ScoredEvent:
    """Deterministic, explainable scoring. Every point added has a reason string."""
    if evidence.event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"unknown event_type: {evidence.event_type!r}")

    reasons: list[str] = []
    score = 0.0

    if evidence.event_type == "NEW_REFERENCE":
        score += 30.0
        reasons.append("+30 new reference not previously observed")
    elif evidence.event_type == "NEW_REGION":
        score += 30.0
        reasons.append(
            f"+30 first observed official listing in region {evidence.region or 'UNKNOWN'}"
            + (
                f" (previously seen in: {', '.join(sorted(evidence.prior_regions))})"
                if evidence.prior_regions
                else ""
            )
        )
        if evidence.price is not None and evidence.currency:
            score += 15.0
            reasons.append(f"+15 first official local price ({evidence.price:g} {evidence.currency})")
        if evidence.availability_status:
            score += 5.0
            reasons.append(
                f"+5 official local availability state ({evidence.availability_status})"
            )
    elif evidence.event_type == "PRICE_CHANGE":
        if evidence.price_delta_pct is not None:
            magnitude = abs(evidence.price_delta_pct)
            direction = "cheaper" if evidence.price_delta_pct < 0 else "more expensive"
            pts = min(35.0, 10.0 + magnitude)
            score += pts
            reasons.append(f"+{pts:.0f} price moved {magnitude:.1f}% {direction}")
        else:
            score += 15.0
            reasons.append("+15 price changed (magnitude not computed)")
    elif evidence.event_type == "SOLD_OUT":
        score += 25.0
        reasons.append("+25 confirmed sold out from a healthy before/after observation pair")
    elif evidence.event_type == "RESTOCK":
        score += 15.0
        reasons.append("+15 confirmed restock from a healthy before/after observation pair")
    elif evidence.event_type == "AVAILABILITY_CHANGE":
        score += 15.0
        reasons.append("+15 availability status changed")

    if evidence.is_first_party:
        score += 10.0
        reasons.append("+10 first-party/official evidence")
    else:
        reasons.append("+0 evidence is not confirmed first-party")

    if _is_recognizable(evidence.collection):
        score += 20.0
        reasons.append(f"+20 recognisable product family ({evidence.collection})")
    else:
        reasons.append("+0 collection not in the recognisable-family list (or unknown)")

    if evidence.is_limited_edition:
        score += 10.0
        if evidence.limited_edition_quantity:
            reasons.append(f"+10 limited edition ({evidence.limited_edition_quantity} pieces)")
        else:
            reasons.append("+10 limited edition (quantity not stated)")
    if evidence.is_collaboration:
        score += 10.0
        reasons.append("+10 named collaboration")
    if evidence.unusual_material:
        score += 10.0
        reasons.append(f"+10 unusual material ({evidence.unusual_material})")

    # Evidence-quality ceiling: without price/spec/availability facts we
    # cannot claim this is more than a routine catalogue observation.
    if evidence.event_type == "NEW_REFERENCE" and evidence.price is None:
        reasons.append(
            "UNKNOWN: price, spec, and availability comparison not evaluated for this event "
            "(no observation data available from a first-party news announcement)"
        )

    score = max(0.0, min(100.0, score))
    if score >= 50:
        confidence = "HIGH" if evidence.is_first_party else "MEDIUM"
    elif score >= 25:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return ScoredEvent(
        event_type=evidence.event_type,
        score=score,
        confidence=confidence,
        reasons=reasons,
    )


def format_alert(
    *,
    manufacturer: str,
    brand: str,
    reference_raw: str | None,
    scored: ScoredEvent,
    region: str | None,
    announcement_title: str | None,
    announcement_url: str,
    observed_at: str,
    experimental: bool = False,
) -> str:
    """Human-readable alert block per the sprint brief's Phase 6 template.

    Never invents facts: only echoes evidence explicitly present in `scored`.
    """
    tag = "[EXPERIMENTAL] " if experimental else ""
    lines = [
        f"{tag}{manufacturer.upper()} — {'HIGH' if scored.confidence == 'HIGH' else scored.confidence} EDITORIAL INTEREST",
        "",
        f"Reference: {reference_raw or 'UNKNOWN'}",
        f"Event: {scored.event_type}",
        f"Region: {region or 'UNKNOWN'}",
        f"Observed: {observed_at}",
        "",
        "What changed:",
        announcement_title or "(no title available)",
        "",
        "Evidence:",
        announcement_url,
        "",
        f"Editorial score: {int(scored.score)}/100",
        "Reasons:",
    ]
    lines.extend(f"- {r}" for r in scored.reasons)
    lines.append("")
    lines.append(f"Confidence: {scored.confidence}")
    return "\n".join(lines)


def format_early_warning_alert(
    *,
    manufacturer: str | None,
    brand: str | None,
    reference_candidates: list[str],
    lead_type: str,
    source_display_name: str,
    source_type: str,
    source_authority_tier: int,
    title: str,
    claim_text: str | None,
    source_url: str,
    published_at: str | None,
    discovered_at: str,
    confidence: float,
) -> str:
    """Layer B alert block — structurally distinct from format_alert (Layer
    A) so an early-warning lead can never be mistaken for an official
    confirmation. Always says "UNCONFIRMED" / "Requires human verification"
    up front; never claims a price/spec/availability fact the caller didn't
    supply. Tier is always shown — a tier-3 social leak must never render
    identically to a tier-2 specialist blog with a longer track record.
    """
    lines = [
        f"EARLY WARNING — UNCONFIRMED (Layer B, tier {source_authority_tier})",
        "",
        f"{(manufacturer or 'UNKNOWN').upper()}" + (f" / {brand}" if brand and brand != manufacturer else ""),
        f"Possible reference(s): {', '.join(reference_candidates) if reference_candidates else 'UNKNOWN'}",
        f"Lead type: {lead_type}",
        f"Source: {source_display_name}",
        f"Source type: {source_type}",
        f"Published: {published_at or 'UNKNOWN'}",
        f"Detected: {discovered_at}",
        "",
        "Claim:",
        claim_text or title,
        "",
        "Evidence:",
        source_url,
        "",
        f"Confidence: {confidence:.0f}/100",
        "",
        "Requires human verification before any editorial use.",
    ]
    return "\n".join(lines)


def format_correlation_followup_alert(
    *,
    manufacturer: str | None,
    brand: str | None,
    lead_reference_candidates: list[str],
    watch_reference_raw: str,
    correlation_type: str,
    source_display_name: str,
    lead_published_at: str | None,
    official_first_observed_at: str | None,
    lead_time_days: float | None,
    source_url: str,
) -> str:
    """Follow-up alert once a Layer B lead correlates with an official
    Watch (Phase 12/Phase 7). correlation_type distinguishes an
    EXACT_REFERENCE_MATCH ("CONFIRMED") from a FAMILY_MATCH — a family
    match must be labeled as such, never presented as an exact-reference
    confirmation."""
    label = "CONFIRMED" if correlation_type == "EXACT_REFERENCE_MATCH" else "FAMILY_MATCH — NOT EXACT"
    lines = [
        f"EARLY WARNING FOLLOW-UP — {label}",
        "",
        f"{(manufacturer or 'UNKNOWN').upper()}" + (f" / {brand}" if brand and brand != manufacturer else ""),
        f"Lead reference(s): {', '.join(lead_reference_candidates) if lead_reference_candidates else 'UNKNOWN'}",
        f"Official reference: {watch_reference_raw}",
        f"Original source: {source_display_name}",
        f"Lead published: {lead_published_at or 'UNKNOWN'}",
        f"Official first observed: {official_first_observed_at or 'UNKNOWN'}",
        f"Approximate lead time: {lead_time_days} days" if lead_time_days is not None else "Approximate lead time: UNKNOWN",
        "",
        "Evidence (original lead):",
        source_url,
    ]
    if correlation_type != "EXACT_REFERENCE_MATCH":
        lines.append("")
        lines.append(
            "Note: family-level match only (colorway/suffix not confirmed identical). "
            "Requires human verification before treating as the same reference."
        )
    return "\n".join(lines)
