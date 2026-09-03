"""Track F regression tests: transport acceptance is not delivery evidence.

Incident being closed (Citizen JY8144-50E, event 442, 2026-09-01): the
system recorded `alerted=true` / `delivery.state=sent` solely because an
HTTP POST returned below 300, kept no destination, no provider response and
no message identity, and therefore could not distinguish "the operator never
received it" from "Discord accepted it into a channel nobody watches".
"""
from unittest.mock import MagicMock, patch

import httpx

from app.models import DeliveryReceipt
from app.services.delivery_receipts import (
    ENTITY_EVENT,
    PURPOSE_EDITORIAL_ALERT,
    DeliveryReceiptService,
    idempotency_key,
)
from app.services.discord_notify import (
    DeliveryAttempt,
    DiscordNotifier,
    destination_alias,
)
from tests.test_core import (
    db_session,  # noqa: F401 -- pytest fixture re-export
    tmp_settings,  # noqa: F401 -- pytest fixture re-export
)


def test_destination_alias_never_leaks_the_webhook():
    url = "https://discord.com/api/webhooks/123456789/SUPER-SECRET-TOKEN-VALUE"
    alias = destination_alias(url, lane="editorial")
    assert alias is not None
    assert alias.startswith("editorial:")
    assert "SUPER-SECRET-TOKEN-VALUE" not in alias
    assert "123456789" not in alias
    # Stable for the same webhook, different for a different one.
    assert alias == destination_alias(url, lane="editorial")
    assert alias != destination_alias(url + "x", lane="editorial")
    assert destination_alias(None, lane="editorial") is None


def test_post_captures_provider_message_identity(tmp_settings):
    """The whole point of track F: a webhook post must come back with a
    durable message id, which requires Discord's ?wait=true. Before this,
    the response object was discarded entirely."""
    seen: dict = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return httpx.Response(
            200,
            json={"id": "1234567890", "channel_id": "555000111"},
            request=httpx.Request("POST", url),
        )

    notifier = DiscordNotifier(tmp_settings)
    with patch("app.services.discord_notify.httpx.post", side_effect=fake_post):
        attempt = notifier._post_detailed("https://discord.test/hook", "hello", lane="editorial")

    assert "wait=true" in seen["url"]
    assert attempt.accepted is True
    assert attempt.provider_status == 200
    assert attempt.provider_message_id == "1234567890"
    assert attempt.provider_channel_id == "555000111"
    assert attempt.lifecycle_state == "PROVIDER_IDENTIFIED"


def test_empty_204_is_accepted_but_not_identified(tmp_settings):
    """A bare 204 is genuine acceptance with weaker evidence -- it must not
    be upgraded to the same confidence as a real message id."""
    notifier = DiscordNotifier(tmp_settings)
    with patch(
        "app.services.discord_notify.httpx.post",
        return_value=httpx.Response(204, request=httpx.Request("POST", "https://discord.test/hook")),
    ):
        attempt = notifier._post_detailed("https://discord.test/hook", "hello", lane="editorial")

    assert attempt.accepted is True
    assert attempt.provider_message_id is None
    assert attempt.lifecycle_state == "PROVIDER_ACCEPTED"


def test_transient_failure_is_retried_then_reported_failed(tmp_settings):
    """F.3 bounded retries: a flapping provider must be retried a bounded
    number of times and then reported honestly, never silently as sent."""
    calls = {"n": 0}

    def flaky(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(503, text="upstream boom", request=httpx.Request("POST", url))

    notifier = DiscordNotifier(tmp_settings)
    with (
        patch("app.services.discord_notify.httpx.post", side_effect=flaky),
        patch("app.services.discord_notify.time.sleep"),  # keep the test fast
    ):
        attempt = notifier._post_detailed("https://discord.test/hook", "hello", lane="editorial")

    assert calls["n"] == 3  # MAX_DELIVERY_ATTEMPTS, bounded
    assert attempt.accepted is False
    assert attempt.provider_status == 503
    assert attempt.lifecycle_state == "FAILED"


def test_non_retryable_status_is_not_retried(tmp_settings):
    calls = {"n": 0}

    def bad_request(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(404, text="no such webhook", request=httpx.Request("POST", url))

    notifier = DiscordNotifier(tmp_settings)
    with patch("app.services.discord_notify.httpx.post", side_effect=bad_request):
        attempt = notifier._post_detailed("https://discord.test/hook", "hello", lane="editorial")

    assert calls["n"] == 1
    assert attempt.accepted is False


def test_disabled_notifier_is_not_a_transport_failure(tmp_settings):
    """An unconfigured/disabled instance is a deliberate authority boundary,
    not a provider error -- it must not fabricate a status code."""
    tmp_settings.discord_editorial_webhook_url = None
    notifier = DiscordNotifier(tmp_settings)
    attempt = notifier.send_editorial_alert_detailed("hello")
    assert attempt.accepted is False
    assert attempt.provider_status is None
    assert attempt.error_summary == "editorial_delivery_disabled"


def test_receipt_records_evidence_and_is_idempotent(db_session):
    service = DeliveryReceiptService(db_session)
    attempt = DeliveryAttempt(
        accepted=True, provider_status=200, provider_message_id="99", provider_channel_id="42",
        destination_alias="editorial:abc123", attempt_count=1,
    )
    receipt = service.record(
        entity_type=ENTITY_EVENT, entity_id=442, purpose=PURPOSE_EDITORIAL_ALERT, attempt=attempt
    )
    db_session.commit()

    assert receipt.idempotency_key == idempotency_key(ENTITY_EVENT, 442, PURPOSE_EDITORIAL_ALERT)
    assert receipt.lifecycle_state == "PROVIDER_IDENTIFIED"
    assert receipt.provider_message_id == "99"
    assert receipt.destination_alias == "editorial:abc123"
    assert service.already_delivered(ENTITY_EVENT, 442, PURPOSE_EDITORIAL_ALERT)

    # A second record for the same (entity, purpose) updates in place.
    service.record(
        entity_type=ENTITY_EVENT, entity_id=442, purpose=PURPOSE_EDITORIAL_ALERT,
        attempt=DeliveryAttempt(accepted=True, provider_status=204, attempt_count=1),
    )
    db_session.commit()
    assert db_session.query(DeliveryReceipt).count() == 1
    reloaded = service.find(ENTITY_EVENT, 442, PURPOSE_EDITORIAL_ALERT)
    assert reloaded.attempt_count == 2
    # A later weaker attempt must not erase a proven message identity.
    assert reloaded.provider_message_id == "99"


def test_failed_delivery_is_not_treated_as_delivered(db_session):
    service = DeliveryReceiptService(db_session)
    service.record(
        entity_type=ENTITY_EVENT, entity_id=777, purpose=PURPOSE_EDITORIAL_ALERT,
        attempt=DeliveryAttempt(accepted=False, provider_status=500, attempt_count=3),
    )
    db_session.commit()
    assert not service.already_delivered(ENTITY_EVENT, 777, PURPOSE_EDITORIAL_ALERT)


def test_event_alert_persists_a_receipt_and_enriched_delivery_block(db_session, tmp_settings):
    """End-to-end at the real call site: an eligible transition must leave
    behind the destination/provider evidence event 442 never had."""
    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    watch = Watch(manufacturer="Citizen", brand="Citizen", reference_raw="JY8144-50E",
                  reference_canonical="JY8144-50E")
    db_session.add(watch)
    db_session.flush()
    before = SourceObservation(
        watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="US", source_url="https://x/jy8144", price=500.0, currency="USD",
        availability_status="SOLD_OUT", overall_confidence=90.0,
    )
    db_session.add(before)
    db_session.commit()
    after = SourceObservation(
        watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="US", source_url="https://x/jy8144", price=500.0, currency="USD",
        availability_status="AVAILABLE", overall_confidence=90.0,
    )
    db_session.add(after)
    db_session.flush()

    notifier_mock = MagicMock()
    notifier_mock.editorial_enabled = True
    notifier_mock.send_editorial_alert.return_value = True
    notifier_mock.last_editorial_attempt = DeliveryAttempt(
        accepted=True, provider_status=200, provider_message_id="m-1", provider_channel_id="c-1",
        destination_alias="editorial:deadbeef", attempt_count=1,
    )
    with (
        patch("app.services.discord_notify.DiscordNotifier", return_value=notifier_mock),
        patch("app.services.editorial.editorial_eligibility", return_value=(True, ["test"])),
        patch("app.services.qualification.QualificationService.delivery_allowed", return_value=True),
    ):
        PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
            watch=watch, new_obs=after, is_new_watch=False, notify=True,
            collector_id="citizen_products",
        )

    event = db_session.query(Event).one()
    delivery = event.extra["delivery"]
    assert event.extra["alerted"] is True
    assert delivery["state"] == "sent"  # preserved for existing surfaces
    assert delivery["lifecycle_state"] == "PROVIDER_IDENTIFIED"
    assert delivery["provider_message_id"] == "m-1"
    assert delivery["provider_channel_id"] == "c-1"
    assert delivery["destination_alias"] == "editorial:deadbeef"
    assert delivery["receipt_id"] is not None

    receipt = db_session.query(DeliveryReceipt).one()
    assert receipt.entity_type == ENTITY_EVENT
    assert receipt.entity_id == str(event.id)
    assert receipt.lifecycle_state == "PROVIDER_IDENTIFIED"


def test_boolean_only_notifier_degrades_honestly(db_session, tmp_settings):
    """A notifier that only implements the old boolean contract must still
    produce a receipt -- with weaker evidence, never invented detail."""
    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    watch = Watch(manufacturer="Citizen", brand="Citizen", reference_raw="AV0104-06W",
                  reference_canonical="AV0104-06W")
    db_session.add(watch)
    db_session.flush()
    before = SourceObservation(
        watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="US", source_url="https://x/av0104", price=400.0, currency="USD",
        availability_status="SOLD_OUT", overall_confidence=90.0,
    )
    db_session.add(before)
    db_session.commit()
    after = SourceObservation(
        watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0", parser_id="t",
        parser_version="0", region="US", source_url="https://x/av0104", price=400.0, currency="USD",
        availability_status="AVAILABLE", overall_confidence=90.0,
    )
    db_session.add(after)
    db_session.flush()

    legacy_notifier = MagicMock(spec=["editorial_enabled", "send_editorial_alert"])
    legacy_notifier.editorial_enabled = True
    legacy_notifier.send_editorial_alert.return_value = True
    with (
        patch("app.services.discord_notify.DiscordNotifier", return_value=legacy_notifier),
        patch("app.services.editorial.editorial_eligibility", return_value=(True, ["test"])),
        patch("app.services.qualification.QualificationService.delivery_allowed", return_value=True),
    ):
        PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
            watch=watch, new_obs=after, is_new_watch=False, notify=True,
            collector_id="citizen_products",
        )

    event = db_session.query(Event).one()
    delivery = event.extra["delivery"]
    assert delivery["lifecycle_state"] == "PROVIDER_ACCEPTED"
    assert delivery["provider_message_id"] is None  # not invented
    receipt = db_session.query(DeliveryReceipt).one()
    assert receipt.provider_message_id is None


def test_reconciliation_flags_accepted_but_unverified(db_session):
    """F.5: the surface that would have caught event 442 on 2026-09-01."""
    from datetime import UTC, datetime, timedelta

    from scripts.delivery_reconciliation import collect

    service = DeliveryReceiptService(db_session)
    stale = service.record(
        entity_type=ENTITY_EVENT, entity_id=442, purpose=PURPOSE_EDITORIAL_ALERT,
        attempt=DeliveryAttempt(accepted=True, provider_status=204,
                                destination_alias="editorial:aaa111", attempt_count=1),
    )
    stale.last_attempt_at = datetime.now(UTC) - timedelta(hours=6)
    service.record(
        entity_type=ENTITY_EVENT, entity_id=443, purpose=PURPOSE_EDITORIAL_ALERT,
        attempt=DeliveryAttempt(accepted=True, provider_status=200, provider_message_id="m-9",
                                destination_alias="editorial:bbb222", attempt_count=1),
    )
    db_session.commit()

    report = collect(db_session, older_than_minutes=60)
    assert report["accepted_unverified_total"] == 2
    assert report["accepted_unverified_aged"] == 1
    assert report["accepted_without_message_identity"] == 1
    # Two distinct destinations in history is exactly the "the webhook
    # changed underneath you" signal.
    assert set(report["destinations_seen"]) == {"editorial:aaa111", "editorial:bbb222"}
    assert report["aged_sample"][0]["entity"] == "EVENT:442"
