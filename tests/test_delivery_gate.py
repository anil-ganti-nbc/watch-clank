"""Fleet-wide experimental delivery-silence gate tests.

Canon (2026-08-25 owner decision): external delivery is a PROMOTION
privilege. Experimental-maturity collectors must be externally silent for
ANY event type and ANY score — not just FIRST_SEEN, and not merely because
initial-fill suppression happens to be active.
"""
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
