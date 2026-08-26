"""Regression tests for the 2026-08-25/26 QC-volume incident.

Incident: 1,183 events reached human QC in one accumulation window —
581 weak FIRST_SEEN_BY_CLANK (score 15, no affirmative novelty) from the
Tissot/Timex UK initial catalogue fill plus 22 availability/region events.
The operator mass-rejected 572 rows via a scripted loop (~269 rows/min),
which the UI then reported as "Reviewed today: 603" indistinguishable from
item-by-item editorial review.

Root causes proven from DB + code:
1. Four single-item deployment-validation runs exhausted INITIAL_FILL_RUNS,
   closing the fill window before any real catalogue pass ran.
2. Weak FS events (catalogue bookkeeping) were QC-eligible by default.
3. No provenance distinguished bulk triage from individual review.

These tests pin all three repairs:
- noise rejection: weak-FS deprioritized out of default queue
- signal preservation: strong FS / NEW_REFERENCE / NEW_REGION still queue
- smoke-test runs no longer burn the initial-fill window
- review mode provenance recorded and counted truthfully
"""
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import CollectorRun, SourceObservation, Watch
from app.services.pipeline_constants import WEAK_FIRST_SEEN_QC_THRESHOLD
from tests.test_core import db_session, tmp_settings  # noqa: F401 -- pytest fixtures

# --- helpers ------------------------------------------------------------------


def _mk_watch(session, ref, manufacturer="Timex"):
    w = Watch(manufacturer=manufacturer, brand=manufacturer, reference_raw=ref,
              reference_canonical=ref)
    session.add(w)
    session.flush()
    return w


def _record_fs(pipeline, session, watch, *, score, collector="timex_uk_products"):
    """Drive _record_product_transition with a stubbed score_event to force a
    specific story_score for a first-sighting. A prior US observation
    establishes the reference so the transition path reaches FS emission."""
    from unittest.mock import patch

    from app.models import SourceObservation

    prior = SourceObservation(
        watch_id=watch.id, collector_id="timex_products", collector_version="t",
        parser_id="t", parser_version="1", region="US",
        source_url=f"https://www.timex.com/products/{watch.reference_canonical.lower()}",
        price=100.0, currency="USD", availability_status="AVAILABLE",
        overall_confidence=90.0,
        observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(prior)
    obs = SourceObservation(
        watch_id=watch.id, collector_id=collector, collector_version="t",
        parser_id="t", parser_version="1", region="UK",
        source_url=f"https://timex.co.uk/products/{watch.reference_canonical.lower()}",
        price=100.0, currency="GBP", availability_status="AVAILABLE",
        overall_confidence=90.0, observed_at=datetime.now(UTC),
    )
    session.add(obs)
    session.flush()

    # No prior observation: this mirrors the incident's initial-fill shape
    # where the watch AND its first observation arrive together and
    # is_new_watch=True drives FIRST_SEEN emission.
    scored = type("S", (), {
        "event_type": "FIRST_SEEN_BY_CLANK", "score": float(score),
        "confidence": "LOW" if score <= 15 else "MEDIUM",
        "reasons": ["first-ever product-catalogue observation of this reference"],
        "scoring_rule_version": "test",
    })()
    with (
        patch("app.services.editorial.score_event", return_value=scored),
        patch("app.services.editorial.editorial_eligibility", return_value=(True, ["test"])),
    ):
        return pipeline._record_product_transition(
            watch=watch, new_obs=obs, is_new_watch=True,
            notify=False, experimental=True, collector_id=collector,
        )


# --- noise rejection ----------------------------------------------------------


def test_weak_first_seen_is_deprioritized_out_of_default_queue(  # noqa: F811 -- pytest fixtures
    db_session, tmp_settings,  # noqa: F811 -- pytest fixtures
):
    """A score<=15 FIRST_SEEN must be persisted but NOT appear in the
    default unreviewed queue (the flood class: 581 rows on 08-25)."""
    from app.services.pipeline import PipelineService
    from app.services.qc import QueueFilters, unreviewed_count
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    assert WEAK_FIRST_SEEN_QC_THRESHOLD == 15.0

    before = unreviewed_count(db_session, QueueFilters())
    w = _mk_watch(db_session, "TWWEAK00001")
    result = _record_fs(pipeline, db_session, w, score=15)
    assert result.get("event_type") == "FIRST_SEEN_BY_CLANK", result

    from app.models import Event

    ev = db_session.get(Event, result["event_id"])
    # Evidence preserved: event exists, flagged, reason recorded.
    assert ev is not None
    assert ev.extra.get("human_qc_deprioritized") is True
    assert "weak FIRST_SEEN" in (ev.extra.get("human_qc_deprioritization_reason") or "")

    # Noise rejected: default queue count unchanged.
    after = unreviewed_count(db_session, QueueFilters())
    assert after == before


def test_strong_first_seen_still_enters_queue(db_session, tmp_settings):  # noqa: F811 -- pytest fixture  # noqa: F811 -- pytest fixture args
    """Signal preservation: a named-collaboration-class FS (score > threshold,
    as every USEFUL FS in history was) must remain queue-eligible."""
    from app.models import Event
    from app.services.pipeline import PipelineService
    from app.services.qc import QueueFilters, unreviewed_count
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    before = unreviewed_count(db_session, QueueFilters())
    w = _mk_watch(db_session, "TWSTRONG001")
    result = _record_fs(pipeline, db_session, w, score=25)

    ev = db_session.get(Event, result["event_id"])
    assert not ev.extra.get("human_qc_deprioritized")

    after = unreviewed_count(db_session, QueueFilters())
    assert after == before + 1


def test_new_reference_and_region_never_weak_suppressed(db_session, tmp_settings):  # noqa: F811 -- pytest fixture  # noqa: F811 -- pytest fixture args
    """The threshold applies ONLY to FIRST_SEEN_BY_CLANK; NEW_REFERENCE /
    NEW_REGION are always queue-eligible regardless of score."""
    from unittest.mock import patch

    from app.services.pipeline import PipelineService
    from app.services.qc import QueueFilters, unreviewed_count
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))

    for etype, score in (("NEW_REFERENCE", 50), ("NEW_REGION", 20)):
        before = unreviewed_count(db_session, QueueFilters())
        w = _mk_watch(db_session, f"TWNR{etype[:4]}{int(score)}")  # noqa: F841 -- identity fixture
        obs = SourceObservation(
            watch_id=w.id, collector_id="timex_uk_products", collector_version="t",
            parser_id="t", parser_version="1", region="UK",
            source_url=f"https://timex.co.uk/products/{w.reference_canonical.lower()}",
            price=None, currency=None, availability_status=None,
            overall_confidence=70.0, observed_at=datetime.now(UTC),
        )
        db_session.add(obs)
        db_session.flush()

        scored = type("S", (), {
            "event_type": etype, "score": float(score), "confidence": "MEDIUM",
            "reasons": [], "scoring_rule_version": "test",
        })()
        with (
            patch("app.services.editorial.score_event", return_value=scored),
            patch("app.services.editorial.editorial_eligibility", return_value=(True, ["t"])),
        ):
            result = pipeline._record_product_transition(
                watch=w, new_obs=obs, is_new_watch=True,
                notify=False, experimental=True, collector_id="timex_uk_products",
            )
        assert result.get("event_type") == etype, result
        from app.models import Event

        ev = db_session.get(Event, result["event_id"])
        assert not ev.extra.get("human_qc_deprioritized"), f"{etype} wrongly suppressed"
        assert unreviewed_count(db_session, QueueFilters()) == before + 1


# --- root cause: initial-fill ceiling -----------------------------------------


def test_smoke_test_runs_do_not_exhaust_initial_fill_window(db_session):  # noqa: F811 -- pytest fixture
    """THE root-cause regression, hardened per owner directive: bounded
    smoke/validation runs must never consume the initial-fill budget,
    regardless of whether they discover 1, 2, 10 or 100 items.

    Qualification is invocation-provenance based: a run counts toward the
    ceiling only when summary_metadata.max_items shows it ran unbounded or
    at full default budget. Bounded runs and legacy rows without the
    provenance field never qualify.
    """
    import json

    from app.services.initial_fill import INITIAL_FILL_RUNS, initial_fill_active

    def _run(disc, new, max_items):
        meta = {"max_items": max_items} if max_items != "ABSENT" else {}
        return CollectorRun(
            collector_id="smoke_collector", collector_version="0.0",
            status="SUCCESS", discovered_count=disc, new_watch_count=new,
            summary_metadata=json.dumps(meta),
        )

    # Bounded runs discovering MORE than one item each — the exact hole the
    # first repair left open (disc > 1 would have qualified them).
    for disc in (1, 2, 10, 100):
        db_session.add(_run(disc, disc, max_items=10))
    # Legacy rows predating the max_items field: conservative no-qualify.
    for _ in range(2):
        db_session.add(CollectorRun(
            collector_id="smoke_collector", collector_version="0.0",
            status="SUCCESS", discovered_count=5, new_watch_count=5,
            summary_metadata=None,
        ))
    db_session.flush()

    assert initial_fill_active(db_session, "smoke_collector"), (
        "bounded smoke/validation runs must never consume the initial-fill "
        "window regardless of their discovery count"
    )

    # Full-budget passes DO count: INITIAL_FILL_RUNS of them close the window.
    for _ in range(INITIAL_FILL_RUNS):
        db_session.add(CollectorRun(
            collector_id="full_pass_collector", collector_version="0.0",
            status="SUCCESS", discovered_count=300, new_watch_count=300,
            summary_metadata=json.dumps({"max_items": None}),  # unbounded pass
        ))
    db_session.flush()
    assert not initial_fill_active(db_session, "full_pass_collector")

    # A single full-budget pass does not close it; mixed with smoke runs
    # the window stays open until enough real passes accumulate.
    db_session.add(_run(300, 300, max_items=300))
    db_session.flush()
    assert initial_fill_active(db_session, "smoke_collector")


# --- review provenance --------------------------------------------------------


def test_review_mode_recorded_and_validated(db_session):  # noqa: F811 -- pytest fixture
    """Bulk vs individual provenance: mode persists orthogonally in
    review_metadata; invalid modes are rejected; dispositions unchanged."""
    from app.services.qc import InvalidDispositionError, submit_review

    _mk_watch(db_session, "TWPROV00001")
    ev = __import__("app.models", fromlist=["Event"]).Event(
        event_type="FIRST_SEEN_BY_CLANK", title="x", status="DRAFT",
        story_score=15.0, confidence_score=30.0, data_completeness_score=90.0,
        scoring_rule_version="t", extra={},
    )
    db_session.add(ev)
    db_session.flush()

    r_bulk = submit_review(db_session, event=ev, disposition="NOT_USEFUL", mode="bulk")
    assert r_bulk.review_metadata["mode"] == "bulk"
    assert r_bulk.disposition == "NOT_USEFUL"

    # correction keeps latest mode
    r_fix = submit_review(db_session, event=ev, disposition="USEFUL", mode="individual")
    assert r_fix.review_metadata["mode"] == "individual"
    assert r_fix.is_corrected
    assert r_fix.review_metadata["correction_history"][0]["previous_disposition"] == "NOT_USEFUL"

    with pytest.raises(InvalidDispositionError):
        submit_review(db_session, event=ev, disposition="USEFUL", mode="telepathy")


def test_reviewed_today_breakdown_separates_bulk(db_session):  # noqa: F811 -- pytest fixture
    """Three accounting classes (owner directive): individual / bulk /
    unspecified. NULL-mode (legacy) rows are counted as UNSPECIFIED — never
    silently relabelled "individual"."""
    from app.services.qc import reviewed_today_breakdown, reviewed_today_count, submit_review

    for _i, (disp, mode) in enumerate([
        ("NOT_USEFUL", "bulk"), ("NOT_USEFUL", "bulk"),
        ("USEFUL", "individual"),
        ("USEFUL", None),  # legacy/unspecified path: no mode recorded
    ]):
        _mk_watch(db_session, f"TWSPLIT{_i:05d}")
        ev = __import__("app.models", fromlist=["Event"]).Event(
            event_type="FIRST_SEEN_BY_CLANK", title=f"x{_i}", status="DRAFT",
            story_score=15.0, confidence_score=30.0, data_completeness_score=90.0,
            scoring_rule_version="t", extra={},
        )
        db_session.add(ev)
        db_session.flush()
        submit_review(db_session, event=ev, disposition=disp, mode=mode)

    breakdown = reviewed_today_breakdown(db_session)
    assert breakdown["bulk"] >= 2
    assert breakdown["individual"] >= 1, f"explicit individual not counted: {breakdown}"
    assert breakdown["unspecified"] >= 1, (
        f"legacy NULL-mode row must count as unspecified, not individual: {breakdown}"
    )
    assert breakdown["total"] == (
        breakdown["bulk"] + breakdown["individual"] + breakdown["unspecified"]
    )

    assert reviewed_today_count(db_session) >= breakdown["total"]


def test_queue_denominators_raw_vs_default_visible(
    db_session,  # noqa: F811 -- pytest fixture
    tmp_settings,  # noqa: F811 -- pytest fixture
):
    """Owner directive — clarify the two denominators:

    - RAW unreviewed: every Event with no EventReview row (audit population).
    - DEFAULT human-QC queue: raw minus availability events lacking
      editorial_eligible, minus human_qc_deprioritized rows.

    The incident's numbers were 639 raw vs 580 default-visible; this test
    pins that both counts are computable and distinct.
    """

    from app.models import Event, EventReview
    from app.services.pipeline import PipelineService
    from app.services.qc import QueueFilters, unreviewed_count
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))

    def raw_unreviewed(session):
        return session.scalar(
            select(func.count(Event.id)).select_from(Event)
            .outerjoin(EventReview, EventReview.event_id == Event.id)
            .where(EventReview.id.is_(None))
        )

    before_raw = raw_unreviewed(db_session)
    queue_before = unreviewed_count(db_session, QueueFilters())

    # A weak FS event: persisted (raw +1) but NOT in the default queue.
    w = _mk_watch(db_session, "TWDENOM001")
    _record_fs(pipeline, db_session, w, score=15)
    after_raw = raw_unreviewed(db_session)

    assert after_raw == before_raw + 1, "weak FS must persist in the audit population"
    assert unreviewed_count(db_session, QueueFilters()) == queue_before, (
        "weak FS must NOT enter the default human-QC queue"
    )
