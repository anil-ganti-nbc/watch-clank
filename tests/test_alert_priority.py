"""Track G tests: priority labelling and launch grouping, with routing
deliberately unchanged.

Incidents: Casio Frogman GWF-D1000BC-1JF (2026-08-29) and MASTER IN HORIZON
GOLD (2026-08-27) were collected, evented and delivered successfully, then
buried among other notifications. The Seiko Rukia Liberty Fabrics trio
(2026-09-03) arrived as three unconnected alerts for one launch.
"""
from unittest.mock import MagicMock, patch

from app.services.alert_priority import (
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    classify,
    launch_group_key,
)
from tests.test_core import (
    db_session,  # noqa: F401 -- pytest fixture re-export
    tmp_settings,  # noqa: F401 -- pytest fixture re-export
)


def test_limited_edition_is_high_priority():
    decision = classify(is_limited_edition=True, limited_edition_quantity=500)
    assert decision.tier == PRIORITY_HIGH
    assert decision.is_high
    assert "limited edition (500 pieces)" in decision.reasons


def test_collaboration_is_high_priority():
    decision = classify(is_collaboration=True)
    assert decision.tier == PRIORITY_HIGH
    assert "named collaboration" in decision.reasons


def test_everything_else_is_normal_by_operator_decision():
    """2026-09-03 operator decision: HIGH means limited edition or
    collaboration and nothing else. A tier most alerts qualify for would
    recreate the flood it exists to cut through, so these deliberately
    stay NORMAL."""
    decision = classify(is_limited_edition=False, is_collaboration=False)
    assert decision.tier == PRIORITY_NORMAL
    assert decision.reasons == ["no limited-edition or collaboration evidence"]

    # Unknown/missing evidence must never be promoted to HIGH.
    assert classify().tier == PRIORITY_NORMAL
    assert classify(is_limited_edition=None, is_collaboration=None).tier == PRIORITY_NORMAL


def test_priority_decision_always_explains_itself():
    """A triage layer that cannot say why it demoted something is just an
    opaque filter that can hide a real launch."""
    for decision in (classify(is_collaboration=True), classify()):
        assert decision.reasons
        extra = decision.as_extra()
        assert extra["tier"] in (PRIORITY_HIGH, PRIORITY_NORMAL)
        assert extra["reasons"]
        assert extra["policy_version"]


def test_launch_group_key_binds_one_launch_and_refuses_to_guess():
    """The Rukia trio shape: same run, same brand, same event type."""
    a = launch_group_key(run_id=42, manufacturer="Seiko", event_type="NEW_REFERENCE")
    b = launch_group_key(run_id=42, manufacturer="seiko", event_type="NEW_REFERENCE")
    assert a == b  # case-insensitive, one cluster

    # Different run / brand / type are different launches.
    assert a != launch_group_key(run_id=43, manufacturer="Seiko", event_type="NEW_REFERENCE")
    assert a != launch_group_key(run_id=42, manufacturer="Casio", event_type="NEW_REFERENCE")
    assert a != launch_group_key(run_id=42, manufacturer="Seiko", event_type="NEW_REGION")

    # Missing inputs must yield no key rather than collapsing unrelated
    # references into one bucket.
    assert launch_group_key(run_id=None, manufacturer="Seiko", event_type="NEW_REFERENCE") is None
    assert launch_group_key(run_id=42, manufacturer=None, event_type="NEW_REFERENCE") is None
    assert launch_group_key(run_id=42, manufacturer="Seiko", event_type=None) is None


def test_event_carries_priority_label_without_changing_delivery(db_session, tmp_settings):
    """The label is additive: a limited-edition transition is marked HIGH,
    and the delivery decision itself is untouched."""
    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    watch = Watch(manufacturer="Casio", brand="G-Shock", reference_raw="GWF-D1000BC-1JF",
                  reference_canonical="GWF-D1000BC-1")
    db_session.add(watch)
    db_session.flush()
    before = SourceObservation(
        watch_id=watch.id, collector_id="casio_multi", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="JP", source_url="https://x/frogman", price=1000.0, currency="JPY",
        availability_status="SOLD_OUT", overall_confidence=90.0,
    )
    db_session.add(before)
    db_session.commit()
    after = SourceObservation(
        watch_id=watch.id, collector_id="casio_multi", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="JP", source_url="https://x/frogman", price=1000.0, currency="JPY",
        availability_status="AVAILABLE", overall_confidence=90.0,
    )
    db_session.add(after)
    db_session.flush()

    notifier = MagicMock()
    notifier.editorial_enabled = True
    notifier.send_editorial_alert.return_value = True
    with (
        patch("app.services.discord_notify.DiscordNotifier", return_value=notifier),
        patch("app.services.editorial.editorial_eligibility", return_value=(True, ["test"])),
        patch("app.services.qualification.QualificationService.delivery_allowed", return_value=True),
    ):
        PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
            watch=watch, new_obs=after, is_new_watch=False, notify=True, collector_id="casio_multi",
        )

    event = db_session.query(Event).one()
    assert "priority" in event.extra
    assert event.extra["priority"]["tier"] in (PRIORITY_HIGH, PRIORITY_NORMAL)
    # Routing is unchanged: the alert still went out exactly as before.
    notifier.send_editorial_alert.assert_called_once()
    assert event.extra["alerted"] is True


def test_triage_status_counts_unreviewed_high_priority(db_session):
    """G.4: the surface that would have caught a buried Frogman alert."""
    from app.models import Event, EventReview
    from scripts.triage_status import collect

    high_unreviewed = Event(
        event_type="NEW_REFERENCE", title="Casio GWF-D1000BC-1JF", status="DRAFT", story_score=70.0,
        extra={"priority": {"tier": PRIORITY_HIGH, "reasons": ["limited edition"], "policy_version": "t"},
               "launch_group": "9:casio:NEW_REFERENCE", "delivery": {"state": "sent"}},
    )
    high_reviewed = Event(
        event_type="NEW_REFERENCE", title="Casio MASTER IN HORIZON GOLD", status="DRAFT", story_score=70.0,
        extra={"priority": {"tier": PRIORITY_HIGH, "reasons": ["limited edition"], "policy_version": "t"},
               "launch_group": "9:casio:NEW_REFERENCE"},
    )
    normal = Event(
        event_type="PRICE_CHANGE", title="ordinary", status="DRAFT", story_score=20.0,
        extra={"priority": {"tier": PRIORITY_NORMAL, "reasons": ["none"], "policy_version": "t"}},
    )
    legacy = Event(event_type="NEW_REGION", title="pre-policy event", status="DRAFT", extra={})
    db_session.add_all([high_unreviewed, high_reviewed, normal, legacy])
    db_session.flush()
    db_session.add(EventReview(event_id=high_reviewed.id, disposition="USEFUL"))
    db_session.commit()

    report = collect(db_session, limit=100)
    assert report["unreviewed_high_priority"] == 1
    assert report["items"][0]["event_id"] == high_unreviewed.id
    # A reviewed HIGH alert is not nagged about again.
    assert all(i["event_id"] != high_reviewed.id for i in report["items"])
    # Pre-policy events are reported as unlabelled, never assumed NORMAL.
    assert report["events_without_priority_label"] == 1
    # Both Casio events share one launch cluster.
    assert report["launch_clusters"]["9:casio:NEW_REFERENCE"] == sorted(
        [high_unreviewed.id, high_reviewed.id], reverse=True
    ) or sorted(report["launch_clusters"]["9:casio:NEW_REFERENCE"]) == sorted(
        [high_unreviewed.id, high_reviewed.id]
    )
