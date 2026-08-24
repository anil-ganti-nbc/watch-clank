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

Distinguishing power (why this shape and not a plain run counter):
- Tissot drip: runs 2..N are 100% unseen → window open → flood prevented.
- Established source / steady state: every run re-serves known items →
  window closed on run 2 → grandfathered collectors unaffected.
- Genuine post-baseline delta (the 2026-08-17 reset proof): run 2's slice
  contains known A/B/C plus new D → known items present → window closed →
  D surfaces normally.

A hard ceiling (INITIAL_FILL_RUNS) bounds the window for pathological
sources where traversal never wraps.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CollectorRun

# Hard ceiling on the fill window, in successful runs. With the observed
# Tissot shape (648 SKUs / 300-item budget) three runs wrap the catalogue;
# four gives headroom without delaying steady-state long.
INITIAL_FILL_RUNS = 4


def initial_fill_active(session: Session, collector_id: str) -> bool:
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
    if len(runs) >= INITIAL_FILL_RUNS:
        return False  # hard ceiling

    # Window holds only while EVERY successful run so far was pure
    # first-pass traversal: all processed items were previously unseen.
    # A run that re-served any known item (discovered > new) proves the
    # catalogue pass has wrapped and closes the window permanently.
    return all(
        (r.new_watch_count or 0) > 0 and (r.discovered_count or 0) == (r.new_watch_count or 0)
        for r in runs
    )
