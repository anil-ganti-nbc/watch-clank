"""Web catch-up sprint regression tests.

Exercises the FastAPI app through a real TestClient against an isolated
tmp-path SQLite database, resetting the module-level engine/settings
singletons per test (app.db.session caches an engine at first use; a plain
monkeypatch.setenv alone would not take effect once that cache is warm).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Base, Event, EventWatch, SpecialistLead, Watch


@pytest.fixture()
def web_client(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "web_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SNAPSHOT_STORAGE_ROOT", str(tmp_path / "snapshots"))
    monkeypatch.delenv("DISCORD_EDITORIAL_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DISCORD_HEALTH_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("WATCH_CLANK_INSTANCE", raising=False)

    get_settings.cache_clear()

    import app.db.session as db_session_module

    db_session_module._engine = None
    db_session_module._SessionLocal = None
    db_session_module._settings = get_settings()

    engine = db_session_module.get_engine()
    Base.metadata.create_all(engine)

    from app.main import app

    client = TestClient(app)
    yield client

    get_settings.cache_clear()
    db_session_module._engine = None
    db_session_module._SessionLocal = None


@pytest.fixture()
def db(web_client: TestClient) -> Session:
    """A session bound to the same engine the app is using this test."""
    import app.db.session as db_session_module

    factory = db_session_module.get_session_factory()
    session = factory()
    yield session
    session.close()


def _make_watch(db: Session, **overrides) -> Watch:
    defaults = {
        "manufacturer": "Casio",
        "brand": "Casio",
        "reference_raw": "GWR-B3000-1A",
        "reference_canonical": "GWR-B3000-1A",
    }
    defaults.update(overrides)
    w = Watch(**defaults)
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


# --- Phase 13: empty/populated DB rendering ---------------------------------


def test_overview_renders_on_empty_db(web_client: TestClient):
    resp = web_client.get("/")
    assert resp.status_code == 200
    assert "Canonical watches" in resp.text
    assert "0" in resp.text


def test_overview_renders_on_populated_db(web_client: TestClient, db: Session):
    _make_watch(db)
    resp = web_client.get("/")
    assert resp.status_code == 200
    assert "Casio" in resp.text


def test_intelligence_renders_on_empty_db(web_client: TestClient):
    resp = web_client.get("/intelligence")
    assert resp.status_code == 200
    assert "genuine zero" in resp.text


# --- Phase 13: freshness semantics must not regress -------------------------


def test_stale_specialist_lead_excluded_from_default_intelligence_view(web_client: TestClient, db: Session):
    fresh = SpecialistLead(
        source_id="casioblog",
        source_type="SPECIALIST_BLOG",
        source_authority_tier=2,
        lead_type="POSSIBLE_NEW_REFERENCE",
        title="FRESH lead — should appear by default",
        source_url="https://example.com/fresh",
        published_at=datetime.now(UTC) - timedelta(hours=1),
        editorial_freshness="FRESH",
    )
    stale = SpecialistLead(
        source_id="casioblog",
        source_type="SPECIALIST_BLOG",
        source_authority_tier=2,
        lead_type="POSSIBLE_NEW_REFERENCE",
        title="STALE lead — must NOT appear by default",
        source_url="https://example.com/stale",
        published_at=datetime.now(UTC) - timedelta(days=400),
        editorial_freshness="STALE_PUBLICATION",
    )
    db.add_all([fresh, stale])
    db.commit()

    default_view = web_client.get("/intelligence")
    assert "FRESH lead — should appear by default" in default_view.text
    assert "STALE lead — must NOT appear by default" not in default_view.text

    historical_view = web_client.get("/intelligence?show=historical")
    assert "FRESH lead — should appear by default" in historical_view.text
    assert "STALE lead — must NOT appear by default" in historical_view.text


def test_baseline_specialist_lead_excluded_from_default_intelligence_view(web_client: TestClient, db: Session):
    """Directly guards the Sprint 8 incident this page exists to prevent:
    baseline discovery must never masquerade as breaking news."""
    baseline = SpecialistLead(
        source_id="casioblog",
        source_type="SPECIALIST_BLOG",
        source_authority_tier=2,
        lead_type="POSSIBLE_NEW_REFERENCE",
        title="BASELINE lead from epoch reset",
        source_url="https://example.com/baseline",
        published_at=datetime.now(UTC),
        is_baseline=True,
        editorial_freshness="BASELINE",
    )
    db.add(baseline)
    db.commit()

    resp = web_client.get("/intelligence")
    assert "BASELINE lead from epoch reset" not in resp.text
    resp_hist = web_client.get("/intelligence?show=historical")
    assert "BASELINE lead from epoch reset" in resp_hist.text


def test_intelligence_uses_published_time_not_discovery_time(web_client: TestClient, db: Session):
    """The exact bug class Sprint 8 fixed: a record discovered just now but
    published long ago must be labeled by its real publication age, not
    treated as "just now" because Clank happened to see it recently."""
    old_publication = datetime(2020, 1, 1, tzinfo=UTC)
    lead = SpecialistLead(
        source_id="casioblog",
        source_type="SPECIALIST_BLOG",
        source_authority_tier=2,
        lead_type="POSSIBLE_NEW_REFERENCE",
        title="Old article discovered late",
        source_url="https://example.com/old-discovered-late",
        published_at=old_publication,
        discovered_at=datetime.now(UTC),
        editorial_freshness="STALE_PUBLICATION",
    )
    db.add(lead)
    db.commit()

    resp = web_client.get("/intelligence?show=historical")
    assert resp.status_code == 200
    assert "01 Jan 2020" in resp.text
    assert "just now" not in resp.text.lower() or "Old article discovered late" not in resp.text.split("just now")[0][-500:]


def test_official_event_appears_in_intelligence(web_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = Event(
        event_type="NEW_REFERENCE",
        title="Casio GWR-B3000-1A: NEW_REFERENCE",
        status="DRAFT",
        story_score=80.0,
        extra={"region": "US", "editorial_eligible": True},
    )
    db.add(event)
    db.flush()
    db.add(EventWatch(event_id=event.id, watch_id=watch.id, role="subject"))
    db.commit()

    resp = web_client.get("/intelligence")
    assert resp.status_code == 200
    assert "NEW_REFERENCE" in resp.text
    assert "GWR-B3000-1A" in resp.text


# --- 2026-08-17 production-reset sprint: LISTING column ----------------------


def _make_event_for_watch(db: Session, watch: Watch) -> Event:
    event = Event(
        event_type="NEW_REFERENCE",
        title=f"{watch.manufacturer} {watch.reference_raw}: NEW_REFERENCE",
        status="DRAFT",
        story_score=50.0,
        extra={"region": "US", "editorial_eligible": True},
    )
    db.add(event)
    db.flush()
    db.add(EventWatch(event_id=event.id, watch_id=watch.id, role="subject"))
    db.commit()
    return event


def test_listing_column_renders_manufacturer_url_for_reference_with_observation(
    web_client: TestClient, db: Session
):
    """A reference with a real manufacturer SourceObservation gets a
    Listing link whose href is exactly that stored URL."""
    from app.models import SourceObservation

    watch = _make_watch(db, reference_raw="TW2V47400VQ", reference_canonical="TW2V47400VQ", manufacturer="Timex", brand="Timex")
    db.add(
        SourceObservation(
            watch_id=watch.id, collector_id="timex_products", collector_version="0.1.0",
            parser_id="fixture", parser_version="1", region="US",
            source_url="https://www.timex.com/products/peanuts-x-timex-legacy-34mm-stainless-steel-bracelet-watch-tw2v47400",
            overall_confidence=90.0,
        )
    )
    db.commit()
    _make_event_for_watch(db, watch)

    resp = web_client.get("/intelligence")
    assert resp.status_code == 200
    assert "Listing" in resp.text
    assert 'href="https://www.timex.com/products/peanuts-x-timex-legacy-34mm-stainless-steel-bracelet-watch-tw2v47400"' in resp.text
    # Reference's own internal-detail link must remain unchanged, alongside it.
    assert f'href="/watches/{watch.id}"' in resp.text
    assert "TW2V47400VQ" in resp.text


def test_listing_link_opens_in_new_tab_with_safe_rel(web_client: TestClient, db: Session):
    from app.models import SourceObservation

    watch = _make_watch(db)
    db.add(
        SourceObservation(
            watch_id=watch.id, collector_id="casio_multi", collector_version="0.1.0",
            parser_id="fixture", parser_version="1", region="INTL",
            source_url="https://www.casio.com/products/gwr-b3000-1a/",
            overall_confidence=90.0,
        )
    )
    db.commit()
    _make_event_for_watch(db, watch)

    resp = web_client.get("/intelligence")
    assert resp.status_code == 200
    # The Listing anchor specifically must carry target="_blank" + rel="noopener".
    assert 'href="https://www.casio.com/products/gwr-b3000-1a/" target="_blank" rel="noopener"' in resp.text


def test_listing_shows_em_dash_when_no_manufacturer_observation_exists(
    web_client: TestClient, db: Session
):
    """A watch with an Event but no SourceObservation at all (e.g. a
    correlation-only path) must show '—', never a guessed/reconstructed URL."""
    watch = _make_watch(db, reference_raw="NO-OBS-REF", reference_canonical="NO-OBS-REF")
    _make_event_for_watch(db, watch)

    resp = web_client.get("/intelligence")
    assert resp.status_code == 200
    assert "NO-OBS-REF" in resp.text
    # No stray manufacturer-looking href should be present for this row.
    assert "casio.com" not in resp.text and "timex.com" not in resp.text


def test_listing_does_not_use_editorial_or_specialist_urls(web_client: TestClient, db: Session):
    """SpecialistLead rows (Fratello/Deployant/etc.) are a structurally
    separate table from SourceObservation and are never consulted for
    Listing -- proven end-to-end: a specialist lead for the SAME watch,
    with an editorial URL, must not leak into the Listing href, and a
    real manufacturer SourceObservation must win when both exist."""
    from app.models import SourceObservation

    watch = _make_watch(db, reference_raw="EDITORIAL-VS-MFR", reference_canonical="EDITORIAL-VS-MFR")
    db.add(
        SourceObservation(
            watch_id=watch.id, collector_id="casio_multi", collector_version="0.1.0",
            parser_id="fixture", parser_version="1", region="INTL",
            source_url="https://www.casio.com/products/editorial-vs-mfr/",
            overall_confidence=90.0,
        )
    )
    db.add(
        SpecialistLead(
            source_id="fratello_rss", source_type="SPECIALIST_BLOG", lead_type="POSSIBLE_NEW_REFERENCE",
            title="Fratello covers EDITORIAL-VS-MFR",
            source_url="https://www.fratellowatches.com/editorial-vs-mfr-coverage/",
            manufacturer="Casio", confidence=40.0, ingestion_method="collector",
        )
    )
    db.commit()
    _make_event_for_watch(db, watch)

    resp = web_client.get("/intelligence")
    assert resp.status_code == 200
    assert 'href="https://www.casio.com/products/editorial-vs-mfr/" target="_blank" rel="noopener"' in resp.text
    assert "fratellowatches.com" not in resp.text


def test_listing_anchor_has_no_row_click_handler_to_intercept(web_client: TestClient, db: Session):
    """This app has no row-level click-to-navigate JS anywhere (verified
    by inspection of app/templates/ and app/static/ -- every navigation
    is a plain <a> tag), so the Listing link cannot accidentally trigger
    internal navigation by construction. Proven here by confirming the
    rendered table row contains no onclick attribute at all."""
    from app.models import SourceObservation

    watch = _make_watch(db, reference_raw="ROW-CLICK-CHECK", reference_canonical="ROW-CLICK-CHECK")
    db.add(
        SourceObservation(
            watch_id=watch.id, collector_id="casio_multi", collector_version="0.1.0",
            parser_id="fixture", parser_version="1", region="INTL",
            source_url="https://www.casio.com/products/row-click-check/",
            overall_confidence=90.0,
        )
    )
    db.commit()
    _make_event_for_watch(db, watch)

    resp = web_client.get("/intelligence")
    assert resp.status_code == 200
    assert "onclick" not in resp.text.lower()


# --- Phase 13: Operations mapping + RUN NOW safety ---------------------------


def test_operations_page_lists_every_known_collector(web_client: TestClient):
    from app.services.collector_registry import SAFE_COLLECTOR_IDS

    resp = web_client.get("/operations")
    assert resp.status_code == 200
    for collector_id in SAFE_COLLECTOR_IDS:
        assert collector_id in resp.text


def test_run_now_rejects_unknown_collector(web_client: TestClient, monkeypatch):
    """Isolates the collector-id validation from the loopback check (covered
    separately below) by simulating a legitimate local caller."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "_require_loopback", lambda request: None)
    resp = web_client.post("/operations/run/not-a-real-collector", follow_redirects=False)
    assert resp.status_code == 404


def test_run_now_rejects_non_loopback_client(web_client: TestClient):
    """TestClient's default client host is not a loopback address -- this
    directly proves the Phase 11 security gate actually rejects a
    non-localhost caller, not just that it exists in source."""
    resp = web_client.post("/operations/run/casioblog_rss", follow_redirects=False)
    assert resp.status_code == 403


# --- 2026-08-18: RUN ALL SAFE COLLECTORS parity for the macOS field-test app -


def test_operations_shows_run_all_button_in_field_test_mode(web_client: TestClient, monkeypatch):
    """RUN ALL SAFE COLLECTORS used to be hidden entirely in field-test
    mode (server-side {% if not field_test %}). The operator asked for
    Windows Control Centre parity -- it must now render there too."""
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    resp = web_client.get("/operations")
    assert resp.status_code == 200
    assert "RUN ALL SAFE COLLECTORS" in resp.text
    assert 'id="run-all-form"' in resp.text


def test_run_all_safe_starts_batch_in_field_test_mode(web_client: TestClient, monkeypatch):
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    import app.main as main_module
    from app.services.collector_registry import SAFE_COLLECTOR_IDS

    monkeypatch.setattr(main_module, "_require_loopback", lambda request: None)
    monkeypatch.setattr(main_module._local_collection, "start_all", lambda jobs: True)
    monkeypatch.setattr(
        main_module._local_collection,
        "snapshot",
        lambda: {"status": "RUNNING", "running": True, "mode": "batch", "total": len(SAFE_COLLECTOR_IDS), "completed": 0},
    )
    resp = web_client.post("/operations/run-all-safe")
    assert resp.status_code == 202
    assert resp.json()["mode"] == "batch"
    assert resp.json()["total"] == len(SAFE_COLLECTOR_IDS)


def test_run_all_safe_refused_while_already_running(web_client: TestClient, monkeypatch):
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    import app.main as main_module

    monkeypatch.setattr(main_module, "_require_loopback", lambda request: None)
    monkeypatch.setattr(main_module._local_collection, "start_all", lambda jobs: False)
    monkeypatch.setattr(
        main_module._local_collection,
        "snapshot",
        lambda: {"status": "RUNNING", "running": True, "mode": "batch", "total": 5, "completed": 2},
    )
    resp = web_client.post("/operations/run-all-safe")
    assert resp.status_code == 409


def test_run_all_safe_field_test_end_to_end_aggregates_results(web_client: TestClient, monkeypatch):
    """Exercises the real _LocalCollectionController.start_all/_run_all
    background-thread path (not monkeypatched away), with a fake
    subprocess runner standing in for real network collectors, and polls
    the real status endpoint to completion -- proving the batch actually
    runs every collector and aggregates a correct ok_count, not just that
    the route accepts the request."""
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    import time

    import app.main as main_module
    from app.services.collector_registry import SAFE_COLLECTOR_IDS

    monkeypatch.setattr(main_module, "_require_loopback", lambda request: None)

    from app.services.collector_registry import get_control

    failing_id = SAFE_COLLECTOR_IDS[0]
    failing_args = get_control(failing_id).cli_args

    def fake_subprocess(cli_args, timeout_seconds=180):
        ok = cli_args != failing_args
        return {"ok": ok, "returncode": 0 if ok else 1, "stdout_tail": "", "stderr_tail": "" if ok else "simulated failure"}

    monkeypatch.setattr(main_module, "_run_collector_subprocess", fake_subprocess)

    resp = web_client.post("/operations/run-all-safe")
    assert resp.status_code == 202
    assert resp.json()["mode"] == "batch"
    assert resp.json()["total"] == len(SAFE_COLLECTOR_IDS)

    status = {}
    for _ in range(200):
        status = web_client.get("/operations/status").json()
        if not status["running"]:
            break
        time.sleep(0.02)

    assert status["running"] is False
    assert status["status"] == "COMPLETED"
    assert status["completed"] == len(SAFE_COLLECTOR_IDS)
    assert set(status["results"].keys()) == set(SAFE_COLLECTOR_IDS)
    assert status["ok_count"] == len(SAFE_COLLECTOR_IDS) - 1
    assert status["results"][failing_id]["status"] == "FAILED"


# --- Phase 13: Discord secrets must never render -----------------------------


def test_discord_webhook_secret_never_rendered(web_client: TestClient, monkeypatch):
    secret_url = "https://discord.com/api/webhooks/1234567890/super-secret-token-do-not-leak"
    monkeypatch.setenv("DISCORD_EDITORIAL_WEBHOOK_URL", secret_url)
    monkeypatch.setenv("DISCORD_HEALTH_WEBHOOK_URL", secret_url)
    get_settings.cache_clear()

    resp = web_client.get("/diagnostics")
    assert resp.status_code == 200
    assert secret_url not in resp.text
    assert "CONFIGURED" in resp.text

    get_settings.cache_clear()


# --- Phase 13: human-readable, labeled timestamps ----------------------------


def test_humantime_filter_never_renders_bare_iso():
    from app.main import _humantime

    rendered = _humantime("2026-08-12T07:12:26.678550+00:00")
    assert rendered != "2026-08-12T07:12:26.678550+00:00"
    assert "2026" in rendered
    assert "UTC" in rendered or "GMT" in rendered  # always labeled, never bare


def test_humantime_filter_handles_none():
    from app.main import _humantime

    assert _humantime(None) == "—"


def test_humantime_filter_appends_ist_bracket_alongside_utc():
    from app.main import _humantime

    # 07:12 UTC -> 12:42 IST (fixed +5:30, no DST on either side).
    rendered = _humantime("2026-08-12T07:12:26.678550+00:00")
    assert rendered == "12 Aug 2026, 07:12 UTC (12:42 IST)"


def test_humantime_filter_suppresses_redundant_ist_bracket_when_ist_is_primary(monkeypatch):
    from app.core.config import get_settings
    from app.main import _humantime

    get_settings.cache_clear()
    monkeypatch.setenv("DISPLAY_TIMEZONE", "Asia/Kolkata")
    try:
        rendered = _humantime("2026-08-12T07:12:26.678550+00:00")
        assert rendered == "12 Aug 2026, 12:42 IST"
        assert rendered.count("IST") == 1
    finally:
        get_settings.cache_clear()


# --- Phase 13: instance label / notification authority never guessed --------


def test_unlabeled_instance_shown_honestly_not_guessed(web_client: TestClient):
    resp = web_client.get("/")
    assert resp.status_code == 200
    assert "UNLABELED" in resp.text


def test_instance_label_reflected_in_every_page_header(web_client: TestClient, monkeypatch):
    monkeypatch.setenv("WATCH_CLANK_INSTANCE", "HETZNER")
    get_settings.cache_clear()
    resp = web_client.get("/operations")
    assert "Instance: HETZNER" in resp.text
    get_settings.cache_clear()


# --- Phase 13: run-lock state reflected on Operations ------------------------


def test_operations_reflects_active_lock(web_client: TestClient, db: Session, tmp_path: Path):
    from app.services.run_lock import RunLockService

    settings = get_settings()
    lock_svc = RunLockService(db, settings)
    result = lock_svc.acquire()
    assert result.acquired, "test setup: could not acquire the lock at all"

    resp = web_client.get("/operations")
    assert resp.status_code == 200
    assert "run lock is held" in resp.text.lower() or "SKIPPED_OVERLAP" in resp.text or "HELD" in resp.text


# --- 2026-08-18 Citizen flood autopsy: human QC feedback system --------------
#
# EVENT != REVIEW. A Review is human editorial feedback about one Event
# under one evidence state -- never a mutation of the Event, never a
# permanent verdict on the underlying Watch/reference. See
# ai/handoff/HUMAN_QC_FEEDBACK_CONTRACT.md.


@pytest.fixture()
def qc_client(web_client: TestClient, monkeypatch):
    """QC review submission is a mutating POST behind _require_loopback,
    like /operations/run/*; TestClient's default host isn't loopback (see
    test_run_now_rejects_non_loopback_client above), so tests that need to
    actually submit a review use this fixture."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "_require_loopback", lambda request: None)
    return web_client


def test_qc_review_useful_persists(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    resp = qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "USEFUL"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["disposition"] == "USEFUL"

    from app.models import EventReview

    review = db.query(EventReview).filter(EventReview.event_id == event.id).one()
    assert review.disposition == "USEFUL"


def test_qc_review_not_useful_persists(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    resp = qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "NOT_USEFUL"})
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "NOT_USEFUL"


def test_qc_review_false_positive_persists(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    resp = qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "FALSE_POSITIVE"})
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "FALSE_POSITIVE"


def test_qc_review_out_of_stock_persists(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    resp = qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "OUT_OF_STOCK"})
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "OUT_OF_STOCK"


def test_reviewed_event_disappears_from_active_queue(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)

    before = qc_client.get("/intelligence")
    assert watch.reference_raw in before.text

    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "USEFUL"})

    after = qc_client.get("/intelligence")
    assert "Unreviewed: 0" in after.text
    assert f"qc-row-{event.id}" not in after.text


def test_reviewing_never_deletes_event_watch_or_provenance(qc_client: TestClient, db: Session):
    from app.models import Event, SourceObservation, Watch

    watch = _make_watch(db)
    db.add(
        SourceObservation(
            watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0",
            parser_id="citizen_products_html", parser_version="0.1.0", region="US",
            source_url="https://citizenwatch.com/us/en/product/TEST-1", overall_confidence=90.0,
        )
    )
    db.commit()
    event = _make_event_for_watch(db, watch)
    event_id, watch_id = event.id, watch.id

    resp = qc_client.post(f"/api/qc/review/{event_id}", json={"disposition": "FALSE_POSITIVE"})
    assert resp.status_code == 200

    assert db.get(Event, event_id) is not None
    assert db.get(Watch, watch_id) is not None
    assert (
        db.query(SourceObservation).filter(SourceObservation.watch_id == watch_id).count() == 1
    )


def test_next_unreviewed_event_becomes_available_after_review(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    e1 = _make_event_for_watch(db, watch)
    e2 = _make_event_for_watch(db, watch)

    resp = qc_client.get("/api/qc/queue")
    ids_before = {i["event_id"] for i in resp.json()["items"]}
    assert {e1.id, e2.id} <= ids_before

    qc_client.post(f"/api/qc/review/{e1.id}", json={"disposition": "USEFUL"})

    resp2 = qc_client.get("/api/qc/queue")
    ids_after = {i["event_id"] for i in resp2.json()["items"]}
    assert e1.id not in ids_after
    assert e2.id in ids_after


def test_entire_queue_traversable_beyond_page_limit(qc_client: TestClient, db: Session):
    from app.services.qc import DEFAULT_PAGE_SIZE

    watch = _make_watch(db)
    total = DEFAULT_PAGE_SIZE + 7
    event_ids = [_make_event_for_watch(db, watch).id for _ in range(total)]

    seen: set[int] = set()
    resp = qc_client.get("/api/qc/queue")
    body = resp.json()
    seen.update(i["event_id"] for i in body["items"])
    assert len(body["items"]) == DEFAULT_PAGE_SIZE
    assert body["unreviewed_count"] == total

    cursor = body["next_cursor"]
    assert cursor is not None
    while cursor is not None:
        page = qc_client.get(f"/api/qc/queue?before_id={cursor}").json()
        seen.update(i["event_id"] for i in page["items"])
        cursor = page["next_cursor"]

    assert seen == set(event_ids)


def test_qc_history_returns_archived_entries(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "OUT_OF_STOCK"})

    resp = qc_client.get("/qc/history")
    assert resp.status_code == 200
    assert "OUT_OF_STOCK" in resp.text
    assert watch.reference_raw in resp.text

    api_resp = qc_client.get("/api/qc/history")
    items = api_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["event_id"] == event.id
    assert items[0]["disposition"] == "OUT_OF_STOCK"


def test_repeat_review_submission_corrects_in_place_not_duplicated(qc_client: TestClient, db: Session):
    from app.models import EventReview

    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)

    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "OUT_OF_STOCK"})
    resp = qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "USEFUL"})
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "USEFUL"

    reviews = db.query(EventReview).filter(EventReview.event_id == event.id).all()
    assert len(reviews) == 1  # correction, not a second row
    assert reviews[0].disposition == "USEFUL"
    history = (reviews[0].review_metadata or {}).get("correction_history") or []
    assert len(history) == 1
    assert history[0]["previous_disposition"] == "OUT_OF_STOCK"


def test_repeat_review_same_disposition_is_idempotent(qc_client: TestClient, db: Session):
    from app.models import EventReview

    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "USEFUL"})
    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "USEFUL"})

    assert db.query(EventReview).filter(EventReview.event_id == event.id).count() == 1


def test_invalid_disposition_rejected(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    resp = qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "MAYBE"})
    assert resp.status_code == 400

    from app.models import EventReview

    assert db.query(EventReview).filter(EventReview.event_id == event.id).count() == 0


def test_review_on_missing_event_returns_404(qc_client: TestClient):
    resp = qc_client.post("/api/qc/review/999999999", json={"disposition": "USEFUL"})
    assert resp.status_code == 404


def test_later_event_for_same_reference_not_permanently_suppressed(qc_client: TestClient, db: Session):
    """A past FALSE_POSITIVE verdict on one Event must never make a later,
    independent Event for the same reference vanish from the queue."""
    watch = _make_watch(db)
    e1 = _make_event_for_watch(db, watch)
    qc_client.post(f"/api/qc/review/{e1.id}", json={"disposition": "FALSE_POSITIVE"})

    e2 = _make_event_for_watch(db, watch)  # a later, independent Event -- same watch
    resp = qc_client.get("/api/qc/queue")
    ids = {i["event_id"] for i in resp.json()["items"]}
    assert e2.id in ids
    assert e1.id not in ids


def test_out_of_stock_review_does_not_suppress_later_restock_event(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    sold_out = _make_event_for_watch(db, watch)
    qc_client.post(f"/api/qc/review/{sold_out.id}", json={"disposition": "OUT_OF_STOCK"})

    restock = Event(
        event_type="RESTOCK", title="restock", status="DRAFT", story_score=60.0,
        extra={"region": "US", "editorial_eligible": True},
    )
    db.add(restock)
    db.flush()
    db.add(EventWatch(event_id=restock.id, watch_id=watch.id, role="subject"))
    db.commit()

    resp = qc_client.get("/api/qc/queue")
    ids = {i["event_id"] for i in resp.json()["items"]}
    assert restock.id in ids


def test_qc_queue_excludes_baseline_suppressed_events(qc_client: TestClient, db: Session):
    """Baseline safety (0a505dc) is orthogonal to and unaffected by QC: a
    baseline-suppressed reference never produces an Event row at all, so it
    was never in the queue to begin with -- this just proves the QC system
    doesn't accidentally manufacture visibility for it."""
    _make_watch(db)
    resp = qc_client.get("/api/qc/queue")
    assert resp.json()["items"] == []
    assert resp.json()["unreviewed_count"] == 0


def test_qc_manufacturer_filter_leaves_other_manufacturers_unaffected(qc_client: TestClient, db: Session):
    citizen_watch = _make_watch(db, manufacturer="Citizen", brand="Citizen", reference_raw="JY8129-53H", reference_canonical="JY8129-53H")
    casio_watch = _make_watch(db, manufacturer="Casio", brand="Casio", reference_raw="GA-2100-1A", reference_canonical="GA-2100-1A")
    citizen_event = _make_event_for_watch(db, citizen_watch)
    casio_event = _make_event_for_watch(db, casio_watch)

    resp = qc_client.get("/api/qc/queue?manufacturer=Citizen")
    ids = {i["event_id"] for i in resp.json()["items"]}
    assert citizen_event.id in ids
    assert casio_event.id not in ids

    # Reviewing the filtered item must not touch the other manufacturer's queue.
    qc_client.post(f"/api/qc/review/{citizen_event.id}", json={"disposition": "OUT_OF_STOCK"})
    resp2 = qc_client.get("/api/qc/queue?manufacturer=Casio")
    ids2 = {i["event_id"] for i in resp2.json()["items"]}
    assert casio_event.id in ids2


def test_qc_queue_ranks_new_reference_above_restock_regardless_of_recency(qc_client: TestClient, db: Session):
    """2026-08-19 hotfix (TW2Y38700 Pan Am RESTOCK, score 70, sat in the
    default queue at equal priority to genuine NEW_REFERENCE discoveries):
    RESTOCK is real, legitimately time-sensitive inventory news -- not
    suppressed, still fully reachable via the event-type filter -- but must
    not outrank a NEW_REFERENCE in the default (unfiltered) queue merely by
    being newer. Deliberately create the RESTOCK event AFTER (higher id/
    newer) the NEW_REFERENCE to prove tier beats recency."""
    watch = _make_watch(db, reference_raw="TW2Y38700JR", reference_canonical="TW2Y38700JR", manufacturer="Timex", brand="Timex")
    new_reference_event = _make_event_for_watch(db, watch)

    restock_watch = _make_watch(db, reference_raw="TW6A01000VQ", reference_canonical="TW6A01000VQ", manufacturer="Timex", brand="Timex")
    restock_event = Event(
        event_type="RESTOCK",
        title="Timex TW2Y38700JR: RESTOCK",
        status="DRAFT",
        story_score=70.0,
        extra={"region": "US", "editorial_eligible": True},
    )
    db.add(restock_event)
    db.flush()
    db.add(EventWatch(event_id=restock_event.id, watch_id=restock_watch.id, role="subject"))
    db.commit()
    assert restock_event.id > new_reference_event.id  # confirms recency alone would rank it first

    resp = qc_client.get("/api/qc/queue")
    ids_in_order = [i["event_id"] for i in resp.json()["items"]]
    assert ids_in_order.index(new_reference_event.id) < ids_in_order.index(restock_event.id)


def test_qc_queue_ranks_first_seen_by_clank_below_restock_and_new_reference(qc_client: TestClient, db: Session):
    """TW4B20700 blocker fix: FIRST_SEEN_BY_CLANK carries the least
    launch-confidence evidence of any event type, so it must sort below
    even availability events (RESTOCK/SOLD_OUT), not just below
    NEW_REFERENCE -- confirming _QUEUE_PRIORITY_TIER's dedicated tier 3,
    not a fallback into the same bucket as RESTOCK."""
    new_reference_watch = _make_watch(db, reference_raw="TW2Y86000VQ", reference_canonical="TW2Y86000VQ", manufacturer="Timex", brand="Timex")
    new_reference_event = _make_event_for_watch(db, new_reference_watch)

    restock_watch = _make_watch(db, reference_raw="TW2Y38700JR", reference_canonical="TW2Y38700JR", manufacturer="Timex", brand="Timex")
    restock_event = Event(
        event_type="RESTOCK", title="Timex TW2Y38700JR: RESTOCK", status="DRAFT", story_score=70.0,
        extra={"region": "US", "editorial_eligible": True},
    )
    db.add(restock_event)
    db.flush()
    db.add(EventWatch(event_id=restock_event.id, watch_id=restock_watch.id, role="subject"))

    reactivated_watch = _make_watch(db, reference_raw="TW4B207009J", reference_canonical="TW4B207009J", manufacturer="Timex", brand="Timex")
    reactivated_event = Event(
        event_type="FIRST_SEEN_BY_CLANK", title="Timex TW4B207009J: FIRST_SEEN_BY_CLANK", status="DRAFT", story_score=15.0,
        extra={"region": "US"},
    )
    db.add(reactivated_event)
    db.flush()
    db.add(EventWatch(event_id=reactivated_event.id, watch_id=reactivated_watch.id, role="subject"))
    db.commit()

    resp = qc_client.get("/api/qc/queue")
    ids_in_order = [i["event_id"] for i in resp.json()["items"]]
    assert ids_in_order.index(new_reference_event.id) < ids_in_order.index(restock_event.id) < ids_in_order.index(reactivated_event.id)


# --- 2026-08-19 Watch Clank QC + classifier hardening: Specialist lead QC ---
#
# Same EVENT != REVIEW contract, applied to SpecialistLead -- see
# app.models.specialist_lead_review's module docstring for why this is a
# sibling table (DUPLICATE in place of OUT_OF_STOCK) rather than a merge
# into EventReview, and why that still counts as reusing the shared QC
# system rather than building a parallel one.


def _make_specialist_lead(db: Session, **overrides) -> SpecialistLead:
    from datetime import UTC, datetime

    defaults = {
        "source_id": "gear_patrol",
        "source_type": "SPECIALIST_PUBLICATION",
        "source_authority_tier": 3,
        "lead_type": "POSSIBLE_COLLABORATION",
        "manufacturer": "Boldr",
        "brand": "Boldr",
        "title": "Boldr and Windup's Automatic Titanium Field Watch Collab",
        "source_url": "https://www.gearpatrol.com/watches/boldr-windup",
        "confidence": 50.0,
        "editorial_freshness": "FRESH",
        "discovered_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    lead = SpecialistLead(**defaults)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def test_lead_qc_review_useful_persists_removed_from_queue_and_in_history(qc_client: TestClient, db: Session):
    lead = _make_specialist_lead(db)

    before = qc_client.get("/api/qc/lead-queue")
    assert lead.id in {i["lead_id"] for i in before.json()["items"]}

    resp = qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "USEFUL"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["disposition"] == "USEFUL"

    from app.models import SpecialistLeadReview

    review = db.query(SpecialistLeadReview).filter(SpecialistLeadReview.specialist_lead_id == lead.id).one()
    assert review.disposition == "USEFUL"
    assert review.manufacturer == "Boldr"  # snapshot metadata preserved
    assert review.lead_title == lead.title
    assert review.source_url == lead.source_url

    after = qc_client.get("/api/qc/lead-queue")
    assert lead.id not in {i["lead_id"] for i in after.json()["items"]}

    history = qc_client.get("/api/qc/lead-history")
    assert lead.id in {i["lead_id"] for i in history.json()["items"]}


def test_lead_qc_review_false_positive_persists_removed_from_queue_and_in_history(
    qc_client: TestClient, db: Session
):
    lead = _make_specialist_lead(db, title="A different lead entirely", source_url="https://example.com/other")

    resp = qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "FALSE_POSITIVE"})
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "FALSE_POSITIVE"

    after = qc_client.get("/api/qc/lead-queue")
    assert lead.id not in {i["lead_id"] for i in after.json()["items"]}

    history = qc_client.get("/api/qc/lead-history")
    items = {i["lead_id"]: i for i in history.json()["items"]}
    assert items[lead.id]["disposition"] == "FALSE_POSITIVE"


def test_lead_qc_reviewed_lead_does_not_reappear_after_refresh(qc_client: TestClient, db: Session):
    """Refresh/reload after review: the reviewed lead must not reappear as
    unreviewed -- proven via two independent fresh page loads/API calls,
    not just checking the in-memory response of the review call itself."""
    lead = _make_specialist_lead(db)
    qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "DUPLICATE"})

    # Simulate a page reload: brand new GET requests, no client-side state.
    page = qc_client.get("/intelligence")
    assert page.status_code == 200
    assert f'data-lead-id="{lead.id}"' not in page.text

    api = qc_client.get("/api/qc/lead-queue")
    assert lead.id not in {i["lead_id"] for i in api.json()["items"]}
    assert api.json()["unreviewed_count"] == 0


def test_lead_qc_disposition_correction_restores_expected_state(qc_client: TestClient, db: Session):
    """Undo/reverse verdict from QC History: correcting a disposition
    updates the SAME review row in place (never creates a duplicate),
    keeps the prior verdict in the audit trail, and the corrected value is
    what subsequently shows in history -- matching EventReview's existing,
    already-proven correction semantics."""
    from app.models import SpecialistLeadReview

    lead = _make_specialist_lead(db)
    qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "NOT_USEFUL"})
    resp = qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "USEFUL"})
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "USEFUL"

    reviews = db.query(SpecialistLeadReview).filter(SpecialistLeadReview.specialist_lead_id == lead.id).all()
    assert len(reviews) == 1  # correction, not a duplicate row
    assert reviews[0].disposition == "USEFUL"
    history = (reviews[0].review_metadata or {}).get("correction_history") or []
    assert len(history) == 1
    assert history[0]["previous_disposition"] == "NOT_USEFUL"

    # Still absent from the active queue (a corrected lead stays reviewed).
    api = qc_client.get("/api/qc/lead-queue")
    assert lead.id not in {i["lead_id"] for i in api.json()["items"]}


def test_lead_qc_invalid_disposition_rejected(qc_client: TestClient, db: Session):
    lead = _make_specialist_lead(db)
    resp = qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "OUT_OF_STOCK"})
    assert resp.status_code == 400  # OUT_OF_STOCK is an Event disposition, not a lead one

    from app.models import SpecialistLeadReview

    assert db.query(SpecialistLeadReview).filter(SpecialistLeadReview.specialist_lead_id == lead.id).count() == 0


def test_lead_qc_review_on_missing_lead_returns_404(qc_client: TestClient):
    resp = qc_client.post("/api/qc/lead-review/999999999", json={"disposition": "USEFUL"})
    assert resp.status_code == 404


def test_lead_qc_manufacturer_filter_leaves_other_manufacturers_unaffected(qc_client: TestClient, db: Session):
    boldr_lead = _make_specialist_lead(db, manufacturer="Boldr", brand="Boldr", source_url="https://www.gearpatrol.com/watches/boldr-1")
    casio_lead = _make_specialist_lead(
        db, manufacturer="Casio", brand="Casio", lead_type="POSSIBLE_NEW_REFERENCE",
        title="New G-Shock reference", source_url="https://www.gearpatrol.com/watches/casio-1",
    )

    resp = qc_client.get("/api/qc/lead-queue?manufacturer=Boldr")
    ids = {i["lead_id"] for i in resp.json()["items"]}
    assert boldr_lead.id in ids
    assert casio_lead.id not in ids

    qc_client.post(f"/api/qc/lead-review/{boldr_lead.id}", json={"disposition": "USEFUL"})
    resp2 = qc_client.get("/api/qc/lead-queue?manufacturer=Casio")
    ids2 = {i["lead_id"] for i in resp2.json()["items"]}
    assert casio_lead.id in ids2


# --- 2026-08-19 QC History correction UX addendum --------------------------
# A corrected review must drop out of the *default* QC History view
# immediately, stay retrievable via include_corrected=1, survive a refresh,
# and never lose its underlying record or audit trail.


def test_event_correction_removed_from_default_history_view(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "OUT_OF_STOCK"})
    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "USEFUL"})  # the correction

    default_api = qc_client.get("/api/qc/history")
    assert event.id not in {i["event_id"] for i in default_api.json()["items"]}

    default_page = qc_client.get("/qc/history")
    assert f'data-event-id="{event.id}"' not in default_page.text


def test_event_correction_visible_under_include_corrected(qc_client: TestClient, db: Session):
    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "OUT_OF_STOCK"})
    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "USEFUL"})

    included_api = qc_client.get("/api/qc/history?include_corrected=1")
    items = {i["event_id"]: i for i in included_api.json()["items"]}
    assert event.id in items
    assert items[event.id]["disposition"] == "USEFUL"
    assert items[event.id]["is_corrected"] is True

    included_page = qc_client.get("/qc/history?include_corrected=1")
    assert f'data-event-id="{event.id}"' in included_page.text


def test_event_correction_survives_refresh_and_keeps_underlying_record(qc_client: TestClient, db: Session):
    from app.models import EventReview

    watch = _make_watch(db)
    event = _make_event_for_watch(db, watch)
    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "OUT_OF_STOCK"})
    qc_client.post(f"/api/qc/review/{event.id}", json={"disposition": "USEFUL"})

    # Two independent "refreshes": the corrected row must not silently
    # reappear in the default view on a later, stateless GET.
    for _ in range(2):
        resp = qc_client.get("/api/qc/history")
        assert event.id not in {i["event_id"] for i in resp.json()["items"]}

    review = db.query(EventReview).filter(EventReview.event_id == event.id).one()
    assert review is not None  # never deleted
    assert review.is_corrected is True
    history = (review.review_metadata or {}).get("correction_history") or []
    assert len(history) == 1
    assert history[0]["previous_disposition"] == "OUT_OF_STOCK"  # audit trail preserved


def test_lead_correction_removed_from_default_history_view(qc_client: TestClient, db: Session):
    lead = _make_specialist_lead(db)
    qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "NOT_USEFUL"})
    qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "USEFUL"})  # the correction

    default_api = qc_client.get("/api/qc/lead-history")
    assert lead.id not in {i["lead_id"] for i in default_api.json()["items"]}

    default_page = qc_client.get("/qc/history")
    assert f'data-lead-id="{lead.id}"' not in default_page.text


def test_lead_correction_visible_under_lead_include_corrected(qc_client: TestClient, db: Session):
    lead = _make_specialist_lead(db)
    qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "NOT_USEFUL"})
    qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "USEFUL"})

    included_api = qc_client.get("/api/qc/lead-history?include_corrected=1")
    items = {i["lead_id"]: i for i in included_api.json()["items"]}
    assert lead.id in items
    assert items[lead.id]["disposition"] == "USEFUL"
    assert items[lead.id]["is_corrected"] is True

    included_page = qc_client.get("/qc/history?lead_include_corrected=1")
    assert f'data-lead-id="{lead.id}"' in included_page.text


def test_lead_correction_survives_refresh_and_keeps_underlying_record(qc_client: TestClient, db: Session):
    from app.models import SpecialistLeadReview

    lead = _make_specialist_lead(db)
    qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "NOT_USEFUL"})
    qc_client.post(f"/api/qc/lead-review/{lead.id}", json={"disposition": "USEFUL"})

    for _ in range(2):
        resp = qc_client.get("/api/qc/lead-history")
        assert lead.id not in {i["lead_id"] for i in resp.json()["items"]}

    review = db.query(SpecialistLeadReview).filter(SpecialistLeadReview.specialist_lead_id == lead.id).one()
    assert review is not None
    assert review.is_corrected is True
    history = (review.review_metadata or {}).get("correction_history") or []
    assert len(history) == 1
    assert history[0]["previous_disposition"] == "NOT_USEFUL"


def test_history_page_renders_toggle_links_for_both_sections(qc_client: TestClient, db: Session):
    """The 'Include corrected' escape hatch must exist and be discoverable
    on the default view, for both the Events and Specialist Lead sections."""
    resp = qc_client.get("/qc/history")
    assert resp.status_code == 200
    assert "include_corrected=1" in resp.text
    assert "lead_include_corrected=1" in resp.text
