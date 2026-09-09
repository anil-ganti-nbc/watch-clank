# ruff: noqa: F811
"""Specialist-lead delivery terminal-state contract (2026-09-09).

Null delivery_state + null notified_at with no receipt is illegal for a
newly ingested lead (Great G-Shock World lead 119). These tests are fully
offline: no live network, no real Discord.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.models import DeliveryReceipt, SpecialistLead
from app.models.specialist_lead import (
    LEAD_DELIVERY_REASON_ALREADY_NOTIFIED,
    LEAD_DELIVERY_REASON_BASELINE,
    LEAD_DELIVERY_REASON_DUPLICATE_REF,
    LEAD_DELIVERY_REASON_INGEST_UNFINALIZED,
    LEAD_DELIVERY_REASON_NOTIFIER_UNAVAILABLE,
    LEAD_DELIVERY_REASON_PROVIDER_ACCEPTED,
    LEAD_DELIVERY_REASON_PROVIDER_ERROR,
    LEAD_DELIVERY_REASON_PROVIDER_IDENTIFIED,
    LEAD_DELIVERY_REASON_UNRESOLVED_HISTORICAL,
)
from app.services.discord_notify import DeliveryAttempt
from app.services.health import get_health_snapshot
from app.services.specialist_leads import SpecialistLeadService, run_great_gshock_world_pipeline
from tests.test_core import db_session, tmp_settings  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
WEBHOOK = "https://discord.example/api/webhooks/UNITTEST/super-secret-token"


def _fresh_settings(**overrides):
    from app.core.config import Settings

    values = {
        "discord_editorial_webhook_url": WEBHOOK,
        "editorial_notifications_enabled": True,
        "discord_specialist_min_confidence": 40.0,
        "specialist_freshness_window_hours": 24 * 30,
    }
    values.update(overrides)
    return Settings(**values)


def _fresh_lead_kwargs(**overrides):
    now = datetime.now(UTC).isoformat()
    values = {
        "source_id": "great_gshock_world",
        "lead_type": "POSSIBLE_NEW_REFERENCE",
        "title": "G-SHOCK GWG-B1000-1A3JF / GWF-D1000BC-1JF",
        "source_url": f"https://gshockjp.blog.jp/terminal-{datetime.now(UTC).timestamp()}",
        "published_at": now,
        "reference_candidates": ["GWG-B1000-1A3JF", "GWF-D1000BC-1JF"],
        "claim_text": "grouped frogman / mudmaster mention",
        "manufacturer": "Casio",
        "brand": "Casio",
        "confidence": 55.0,
    }
    values.update(overrides)
    return values


def _no_secret_in_receipt(receipt: DeliveryReceipt) -> None:
    blob = " ".join(
        str(part)
        for part in (
            receipt.destination_alias,
            receipt.provider_message_id,
            receipt.provider_channel_id,
            receipt.error_summary,
            receipt.extra,
            receipt.reconciliation_note,
        )
        if part is not None
    )
    assert WEBHOOK not in blob
    assert "super-secret-token" not in blob
    assert "discord.com/api/webhooks" not in blob.lower()


def test_ggw_grouped_lead_policy_gate_records_reason_without_send(
    db_session, tmp_settings, monkeypatch
):
    from app.core import config as config_mod

    monkeypatch.setattr(config_mod, "get_settings", lambda: tmp_settings)
    xml = (FIXTURES / "great_gshock_world_gcwb5000_feed.xml").read_bytes()
    with (
        patch("app.services.specialist_leads.get_settings", return_value=tmp_settings),
        patch("app.services.discord_notify.DiscordNotifier.send_editorial_alert") as send_alert,
    ):
        run = run_great_gshock_world_pipeline(db_session, feed_xml=xml, force_baseline=True)

    lead = db_session.query(SpecialistLead).one()
    assert lead.reference_candidates == ["GCW-B5000", "MRG-B5000SA-2"]
    assert lead.delivery_state == "gated"
    assert lead.delivery_reason == LEAD_DELIVERY_REASON_BASELINE
    assert lead.notified_at is None
    assert lead.delivery_receipt_id is None
    send_alert.assert_not_called()
    report = run.summary_metadata["lead_delivery_outcomes"]
    assert report["lead_count"] == 1
    assert report["missing_repaired_count"] == 0
    assert report["by_state"] == {"gated": 1}


def test_successful_specialist_send_is_provider_identified_with_receipt(db_session):
    settings = _fresh_settings()
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(**_fresh_lead_kwargs())
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    assert lead.delivery_state == "unresolved"
    assert lead.delivery_reason == LEAD_DELIVERY_REASON_INGEST_UNFINALIZED

    notifier = MagicMock()
    notifier.editorial_enabled = True
    notifier.send_editorial_alert.return_value = True
    notifier.last_editorial_attempt = DeliveryAttempt(
        accepted=True,
        provider_status=200,
        provider_message_id="msg-ggw-119-test",
        provider_channel_id="chan-1",
        destination_alias="editorial:cf85cfc916be",
        attempt_count=1,
    )
    with patch("app.services.specialist_leads.get_settings", return_value=settings):
        sent = svc.notify_new_lead(lead, notifier=notifier)

    assert sent is True
    assert lead.delivery_state == "provider_identified"
    assert lead.delivery_reason == LEAD_DELIVERY_REASON_PROVIDER_IDENTIFIED
    assert lead.notified_at is not None
    assert lead.delivery_receipt_id is not None
    receipt = db_session.get(DeliveryReceipt, lead.delivery_receipt_id)
    assert receipt is not None
    assert receipt.entity_type == "SPECIALIST_LEAD"
    assert receipt.entity_id == str(lead.id)
    assert receipt.lifecycle_state == "PROVIDER_IDENTIFIED"
    assert receipt.provider_message_id == "msg-ggw-119-test"
    _no_secret_in_receipt(receipt)
    notifier.send_editorial_alert.assert_called_once()


def test_notifier_exception_persists_lead_as_failed(db_session):
    settings = _fresh_settings()
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(**_fresh_lead_kwargs())
    lead_id = outcome["lead_id"]
    lead = db_session.get(SpecialistLead, lead_id)

    notifier = MagicMock()
    notifier.editorial_enabled = True
    notifier.send_editorial_alert.side_effect = RuntimeError("provider exploded")
    with patch("app.services.specialist_leads.get_settings", return_value=settings):
        sent = svc.notify_new_lead(lead, notifier=notifier)

    assert sent is False
    persisted = db_session.get(SpecialistLead, lead_id)
    assert persisted is not None
    assert persisted.delivery_state == "failed"
    assert persisted.delivery_reason == LEAD_DELIVERY_REASON_NOTIFIER_UNAVAILABLE
    assert persisted.notified_at is None
    assert persisted.delivery_state is not None


def test_notifier_false_return_is_provider_error(db_session):
    settings = _fresh_settings()
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(**_fresh_lead_kwargs())
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    notifier = MagicMock()
    notifier.editorial_enabled = True
    notifier.send_editorial_alert.return_value = False
    notifier.last_editorial_attempt = DeliveryAttempt(
        accepted=False, provider_status=500, attempt_count=1, error_summary="500"
    )
    with patch("app.services.specialist_leads.get_settings", return_value=settings):
        assert svc.notify_new_lead(lead, notifier=notifier) is False
    assert lead.delivery_state == "failed"
    assert lead.delivery_reason == LEAD_DELIVERY_REASON_PROVIDER_ERROR


def test_duplicate_reference_preserves_second_source_and_gates_resend(db_session):
    settings = _fresh_settings()
    svc = SpecialistLeadService(db_session)
    first = svc.ingest_candidate(**_fresh_lead_kwargs(source_url="https://example.test/a"))
    second = svc.ingest_candidate(
        **_fresh_lead_kwargs(
            source_id="fratello",
            source_url="https://example.test/b",
            title="Independent Fratello mention",
        )
    )
    first_lead = db_session.get(SpecialistLead, first["lead_id"])
    second_lead = db_session.get(SpecialistLead, second["lead_id"])

    notifier = MagicMock()
    notifier.editorial_enabled = True
    notifier.send_editorial_alert.return_value = True
    notifier.last_editorial_attempt = DeliveryAttempt(
        accepted=True,
        provider_status=200,
        provider_message_id="msg-first",
        destination_alias="editorial:deadbeef12ab",
        attempt_count=1,
    )
    with patch("app.services.specialist_leads.get_settings", return_value=settings):
        assert svc.notify_new_lead(first_lead, notifier=notifier) is True
        assert svc.notify_new_lead(second_lead, notifier=notifier) is False

    assert first_lead.delivery_state == "provider_identified"
    assert second_lead.id != first_lead.id
    assert second_lead.source_url != first_lead.source_url
    assert second_lead.delivery_state == "gated"
    assert second_lead.delivery_reason == LEAD_DELIVERY_REASON_DUPLICATE_REF
    assert second_lead.notified_at is None
    assert notifier.send_editorial_alert.call_count == 1


def test_historical_null_backfill_is_unresolved_without_notification(tmp_path):
    db_path = tmp_path / "hist.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "017_sentinel_identities")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialist_leads ("
                "source_id, source_type, source_authority_tier, lead_type, title, source_url, "
                "confidence, verification_status, ingestion_method, is_baseline"
                ") VALUES ("
                "'great_gshock_world', 'SPECIALIST_BLOG', 2, 'POSSIBLE_NEW_REFERENCE', "
                "'grouped GGW', 'https://example.test/lead-119', "
                "55.0, 'UNCONFIRMED', 'collector', 0)"
            )
        )
        before = conn.execute(
            text(
                "SELECT id, delivery_state, notified_at FROM specialist_leads "
                "WHERE source_url = 'https://example.test/lead-119'"
            )
        ).one()
        assert before[1] is None
        assert before[2] is None
        receipt_count_before = conn.execute(text("SELECT COUNT(*) FROM delivery_receipts")).scalar()

    command.upgrade(cfg, "018_lead_delivery_terminal_state")

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT delivery_state, delivery_reason, delivery_receipt_id, notified_at, title "
                "FROM specialist_leads WHERE source_url = 'https://example.test/lead-119'"
            )
        ).one()
        assert row[0] == "unresolved_historical"
        assert row[1] == LEAD_DELIVERY_REASON_UNRESOLVED_HISTORICAL
        assert row[2] is None
        assert row[3] is None
        assert row[4] == "grouped GGW"
        assert (
            conn.execute(text("SELECT COUNT(*) FROM delivery_receipts")).scalar()
            == receipt_count_before
        )

    command.upgrade(cfg, "019_lead_ingest_unresolved")
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT delivery_state, delivery_reason, notified_at FROM specialist_leads "
                "WHERE source_url = 'https://example.test/lead-119'"
            )
        ).one()
        assert row[0] == "unresolved_historical"
        assert row[1] == LEAD_DELIVERY_REASON_UNRESOLVED_HISTORICAL
        assert row[2] is None


def test_ggw_runner_fleet_invariant_every_created_lead_has_outcome(
    db_session, tmp_settings, monkeypatch
):
    from app.core import config as config_mod

    monkeypatch.setattr(config_mod, "get_settings", lambda: tmp_settings)
    xml = (FIXTURES / "great_gshock_world_gcwb5000_feed.xml").read_bytes()
    with (
        patch("app.services.specialist_leads.get_settings", return_value=tmp_settings),
        patch("app.services.discord_notify.DiscordNotifier.send_editorial_alert"),
    ):
        run = run_great_gshock_world_pipeline(db_session, feed_xml=xml, force_baseline=True)

    leads = db_session.query(SpecialistLead).filter(SpecialistLead.collector_run_id == run.id).all()
    assert leads
    for lead in leads:
        assert lead.delivery_state is not None
        assert lead.delivery_reason is not None
    assert run.summary_metadata["lead_delivery_outcomes"]["missing_repaired_count"] == 0


def test_health_snapshot_surfaces_unresolved_historical(db_session, tmp_settings):
    lead = SpecialistLead(
        source_id="great_gshock_world",
        source_type="SPECIALIST_BLOG",
        lead_type="POSSIBLE_NEW_REFERENCE",
        title="historical null",
        source_url="https://example.test/unresolved-health",
        confidence=55.0,
        editorial_freshness="FRESH",
        delivery_state="unresolved_historical",
        delivery_reason=LEAD_DELIVERY_REASON_UNRESOLVED_HISTORICAL,
    )
    db_session.add(lead)
    db_session.commit()
    snap = get_health_snapshot(db_session, tmp_settings, engine=db_session.get_bind())
    assert snap.specialist_leads_unresolved_historical == 1
    assert snap.specialist_leads_missing_delivery_outcome == 0
    assert snap.specialist_leads_ingest_unfinalized == 0


def test_ensure_run_leads_have_outcomes_repairs_null_without_send(db_session):
    from app.models import CollectorRun

    run = CollectorRun(
        collector_id="great_gshock_world_atom", collector_version="t", status="SUCCESS"
    )
    db_session.add(run)
    db_session.flush()
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(**_fresh_lead_kwargs(collector_run_id=run.id))
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    assert lead.delivery_state == "unresolved"
    assert lead.delivery_reason == LEAD_DELIVERY_REASON_INGEST_UNFINALIZED
    report = svc.ensure_run_leads_have_outcomes(run.id)
    assert lead.id in report["missing_repaired"]
    assert lead.delivery_state == "failed"
    assert lead.delivery_reason == "RUN_COMPLETION_MISSING_OUTCOME"
    assert lead.notified_at is None


def test_ingest_candidate_inserts_unresolved_not_null(db_session):
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(**_fresh_lead_kwargs())
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    db_session.commit()
    persisted = db_session.get(SpecialistLead, lead.id)
    assert persisted.delivery_state == "unresolved"
    assert persisted.delivery_reason == LEAD_DELIVERY_REASON_INGEST_UNFINALIZED
    assert persisted.notified_at is None
    assert persisted.delivery_receipt_id is None


def test_already_notified_without_message_id_is_provider_accepted(db_session):
    settings = _fresh_settings()
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(**_fresh_lead_kwargs())
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    notifier = MagicMock()
    notifier.editorial_enabled = True
    notifier.send_editorial_alert.return_value = True
    notifier.last_editorial_attempt = DeliveryAttempt(
        accepted=True, provider_status=204, attempt_count=1, destination_alias="editorial:deadbeef12ab"
    )
    with patch("app.services.specialist_leads.get_settings", return_value=settings):
        assert svc.notify_new_lead(lead, notifier=notifier) is True
        assert lead.delivery_state == "provider_accepted"
        assert lead.delivery_reason == LEAD_DELIVERY_REASON_PROVIDER_ACCEPTED
        second = svc.notify_new_lead(lead, notifier=notifier)
    assert second is False
    assert lead.delivery_state == "provider_accepted"
    assert lead.delivery_reason == LEAD_DELIVERY_REASON_ALREADY_NOTIFIED
    assert notifier.send_editorial_alert.call_count == 1


def test_already_notified_with_message_id_stays_provider_identified(db_session):
    settings = _fresh_settings()
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(**_fresh_lead_kwargs())
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    notifier = MagicMock()
    notifier.editorial_enabled = True
    notifier.send_editorial_alert.return_value = True
    notifier.last_editorial_attempt = DeliveryAttempt(
        accepted=True,
        provider_status=200,
        provider_message_id="msg-keep",
        destination_alias="editorial:deadbeef12ab",
        attempt_count=1,
    )
    with patch("app.services.specialist_leads.get_settings", return_value=settings):
        assert svc.notify_new_lead(lead, notifier=notifier) is True
        assert svc.notify_new_lead(lead, notifier=notifier) is False
    assert lead.delivery_state == "provider_identified"
    assert notifier.send_editorial_alert.call_count == 1


def test_health_snapshot_counts_ingest_unfinalized(db_session, tmp_settings):
    svc = SpecialistLeadService(db_session)
    svc.ingest_candidate(**_fresh_lead_kwargs())
    db_session.commit()
    snap = get_health_snapshot(db_session, tmp_settings, engine=db_session.get_bind())
    assert snap.specialist_leads_ingest_unfinalized == 1
    assert snap.specialist_leads_missing_delivery_outcome == 0
