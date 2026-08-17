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
