"""Track A tests: measure the handoff latency Watch Clank actually owns,
and refuse to invent the part it does not.

Seiko Rukia Liberty Fabrics trio (2026-09-03): sound discovery, sound
angle, draft ready after competing coverage appeared. A latency loss, not
a detection defect.
"""
from datetime import UTC, datetime, timedelta

from app.models import DeliveryReceipt, Event
from app.services.alert_priority import PRIORITY_HIGH, PRIORITY_NORMAL
from scripts.opportunity_latency import collect
from tests.test_core import (
    db_session,  # noqa: F401 -- pytest fixture re-export
    tmp_settings,  # noqa: F401 -- pytest fixture re-export
)


def _event(session, *, title, tier=None, created_at=None):
    extra = {}
    if tier:
        extra["priority"] = {"tier": tier, "reasons": ["t"], "policy_version": "t"}
    event = Event(event_type="NEW_REFERENCE", title=title, status="DRAFT", extra=extra)
    if created_at:
        event.created_at = created_at
    session.add(event)
    session.flush()
    return event


def test_measures_qualified_to_handoff_from_existing_facts(db_session):
    """Both owned timestamps already exist -- Event.created_at and the
    track F receipt's first_attempt_at -- so no new columns were added and
    there is exactly one source of truth for each."""
    qualified = datetime.now(UTC) - timedelta(minutes=30)
    event = _event(db_session, title="Seiko HEG004J", tier=PRIORITY_HIGH, created_at=qualified)
    db_session.add(
        DeliveryReceipt(
            entity_type="EVENT", entity_id=str(event.id), purpose="editorial_alert",
            idempotency_key=f"EVENT:{event.id}:editorial_alert", provider="discord",
            lifecycle_state="PROVIDER_IDENTIFIED",
            first_attempt_at=qualified + timedelta(minutes=5),
            last_attempt_at=qualified + timedelta(minutes=5),
        )
    )
    db_session.commit()

    report = collect(db_session, limit=50)
    assert report["measured_handoffs"] == 1
    assert report["median_handoff_latency_seconds"] == 300.0
    row = report["items"][0]
    assert row["handoff_latency_seconds"] == 300.0
    assert row["awaiting_handoff"] is False


def test_unowned_timestamps_stay_unknown(db_session):
    """Watch Clank owns detection and QC, not publication. draft_started_at
    and published_or_lost_at must be reported UNKNOWN, never inferred from
    anything Watch Clank happens to have."""
    _event(db_session, title="anything", tier=PRIORITY_NORMAL)
    db_session.commit()

    report = collect(db_session, limit=50)
    row = report["items"][0]
    assert row["draft_started_at"] == "UNKNOWN"
    assert row["published_or_lost_at"] == "UNKNOWN"
    assert report["unowned_timestamps"] == ["draft_started_at", "published_or_lost_at"]


def test_expiry_sensitive_backlog_uses_the_same_high_tier_definition(db_session):
    """The expiry-risk signal reuses the operator's HIGH tier (limited
    edition / collaboration) rather than inventing a second, divergent
    notion of urgency that could drift from it."""
    _event(db_session, title="Rukia limited trio", tier=PRIORITY_HIGH)   # no receipt
    _event(db_session, title="ordinary price move", tier=PRIORITY_NORMAL)  # no receipt
    db_session.commit()

    report = collect(db_session, limit=50)
    assert report["expiry_sensitive_awaiting_handoff"] == 1
    high = [r for r in report["items"] if r["priority_tier"] == PRIORITY_HIGH][0]
    assert high["expiry_sensitive"] is True
    assert high["awaiting_handoff"] is True
    normal = [r for r in report["items"] if r["priority_tier"] == PRIORITY_NORMAL][0]
    assert normal["expiry_sensitive"] is False


def test_no_handoff_data_reports_nothing_rather_than_zero(db_session):
    """An unmeasured latency is absent, not 0 -- reporting zero would claim
    an instant handoff that never happened."""
    _event(db_session, title="never delivered", tier=PRIORITY_NORMAL)
    db_session.commit()

    report = collect(db_session, limit=50)
    assert report["measured_handoffs"] == 0
    assert report["median_handoff_latency_seconds"] is None
    assert report["max_handoff_latency_seconds"] is None
    assert report["items"][0]["handoff_latency_seconds"] is None
