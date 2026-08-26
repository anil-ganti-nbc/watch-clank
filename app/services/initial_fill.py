"""Initial-catalogue-fill suppression for bounded-budget catalogue brands.

Fleet Law 1 extension proven necessary by the 2026-08-25 Tissot
repeated-run validation: with a bounded per-run budget (max_items=120)
against a 648-SKU catalogue, run 1 is auto-baselined (silent, correct),
but runs 2..N each discover a further batch of genuinely-unseen SKUs from
the SAME initial catalogue pass and emit FIRST_SEEN events for each — a
slow-drip flood until the first full catalogue pass completes.

Fix: the "initial fill" window. The window stays open only while every
successful run so far consisted ENTIRELY of first-time URLs (new-first
traversal guarantees unseen items are served before known ones, so while
unseen catalogue remains, each run is 100% unseen). The moment any
successful run's slice also re-served already-known items — proving the
first catalogue pass has wrapped — ordinary novelty semantics resume
permanently.

2026-08-26 incident hardening (owner directive): run qualification is
INVOCATION-BASED, not discovery-count-based. The original repair counted a
run as a catalogue pass when discovered_count > 1 — but a smoke/validation
invocation can legitimately discover 2, 10 or even 100 items while still
being bounded. Qualification now reads the invocation provenance recorded
in CollectorRun.summary_metadata:

  - ``max_items`` present and below the registry default (or any explicit
    small cap) => BOUNDED smoke/validation run. Never consumes budget,
    regardless of how many items it happened to discover.
  - ``max_items is None`` (unbounded) or == default budget => real
    catalogue pass; counts toward INITIAL_FILL_RUNS.

A missing summary_metadata/max_items (legacy rows predating the field)
falls back conservatively: the run does NOT count toward the ceiling, so
an unknown-history collector stays protected rather than silently losing
its fill window.

Distinguishing power (why the wrap rule and not a plain counter):
- Tissot drip: runs 2..N at full budget, 100% unseen → window open.
- Established source / steady state: slices re-serve known items → closed.
- Genuine post-baseline delta: slice contains known A/B/C plus new D → closed.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import CollectorRun

# Hard ceiling on the fill window, in successful FULL-budget runs. With the
# observed Tissot shape (648 SKUs / 300-item budget) three runs wrap the
# catalogue; four gives headroom without delaying steady-state long.
INITIAL_FILL_RUNS = 4


def _is_catalogue_pass(run: CollectorRun, default_budget: int = 300) -> bool:
    """True when this run was a genuine (unbounded or full-budget) pass.

    Invocation-provenance based: reads summary_metadata.max_items written by
    the runner. Bounded (explicitly capped below default) runs are
    smoke/validation invocations and never qualify — even if they happened
    to discover many items. Legacy rows without the field do not qualify
    (conservative: keeps the fill window open rather than closing it early).
    """
    meta_raw = run.summary_metadata
    if not meta_raw:
        return False  # legacy row with no metadata: conservative no-qualify
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if "max_items" not in meta:
        return False  # provenance field absent: conservative no-qualify
    max_items = meta["max_items"]
    if max_items is None:
        return True  # explicit null = unbounded invocation: a real pass
    try:
        # Bounded at or above the default budget still counts as a full pass.
        return int(max_items) >= default_budget
    except (TypeError, ValueError):
        return False


def initial_fill_active(
    session: Session, collector_id: str, *, default_budget: int = 300
) -> bool:
    """True while this collector is still filling its initial catalogue."""
    runs = (
        session.query(CollectorRun)
        .filter(
            CollectorRun.collector_id == collector_id,
            CollectorRun.status.in_(("SUCCESS", "PARTIAL")),
        )
        .order_by(CollectorRun.started_at.asc(), CollectorRun.id.asc())
        .all()
    )
    if not runs:
        return True  # never run: the very first pass is pure acquisition

    # Window holds only while EVERY successful run so far was pure
    # first-pass traversal: all processed items were previously unseen.
    # A run that re-served any known item (discovered > new) proves the
    # catalogue pass has wrapped and closes the window permanently.
    if not all(
        (r.new_watch_count or 0) > 0 and (r.discovered_count or 0) == (r.new_watch_count or 0)
        for r in runs
    ):
        return False

    # Hard ceiling counts only genuine catalogue passes (invocation-
    # qualified); bounded smoke/validation runs never consume budget.
    catalogue_passes = sum(
        1 for r in runs if _is_catalogue_pass(r, default_budget=default_budget)
    )
    return catalogue_passes < INITIAL_FILL_RUNS
