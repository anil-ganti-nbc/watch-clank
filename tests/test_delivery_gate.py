"""Fleet-wide experimental delivery-silence gate tests.

Canon (2026-08-25 owner decision): external delivery is a PROMOTION
privilege. Experimental-maturity collectors must be externally silent for
ANY event type and ANY score — not just FIRST_SEEN, and not merely because
initial-fill suppression happens to be active.
"""
from sqlalchemy import select

from app.services.delivery_gate import (
    EXPERIMENTAL_MATURITY_COLLECTORS,
    experimental_delivery_blocked,
)
from tests.test_core import (
    db_session,  # noqa: F401 -- pytest fixture re-export
    tmp_settings,  # noqa: F401 -- pytest fixture re-export
)


def test_experimental_collectors_are_delivery_blocked():
    assert experimental_delivery_blocked("tissot_sitemap")
    assert experimental_delivery_blocked("timex_uk_products")


def test_established_collectors_are_not_blocked_by_the_maturity_gate():
    """The gate must be scoped to the experimental set — production
    collectors' delivery path must be untouched."""
    assert not experimental_delivery_blocked("timex_products")
    assert not experimental_delivery_blocked("casio_multi")
    assert not experimental_delivery_blocked(None)  # unregistered: upstream handles


def test_promotion_removes_block():
    """Promotion review = removing the id from the maturity set. Simulate a
    promoted tissot by patching the frozenset."""
    from unittest.mock import patch

    promoted = EXPERIMENTAL_MATURITY_COLLECTORS - {"tissot_sitemap"}
    with patch("app.services.delivery_gate.EXPERIMENTAL_MATURITY_COLLECTORS", promoted):
        assert not experimental_delivery_blocked("tissot_sitemap")
        assert experimental_delivery_blocked("timex_uk_products")  # others unaffected


def test_notify_path_respects_gate_for_non_first_seen_events(db_session, tmp_settings):
    """Runtime enforcement: an experimental collector emitting a NEW_REGION
    event at high score must NOT dispatch Discord while its maturity state
    is experimental — even though first_seen gating doesn't apply here."""
    from unittest.mock import MagicMock, patch

    from app.models import Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    w = Watch(manufacturer="Tissot", brand="Tissot", reference_raw="TGATE001",
              reference_canonical="TGATE001")
    db_session.add(w)
    db_session.flush()

    notifier_mock = MagicMock()
    notifier_mock.editorial_enabled = True
    with (
        patch("app.services.discord_notify.DiscordNotifier", return_value=notifier_mock) as ctor,
        patch("app.services.editorial.editorial_eligibility", return_value=(True, ["test"])),
        patch(
            "app.services.delivery_gate.EXPERIMENTAL_MATURITY_COLLECTORS",
            frozenset({"tissot_sitemap"}),
        ),
    ):
        pipeline._record_product_transition(
            watch=w,
            new_obs=_gate_obs(w.id),
            is_new_watch=False,
            notify=True,
            experimental=True,
            collector_id="tissot_sitemap",
        )
        # Gate blocked before any notifier construction/dispatch.
        ctor.assert_not_called()
        notifier_mock.send.assert_not_called()


def _gate_obs(watch_id):
    from datetime import UTC, datetime

    from app.models import SourceObservation

    return SourceObservation(
        watch_id=watch_id,
        collector_id="tissot_sitemap",
        collector_version="test",
        parser_id="test",
        parser_version="1",
        region="US",
        source_url="https://www.tissotwatches.com/en-us/TGATE001.html",
        price=100.0,
        currency="USD",
        availability_status="AVAILABLE",
        overall_confidence=90.0,
        observed_at=datetime.now(UTC),
    )


def _seed_seiko_jp_qualification_evidence(db_session, provenance: str) -> None:
    """Simulate the collector's own prior completed run recording its
    terminal qualification credit under the given provenance -- exactly
    what scripts/run_pipeline.py's --experimental-product path does at the
    end of every real run via PipelineService._record_qualification_execution.
    QualificationService.delivery_allowed() reads THIS prior record when
    gating the current run's events (see app/services/qualification.py),
    so a realistic test seeds it via a separate, already-completed run
    rather than asserting the current run gates on its own evidence."""
    from app.models import CollectorRun
    from app.services.qualification import QualificationService

    prior_run = CollectorRun(collector_id="seiko_jp_products", collector_version="test", status="SUCCESS")
    db_session.add(prior_run)
    db_session.flush()
    QualificationService(db_session).record_execution(prior_run, provenance)
    db_session.commit()


def test_seiko_jp_scheduled_provenance_unblocks_eligible_delivery(db_session, tmp_settings):
    """2026-09-03 incident regression, positive case: once a Seiko JP run
    has recorded SCHEDULED provenance (the render_units.py fix's real
    effect), a subsequent editorially-eligible product transition must NOT
    be gated -- confirmed via a real QualificationEvidence row and a real
    notifier dispatch attempt, not just the isolated gate predicate."""
    from unittest.mock import MagicMock, patch

    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    _seed_seiko_jp_qualification_evidence(db_session, "SCHEDULED")

    watch = Watch(manufacturer="Seiko", brand="Seiko", reference_raw="HBC008J", reference_canonical="HBC008J")
    db_session.add(watch)
    db_session.flush()
    not_yet = SourceObservation(
        watch_id=watch.id, collector_id="seiko_jp_products", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="JP", source_url="https://x/hbc008j", price=155100.0, currency="JPY",
        availability_status="SOLD_OUT", overall_confidence=90.0,
    )
    db_session.add(not_yet)
    db_session.commit()
    now_orderable = SourceObservation(
        watch_id=watch.id, collector_id="seiko_jp_products", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="JP", source_url="https://x/hbc008j", price=155100.0, currency="JPY",
        availability_status="AVAILABLE", overall_confidence=90.0,
    )
    db_session.add(now_orderable)
    db_session.flush()

    notifier_mock = MagicMock()
    notifier_mock.editorial_enabled = True
    notifier_mock.send_editorial_alert.return_value = True
    with (
        patch("app.services.discord_notify.DiscordNotifier", return_value=notifier_mock) as ctor,
        patch("app.services.editorial.editorial_eligibility", return_value=(True, ["test"])),
    ):
        result = PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
            watch=watch, new_obs=now_orderable, is_new_watch=False, notify=True,
            collector_id="seiko_jp_products",
        )

    assert result["event_type"] == "RESTOCK"
    event = db_session.scalars(select(Event).where(Event.event_type == "RESTOCK")).first()
    assert event is not None
    assert event.extra.get("delivery", {}).get("state") == "sent"
    ctor.assert_called_once()
    notifier_mock.send_editorial_alert.assert_called_once()


def test_seiko_jp_unknown_provenance_stays_gated(db_session, tmp_settings):
    """2026-09-03 incident regression, safety control: UNKNOWN provenance
    (the pre-fix default every --experimental-product invocation carried)
    must continue failing closed -- this is the exact mechanism the fix
    must NOT weaken. Same transition as the positive case above, only the
    seeded provenance differs."""
    from unittest.mock import MagicMock, patch

    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    _seed_seiko_jp_qualification_evidence(db_session, "UNKNOWN")

    watch = Watch(manufacturer="Seiko", brand="Seiko", reference_raw="HBC009J", reference_canonical="HBC009J")
    db_session.add(watch)
    db_session.flush()
    not_yet = SourceObservation(
        watch_id=watch.id, collector_id="seiko_jp_products", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="JP", source_url="https://x/hbc009j", price=155100.0, currency="JPY",
        availability_status="SOLD_OUT", overall_confidence=90.0,
    )
    db_session.add(not_yet)
    db_session.commit()
    now_orderable = SourceObservation(
        watch_id=watch.id, collector_id="seiko_jp_products", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="JP", source_url="https://x/hbc009j", price=155100.0, currency="JPY",
        availability_status="AVAILABLE", overall_confidence=90.0,
    )
    db_session.add(now_orderable)
    db_session.flush()

    notifier_mock = MagicMock()
    notifier_mock.editorial_enabled = True
    with (
        patch("app.services.discord_notify.DiscordNotifier", return_value=notifier_mock) as ctor,
        patch("app.services.editorial.editorial_eligibility", return_value=(True, ["test"])),
    ):
        PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
            watch=watch, new_obs=now_orderable, is_new_watch=False, notify=True,
            collector_id="seiko_jp_products",
        )

    event = db_session.scalars(select(Event).where(Event.event_type == "RESTOCK")).first()
    assert event is not None
    assert event.extra.get("delivery", {}).get("state") == "gated"
    assert event.extra.get("delivery", {}).get("reason") == "experimental_maturity"
    ctor.assert_not_called()
    notifier_mock.send_editorial_alert.assert_not_called()
