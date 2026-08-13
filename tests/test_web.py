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
