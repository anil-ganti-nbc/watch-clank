"""Synthetic transport/admission controls for the native MONOCHROME lane; no publisher HTTP."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import CollectorRun, SpecialistLead
from app.models.base import Base
from app.services.specialist_leads import run_monochrome_pipeline

URL = "https://monochrome-watches.com/feed/"
FEED = (Path(__file__).parent / "fixtures" / "monochrome_hcc009j1_feed.xml").read_bytes()


@pytest.fixture
def native(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'native.db'}")
    engine = create_engine(settings.resolved_database_url)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with (
        Session(engine, expire_on_commit=False) as session,
        patch("app.services.specialist_leads.get_settings", return_value=settings),
    ):
        yield session
    engine.dispose()


def admission(monkeypatch, tmp_path, *, limit=1, expiry_hours=1):
    now = datetime.now(UTC)
    grant = {
        "grant_id": "synthetic-mono-001", "source_id": "monochrome",
        "feed_url": URL, "max_attempts": limit,
        "not_before": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(hours=expiry_hours)).isoformat(),
    }
    path = tmp_path / "synthetic-admission.json"
    path.write_text(json.dumps(grant), encoding="utf-8")
    monkeypatch.setenv("MONOCHROME_REQUEST_ADMISSION", str(path))
    monkeypatch.setenv("MONOCHROME_REQUEST_ADMISSION_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


@pytest.mark.parametrize("status", [200, 302])
def test_feed_or_redirect_charges_one_and_restart_exhausts(native, monkeypatch, tmp_path, status):
    admission(monkeypatch, tmp_path)
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(status, content=FEED, headers={"Location": "https://other.example/"})
    real_client = httpx.Client
    with patch("app.collectors.specialist_publications.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)):
        first = run_monochrome_pipeline(native, force_baseline=True)
        native.expunge_all()  # next call reloads persisted rows, not in-memory counter
        with pytest.raises(ValueError, match="budget exhausted"):
            run_monochrome_pipeline(native)
        second = native.scalars(select(CollectorRun).order_by(CollectorRun.id.desc())).first()
    assert calls == [URL]
    assert first.summary_metadata["monochrome_request"]["attempts_charged"] == 1
    assert second.status == "FAILED"
    assert second.summary_metadata.get("monochrome_request", {}).get("attempts_charged", 0) == 0
    if status == 200:
        assert first.status == "SUCCESS"
        assert native.scalars(select(SpecialistLead).where(SpecialistLead.source_id == "monochrome")).first()
    else:
        assert first.status == "FAILED"


@pytest.mark.parametrize("mode", ["missing", "expired", "zero"])
def test_no_valid_finite_admission_never_calls_transport(native, monkeypatch, tmp_path, mode):
    if mode != "missing":
        admission(monkeypatch, tmp_path, limit=0 if mode == "zero" else 1,
                  expiry_hours=-1 if mode == "expired" else 1)
    with (
        patch("app.collectors.specialist_publications.httpx.Client") as client,
        pytest.raises(ValueError, match="MONOCHROME"),
    ):
        run_monochrome_pipeline(native)
    result = native.scalars(select(CollectorRun).order_by(CollectorRun.id.desc())).first()
    client.assert_not_called()
    assert result.status == "FAILED"
    assert result.summary_metadata.get("monochrome_request", {}).get("attempts_charged", 0) == 0


def test_grant_replacement_cannot_reset_same_identity(native, monkeypatch, tmp_path):
    path = admission(monkeypatch, tmp_path)
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=FEED)
    real_client = httpx.Client
    with patch("app.collectors.specialist_publications.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)):
        run_monochrome_pipeline(native, force_baseline=True)
        grant = json.loads(path.read_text(encoding="utf-8"))
        grant["max_attempts"] = 100
        path.write_text(json.dumps(grant), encoding="utf-8")
        monkeypatch.setenv("MONOCHROME_REQUEST_ADMISSION_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
        with pytest.raises(ValueError, match="grant id reused"):
            run_monochrome_pipeline(native)
    assert calls == [URL]


def test_direct_collector_without_native_reservation_is_offline_only():
    from app.collectors.specialist_publications import SpecialistPublicationCollector

    with patch("app.collectors.specialist_publications.httpx.Client") as client:
        result = SpecialistPublicationCollector("monochrome").run()
    client.assert_not_called()
    assert result.metadata["component_status"] == "FAILED"


def test_same_genuine_run_is_single_use_and_distinct_runs_use_finite_budget(native, monkeypatch, tmp_path):
    """Synthetic transport only; all lock/DB output is under pytest basetemp."""
    from sqlalchemy import text

    from app.collectors.specialist_publications import SpecialistPublicationCollector
    from app.services.run_lock import RunLockService

    path = admission(monkeypatch, tmp_path, limit=2)
    original_pin = hashlib.sha256(path.read_bytes()).hexdigest()
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'native.db'}")
    lock_path = tmp_path / "monochrome-r3.run.lock"
    collector = SpecialistPublicationCollector("monochrome")
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=FEED)

    def new_run():
        row = CollectorRun(
            collector_id="monochrome_rss", collector_version="0.1.0",
            started_at=datetime.now(UTC), status="RUNNING",
            summary_metadata={"monochrome_request": {"attempts_charged": 0, "disposition": "NO_TRANSPORT"}},
        )
        native.add(row)
        native.commit()
        lock = RunLockService(native, settings, collector_id="monochrome_rss", lock_path=lock_path)
        assert lock.acquire().acquired
        lock.update_run_id(row.id)
        return row, lock

    def stored(row):
        native.flush()
        return native.execute(text("SELECT summary_metadata FROM collector_runs WHERE id=:id"), {"id": row.id}).scalar_one()

    real_client = httpx.Client
    with patch("app.collectors.specialist_publications.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)):
        first, lock1 = new_run()
        try:
            assert collector._fetch_monochrome(native, first, lock1).status_code == 200
            first_bytes = stored(first)
            assert json.loads(first_bytes)["monochrome_request"]["reservation_sequence"] == 1
            with pytest.raises(ValueError, match="already charged"):
                collector._fetch_monochrome(native, first, lock1)
            assert stored(first) == first_bytes
            grant = json.loads(path.read_text(encoding="utf-8"))
            grant["grant_id"] = "synthetic-other-grant"
            other = tmp_path / "synthetic-other-admission.json"
            other.write_text(json.dumps(grant), encoding="utf-8")
            monkeypatch.setenv("MONOCHROME_REQUEST_ADMISSION", str(other))
            monkeypatch.setenv("MONOCHROME_REQUEST_ADMISSION_SHA256", hashlib.sha256(other.read_bytes()).hexdigest())
            with pytest.raises(ValueError, match="already charged"):
                collector._fetch_monochrome(native, first, lock1)
            assert stored(first) == first_bytes
        finally:
            first.status = "SUCCESS"
            native.commit()
            lock1.release()
        monkeypatch.setenv("MONOCHROME_REQUEST_ADMISSION", str(path))
        monkeypatch.setenv("MONOCHROME_REQUEST_ADMISSION_SHA256", original_pin)
        second, lock2 = new_run()
        try:
            assert collector._fetch_monochrome(native, second, lock2).status_code == 200
            assert json.loads(stored(second))["monochrome_request"]["reservation_sequence"] == 2
        finally:
            second.status = "SUCCESS"
            native.commit()
            lock2.release()
        third, lock3 = new_run()
        try:
            with pytest.raises(ValueError, match="budget exhausted"):
                collector._fetch_monochrome(native, third, lock3)
            assert json.loads(stored(third))["monochrome_request"]["attempts_charged"] == 0
        finally:
            lock3.release()
    assert calls == [URL, URL]


def test_direct_transport_cannot_bypass_reservation(native, monkeypatch, tmp_path):
    from app.collectors.specialist_publications import SpecialistPublicationCollector

    admission(monkeypatch, tmp_path)
    collector = SpecialistPublicationCollector("monochrome")
    with patch("app.collectors.specialist_publications.httpx.Client") as client:
        with pytest.raises(TypeError):
            collector._fetch_monochrome()
        with pytest.raises(ValueError, match="native source lock"):
            collector._fetch_monochrome(native, CollectorRun(
                collector_id="monochrome_rss", collector_version="0.1.0",
                started_at=datetime.now(UTC), status="RUNNING",
            ), None)
    client.assert_not_called()
    assert not native.scalars(select(CollectorRun)).all()


def test_legacy_history_blocks_new_grant_without_credit_reset(native, monkeypatch, tmp_path):
    admission(monkeypatch, tmp_path)
    native.add(CollectorRun(
        collector_id="monochrome_rss", collector_version="0.1.0",
        started_at=datetime.now(UTC), status="SUCCESS", summary_metadata=None,
    ))
    native.commit()
    with (
        patch("app.collectors.specialist_publications.httpx.Client") as client,
        pytest.raises(ValueError, match="legacy request history unresolved"),
    ):
        run_monochrome_pipeline(native)
    client.assert_not_called()
    rows = native.scalars(select(CollectorRun).order_by(CollectorRun.id)).all()
    assert len(rows) == 2
    assert rows[-1].status == "FAILED"
    assert rows[-1].summary_metadata["monochrome_request"]["attempts_charged"] == 0


def test_transport_error_charged_once_and_survives_reopen(native, monkeypatch, tmp_path):
    admission(monkeypatch, tmp_path)
    calls = []
    def respond(request):
        calls.append(str(request.url))
        raise httpx.ConnectError("synthetic offline transport failure")
    real_client = httpx.Client
    with patch("app.collectors.specialist_publications.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)):
        first = run_monochrome_pipeline(native)
        native.close()
        with Session(native.get_bind(), expire_on_commit=False) as reopened:
            with pytest.raises(ValueError, match="budget exhausted"):
                run_monochrome_pipeline(reopened)
            second = reopened.scalars(select(CollectorRun).order_by(CollectorRun.id.desc())).first()
    assert first.status == "FAILED" and second.status == "FAILED"
    assert first.summary_metadata["monochrome_request"]["attempts_charged"] == 1
    assert second.summary_metadata.get("monochrome_request", {}).get("attempts_charged", 0) == 0
    assert calls == [URL]
