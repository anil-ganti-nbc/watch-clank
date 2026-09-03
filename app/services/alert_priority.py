"""Transparent alert-priority policy and launch grouping (track G).

Incidents being addressed: Casio Frogman GWF-D1000BC-1JF (2026-08-29) and
the MASTER IN HORIZON GOLD trio (2026-08-27). In both, collection,
eventing and Discord delivery all SUCCEEDED -- the alerts were simply
buried among other notifications. Neither is a collector, source, region
or delivery defect, so nothing here changes what is collected or whether
anything is delivered.

**Scope discipline (operator decision, 2026-09-03).** HIGH priority means
exactly one thing: a limited edition or a named collaboration. Other
candidate signals were explicitly considered and NOT adopted -- official
first-party origin, high story score plus HIGH confidence, and NEW_REGION
on a known reference all stay NORMAL. That is a deliberate narrow choice,
not an oversight: a priority tier that most alerts qualify for would
re-create the flood it exists to cut through.

**This module does not route anything.** It labels. Delivery behaviour is
byte-for-byte unchanged -- same alerts, same immediacy, same destination --
because grouped/digest routing is a separate operator decision that has
not been made. The labels and group keys exist so that decision can later
be made against real observed data rather than a guess, and so a buried
alert is at least *findable* after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

POLICY_VERSION = "2026-09-03.1"

PRIORITY_HIGH = "HIGH"
PRIORITY_NORMAL = "NORMAL"


@dataclass(frozen=True)
class PriorityDecision:
    """A priority tier plus the reasons that produced it.

    Reasons are mandatory, mirroring how editorial scoring already explains
    itself: an operator must be able to see why something was or was not
    raised, otherwise a triage layer becomes another opaque filter that can
    hide a real launch.
    """

    tier: str
    reasons: list[str] = field(default_factory=list)

    @property
    def is_high(self) -> bool:
        return self.tier == PRIORITY_HIGH

    def as_extra(self) -> dict:
        return {"tier": self.tier, "reasons": list(self.reasons), "policy_version": POLICY_VERSION}


def classify(
    *,
    is_limited_edition: bool | None = None,
    is_collaboration: bool | None = None,
    limited_edition_quantity: int | None = None,
) -> PriorityDecision:
    """Classify one event's alert priority from first-class evidence flags.

    Deliberately reads the same `EventEvidence` fields the editorial scorer
    already uses (`is_limited_edition`, `is_collaboration`) rather than
    re-deriving them or pattern-matching alert text -- one source of truth
    for what "limited" means, so the two layers can never disagree.
    """
    reasons: list[str] = []
    if is_limited_edition:
        if limited_edition_quantity:
            reasons.append(f"limited edition ({limited_edition_quantity} pieces)")
        else:
            reasons.append("limited edition (quantity not stated)")
    if is_collaboration:
        reasons.append("named collaboration")

    if reasons:
        return PriorityDecision(tier=PRIORITY_HIGH, reasons=reasons)
    return PriorityDecision(
        tier=PRIORITY_NORMAL,
        reasons=["no limited-edition or collaboration evidence"],
    )


def launch_group_key(
    *, run_id: int | None, manufacturer: str | None, event_type: str | None
) -> str | None:
    """Stable key for references that belong to one launch cluster.

    The Seiko Rukia Liberty Fabrics trio (HEG004J / HEE007J / HEA005J,
    2026-09-03) is the shape this targets: one coherent limited-edition
    launch arriving as three separate alerts. Note the existing
    `WatchFamily` grouping does NOT capture this -- those three are three
    different Seiko model lines, so their family keys differ. What actually
    binds them is the same run, same manufacturer, same event type.

    Returns None when the inputs cannot identify a cluster, so a missing
    run_id can never collapse unrelated references into one bucket.
    """
    if run_id is None or not manufacturer or not event_type:
        return None
    return f"{run_id}:{manufacturer.strip().lower()}:{event_type}"
