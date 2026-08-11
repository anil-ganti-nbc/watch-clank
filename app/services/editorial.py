"""Deterministic, explainable change-intelligence and editorial scoring.

Design constraints (see HANDOFF.md sprint brief):
- No black-box classifier. Every score has a stored list of concrete reasons.
- A baseline observation (first time we see a reference) is evidence, not
  automatically "news" on its own — NEW_REFERENCE still gets a score, but the
  reasons are explicit about what is and is not known.
- Never claim a fact (price comparison, spec change) the caller did not
  supply evidence for. Absence of evidence means that dimension contributes
  zero score and is not asserted as a reason.

This module is pure logic: it takes already-fetched facts and returns
(event_type, score, reasons). It does not query the database or the network.
Callers (app/services/pipeline.py) are responsible for gathering evidence and
persisting the result as an Event/EventWatch row.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCORING_RULE_VERSION = "0.1.0"

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


def score_event(evidence: EventEvidence) -> ScoredEvent:
    """Deterministic, explainable scoring. Every point added has a reason string."""
    reasons: list[str] = []
    score = 0.0

    if evidence.event_type == "NEW_REFERENCE":
        score += 30.0
        reasons.append("+30 new reference not previously observed")
    elif evidence.event_type == "NEW_REGION":
        score += 15.0
        reasons.append(
            f"+15 first observed availability in region {evidence.region or 'UNKNOWN'}"
            + (
                f" (previously seen in: {', '.join(sorted(evidence.prior_regions))})"
                if evidence.prior_regions
                else ""
            )
        )
    else:
        reasons.append(f"0 no scoring rule yet implemented for event_type={evidence.event_type}")

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

    # Evidence-quality ceiling: without price/spec/availability facts we
    # cannot claim this is more than a routine catalogue observation.
    if evidence.event_type == "NEW_REFERENCE":
        reasons.append(
            "UNKNOWN: price, spec, and availability comparison not evaluated for this event "
            "(no observation data available from a first-party news announcement)"
        )

    score = max(0.0, min(100.0, score))
    if score >= 55:
        confidence = "HIGH" if evidence.is_first_party else "MEDIUM"
    elif score >= 30:
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
) -> str:
    """Human-readable alert block per the sprint brief's Phase 6 template.

    Never invents facts: only echoes evidence explicitly present in `scored`.
    """
    lines = [
        f"{manufacturer.upper()} — {'HIGH' if scored.confidence == 'HIGH' else scored.confidence} EDITORIAL INTEREST",
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
