"""Regression coverage for the 2026-08-31 STD-UI remediation pass.

- STD-UI-COM-009: per-run pipeline-stage detail is reachable and
  discoverable from the primary /runs surface; correlation ids hyperlink
  from the per-watch ledger.
- STD-UI-COM-010: no raw timestamps on watch_detail / correlation /
  run_detail; the leads table states each row's timing semantic role under
  the neutral "Timing" heading (never "Published / observed").
- STD-UI-COM-011: delivery outcomes are recorded distinctly
  (sent / failed / gated / ineligible) in the event and lead write paths
  and surfaced per item on Recent Intelligence (template + JS dict).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from tests.test_core import db_session, tmp_settings  # noqa: F401 -- pytest fixtures
from tests.test_web import db, web_client  # noqa: F401 -- pytest fixtures

_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")


def _settings_patch(**overrides):
    """A get_settings() stand-in for the delivery paths: first_seen opt-in
    on, all score/confidence floors at 0, so the attempt path is what's
    under test."""
    settings = MagicMock()
    settings.discord_first_seen_enabled = True
    settings.discord_experimental_min_score = 0
    settings.discord_official_min_score = 0
    settings.discord_specialist_min_confidence = 0
    settings.editorial_notifications_enabled = True
    settings.specialist_freshness_window_hours = 24  # staleness gate reads this
    for k, v in overrides.items():
        setattr(settings, k, v)
    return settings


def _gate_obs(watch_id):
    from app.models import SourceObservation

    return SourceObservation(
        watch_id=watch_id,
        collector_id="timex_products",
        collector_version="test",
        parser_id="test",
        parser_version="1",
        region="US",
        source_url="https://www.timex.com/T2N001.html",
        price=100.0,
        currency="USD",
        availability_status="AVAILABLE",
        overall_confidence=90.0,
        observed_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------- COM-009


def test_runs_page_links_to_per_run_stage_detail(db, web_client):
    from app.models import CollectorRun, PipelineLedger

    run = CollectorRun(
        collector_id="casio_jp_sitemap",
        collector_version="1",
        status="FAILED",
        started_at=datetime.now(UTC),
        discovered_count=3,
        fetched_count=3,
        parsed_count=1,
    )
    db.add(run)
    db.flush()
    for stage in ("fetch", "parsing", "identity_resolution"):
        db.add(
            PipelineLedger(
                correlation_id="CORR-ABC-1",
                run_id=run.id,
                entity_type="watch",
                entity_id="7",
                stage=stage,
                action="ok",
                created_at=datetime.now(UTC),
            )
        )
    db.commit()

    listing = web_client.get("/runs")
    assert listing.status_code == 200
    assert f'href="/runs/{run.id}"' in listing.text
    assert "stages" in listing.text

    detail = web_client.get(f"/runs/{run.id}")
    assert detail.status_code == 200
    assert "/correlation/CORR-ABC-1" in detail.text  # correlation ids are links
    assert "identity_resolution" in detail.text  # ledger stages are reachable
    assert "Funnel" in detail.text  # funnel counters preserved


def test_run_detail_404s_for_unknown_run(web_client):
    assert web_client.get("/runs/999999").status_code == 404


def test_watch_detail_ledger_hyperlinks_correlation(db, web_client):
    from app.models import PipelineLedger, Watch

    w = Watch(
        manufacturer="Timex",
        brand="Timex",
        reference_raw="T2N001",
        reference_canonical="T2N001",
    )
    db.add(w)
    db.flush()
    db.add(
        PipelineLedger(
            correlation_id="CORR-XYZ-9",
            run_id=None,
            entity_type="watch",
            entity_id=str(w.id),
            stage="discovery",
            action="ok",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    page = web_client.get(f"/watches/{w.id}")
    assert page.status_code == 200
    assert 'href="/correlation/CORR-XYZ-9"' in page.text


# ---------------------------------------------------------------- COM-010


def test_no_raw_timestamps_on_drilldown_surfaces(db, web_client):
    from app.models import PipelineLedger, SourceObservation, Watch

    w = Watch(
        manufacturer="Timex",
        brand="Timex",
        reference_raw="T2N001",
        reference_canonical="T2N001",
    )
    db.add(w)
    db.flush()
    db.add(
        SourceObservation(
            watch_id=w.id,
            collector_id="timex_products",
            collector_version="test",
            parser_id="test",
            parser_version="1",
            region="US",
            source_url="https://www.timex.com/T2N001.html",
            availability_status="AVAILABLE",
            overall_confidence=90.0,
            observed_at=datetime.now(UTC),
        )
    )
    db.add(
        PipelineLedger(
            correlation_id="CORR-TS-1",
            run_id=None,
            entity_type="watch",
            entity_id=str(w.id),
            stage="fetch",
            action="ok",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    for url in (f"/watches/{w.id}", "/correlation/CORR-TS-1"):
        page = web_client.get(url)
        assert page.status_code == 200
        assert not _ISO_RE.search(page.text), f"raw timestamp rendered on {url}"


def test_intelligence_timing_column_labels_each_row_semantic(db, web_client):
    from app.models import SpecialistLead

    db.add(
        SpecialistLead(
            source_id="hodinkee",
            source_type="SPECIALIST_PUBLICATION",
            lead_type="POSSIBLE_NEW_REFERENCE",
            title="Leak: new Timex",
            source_url="https://example.com/leak-1",
            published_at=datetime.now(UTC) - timedelta(days=1),
            confidence=80.0,
            editorial_freshness="FRESH",
        )
    )
    db.add(
        SpecialistLead(
            source_id="reddit",
            source_type="COMMUNITY_SIGNAL",
            lead_type="EDITORIAL_MENTION",
            title="Undated forum mention",
            source_url="https://example.com/mention-1",
            published_at=None,
            confidence=40.0,
            editorial_freshness="MANUAL_UNDATED",
        )
    )
    db.commit()

    page = web_client.get("/intelligence?show=historical")
    assert page.status_code == 200
    assert ">Timing<" in page.text
    assert "Published / observed" not in page.text
    assert "published" in page.text and "discovered" in page.text


# ---------------------------------------------------------------- COM-011


def _make_lead(db, **overrides):
    from app.models import SpecialistLead

    defaults = dict(
        source_id="monochrome",  # registered in SOURCE_REGISTRY
        source_type="SPECIALIST_PUBLICATION",
        lead_type="POSSIBLE_NEW_REFERENCE",
        title="Leak: new Timex",
        source_url=f"https://example.com/{datetime.now(UTC).timestamp()}",
        published_at=datetime.now(UTC) - timedelta(days=1),
        confidence=80.0,
        editorial_freshness="FRESH",
    )
    defaults.update(overrides)
    lead = SpecialistLead(**defaults)
    db.add(lead)
    db.commit()
    return lead


def _notify(db, lead, *, enabled=True, sends=True):
    from app.services.specialist_leads import SpecialistLeadService

    notifier = MagicMock()
    notifier.editorial_enabled = enabled
    notifier.send_editorial_alert.return_value = sends
    service = SpecialistLeadService(db)
    with patch(
        "app.services.specialist_leads.get_settings", return_value=_settings_patch()
    ):
        outcome = service.notify_new_lead(lead, notifier=notifier)
    return notifier, outcome


def test_lead_delivery_sent_records_state_and_time(db):
    lead = _make_lead(db)
    notifier, sent = _notify(db, lead)
    assert sent is True
    assert notifier.send_editorial_alert.called
    assert lead.notified_at is not None
    assert lead.delivery_state == "sent"


def test_lead_delivery_failed_is_distinguishable_from_never_attempted(db):
    lead = _make_lead(db)
    _, sent = _notify(db, lead, sends=False)
    assert sent is False
    assert lead.notified_at is None
    assert lead.delivery_state == "failed"


def test_lead_delivery_gated_by_baseline(db):
    lead = _make_lead(db, is_baseline=True)
    notifier, sent = _notify(db, lead)
    assert sent is False
    assert notifier.send_editorial_alert.called is False
    assert lead.delivery_state == "gated"


def test_lead_sent_state_is_never_downgraded(db):
    lead = _make_lead(db, notified_at=datetime.now(UTC), delivery_state="sent")
    _, sent = _notify(db, lead)
    assert sent is False  # dedupe guard
    assert lead.delivery_state == "sent"


def test_lead_delivery_state_defaults_to_never_attempted(db):
    lead = _make_lead(db)
    assert lead.delivery_state is None
    assert lead.notified_at is None


def test_event_delivery_gated_by_maturity_records_reason(db_session, tmp_settings):
    from unittest.mock import patch

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
        result = pipeline._record_product_transition(
            watch=w,
            new_obs=_gate_obs(w.id),
            is_new_watch=True,
            notify=True,
            experimental=True,
            collector_id="tissot_sitemap",
        )
        ctor.assert_not_called()

    from app.models import Event

    event = db_session.get(Event, result["event_id"])
    assert event.extra["alerted"] is False
    assert event.extra["delivery"]["state"] == "gated"
    assert event.extra["delivery"]["reason"] == "experimental_maturity"


def test_event_delivery_records_sent_and_failed(db_session, tmp_settings):
    from unittest.mock import patch

    from app.models import CollectorRun, Event, Watch
    from app.services.pipeline import PipelineService
    from app.services.qualification import QualificationService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    w = Watch(manufacturer="Timex", brand="Timex", reference_raw="T2N001",
              reference_canonical="T2N001")
    db_session.add(w)
    db_session.flush()
    qualifying_run = CollectorRun(collector_id="timex_products", collector_version="test", status="SUCCESS")
    db_session.add(qualifying_run); db_session.flush()
    QualificationService(db_session).record_execution(qualifying_run, "SCHEDULED")
    db_session.flush()

    def run_once(sends: bool) -> Event:
        notifier_mock = MagicMock()
        notifier_mock.editorial_enabled = True
        notifier_mock.send_editorial_alert.return_value = sends
        with (
            patch("app.services.discord_notify.DiscordNotifier", return_value=notifier_mock),
            patch("app.services.editorial.editorial_eligibility", return_value=(True, ["test"])),
            patch("app.services.pipeline.get_settings", return_value=_settings_patch()),
        ):
            result = pipeline._record_product_transition(
                watch=w,
                new_obs=_gate_obs(w.id),
                is_new_watch=True,
                notify=True,
                experimental=False,
                collector_id="timex_products",
            )
        return db_session.get(Event, result["event_id"])

    sent_event = run_once(sends=True)
    assert sent_event.extra["alerted"] is True
    assert sent_event.extra["delivery"]["state"] == "sent"
    assert sent_event.extra["delivery"]["attempted_at"]

    failed_event = run_once(sends=False)
    assert failed_event.extra["alerted"] is False
    assert failed_event.extra["delivery"]["state"] == "failed"
    assert failed_event.extra["delivery"]["attempted_at"]


def test_event_delivery_records_ineligible(db_session, tmp_settings):
    from unittest.mock import patch

    from app.models import Event, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    w = Watch(manufacturer="Timex", brand="Timex", reference_raw="T2N003",
              reference_canonical="T2N003")
    db_session.add(w)
    db_session.flush()

    with (
        patch("app.services.discord_notify.DiscordNotifier") as ctor,
        patch("app.services.editorial.editorial_eligibility", return_value=(False, ["weak"])),
        patch("app.services.pipeline.get_settings", return_value=_settings_patch()),
    ):
        result = pipeline._record_product_transition(
            watch=w,
            new_obs=_gate_obs(w.id),
            is_new_watch=True,
            notify=True,
            experimental=False,
            collector_id="timex_products",
        )
        ctor.assert_not_called()

    event = db_session.get(Event, result["event_id"])
    assert event.extra["delivery"]["state"] == "ineligible"


def test_event_delivery_legacy_rows_still_read_as_sent(db, web_client):
    """Pre-remediation events carry only extra['alerted']; the UI must not
    report them as 'not delivered'."""
    from app.main import _event_delivery_view
    from app.models import Event

    legacy = Event(event_type="NEW_REFERENCE", title="t", extra={"alerted": True})
    assert _event_delivery_view(legacy)["legacy_alerted"] is True

    fresh = Event(
        event_type="NEW_REFERENCE",
        title="t",
        extra={"alerted": False, "delivery": {"state": "gated", "reason": "below_threshold"}},
    )
    view = _event_delivery_view(fresh)
    assert view["state"] == "gated" and view["reason"] == "below_threshold"


def test_qc_dict_builders_expose_delivery_and_timing_role(db):
    from app.main import _event_to_qc_dict, _lead_to_qc_dict
    from app.models import Event, SpecialistLead

    event = Event(
        event_type="NEW_REFERENCE",
        title="t",
        extra={"alerted": True, "delivery": {"state": "sent", "attempted_at": "2026-08-31T10:00:00+00:00"}},
    )
    item = _event_to_qc_dict(event)
    assert item["delivery"]["state"] == "sent"

    lead = _make_lead(db)
    row = _lead_to_qc_dict(lead)
    assert row["when_role"] == "published"
    assert row["delivery"]["state"] is None

    undated = _make_lead(db, published_at=None)
    assert _lead_to_qc_dict(undated)["when_role"] == "discovered"


# ------------------------------------------------- post-verification gaps
# (correlation-path delivery, site-2 notify=False, human attempted_at,
# run-detail stage ordering)


def _make_watch(db, **overrides):
    from app.models import Watch

    defaults = dict(
        manufacturer="Timex",
        brand="Timex",
        reference_raw="T2N900",
        reference_canonical="T2N900",
    )
    defaults.update(overrides)
    w = Watch(**defaults)
    db.add(w)
    db.commit()
    return w


def test_correlation_followup_sent_records_state_without_touching_notified_at(db):
    from app.services.specialist_leads import SpecialistLeadService
    from unittest.mock import MagicMock, patch

    w = _make_watch(db)
    lead = _make_lead(db, correlated_watch_id=w.id, correlation_type="EXACT_REFERENCE_MATCH")
    notifier = MagicMock()
    notifier.editorial_enabled = True
    notifier.send_editorial_alert.return_value = True
    service = SpecialistLeadService(db)
    with patch("app.services.specialist_leads.get_settings", return_value=_settings_patch()):
        sent = service.notify_correlation(lead, notifier=notifier)
    assert sent is True
    assert lead.delivery_state == "sent"
    assert lead.notified_at is None, "notified_at is the early-warning dedup guard; follow-up must not set it"


def test_correlation_followup_gated_and_failed_states(db):
    from app.services.specialist_leads import SpecialistLeadService
    from unittest.mock import MagicMock, patch

    # gated: notifier disabled
    w = _make_watch(db, reference_raw="T2N901", reference_canonical="T2N901")
    lead = _make_lead(db, correlated_watch_id=w.id, correlation_type="EXACT_REFERENCE_MATCH",
                      source_url="https://example.com/corr-gated")
    disabled = MagicMock()
    disabled.editorial_enabled = False
    service = SpecialistLeadService(db)
    with patch("app.services.specialist_leads.get_settings", return_value=_settings_patch()):
        assert service.notify_correlation(lead, notifier=disabled) is False
    assert lead.delivery_state == "gated"

    # failed: attempted, Discord did not accept
    w2 = _make_watch(db, reference_raw="T2N902", reference_canonical="T2N902")
    lead2 = _make_lead(db, correlated_watch_id=w2.id, correlation_type="EXACT_REFERENCE_MATCH",
                       source_url="https://example.com/corr-failed")
    failing = MagicMock()
    failing.editorial_enabled = True
    failing.send_editorial_alert.return_value = False
    with patch("app.services.specialist_leads.get_settings", return_value=_settings_patch()):
        assert service.notify_correlation(lead2, notifier=failing) is False
    assert lead2.delivery_state == "failed"
    assert lead2.notified_at is None


def test_second_event_path_notify_false_records_gated(db_session, tmp_settings):
    from unittest.mock import patch
    from datetime import UTC, datetime

    from app.models import Event, ReleaseLead, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    w = Watch(manufacturer="Timex", brand="Timex", reference_raw="T2N910",
              reference_canonical="T2N910")
    db_session.add(w)
    # This path consumes a Layer A ReleaseLead (official announcement), not
    # a SpecialistLead.
    lead = ReleaseLead(
        source_id="timex_news",
        announcement_title="Timex announces T2N910",
        announcement_url="https://example.com/announce-1",
            announcement_date=datetime.now(UTC).strftime("%B %d, %Y"),  # current: passes the staleness gate
        completeness_score=90.0,
    )
    db_session.add(lead)
    db_session.flush()

    with patch("app.services.pipeline.get_settings", return_value=_settings_patch()):
        result = pipeline._record_watch_event(
            watch=w, is_new_watch=True, lead=lead, region="US", notify=False,
        )
    assert result.get("event_id"), result
    event = db_session.get(Event, result["event_id"])
    assert event.extra["alerted"] is False
    assert event.extra["delivery"] == {"state": "gated", "reason": "notify_disabled"}


def test_attempted_at_is_rendered_as_human_time(db):
    from app.main import _event_delivery_view
    from app.models import Event

    event = Event(
        event_type="NEW_REFERENCE",
        title="t",
        extra={"delivery": {"state": "sent", "attempted_at": "2026-08-31T10:00:00+00:00"}},
    )
    human = _event_delivery_view(event)["attempted_human"]
    assert human
    assert re.search(r"\d{2} [A-Z][a-z]{2} \d{4}", human), human
    assert not _ISO_RE.search(human), human


def test_run_detail_orders_stages_by_pipeline_sequence(db, web_client):
    from app.models import CollectorRun, PipelineLedger

    run = CollectorRun(
        collector_id="casio_jp_sitemap",
        collector_version="1",
        status="SUCCESS",
        started_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    # Insert in NON-pipeline order with increasing timestamps: the page must
    # present stages in canonical pipeline sequence, not insertion order.
    for stage in ("parsing", "discovery", "normalization"):
        db.add(
            PipelineLedger(
                correlation_id="CORR-ORDER-1",
                run_id=run.id,
                entity_type="watch",
                entity_id="1",
                stage=stage,
                action="ok",
                created_at=datetime.now(UTC),
            )
        )
    db.commit()

    page = web_client.get(f"/runs/{run.id}")
    assert page.status_code == 200
    assert page.text.index("discovery") < page.text.index("parsing") < page.text.index("normalization")
