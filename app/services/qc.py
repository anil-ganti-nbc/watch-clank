"""Human QC feedback: active-queue querying and review persistence.

Built in response to the 2026-08-18 Citizen stale/out-of-stock flood
autopsy. See ai/handoff/HUMAN_QC_FEEDBACK_CONTRACT.md for the full
contract this module implements: EVENT != REVIEW, a Review is feedback
about one Event under one evidence state, never a permanent blacklist of
a reference, and future use of this data (ranking, noise-repeat
detection) stays conservative and explainable -- no ML, no opaque
suppression, no reference-family collateral damage.

2026-08-19 (Watch Clank QC + classifier hardening pass): extended with the
same contract for SpecialistLead -- see SpecialistLeadReview's own module
docstring for why that's a sibling table rather than a merge into
EventReview. `QueueFilters`/`DEFAULT_PAGE_SIZE`/`InvalidDispositionError`
are reused as-is (manufacturer/region/run_id mean exactly the same thing
for a lead as for an event; `event_type` doubles as the lead_type filter).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    CollectorRun,
    Event,
    EventReview,
    EventWatch,
    SpecialistLead,
    SpecialistLeadReview,
    Watch,
)
from app.models.review import DISPOSITIONS
from app.models.specialist_lead_review import LEAD_DISPOSITIONS
from app.services.editorial import AVAILABILITY_EVENT_TYPES

DEFAULT_PAGE_SIZE = 25


class InvalidDispositionError(ValueError):
    """Raised when a disposition outside DISPOSITIONS/LEAD_DISPOSITIONS is submitted."""


@dataclass(frozen=True)
class QueueFilters:
    manufacturer: str | None = None
    event_type: str | None = None
    region: str | None = None
    run_id: int | None = None


def _run_window(db: Session, run_id: int) -> tuple[datetime, datetime] | None:
    """(started_at, effective end) for a CollectorRun, or None if unknown.
    Shared by both the Event and SpecialistLead run_id filters."""
    run = db.get(CollectorRun, run_id)
    if run is None:
        return None
    return run.started_at, run.completed_at or (run.started_at + timedelta(minutes=30))


def _eligibility_clause():
    """Same rule as app.services.editorial.event_row_is_editorially_eligible,
    expressed in SQL so pagination/counts come from the database instead of
    over-fetching and filtering in Python (which would make "unreviewed
    count" and cursor pagination unreliable together)."""
    return or_(
        Event.event_type.notin_(AVAILABILITY_EVENT_TYPES),
        # SQLite has no real boolean type -- a JSON `true` comes back as
        # integer 1 via json_extract, and a missing/absent key comes back
        # NULL (never equal to 1), matching
        # event_row_is_editorially_eligible's Python-side "missing flag ==
        # not eligible" behavior for availability events.
        func.json_extract(Event.extra, "$.editorial_eligible") == 1,
    )


def _apply_filters(stmt, filters: QueueFilters, *, db: Session):
    if filters.manufacturer:
        stmt = stmt.where(Watch.manufacturer == filters.manufacturer)
    if filters.event_type:
        stmt = stmt.where(Event.event_type == filters.event_type)
    if filters.region:
        stmt = stmt.where(func.json_extract(Event.extra, "$.region") == filters.region)
    if filters.run_id is not None:
        window = _run_window(db, filters.run_id)
        if window is not None:
            stmt = stmt.where(Event.created_at >= window[0], Event.created_at <= window[1])
        else:
            # Unknown run id: show nothing rather than silently dropping the
            # filter and rendering an unrelated, unfiltered queue.
            stmt = stmt.where(Event.id.is_(None))
    return stmt


def _base_unreviewed_query(db: Session, filters: QueueFilters):
    stmt = (
        select(Event)
        .join(EventWatch, EventWatch.event_id == Event.id)
        .join(Watch, Watch.id == EventWatch.watch_id)
        .outerjoin(EventReview, EventReview.event_id == Event.id)
        .where(EventReview.id.is_(None))
        .where(_eligibility_clause())
    )
    return _apply_filters(stmt, filters, db=db)


def unreviewed_count(db: Session, filters: QueueFilters) -> int:
    stmt = _base_unreviewed_query(db, filters).with_only_columns(func.count(func.distinct(Event.id)))
    return db.scalar(stmt) or 0


def reviewed_today_count(db: Session) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count(func.distinct(EventReview.event_id))).where(EventReview.reviewed_at >= start)
    ) or 0


def fetch_queue_page(
    db: Session, filters: QueueFilters, *, before_id: int | None = None, limit: int = DEFAULT_PAGE_SIZE
) -> list[Event]:
    """Newest-first page of unreviewed, editorially-eligible Events.
    ``before_id``, if given, continues the queue past that event's id."""
    stmt = _base_unreviewed_query(db, filters).options(
        joinedload(Event.watches).joinedload(EventWatch.watch).joinedload(Watch.observations)
    )
    if before_id is not None:
        stmt = stmt.where(Event.id < before_id)
    stmt = stmt.order_by(desc(Event.id)).limit(limit)
    return list(db.scalars(stmt).unique().all())


def fetch_history_page(
    db: Session,
    filters: QueueFilters,
    *,
    disposition: str | None = None,
    include_corrected: bool = False,
    before_id: int | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[EventReview]:
    """Default view excludes already-corrected reviews -- QC History is a
    workable correction queue, not an immutable dump (2026-08-19 addendum).
    ``include_corrected=True`` reveals them again; nothing is ever deleted."""
    stmt = select(EventReview).options(joinedload(EventReview.event))
    if not include_corrected:
        stmt = stmt.where(EventReview.is_corrected.is_(False))
    if filters.manufacturer:
        stmt = stmt.where(EventReview.manufacturer == filters.manufacturer)
    if filters.event_type:
        stmt = stmt.where(EventReview.event_type == filters.event_type)
    if filters.region:
        stmt = stmt.where(EventReview.region == filters.region)
    if disposition:
        stmt = stmt.where(EventReview.disposition == disposition)
    if filters.run_id is not None:
        window = _run_window(db, filters.run_id)
        if window is not None:
            stmt = stmt.join(Event, Event.id == EventReview.event_id).where(
                Event.created_at >= window[0], Event.created_at <= window[1]
            )
        else:
            stmt = stmt.where(EventReview.id.is_(None))
    if before_id is not None:
        stmt = stmt.where(EventReview.id < before_id)
    stmt = stmt.order_by(desc(EventReview.id)).limit(limit)
    return list(db.scalars(stmt).unique().all())


def submit_review(db: Session, *, event: Event, disposition: str, reason: str | None = None) -> EventReview:
    """Persist (or correct) the operator's verdict on ``event``.

    Idempotent-by-correction: a second submission for the same event_id
    never creates a duplicate row. If the disposition differs from what is
    already on file, the prior verdict is appended to
    ``review_metadata.correction_history`` before being overwritten, so a
    correction stays auditable without a separate history table. Never
    touches the Event row itself or any other Event for the same
    watch/reference.
    """
    if disposition not in DISPOSITIONS:
        raise InvalidDispositionError(f"unknown disposition: {disposition!r}")

    watch = event.watches[0].watch if event.watches else None
    latest_obs = None
    if watch and watch.observations:
        latest_obs = max(watch.observations, key=lambda o: o.observed_at)

    now = datetime.now(UTC)
    existing = db.scalar(select(EventReview).where(EventReview.event_id == event.id))

    if existing is not None:
        if existing.disposition != disposition:
            metadata = dict(existing.review_metadata or {})
            history = list(metadata.get("correction_history") or [])
            history.append(
                {
                    "previous_disposition": existing.disposition,
                    "previous_reviewed_at": existing.reviewed_at.isoformat(),
                    "corrected_at": now.isoformat(),
                }
            )
            metadata["correction_history"] = history
            existing.review_metadata = metadata
            existing.disposition = disposition
            existing.is_corrected = True
        existing.reason = reason or existing.reason
        db.flush()
        return existing

    review = EventReview(
        event_id=event.id,
        watch_id=watch.id if watch else None,
        manufacturer=watch.manufacturer if watch else None,
        reference_canonical=watch.reference_canonical if watch else None,
        source_collector_id=latest_obs.collector_id if latest_obs else None,
        region=(event.extra or {}).get("region"),
        event_type=event.event_type,
        disposition=disposition,
        evidence_observed_at=latest_obs.observed_at if latest_obs else None,
        availability_status=latest_obs.availability_status if latest_obs else None,
        provenance_url=latest_obs.source_url if latest_obs else None,
        reason=reason,
    )
    db.add(review)
    db.flush()
    return review


def prior_reviews_for_reference(db: Session, *, manufacturer: str, reference_canonical: str) -> list[EventReview]:
    """Structured context for a *different* Event on the same reference --
    e.g. "this reference has 2 prior OUT_OF_STOCK reviews". Deliberately
    read-only/advisory: nothing in this module acts on this by itself. See
    module docstring -- annotation only, never auto-suppression."""
    return list(
        db.scalars(
            select(EventReview)
            .where(EventReview.manufacturer == manufacturer)
            .where(EventReview.reference_canonical == reference_canonical)
            .order_by(desc(EventReview.reviewed_at))
        ).all()
    )


# --- SpecialistLead (Layer B) QC -- sibling of the Event functions above ---


def _base_unreviewed_lead_query(db: Session, filters: QueueFilters):
    """Scoped to the same "current" set already shown on /intelligence's
    Specialist leads section (editorial_freshness == FRESH) -- historical/
    stale leads are not part of the featured triage workflow, matching
    Official Events' queue never offering to review suppressed events
    either. `filters.event_type` doubles as the lead_type filter."""
    stmt = (
        select(SpecialistLead)
        .outerjoin(SpecialistLeadReview, SpecialistLeadReview.specialist_lead_id == SpecialistLead.id)
        .where(SpecialistLeadReview.id.is_(None))
        .where(SpecialistLead.editorial_freshness == "FRESH")
    )
    if filters.manufacturer:
        stmt = stmt.where(SpecialistLead.manufacturer == filters.manufacturer)
    if filters.event_type:
        stmt = stmt.where(SpecialistLead.lead_type == filters.event_type)
    if filters.region:
        stmt = stmt.where(SpecialistLead.region == filters.region)
    if filters.run_id is not None:
        stmt = stmt.where(SpecialistLead.collector_run_id == filters.run_id)
    return stmt


def unreviewed_lead_count(db: Session, filters: QueueFilters) -> int:
    stmt = _base_unreviewed_lead_query(db, filters).with_only_columns(func.count(func.distinct(SpecialistLead.id)))
    return db.scalar(stmt) or 0


def reviewed_leads_today_count(db: Session) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count(func.distinct(SpecialistLeadReview.specialist_lead_id))).where(
            SpecialistLeadReview.reviewed_at >= start
        )
    ) or 0


def fetch_lead_queue_page(
    db: Session, filters: QueueFilters, *, before_id: int | None = None, limit: int = DEFAULT_PAGE_SIZE
) -> list[SpecialistLead]:
    """Newest-first (by id) page of unreviewed, currently-fresh
    SpecialistLeads. ``before_id``, if given, continues past that lead's id."""
    stmt = _base_unreviewed_lead_query(db, filters)
    if before_id is not None:
        stmt = stmt.where(SpecialistLead.id < before_id)
    stmt = stmt.order_by(desc(SpecialistLead.id)).limit(limit)
    return list(db.scalars(stmt).unique().all())


def fetch_lead_history_page(
    db: Session,
    filters: QueueFilters,
    *,
    disposition: str | None = None,
    include_corrected: bool = False,
    before_id: int | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[SpecialistLeadReview]:
    """Same default-excludes-corrected behavior as `fetch_history_page`."""
    stmt = select(SpecialistLeadReview).options(joinedload(SpecialistLeadReview.lead))
    if not include_corrected:
        stmt = stmt.where(SpecialistLeadReview.is_corrected.is_(False))
    if filters.manufacturer:
        stmt = stmt.where(SpecialistLeadReview.manufacturer == filters.manufacturer)
    if filters.event_type:
        stmt = stmt.where(SpecialistLeadReview.lead_type == filters.event_type)
    if filters.region:
        stmt = stmt.where(SpecialistLeadReview.region == filters.region)
    if disposition:
        stmt = stmt.where(SpecialistLeadReview.disposition == disposition)
    if filters.run_id is not None:
        stmt = stmt.where(SpecialistLeadReview.collector_run_id == filters.run_id)
    if before_id is not None:
        stmt = stmt.where(SpecialistLeadReview.id < before_id)
    stmt = stmt.order_by(desc(SpecialistLeadReview.id)).limit(limit)
    return list(db.scalars(stmt).unique().all())


def submit_lead_review(
    db: Session, *, lead: SpecialistLead, disposition: str, reason: str | None = None
) -> SpecialistLeadReview:
    """Persist (or correct) the operator's verdict on ``lead`` -- exact
    same idempotent-by-correction contract as `submit_review`. Never
    touches the SpecialistLead row itself, never touches any other lead
    for the same manufacturer/reference."""
    if disposition not in LEAD_DISPOSITIONS:
        raise InvalidDispositionError(f"unknown disposition: {disposition!r}")

    now = datetime.now(UTC)
    existing = db.scalar(select(SpecialistLeadReview).where(SpecialistLeadReview.specialist_lead_id == lead.id))

    if existing is not None:
        if existing.disposition != disposition:
            metadata = dict(existing.review_metadata or {})
            history = list(metadata.get("correction_history") or [])
            history.append(
                {
                    "previous_disposition": existing.disposition,
                    "previous_reviewed_at": existing.reviewed_at.isoformat(),
                    "corrected_at": now.isoformat(),
                }
            )
            metadata["correction_history"] = history
            existing.review_metadata = metadata
            existing.disposition = disposition
            existing.is_corrected = True
        existing.reason = reason or existing.reason
        db.flush()
        return existing

    review = SpecialistLeadReview(
        specialist_lead_id=lead.id,
        lead_title=lead.title,
        source_url=lead.source_url,
        source_id=lead.source_id,
        manufacturer=lead.manufacturer,
        region=lead.region,
        lead_type=lead.lead_type,
        source_authority_tier=lead.source_authority_tier,
        confidence=lead.confidence,
        published_at=lead.published_at,
        discovered_at=lead.discovered_at,
        collector_run_id=lead.collector_run_id,
        disposition=disposition,
        reason=reason,
    )
    db.add(review)
    db.flush()
    return review
