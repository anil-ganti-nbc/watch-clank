"""Editorial freshness classification for specialist leads and, narrowly,
baseline-discovered product-catalogue references.

See ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md for the incident this exists
to fix: DISCOVERY NOVELTY ("has Clank seen this before" -- is_baseline,
dedup-by-URL) and EDITORIAL FRESHNESS ("is this current enough to show a
journalist as news") are different questions. A record can be newly
discovered and simultaneously stale.

`classify_lead_freshness` is scoped to SpecialistLead. It was written on
the stated assumption that official Events need no equivalent: "a
NEW_REFERENCE/NEW_REGION/PRICE_CHANGE/etc. Event is only ever created for
a genuine transition detected after a healthy baseline... there is no
'old official event discovered late' failure mode to fix, because Events
don't carry an independent publication timestamp the way a blog article
does." That was true when written and became false without anyone
revisiting it: `timex_products` started opportunistically capturing
Shopify's own `published_at` into `Watch.extra_specs` (see
app/parsers/timex_products.py), but nothing downstream ever read it. The
result (see ai/handoff/INCIDENT_TIMEX_BASELINE_ABSORPTION.md): a real
product -- Timex Weekender New England, TW2Y86600/TW2Y86500, first
publicly published 2026-08-04 per Timex's own Shopify data -- was first
discovered by Watch Clank ten days later, on 2026-08-14, during the
Hetzner redeploy's mandatory catalogue baseline, and was therefore
permanently silenced despite carrying first-party evidence it was still
genuinely recent. `classify_baseline_product_freshness` below closes that
one specific gap without touching `classify_lead_freshness` or loosening
baseline suppression generally: it is consulted from exactly one call
site (the is_new_watch/NEW_REFERENCE branch of
PipelineService._record_product_transition), and only ever changes
behavior when a source captured a genuinely parseable publication
timestamp -- every other collector, and every other transition type
(NEW_REGION, price/availability changes) during baseline, is completely
unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.time import ensure_utc

# Source types that don't naturally carry a publication timestamp -- their
# authoritative "when" is when Watch Clank observed them, not when they
# were "published" (Phase 2D of the freshness bugfix brief).
_OBSERVATION_TIME_SOURCE_TYPES = frozenset({"RETAILER_EARLY_LISTING"})

# 2026-08-24 Windows field-test repair: a `published_at` materially AFTER the
# observation cannot be launch evidence -- the source clock disagrees with our
# own or the value is a bulk-catalogue-touch artifact (live case:
# TW2Y70900VQ/TW4B34900VQ carried published_at minutes-to-hours AFTER their
# own first observation; Shopify re-publishes catalogue rows during routine
# maintenance). The tolerance below only forgives plausible sub-minute clock
# skew; anything beyond it makes the timestamp unusable for positive
# freshness/launch evidence. The raw value stays preserved in provenance
# (extra_specs / novelty_evidence.source_published_at) -- we reject it as
# EVIDENCE, never delete it.
_FUTURE_TIMESTAMP_TOLERANCE = timedelta(minutes=1)


def publication_timestamp_is_usable(
    *, published_at: datetime | None, observed_at: datetime
) -> bool:
    """May ``published_at`` contribute POSITIVE freshness/launch evidence?

    True only when the timestamp exists and is not in the future relative to
    the observation (beyond a small documented clock-skew tolerance). A
    False return means "no positive evidence available"; it never asserts
    the opposite direction (an old timestamp is separately judged by each
    caller's window). Callers should keep surfacing the raw value as
    provenance either way.
    """
    if published_at is None:
        return False
    return ensure_utc(published_at) <= ensure_utc(observed_at) + _FUTURE_TIMESTAMP_TOLERANCE


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


def classify_baseline_product_freshness(
    *, published_at: datetime | None, observed_at: datetime, window_hours: int
) -> FreshnessResult:
    """Should a product first discovered while a baseline is active still
    be allowed to raise a NEW_REFERENCE Event, because the source's own
    evidence proves it's recent rather than backfilled inventory?

    Baseline suppression correctly treats "we just noticed this" as
    non-news by default -- most of a freshly onboarded source's catalogue
    really is old, already-existing inventory, and re-baselining an
    established source must stay silent. But without independent evidence,
    baseline has no way to distinguish that from "a product that happens to
    have launched shortly before baseline ran." A structured `published_at`
    (present today only when a parser opportunistically captured one, e.g.
    timex_products from Shopify's product JSON -- absent for most
    collectors) is exactly that evidence when present.

    window_hours is deliberately tight (default 72h, matching the same
    already-trusted specialist_freshness_window_hours bar), not the wider
    ~30-day "recent launch" window used elsewhere in this module for
    scoring bonuses. Checked empirically against the real Hetzner catalogue
    (1485 Timex watches, one real baseline run): a 30-day window would have
    let through 240 products (16% of the catalogue) because Shopify's
    published_at is frequently bulk-touched across many unrelated,
    genuinely old products by routine catalogue maintenance -- 738 of the
    1485 even share one identical 2022 date, clearly a bulk-import
    artifact, not a launch date. At 72 hours, only 7 products qualified,
    and every one belonged to a tight (seconds-apart) multi-SKU cluster
    sharing a `collection` with a sibling reference -- the actual signature
    of a genuine coordinated product-family launch, not maintenance noise.
    A tighter window trades some recall (it would not have caught
    TW2Y86600/TW2Y86500 themselves, whose real publish-to-baseline gap was
    10 days) for materially lower false-positive risk on every future
    baseline. See ai/handoff/INCIDENT_TIMEX_BASELINE_ABSORPTION.md for the
    full analysis.

    No published_at -> UNKNOWN_TIMESTAMP, i.e. no behavior change: baseline
    stays silent exactly as before. Only a genuinely parseable, non-future
    timestamp within `window_hours` of the observation returns FRESH.
    """
    if published_at is None:
        return FreshnessResult(
            "UNKNOWN_TIMESTAMP", "no publication timestamp available for this source; baseline stays silent"
        )
    # 2026-08-24: a future-dated timestamp is bulk-touch/clock noise, not
    # launch evidence -- see publication_timestamp_is_usable. Treated exactly
    # like "no usable evidence"; the raw value stays in provenance.
    if not publication_timestamp_is_usable(published_at=published_at, observed_at=observed_at):
        return FreshnessResult(
            "FUTURE_TIMESTAMP",
            "publication timestamp is after the observation beyond clock-skew tolerance; "
            "rejected as freshness evidence (bulk-catalogue-touch or clock artifact)",
        )
    window = timedelta(hours=window_hours)
    age = ensure_utc(observed_at) - ensure_utc(published_at)
    if age <= window:
        return FreshnessResult(
            "FRESH", f"published {age} before baseline discovery, within the {window_hours}h window"
        )
    return FreshnessResult(
        "STALE_PUBLICATION", f"published {age} before baseline discovery, exceeds the {window_hours}h window"
    )
