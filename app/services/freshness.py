"""Editorial freshness classification for specialist leads.

See ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md for the incident this exists
to fix: DISCOVERY NOVELTY ("has Clank seen this before" -- is_baseline,
dedup-by-URL) and EDITORIAL FRESHNESS ("is this current enough to show a
journalist as news") are different questions. A record can be newly
discovered and simultaneously stale.

Deliberately scoped to SpecialistLead only. Official Events already have
correct freshness semantics without this concept: a NEW_REFERENCE/
NEW_REGION/PRICE_CHANGE/etc. Event is only ever created for a genuine
transition detected after a healthy baseline (see
app/services/pipeline.py's is_baseline_active() guards) -- there is no
"old official event discovered late" failure mode to fix, because Events
don't carry an independent publication timestamp the way a blog article
does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.time import ensure_utc

# Source types that don't naturally carry a publication timestamp -- their
# authoritative "when" is when Watch Clank observed them, not when they
# were "published" (Phase 2D of the freshness bugfix brief).
_OBSERVATION_TIME_SOURCE_TYPES = frozenset({"RETAILER_EARLY_LISTING"})


@dataclass(frozen=True)
class FreshnessResult:
    state: str
    reason: str


def classify_lead_freshness(
    *,
    source_type: str,
    ingestion_method: str,
    is_baseline: bool,
    published_at: datetime | None,
    discovered_at: datetime,
    now: datetime,
    window_hours: int,
) -> FreshnessResult:
    """Deterministic, timezone-safe. Never mutates anything -- callers
    stamp the result onto the lead themselves."""
    if is_baseline:
        return FreshnessResult("BASELINE", "discovered during an epoch baseline run")

    reference_time = published_at
    reference_label = "published"

    if reference_time is None:
        if source_type in _OBSERVATION_TIME_SOURCE_TYPES:
            reference_time = discovered_at
            reference_label = "observed"
        elif ingestion_method == "manual":
            return FreshnessResult(
                "MANUAL_UNDATED",
                "manually ingested with no publication timestamp supplied -- freshness not assumed",
            )
        else:
            return FreshnessResult(
                "UNKNOWN_TIMESTAMP",
                "no publication timestamp available for this source class -- freshness not assumed",
            )

    age = ensure_utc(now) - ensure_utc(reference_time)
    window = timedelta(hours=window_hours)
    if age <= window:
        return FreshnessResult("FRESH", f"{reference_label} {age} ago, within the {window_hours}h freshness window")
    return FreshnessResult(
        "STALE_PUBLICATION", f"{reference_label} {age} ago, exceeds the {window_hours}h freshness window"
    )
