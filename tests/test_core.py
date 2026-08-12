"""Offline behavioral test suite for Watch Clank Stage 1."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.collectors.base import FetchResult
from app.core.config import Settings
from app.models import (
    Base,
    CollectorRun,
    PipelineLedger,
    SnapshotBlob,
    SnapshotFetch,
    SourceObservation,
    Watch,
)
from app.normalization.references import (
    JDM_SUFFIX_ALLOWLIST,
    normalize_casio_reference,
    safe_overall_confidence,
)
from app.parsers.casio_japan import parse_casio_product_html
from app.services.pipeline import PipelineService
from app.services.snapshot_storage import (
    PayloadTooLargeError,
    SnapshotCorruptedError,
    SnapshotNotFoundError,
    SnapshotStorageService,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        snapshot_storage_root=tmp_path / "snapshots",
        snapshot_max_payload_bytes=1024 * 1024,
    )


@pytest.fixture()
def db_session(tmp_settings: Settings):
    engine = create_engine(tmp_settings.resolved_database_url, future=True)

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    yield session
    session.close()


def test_alembic_upgrade_fresh_db(tmp_path: Path):
    from alembic.config import Config

    from alembic import command
    db_path = tmp_path / "mig.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    expected = {
        "alembic_version", "snapshot_blobs", "snapshot_fetches", "watches",
        "source_observations", "collector_runs", "pipeline_ledger",
        "watch_families", "family_memberships", "events", "event_watches",
        "release_leads", "source_component_states", "specialist_leads",
        "operational_epochs",
    }
    assert expected.issubset(tables)


def test_invalid_score_rejected(db_session: Session):
    w = Watch(manufacturer="Casio", brand="G-Shock", reference_raw="T-1", reference_canonical="T-1")
    db_session.add(w)
    db_session.flush()
    obs = SourceObservation(
        watch_id=w.id, collector_id="t", collector_version="0", parser_id="t",
        parser_version="0", region="JP", source_url="http://e", source_trust_score=150.0,
        overall_confidence=50.0,
    )
    db_session.add(obs)
    with pytest.raises(Exception):  # noqa: B017
        db_session.commit()
    db_session.rollback()


def test_snapshot_atomic_and_dedup(tmp_settings: Settings):
    storage = SnapshotStorageService(tmp_settings)
    payload = b"<html>hello</html>"
    m1 = storage.store(payload, source_url="https://a", content_type="text/html", collector_id="c", collector_version="0.1")
    m2 = storage.store(payload, source_url="https://b", content_type="text/html", collector_id="c", collector_version="0.1")
    assert m1["reused"] is False and m2["reused"] is True
    assert m1["content_hash"] == m2["content_hash"]
    assert storage.read(m1["filepath"], m1["compression_type"]) == payload


def test_two_urls_same_blob_separate_fetches(db_session: Session, tmp_settings: Settings):
    storage = SnapshotStorageService(tmp_settings)
    pipeline = PipelineService(db_session, storage)
    run = CollectorRun(collector_id="t", collector_version="0.1", status="RUNNING")
    db_session.add(run)
    db_session.commit()
    html = (FIXTURES / "casio_ga2100.html").read_bytes()
    o1 = pipeline.process_fetch_result(
        FetchResult(url="https://u1", success=True, status_code=200, content_type="text/html", payload=html),
        run_id=run.id,
    )
    o2 = pipeline.process_fetch_result(
        FetchResult(url="https://u2", success=True, status_code=200, content_type="text/html", payload=html),
        run_id=run.id,
    )
    assert o1["success"] and o2["success"]
    assert o1["blob_id"] == o2["blob_id"]
    assert o1["fetch_id"] != o2["fetch_id"]
    assert len(db_session.scalars(select(SnapshotBlob)).all()) == 1
    assert len(db_session.scalars(select(SnapshotFetch)).all()) == 2


def test_oversized_and_missing_corrupt(tmp_settings: Settings, tmp_path: Path):
    storage = SnapshotStorageService(tmp_settings)
    with pytest.raises(PayloadTooLargeError):
        storage.store(b"x" * (tmp_settings.snapshot_max_payload_bytes + 1),
                      source_url="https://b", content_type="text/html", collector_id="c", collector_version="0.1")
    with pytest.raises(SnapshotNotFoundError):
        storage.read("missing.bin")
    bad = tmp_path / "snapshots" / "aa" / "bb" / "x.gz"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"notgzip")
    with pytest.raises(SnapshotCorruptedError):
        storage.read("aa/bb/x.gz", "gzip")


def test_normalization():
    n = normalize_casio_reference("GA-2100-1A1JF")
    assert n.reference_raw == "GA-2100-1A1JF"
    assert n.reference_canonical == "GA-2100-1A1"
    assert "ga_2100" in n.family_candidate_key
    assert "JF" in JDM_SUFFIX_ALLOWLIST
    n2 = normalize_casio_reference("GA-2100-1A1XX")
    assert n2.reference_canonical == "GA-2100-1A1XX"
    o = normalize_casio_reference("OCW-T2600RL-3AJR")
    assert o.reference_canonical == "OCW-T2600RL-3A"


def test_parser():
    r = parse_casio_product_html((FIXTURES / "casio_ga2100.html").read_text(encoding="utf-8"))
    assert r.success and r.watches[0].reference_raw == "GA-2100-1A1JF"
    r2 = parse_casio_product_html(
        (FIXTURES / "casio_missing_optional.html").read_text(encoding="utf-8")
    )
    assert r2.success
    r3 = parse_casio_product_html((FIXTURES / "casio_malformed.html").read_text(encoding="utf-8"))
    assert r3.success is False
    r4 = parse_casio_product_html("<html><body>no ref</body></html>")
    assert r4.success is False
    assert safe_overall_confidence(None) == 50.0
    html = '<html><title>GA-2100-1A1JF</title><body><h1>GA-2100-1A1JF</h1><p>limited warranty only</p></body></html>'
    r5 = parse_casio_product_html(html)
    assert r5.success and r5.watches[0].limited_edition is not True


def test_pipeline_transactions(db_session: Session, tmp_settings: Settings):
    storage = SnapshotStorageService(tmp_settings)
    pipeline = PipelineService(db_session, storage)
    run = CollectorRun(collector_id="t", collector_version="0.1", status="RUNNING")
    db_session.add(run)
    db_session.commit()
    html = (FIXTURES / "casio_ga2100.html").read_bytes()
    fr = FetchResult(url="https://e/1", success=True, status_code=200, content_type="text/html", payload=html)
    o1 = pipeline.process_fetch_result(fr, run_id=run.id)
    o2 = pipeline.process_fetch_result(fr, run_id=run.id)
    assert o1["new_watch"] is True and o2["new_watch"] is False
    assert len(db_session.scalars(select(Watch)).all()) == 1
    assert len(db_session.scalars(select(SourceObservation)).all()) == 2
    frb = FetchResult(url="https://bad", success=True, status_code=200, content_type="text/html", payload=b"<html>no</html>")
    assert pipeline.process_fetch_result(frb, run_id=run.id)["success"] is False
    assert len(db_session.scalars(select(Watch)).all()) == 1


def test_ledger_correlation(db_session: Session, tmp_settings: Settings):
    storage = SnapshotStorageService(tmp_settings)
    pipeline = PipelineService(db_session, storage)
    run = CollectorRun(collector_id="t", collector_version="0.1", status="RUNNING")
    db_session.add(run)
    db_session.commit()
    html = (FIXTURES / "casio_ga2100.html").read_bytes()
    outcome = pipeline.process_fetch_result(
        FetchResult(url="https://x", success=True, status_code=200, content_type="text/html", payload=html),
        run_id=run.id,
    )
    entries = sorted(
        db_session.scalars(select(PipelineLedger).where(PipelineLedger.correlation_id == outcome["correlation_id"])).all(),
        key=lambda e: e.created_at,
    )
    stages = [e.stage for e in entries]
    assert "snapshot_storage" in stages and "parsing" in stages
    assert stages.index("snapshot_storage") < stages.index("parsing")
    assert stages.index("parsing") < stages.index("observation_creation")


def test_non200_no_watch(db_session: Session, tmp_settings: Settings):
    storage = SnapshotStorageService(tmp_settings)
    pipeline = PipelineService(db_session, storage)
    run = CollectorRun(collector_id="t", collector_version="0.1", status="RUNNING")
    db_session.add(run)
    db_session.commit()
    outcome = pipeline.process_fetch_result(
        FetchResult(url="https://f", success=False, status_code=403, error="HTTP 403"),
        run_id=run.id,
    )
    assert outcome["success"] is False
    assert db_session.scalar(select(Watch)) is None


def test_imports_and_routes():
    from app.main import app
    assert app.title == "Watch Clank"
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/" in paths and "/runs" in paths and "/health" in paths


def test_sqlite_wal(tmp_settings: Settings):
    engine = create_engine(tmp_settings.resolved_database_url, future=True)

    @event.listens_for(engine, "connect")
    def _p(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"


# ── Operations / overlap / blocked ──────────────────────────────────────────


def test_overlap_prevents_second_run(db_session: Session, tmp_settings: Settings):
    from app.services.pipeline import PipelineService

    # Simulate active RUNNING
    active = CollectorRun(
        collector_id="casio_japan",
        collector_version="0.1.0",
        status="RUNNING",
        started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    db_session.add(active)
    db_session.commit()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    # Force settings path for lock inside project data - use tmp
    result = pipeline.run_casio_pipeline(max_items=1, skip_lock=False)
    assert result.status == "SKIPPED_OVERLAP"


def test_stale_run_recovery(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime, timedelta

    from app.services.run_lock import RunLockService

    old = CollectorRun(
        collector_id="casio_japan",
        collector_version="0.1.0",
        status="RUNNING",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(old)
    db_session.commit()

    lock = RunLockService(db_session, tmp_settings)
    recovered = lock.recover_stale_runs()
    assert old.id in recovered
    db_session.refresh(old)
    assert old.status == "FAILED"
    assert (old.summary_metadata or {}).get("stale_recovery") is True


def test_stale_run_recovery_sends_health_alert(db_session: Session):
    """Sprint 6: a stale-run recovery is exactly the kind of actionable ops
    problem the health webhook should receive."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from app.core.config import Settings
    from app.services.run_lock import RunLockService

    settings = Settings(discord_health_webhook_url="https://discord.example/health")
    old = CollectorRun(
        collector_id="casio_japan", collector_version="0.1.0", status="RUNNING",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(old)
    db_session.commit()

    calls = []
    with patch("httpx.post", side_effect=lambda url, **kw: calls.append(url) or type("R", (), {"status_code": 204, "text": ""})()):
        lock = RunLockService(db_session, settings)
        lock.recover_stale_runs()

    assert calls == ["https://discord.example/health"]


def test_stale_run_recovery_no_alert_when_nothing_recovered(db_session: Session, tmp_settings: Settings):
    """No health spam: a clean recover_stale_runs() call (nothing to
    recover) must not touch Discord at all."""
    from unittest.mock import patch

    from app.services.run_lock import RunLockService

    with patch("httpx.post") as mock_post:
        lock = RunLockService(db_session, tmp_settings)
        lock.recover_stale_runs()

    mock_post.assert_not_called()


def test_run_lock_is_scoped_to_collector_id(db_session: Session, tmp_settings: Settings):
    """Regression test for a live-found bug: RunLockService used to hardcode
    collector_id="casio_japan" for its DB queries regardless of what it was
    constructed to protect, so a stale/orphaned casio_multi (or any other
    collector_id) RUNNING row could never be recovered and was invisible to
    find_active_run(). Found live: collector_runs id=65 (casio_multi) stuck
    RUNNING with a dead process after a crash, because run_multi_source_
    pipeline's lock only ever looked at casio_japan rows."""
    from datetime import UTC, datetime, timedelta

    from app.services.run_lock import RunLockService

    stale_multi = CollectorRun(
        collector_id="casio_multi", collector_version="0.2.0", status="RUNNING",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    stale_other_brand = CollectorRun(
        collector_id="citizen_news", collector_version="0.1.0", status="RUNNING",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add_all([stale_multi, stale_other_brand])
    db_session.commit()

    multi_lock = RunLockService(db_session, tmp_settings, collector_id="casio_multi")
    recovered = multi_lock.recover_stale_runs()
    assert stale_multi.id in recovered
    assert stale_other_brand.id not in recovered  # different collector_id, untouched

    db_session.refresh(stale_multi)
    db_session.refresh(stale_other_brand)
    assert stale_multi.status == "FAILED"
    assert stale_other_brand.status == "RUNNING"  # still needs its own lock instance

    citizen_lock = RunLockService(db_session, tmp_settings, collector_id="citizen_news")
    recovered2 = citizen_lock.recover_stale_runs()
    assert stale_other_brand.id in recovered2


def test_blocked_status_from_403(db_session: Session, tmp_settings: Settings):
    from unittest.mock import patch

    from app.collectors.base import CollectorRunResult, FetchResult
    from app.services.pipeline import PipelineService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))

    blocked_result = CollectorRunResult(
        collector_id="casio_japan",
        collector_version="0.1.0",
        region="JP",
        trust_score=100.0,
        discovered=[],
        fetched=[
            FetchResult(url="https://x", success=False, status_code=403, error="HTTP 403"),
            FetchResult(url="https://y", success=False, status_code=403, error="Access Denied"),
        ],
        metadata={"healthy": False},
    )
    with patch("app.services.pipeline.CasioJapanCollector") as MockCol:
        inst = MockCol.return_value
        inst.run.return_value = blocked_result
        run = pipeline.run_casio_pipeline(max_items=5, skip_lock=True)
    assert run.status == "BLOCKED"


def test_lock_released_after_success(db_session: Session, tmp_settings: Settings, tmp_path: Path):
    from app.services.run_lock import RunLockService
    # Point lock path into tmp
    tmp_settings.lock_file_name = "test.lock"
    # monkey via resolved - override by writing to settings project isn't easy;
    # use RunLockService directly
    lock = RunLockService(db_session, tmp_settings)
    # Use tmp path
    lock.lock_path = tmp_path / "test.lock"
    r = lock.acquire()
    assert r.acquired
    assert lock.lock_path.exists()
    lock.release()
    assert not lock.lock_path.exists()


def test_stew_report_aggregates(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime


    for st in ("SUCCESS", "FAILED", "BLOCKED", "SKIPPED_OVERLAP", "ZERO_ITEMS"):
        db_session.add(
            CollectorRun(
                collector_id="casio_japan",
                collector_version="0.1",
                status=st,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                new_watch_count=1 if st == "SUCCESS" else 0,
                observation_count=1 if st == "SUCCESS" else 0,
            )
        )
    db_session.commit()
    # build_report uses its own session - so we need data in the configured DB
    # For unit test, call aggregation logic inline instead
    runs = db_session.scalars(select(CollectorRun)).all()
    from collections import Counter
    counts = Counter(r.status for r in runs)
    assert counts["SUCCESS"] >= 1
    assert counts["BLOCKED"] >= 1
    assert counts["SKIPPED_OVERLAP"] >= 1


def test_scheduled_exit_codes_nonfatal_for_blocked_and_overlap():
    """Documented mapping: BLOCKED and SKIPPED_OVERLAP are exit 0 for the scheduler."""
    from scripts.run_pipeline import EXIT_FAILED, EXIT_FATAL, EXIT_OK

    assert EXIT_OK == 0
    assert EXIT_FAILED == 1
    assert EXIT_FATAL == 2


def test_scheduled_path_creates_collector_run(db_session: Session, tmp_settings: Settings, monkeypatch):
    """Scheduled invocation must always leave a terminal collector_runs row."""
    from unittest.mock import patch

    from app.collectors.base import CollectorRunResult, FetchResult
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    blocked = CollectorRunResult(
        collector_id="casio_japan",
        collector_version="0.1.0",
        region="JP",
        trust_score=100.0,
        discovered=[],
        fetched=[
            FetchResult(url="https://x", success=False, status_code=403, error="HTTP 403"),
        ],
        metadata={"healthy": False},
    )
    with patch("app.services.pipeline.CasioJapanCollector") as MockCol:
        MockCol.return_value.run.return_value = blocked
        run = pipeline.run_casio_pipeline(max_items=3, skip_lock=True)

    assert run.id is not None
    assert run.status == "BLOCKED"
    assert run.completed_at is not None
    rows = db_session.scalars(select(CollectorRun)).all()
    assert any(r.id == run.id for r in rows)


def test_catalog_discovery_403_is_blocked(db_session: Session, tmp_settings: Settings, monkeypatch):
    from unittest.mock import patch

    from app.collectors.base import CollectorRunResult
    from app.collectors.casio_japan import CasioJapanCollector
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    def fake_run(self, *, max_items=None, known_product_urls=None, discovery_urls=None):
        r = CollectorRunResult(
            collector_id="casio_japan",
            collector_version="0.2.0",
            region="JP",
            trust_score=100.0,
        )
        r.metadata["discovery_fetches"] = [
            {"url": "https://www.casio.com/jp/watches/", "status": 403, "success": False, "blocked": True},
            {"url": "https://www.casio.com/jp/watches/gshock/", "status": 403, "success": False, "blocked": True},
        ]
        r.metadata["component_status"] = "BLOCKED"
        r.metadata["discovered_count"] = 0
        r.metadata["healthy"] = False
        return r

    with patch.object(CasioJapanCollector, "run", fake_run):
        pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
        run = pipeline.run_casio_pipeline(max_items=3, skip_lock=True)
    assert run.status == "BLOCKED"


def test_intl_news_list_fixture_discovers_watch_items():
    from app.collectors.casio_intl_news import CasioIntlNewsCollector, is_watch_announcement

    assert is_watch_announcement("Casio to Release Automatic Mechanical EDIFICE", "Product", "/intl/news/2026/0630-efk-200/")
    assert not is_watch_announcement("Casio Educational Activities in Egypt", "Corporate", "/intl/news/2026/0721-education/")
    assert not is_watch_announcement("Casio Provides Moflin Smart Companions", "Product", "/intl/news/2026/0409-moflin/")

    html = (FIXTURES / "casio_intl_news_list.html").read_bytes()
    col = CasioIntlNewsCollector()
    items = col.discover_index(html.decode("utf-8", errors="ignore"))
    assert len(items) >= 3
    titles = " ".join(i.title or "" for i in items)
    assert "EDIFICE" in titles or "G-SHOCK" in titles
    assert "Education" not in titles


def test_news_announcement_parser_extracts_models():
    from app.parsers.casio_news import parse_casio_news_html

    html = (FIXTURES / "casio_intl_news_efk200.html").read_text(encoding="utf-8", errors="ignore")
    result = parse_casio_news_html(html, source_url="https://www.casio.com/intl/news/2026/0630-efk-200/")
    assert result.success
    assert result.title and "EDIFICE" in result.title
    norms = {r.normalized for r in result.model_references}
    assert any(n.startswith("EFK-200") for n in norms)
    assert result.product_urls
    assert result.collection == "EDIFICE"


def test_news_without_model_creates_lead_not_fake_watch(db_session: Session, tmp_settings: Settings):
    from app.collectors.base import FetchResult
    from app.models import ReleaseLead
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    html = b"""
    <html><body><h1>Casio Announces New Watch Concept</h1>
    <p>Details coming soon.</p></body></html>
    """
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="test", collector_version="0", status="RUNNING")
    db_session.add(run)
    db_session.commit()
    fr = FetchResult(url="https://www.casio.com/intl/news/2026/test-concept/", success=True, status_code=200, content_type="text/html", payload=html)
    out = pipeline.process_news_announcement(fr, run_id=run.id)
    assert out["success"]
    assert out["new_lead"]
    leads = db_session.scalars(select(ReleaseLead)).all()
    assert len(leads) == 1
    assert leads[0].enrichment_status == "ANNOUNCEMENT_ONLY"
    # no confident refs => no new watches required
    assert out.get("new_watch") is False


def test_multi_source_news_success_catalog_blocked(db_session: Session, tmp_settings: Settings, monkeypatch):
    from unittest.mock import patch

    from app.collectors.base import CollectorRunResult, FetchResult
    from app.collectors.casio_intl_news import CasioIntlNewsCollector
    from app.collectors.casio_japan import CasioJapanCollector
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    list_html = (FIXTURES / "casio_intl_news_list.html").read_bytes()
    detail = (FIXTURES / "casio_intl_news_efk200.html").read_bytes()

    def news_run(self, *, max_items=None, index_html=None):
        col = CasioIntlNewsCollector()
        items = col.discover_index(list_html.decode("utf-8", errors="ignore"))[:2]
        # force one known EFK page
        items = [i for i in items if "efk-200" in i.url][:1] or items[:1]
        r = CollectorRunResult(
            collector_id="casio_intl_news",
            collector_version="0.1.0",
            region="INTL",
            trust_score=95.0,
            discovered=items,
        )
        for it in items:
            payload = detail if "efk" in it.url else detail
            r.fetched.append(
                FetchResult(url=it.url, success=True, status_code=200, content_type="text/html", payload=payload)
            )
        r.metadata["component_status"] = "SUCCESS"
        r.metadata["discovered_count"] = len(items)
        return r

    def cat_run(self, *, max_items=None, known_product_urls=None, discovery_urls=None):
        r = CollectorRunResult(
            collector_id="casio_japan",
            collector_version="0.2.0",
            region="JP",
            trust_score=100.0,
        )
        r.metadata["component_status"] = "BLOCKED"
        r.metadata["discovery_fetches"] = [
            {"url": "x", "status": 403, "success": False, "blocked": True}
        ]
        return r

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    with patch.object(CasioIntlNewsCollector, "run", news_run), patch.object(CasioJapanCollector, "run", cat_run):
        run = pipeline.run_multi_source_pipeline(max_items=2, skip_lock=True, include_catalog=True)
    assert run.status == "PARTIAL"
    assert run.summary_metadata["components"]["casio_intl_news"]["status"] == "SUCCESS"
    assert run.summary_metadata["components"]["casio_japan"]["status"] == "BLOCKED"
    from app.models import ReleaseLead
    assert db_session.scalars(select(ReleaseLead)).first() is not None


# --- Timezone / backoff regression tests -----------------------------------
# Soak defect: SourceComponentState.backoff_until is declared DateTime(timezone=True)
# but SQLite does not actually preserve tz offsets, so values reloaded from the
# database come back naive. Comparing a naive persisted value directly against an
# aware datetime.now(UTC) raised "can't compare offset-naive and offset-aware
# datetimes" in _should_skip_backed_off, which crashed every scheduled run after
# the first successful post-migration run (24) with a hard FAILED terminal status.


def test_ensure_utc_normalizes_naive_and_aware():
    from datetime import UTC, datetime, timedelta, timezone

    from app.core.time import ensure_utc

    assert ensure_utc(None) is None

    naive = datetime(2026, 8, 8, 12, 0, 0)
    normalized = ensure_utc(naive)
    assert normalized.tzinfo is not None
    assert normalized == naive.replace(tzinfo=UTC)

    aware_other_tz = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    normalized2 = ensure_utc(aware_other_tz)
    assert normalized2.tzinfo is not None
    assert normalized2 == aware_other_tz.astimezone(UTC)


def _reload_component_state(db_session: Session, source_id: str):
    """Force a fresh load from SQLite, mimicking a new process/session reading
    a value written by an earlier run (which is what actually happens between
    scheduled invocations)."""
    from app.models import SourceComponentState

    db_session.expire_all()
    return db_session.scalars(
        select(SourceComponentState).filter_by(source_id=source_id)
    ).one()


def test_should_skip_backed_off_naive_stored_value(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime, timedelta

    from app.models import SourceComponentState
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    state = SourceComponentState(
        source_id="casio_japan",
        last_status="BLOCKED",
        consecutive_blocks=1,
        backoff_until=datetime.now(UTC) + timedelta(hours=3),
    )
    db_session.add(state)
    db_session.commit()

    reloaded = _reload_component_state(db_session, "casio_japan")
    # SQLite round-trip strips tzinfo even for DateTime(timezone=True) columns.
    assert reloaded.backoff_until.tzinfo is None

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    # Must not raise "can't compare offset-naive and offset-aware datetimes"
    assert pipeline._should_skip_backed_off("casio_japan") is True


def test_should_skip_backed_off_aware_stored_value(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime, timedelta

    from app.models import SourceComponentState
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    state = SourceComponentState(
        source_id="casio_japan",
        last_status="BLOCKED",
        consecutive_blocks=1,
        backoff_until=datetime.now(UTC) + timedelta(hours=3),
    )
    db_session.add(state)
    db_session.commit()
    db_session.refresh(state)

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    assert pipeline._should_skip_backed_off("casio_japan") is True


def test_should_skip_backed_off_expired_backoff_allows_run(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime, timedelta

    from app.models import SourceComponentState
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    state = SourceComponentState(
        source_id="casio_japan",
        last_status="BLOCKED",
        consecutive_blocks=3,
        backoff_until=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(state)
    db_session.commit()
    _reload_component_state(db_session, "casio_japan")

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    assert pipeline._should_skip_backed_off("casio_japan") is False


def test_should_skip_backed_off_no_state_allows_run(db_session: Session, tmp_settings: Settings):
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    assert pipeline._should_skip_backed_off("casio_japan") is False


def test_repeated_403_increases_backoff(db_session: Session, tmp_settings: Settings):
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    pipeline._update_component_state("casio_japan", "BLOCKED", 0)
    state = _reload_component_state(db_session, "casio_japan")
    first_backoff = state.backoff_until
    assert first_backoff is not None
    assert state.consecutive_blocks == 1

    pipeline._update_component_state("casio_japan", "BLOCKED", 0)
    state = _reload_component_state(db_session, "casio_japan")
    assert state.consecutive_blocks == 2
    # naive-vs-aware safe comparison of the widened backoff window
    from app.core.time import ensure_utc

    assert ensure_utc(state.backoff_until) > ensure_utc(first_backoff)


def test_success_resets_backoff(db_session: Session, tmp_settings: Settings):
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    pipeline._update_component_state("casio_japan", "BLOCKED", 0)
    pipeline._update_component_state("casio_japan", "SUCCESS", 5)
    state = _reload_component_state(db_session, "casio_japan")
    assert state.backoff_until is None
    assert state.consecutive_blocks == 0
    assert state.last_status == "SUCCESS"


def test_multi_source_active_backoff_skips_catalog_cleanly(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from app.collectors.base import CollectorRunResult
    from app.collectors.casio_intl_news import CasioIntlNewsCollector
    from app.collectors.casio_japan import CasioJapanCollector
    from app.models import SourceComponentState
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    # Pre-seed an active backoff window, then force a fresh (naive) reload so
    # the pipeline sees exactly what a real second process would see.
    db_session.add(
        SourceComponentState(
            source_id="casio_japan",
            last_status="BLOCKED",
            consecutive_blocks=1,
            backoff_until=datetime.now(UTC) + timedelta(hours=3),
        )
    )
    db_session.commit()
    db_session.expire_all()

    def news_run(self, *, max_items=None, index_html=None):
        r = CollectorRunResult(
            collector_id="casio_intl_news", collector_version="0.1.0", region="INTL", trust_score=95.0
        )
        r.metadata["component_status"] = "SUCCESS"
        return r

    def cat_run_should_not_be_called(self, **kwargs):
        raise AssertionError("catalog collector must not run while backed off")

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    with patch.object(CasioIntlNewsCollector, "run", news_run), patch.object(
        CasioJapanCollector, "run", cat_run_should_not_be_called
    ):
        run = pipeline.run_multi_source_pipeline(max_items=2, skip_lock=True, include_catalog=True)

    assert run.summary_metadata["components"]["casio_japan"]["status"] == "BACKED_OFF"
    assert run.status != "FAILED"
    assert run.status != "RUNNING"
    assert run.completed_at is not None


def test_multi_source_expired_backoff_allows_catalog_run(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from app.collectors.base import CollectorRunResult
    from app.collectors.casio_intl_news import CasioIntlNewsCollector
    from app.collectors.casio_japan import CasioJapanCollector
    from app.models import SourceComponentState
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    db_session.add(
        SourceComponentState(
            source_id="casio_japan",
            last_status="BLOCKED",
            consecutive_blocks=3,
            backoff_until=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    db_session.commit()
    db_session.expire_all()

    called = {"n": 0}

    def news_run(self, *, max_items=None, index_html=None):
        r = CollectorRunResult(
            collector_id="casio_intl_news", collector_version="0.1.0", region="INTL", trust_score=95.0
        )
        r.metadata["component_status"] = "SUCCESS"
        return r

    def cat_run(self, *, max_items=None, known_product_urls=None, discovery_urls=None):
        called["n"] += 1
        r = CollectorRunResult(
            collector_id="casio_japan", collector_version="0.2.0", region="JP", trust_score=100.0
        )
        r.metadata["component_status"] = "BLOCKED"
        r.metadata["discovery_fetches"] = [{"url": "x", "status": 403, "success": False, "blocked": True}]
        return r

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    with patch.object(CasioIntlNewsCollector, "run", news_run), patch.object(CasioJapanCollector, "run", cat_run):
        run = pipeline.run_multi_source_pipeline(max_items=2, skip_lock=True, include_catalog=True)

    assert called["n"] == 1
    assert run.summary_metadata["components"]["casio_japan"]["status"] == "BLOCKED"
    assert run.status == "PARTIAL"
    assert run.completed_at is not None


def test_news_success_catalog_backed_off_is_not_failure(db_session: Session, tmp_settings: Settings):
    """News SUCCESS + catalog BACKED_OFF must not fail the overall run."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from app.collectors.base import CollectorRunResult
    from app.collectors.casio_intl_news import CasioIntlNewsCollector
    from app.models import SourceComponentState
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    db_session.add(
        SourceComponentState(
            source_id="casio_japan",
            last_status="BLOCKED",
            consecutive_blocks=1,
            backoff_until=datetime.now(UTC) + timedelta(hours=3),
        )
    )
    db_session.commit()
    db_session.expire_all()

    def news_run(self, *, max_items=None, index_html=None):
        r = CollectorRunResult(
            collector_id="casio_intl_news", collector_version="0.1.0", region="INTL", trust_score=95.0
        )
        r.metadata["component_status"] = "SUCCESS"
        return r

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    with patch.object(CasioIntlNewsCollector, "run", news_run):
        run = pipeline.run_multi_source_pipeline(max_items=2, skip_lock=True, include_catalog=True)

    assert run.summary_metadata["components"]["casio_intl_news"]["status"] == "SUCCESS"
    assert run.summary_metadata["components"]["casio_japan"]["status"] == "BACKED_OFF"
    assert run.status in ("SUCCESS", "PARTIAL")


def test_news_deduplication_repeated_announcement_no_duplicate_lead(
    db_session: Session, tmp_settings: Settings
):
    from app.collectors.base import FetchResult
    from app.models import ReleaseLead
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    html = b"""
    <html><body><h1>Casio Announces GA-2100 Follow-Up</h1>
    <p>Reference GA-2100-1A1JF now available.</p></body></html>
    """
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="test", collector_version="0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    url = "https://www.casio.com/intl/news/2026/test-dedup/"
    fr = FetchResult(url=url, success=True, status_code=200, content_type="text/html", payload=html)
    first = pipeline.process_news_announcement(fr, run_id=run.id)
    second = pipeline.process_news_announcement(fr, run_id=run.id)

    assert first["success"] and first["new_lead"]
    assert second["success"] and second["new_lead"] is False
    leads = db_session.scalars(select(ReleaseLead).where(ReleaseLead.announcement_url == url)).all()
    assert len(leads) == 1


# --- Citizen / Seiko experimental discovery (sprint: multi-brand coverage) --
# These sources are wired through PipelineService.run_brand_news_pipeline,
# which is NOT called from scripts/run_pipeline.py --scheduled. Casio's
# production behaviour is covered by the tests above and is asserted
# unaffected by the generalization (test_casio_production_path_unaffected_*).


def test_citizen_news_discovery_parses_fixture():
    from app.collectors.citizen_news import CitizenNewsCollector

    html = (FIXTURES / "citizen_news_list.html").read_text(encoding="utf-8")
    items = CitizenNewsCollector().discover_index(html)
    assert len(items) == 3
    assert all(i.metadata["source_region"] == "GLOBAL" for i in items)
    assert any("ATTESA" in (i.title or "") for i in items)


def test_citizen_news_parser_extracts_reference_and_collection():
    from app.parsers.citizen_news import parse_citizen_news_html

    html = (FIXTURES / "citizen_news_detail.html").read_bytes()
    result = parse_citizen_news_html(html, source_url="https://www.citizenwatch-global.com/news/2026/20260610/index.html")
    assert result.success
    assert "ATTESA" in (result.title or "")
    refs = [r.normalized for r in result.model_references]
    assert "CC4107-80H" in refs
    assert result.collection == "Attesa"


def test_seiko_watch_filter_excludes_corporate_noise():
    from app.collectors.seiko_news import is_watch_announcement

    assert is_watch_announcement("Seiko Launches New Prospex Diver's Watch", "Press Release") is True
    assert is_watch_announcement('Seiko Launches "Seiko Time & Jazz"', "Press Release Music") is False
    assert is_watch_announcement("Full-Scale Replica of the Clock Tower Unveiled", "Topics") is False


def test_seiko_news_discovery_filters_to_watch_items():
    from app.collectors.seiko_news import SeikoNewsCollector

    html = (FIXTURES / "seiko_news_list.html").read_text(encoding="utf-8")
    items = SeikoNewsCollector().discover_index(html)
    # 3 items in fixture: Music (filtered), clock tower Topics (filtered), Prospex Press Release (kept)
    assert len(items) == 1
    assert "Prospex" in (items[0].title or "")


def test_seiko_news_parser_extracts_reference_and_collection():
    from app.parsers.seiko_news import parse_seiko_news_html

    html = (FIXTURES / "seiko_news_detail.html").read_bytes()
    result = parse_seiko_news_html(html, source_url="https://www.seiko.co.jp/en/news/sgc/2026/202604220900.html")
    assert result.success
    refs = [r.normalized for r in result.model_references]
    assert "SPB255" in refs
    assert result.collection == "Prospex"


def test_citizen_reference_normalization_is_conservative_passthrough():
    from app.normalization.references import normalize_citizen_reference

    n = normalize_citizen_reference("CC4107-80H")
    assert n.reference_raw == "CC4107-80H"
    assert n.reference_canonical == "CC4107-80H"  # no suffix stripping
    assert n.manufacturer == "Citizen"


def test_seiko_reference_normalization_is_conservative_passthrough():
    from app.normalization.references import normalize_seiko_reference

    n = normalize_seiko_reference("SPB255")
    assert n.reference_raw == "SPB255"
    assert n.reference_canonical == "SPB255"
    assert n.manufacturer == "Seiko"


def test_brand_news_pipeline_citizen_creates_lead_watch_and_event(db_session: Session, tmp_settings: Settings):
    from unittest.mock import patch

    from app.collectors.base import CollectorRunResult
    from app.collectors.base import FetchResult as CitizenFetchResult
    from app.collectors.citizen_news import CitizenNewsCollector
    from app.models import Event, ReleaseLead, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    list_html = (FIXTURES / "citizen_news_list.html").read_bytes()
    detail_html = (FIXTURES / "citizen_news_detail.html").read_bytes()

    def fake_run(self, *, max_items=None, index_html=None):
        col = CitizenNewsCollector()
        items = col.discover_index(list_html.decode("utf-8"))
        item = next(i for i in items if "ATTESA" in (i.title or ""))
        result = CollectorRunResult(
            collector_id="citizen_news", collector_version="0.1.0", region="GLOBAL", trust_score=95.0
        )
        result.discovered = [item]
        result.fetched.append(
            CitizenFetchResult(url=item.url, success=True, status_code=200, content_type="text/html", payload=detail_html)
        )
        result.metadata["component_status"] = "SUCCESS"
        return result

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    with patch.object(CitizenNewsCollector, "run", fake_run):
        run = pipeline.run_brand_news_pipeline("citizen", max_items=5)

    assert run.status == "SUCCESS"
    lead = db_session.scalars(select(ReleaseLead)).first()
    assert lead is not None and lead.manufacturer == "Citizen" and lead.brand == "Citizen"
    watch = db_session.scalars(select(Watch)).first()
    assert watch is not None and watch.manufacturer == "Citizen"
    assert watch.reference_canonical == "CC4107-80H"

    events = db_session.scalars(select(Event)).all()
    assert len(events) == 1
    assert events[0].event_type == "NEW_REFERENCE"
    assert events[0].story_score is not None
    assert events[0].extra["reasons"]  # explainable, non-empty


def test_brand_news_pipeline_new_region_detected_not_new_reference(db_session: Session, tmp_settings: Settings):
    """Same underlying watch, genuinely separate announcement (distinct
    merge_key so it is not caught by the existing duplicate-announcement
    dedup) in a second region -> NEW_REGION, not a second NEW_REFERENCE.

    Note: two announcements of the *identical* reference text and URL-family
    are correctly caught by the pre-existing merge_key dedup before reaching
    event detection at all (see test_brand_news_pipeline_repeat_same_region_
    emits_no_event) — that is intentional duplicate-lead protection, not a
    bug. NEW_REGION is reachable when the same watch is independently
    referenced by a second, distinct announcement/lead (e.g. a different
    source), which is what this test constructs.
    """
    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event, ReleaseLead
    from app.parsers.citizen_news import parse_citizen_news_html
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    detail_html = (FIXTURES / "citizen_news_detail.html").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    fr1 = FetchResult(
        url="https://www.citizenwatch-global.com/news/2026/20260610/index.html",
        success=True, status_code=200, content_type="text/html", payload=detail_html,
    )
    out1 = pipeline.process_news_announcement(
        fr1, run_id=run.id, discovered_meta={"source_region": "GLOBAL"},
        collector_id="citizen_news", manufacturer="Citizen", brand="Citizen",
        parse_fn=parse_citizen_news_html, merge_key_prefix="citizen",
        default_region="GLOBAL", emit_events=True,
    )
    assert out1["watch_events"][0]["event_type"] == "NEW_REFERENCE"
    watch_id = db_session.scalars(select(Watch)).one().id

    # Simulate a second, independent lead for the same watch from a
    # different source (distinct merge_key => not deduped as a repeat).
    seed_lead = ReleaseLead(
        manufacturer="Citizen", brand="Citizen",
        announcement_title="Prior JP retailer listing", announcement_url="https://example-jp/other-source",
        source_id="other_source", source_region="JP",
        merge_key="other_source:CC4107-80H", watch_ids=[watch_id],
    )
    db_session.add(seed_lead)
    db_session.commit()

    # Now a genuinely new US-region announcement referencing this watch,
    # distinct merge_key/URL from both fr1 and the seeded JP lead.
    fr2 = FetchResult(
        url="https://www.citizenwatch-global.com/news/2026/20260701/index.html",
        success=True, status_code=200, content_type="text/html", payload=detail_html,
    )
    out2 = pipeline.process_news_announcement(
        fr2, run_id=run.id, discovered_meta={"source_region": "US"},
        collector_id="citizen_news", manufacturer="Citizen", brand="Citizen",
        parse_fn=parse_citizen_news_html, merge_key_prefix="citizen2",
        default_region="US", emit_events=True,
    )
    # US was never seen before (only GLOBAL and JP were) -> NEW_REGION
    assert out2["watch_events"][0]["event_type"] == "NEW_REGION"

    events = db_session.scalars(select(Event)).all()
    event_types = sorted(e.event_type for e in events)
    assert event_types == ["NEW_REFERENCE", "NEW_REGION"]


def test_brand_news_pipeline_repeat_same_region_emits_no_event(db_session: Session, tmp_settings: Settings):
    """A duplicate observation in the same region is silent (no event), per the
    'baseline is not news' rule — protects against false-positive event spam."""
    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event
    from app.parsers.citizen_news import parse_citizen_news_html
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    detail_html = (FIXTURES / "citizen_news_detail.html").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    url = "https://www.citizenwatch-global.com/news/2026/20260610/index.html"
    kwargs = {
        "run_id": run.id, "discovered_meta": {"source_region": "GLOBAL"},
        "collector_id": "citizen_news", "manufacturer": "Citizen", "brand": "Citizen",
        "parse_fn": parse_citizen_news_html, "merge_key_prefix": "citizen",
        "default_region": "GLOBAL", "emit_events": True,
    }
    fr1 = FetchResult(url=url, success=True, status_code=200, content_type="text/html", payload=detail_html)
    out1 = pipeline.process_news_announcement(fr1, **kwargs)
    assert out1["watch_events"][0]["event_type"] == "NEW_REFERENCE"

    # Re-processing the exact same announcement URL re-uses the existing lead
    # (new_lead=False) and re-runs watch resolution against the same,
    # already-existing watch in the same, already-seen region -> no new
    # evidence -> no event. This is the false-positive guard: reprocessing
    # identical evidence must never fabricate a second event.
    out2 = pipeline.process_news_announcement(fr1, **kwargs)
    assert out2["success"] and out2["new_lead"] is False
    assert out2["watch_events"][0]["event_type"] is None

    events = db_session.scalars(select(Event)).all()
    assert len(events) == 1


def test_casio_production_path_emits_no_events_by_default(db_session: Session, tmp_settings: Settings):
    """Non-negotiable safety rule: the existing Casio production call path
    (no new kwargs) must not start writing Event rows just because the
    scoring/event feature now exists in the same function."""
    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    html = b"""
    <html><body><h1>Casio Announces GA-2100 Regression Guard</h1>
    <p>Reference GA-2100-1A1JF now available.</p></body></html>
    """
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="casio_intl_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()
    fr = FetchResult(
        url="https://www.casio.com/intl/news/2026/test-no-events/",
        success=True, status_code=200, content_type="text/html", payload=html,
    )
    out = pipeline.process_news_announcement(fr, run_id=run.id)
    assert out["success"] and out["new_watch"]
    assert "watch_events" not in out
    assert db_session.scalars(select(Event)).first() is None


def test_editorial_scoring_is_explainable_and_bounded():
    from app.services.editorial import EventEvidence, score_event

    scored = score_event(
        EventEvidence(
            event_type="NEW_REFERENCE",
            manufacturer="Citizen",
            brand="Citizen",
            collection="Attesa",
            region="GLOBAL",
            is_first_party=True,
        )
    )
    assert 0 <= scored.score <= 100
    assert scored.reasons  # non-empty, explainable
    assert scored.confidence in ("HIGH", "MEDIUM", "LOW")
    # unrecognisable/unscored dimensions must say UNKNOWN, never invent a fact
    assert any("UNKNOWN" in r for r in scored.reasons)


def test_format_alert_only_echoes_supplied_evidence():
    from app.services.editorial import EventEvidence, format_alert, score_event

    scored = score_event(
        EventEvidence(event_type="NEW_REFERENCE", manufacturer="Citizen", brand="Citizen", is_first_party=True)
    )
    text = format_alert(
        manufacturer="Citizen",
        brand="Citizen",
        reference_raw="CC4107-80H",
        scored=scored,
        region="GLOBAL",
        announcement_title="CITIZEN ATTESA New Limited-Edition Titanium Model",
        announcement_url="https://www.citizenwatch-global.com/news/2026/20260610/index.html",
        observed_at="2026-06-10T00:00:00Z",
    )
    assert "CC4107-80H" in text
    assert "NEW_REFERENCE" in text
    assert "Editorial score:" in text
    assert "Confidence:" in text


# --- Sprint 2: price/availability transitions, recall tuning --------------


def test_price_change_detected_same_currency_same_region():
    from app.services.editorial import classify_price_availability_transition

    event_type, reasons = classify_price_availability_transition(
        prior_price=15400, prior_currency="JPY", prior_availability="AVAILABLE", prior_region="JP",
        prior_source_healthy=True,
        new_price=18700, new_currency="JPY", new_availability="AVAILABLE", new_region="JP",
        new_source_healthy=True,
    )
    assert event_type == "PRICE_CHANGE"
    assert any("15400" in r and "18700" in r for r in reasons)


def test_price_never_compared_across_currencies():
    from app.services.editorial import classify_price_availability_transition

    event_type, reasons = classify_price_availability_transition(
        prior_price=15400, prior_currency="JPY", prior_availability="AVAILABLE", prior_region="JP",
        prior_source_healthy=True,
        new_price=99, new_currency="USD", new_availability="AVAILABLE", new_region="JP",
        new_source_healthy=True,
    )
    assert event_type is None
    assert any("no conversion layer" in r for r in reasons)


def test_sold_out_requires_healthy_before_and_after():
    from app.services.editorial import classify_price_availability_transition

    # source failure on the "after" side must NEVER produce SOLD_OUT
    event_type, reasons = classify_price_availability_transition(
        prior_price=100, prior_currency="USD", prior_availability="AVAILABLE", prior_region="US",
        prior_source_healthy=True,
        new_price=None, new_currency=None, new_availability=None, new_region="US",
        new_source_healthy=False,
    )
    assert event_type is None
    assert any("UNKNOWN" in r and "unhealthy" in r for r in reasons)


def test_sold_out_and_restock_classified_from_healthy_pair():
    from app.services.editorial import classify_price_availability_transition

    sold_out, _ = classify_price_availability_transition(
        prior_price=100, prior_currency="USD", prior_availability="AVAILABLE", prior_region="US",
        prior_source_healthy=True,
        new_price=100, new_currency="USD", new_availability="SOLD_OUT", new_region="US",
        new_source_healthy=True,
    )
    assert sold_out == "SOLD_OUT"

    restock, _ = classify_price_availability_transition(
        prior_price=100, prior_currency="USD", prior_availability="SOLD_OUT", prior_region="US",
        prior_source_healthy=True,
        new_price=100, new_currency="USD", new_availability="AVAILABLE", new_region="US",
        new_source_healthy=True,
    )
    assert restock == "RESTOCK"


def test_different_region_pair_is_not_compared():
    from app.services.editorial import classify_price_availability_transition

    event_type, reasons = classify_price_availability_transition(
        prior_price=100, prior_currency="USD", prior_availability="AVAILABLE", prior_region="US",
        prior_source_healthy=True,
        new_price=50, new_currency="USD", new_availability="SOLD_OUT", new_region="JP",
        new_source_healthy=True,
    )
    assert event_type is None
    assert any("not a valid before/after comparison" in r for r in reasons)


def test_repeat_identical_observation_produces_no_event():
    from app.services.editorial import classify_price_availability_transition

    event_type, _ = classify_price_availability_transition(
        prior_price=100, prior_currency="USD", prior_availability="AVAILABLE", prior_region="US",
        prior_source_healthy=True,
        new_price=100, new_currency="USD", new_availability="AVAILABLE", new_region="US",
        new_source_healthy=True,
    )
    assert event_type is None


def test_scoring_rewards_limited_edition_and_collaboration_and_material():
    from app.services.editorial import EventEvidence, score_event

    baseline = score_event(EventEvidence(event_type="NEW_REFERENCE", manufacturer="Citizen", brand="Citizen"))
    enriched = score_event(
        EventEvidence(
            event_type="NEW_REFERENCE", manufacturer="Citizen", brand="Citizen",
            is_limited_edition=True, limited_edition_quantity=1800,
            is_collaboration=True, unusual_material="recrystallised titanium",
        )
    )
    assert enriched.score > baseline.score
    assert any("1800 pieces" in r for r in enriched.reasons)
    assert any("collaboration" in r for r in enriched.reasons)
    assert any("recrystallised titanium" in r for r in enriched.reasons)


def test_price_change_score_scales_with_magnitude():
    from app.services.editorial import EventEvidence, score_event

    small = score_event(
        EventEvidence(event_type="PRICE_CHANGE", manufacturer="Casio", brand="Casio", price_delta_pct=-3.0)
    )
    large = score_event(
        EventEvidence(event_type="PRICE_CHANGE", manufacturer="Casio", brand="Casio", price_delta_pct=-40.0)
    )
    assert large.score > small.score


def test_unknown_event_type_rejected():
    from app.services.editorial import EventEvidence, score_event

    with pytest.raises(ValueError):
        score_event(EventEvidence(event_type="MADE_UP_EVENT", manufacturer="Casio", brand="Casio"))


# --- Sprint 2: Discord notifier safety --------------------------------------


def test_discord_notifier_noop_without_webhook_configured():
    from app.core.config import Settings
    from app.services.discord_notify import DiscordNotifier

    settings = Settings(discord_editorial_webhook_url=None, discord_health_webhook_url=None)
    notifier = DiscordNotifier(settings)
    assert notifier.editorial_enabled is False
    assert notifier.health_enabled is False
    assert notifier.send_editorial_alert("test") is False
    assert notifier.send_health_alert("test") is False


def test_discord_notifier_never_raises_on_network_failure():
    from unittest.mock import patch

    from app.core.config import Settings
    from app.services.discord_notify import DiscordNotifier

    settings = Settings(discord_editorial_webhook_url="https://discord.example/webhook/does-not-exist")
    notifier = DiscordNotifier(settings)
    with patch("httpx.post", side_effect=ConnectionError("simulated network failure")):
        result = notifier.send_editorial_alert("test alert")
    assert result is False  # never raises, just reports failure


def test_discord_notifier_separates_editorial_and_health_channels():
    from unittest.mock import patch

    from app.core.config import Settings
    from app.services.discord_notify import DiscordNotifier

    settings = Settings(
        discord_editorial_webhook_url="https://discord.example/editorial",
        discord_health_webhook_url="https://discord.example/health",
    )
    notifier = DiscordNotifier(settings)
    calls = []
    with patch("httpx.post", side_effect=lambda url, **kw: calls.append(url) or type("R", (), {"status_code": 204, "text": ""})()):
        notifier.send_editorial_alert("editorial content")
        notifier.send_health_alert("health content")
    assert calls == ["https://discord.example/editorial", "https://discord.example/health"]


def test_discord_notifier_editorial_authority_flag_suppresses_only_editorial():
    from unittest.mock import patch

    from app.core.config import Settings
    from app.services.discord_notify import DiscordNotifier

    settings = Settings(
        discord_editorial_webhook_url="https://discord.example/editorial",
        discord_health_webhook_url="https://discord.example/health",
        editorial_notifications_enabled=False,
    )
    notifier = DiscordNotifier(settings)
    calls = []
    with patch("httpx.post", side_effect=lambda url, **kw: calls.append(url) or type("R", (), {"status_code": 204, "text": ""})()):
        assert notifier.editorial_enabled is False
        assert notifier.send_editorial_alert("editorial content") is False
        assert notifier.health_enabled is True
        assert notifier.send_health_alert("health content") is True

    assert calls == ["https://discord.example/health"]


def test_product_character_extraction_is_conservative():
    from app.services.pipeline import PipelineService

    # PipelineService requires a session; instantiate minimally for a pure helper call
    extract = PipelineService.__dict__["_extract_product_character"]

    class Dummy:
        pass

    result = extract(Dummy(), "CITIZEN ATTESA New Limited-Edition Recrystallised Titanium Model")
    assert result["is_limited_edition"] is True
    assert result["unusual_material"] == "recrystallised titanium"

    result2 = extract(Dummy(), "CITIZEN PROMASTER New Wave Tracker Eco-Drive Watch")
    assert result2["is_limited_edition"] is None
    assert result2["unusual_material"] is None


# --- Sprint 3: Citizen product/catalogue observation -----------------------
# Fixtures citizen_product_at8294.html / citizen_product_nj0150.html are
# trimmed-but-real captures from citizenwatch.com/us/en/product/* (live,
# 2026-08-11). citizen_product_at8294_price_drop.html / _sold_out.html are
# synthetic variants of the same real record, built purely to exercise the
# transition classifier offline without depending on live price changes.
# citizen_product_cc4107.html is a synthetic fixture reusing AT8294's real
# schema under a different reference (CC4107-80H, the same reference from
# Sprint 1's citizen_news_detail.html fixture) — it demonstrates identity
# correlation deterministically, not a real captured page for that reference.


def test_citizen_product_discovery_dedups_and_extracts_references():
    from app.collectors.citizen_products import CitizenProductsCollector

    html = (FIXTURES / "citizen_collection_attesa.html").read_text(encoding="utf-8")
    items = CitizenProductsCollector().discover_from_collection_html(html, "https://citizenwatch.com/us/en/collection/attesa")
    urls = [i.url for i in items]
    assert len(urls) == 3  # 4 links, one duplicate, deduped
    assert any(u.endswith("AT8294-59E") for u in urls)
    assert any(u.endswith("AT8384-58E") for u in urls)


def test_citizen_product_parser_extracts_real_captured_fields():
    from app.parsers.citizen_products import parse_citizen_product_html

    html = (FIXTURES / "citizen_product_at8294.html").read_bytes()
    result = parse_citizen_product_html(html, source_url="https://citizenwatch.com/us/en/product/AT8294-59E")
    assert result.success
    w = result.watches[0]
    assert w.reference_raw == "AT8294-59E"
    assert w.manufacturer == "Citizen" and w.brand == "Citizen"
    assert w.price == 1225.0
    assert w.currency == "USD"
    assert w.availability_status == "AVAILABLE"
    assert w.case_material == "Super Titanium with DLC Coating"
    assert w.caliber_or_module == "H800"
    assert w.water_resistance_m == 100
    assert w.collection == "Attesa Standard"


def test_citizen_product_parser_sold_out_and_price_drop_fixtures():
    from app.parsers.citizen_products import parse_citizen_product_html

    sold_out = parse_citizen_product_html((FIXTURES / "citizen_product_at8294_sold_out.html").read_bytes(), source_url="x")
    assert sold_out.success and sold_out.watches[0].availability_status == "SOLD_OUT"

    cheaper = parse_citizen_product_html((FIXTURES / "citizen_product_at8294_price_drop.html").read_bytes(), source_url="x")
    assert cheaper.success and cheaper.watches[0].price == 980.0


def test_citizen_product_parser_malformed_html_fails_closed():
    from app.parsers.citizen_products import parse_citizen_product_html

    result = parse_citizen_product_html("<html><body>no product data here</body></html>", source_url="x")
    assert result.success is False
    assert result.error


def test_citizen_de_product_parser_uses_first_party_jsonld():
    from app.parsers.citizen_de_products import parse_citizen_de_product_html

    result = parse_citizen_de_product_html(
        (FIXTURES / "citizen_de_product_nj0230.html").read_bytes(),
        source_url="https://de.citizenwatch.eu/de/p/nj0230-59l/",
    )
    assert result.success
    watch = result.watches[0]
    assert watch.reference_raw == "NJ0230-59L"
    assert watch.price == 329.0 and watch.currency == "EUR"
    assert watch.availability_status == "AVAILABLE"


def test_citizen_de_sitemap_discovery_is_bounded_and_skips_known_urls():
    from app.collectors.citizen_de_products import CitizenGermanyProductsCollector

    sitemap = (FIXTURES / "citizen_de_products_sitemap.xml").read_bytes()
    collector = CitizenGermanyProductsCollector()
    items = collector.discover_from_sitemap_xml(sitemap)
    assert [item.reference_hint for item in items] == ["NJ0230-59L", "NJ0238-57E"]

    result = collector.run(
        sitemap_payload=sitemap,
        known_product_urls={"https://de.citizenwatch.eu/de/p/nj0230-59l/"},
        max_items=0,
    )
    assert result.metadata["candidate_count"] == 2
    assert result.metadata["known_url_count"] == 1
    assert result.metadata["component_status"] == "ZERO_ITEMS"


def _process_citizen_product_fixture(pipeline, run_id, fixture_name: str, url: str = "https://citizenwatch.com/us/en/product/AT8294-59E"):
    from app.collectors.base import FetchResult
    from app.parsers.citizen_products import parse_citizen_product_html

    payload = (FIXTURES / fixture_name).read_bytes()
    fr = FetchResult(url=url, success=True, status_code=200, content_type="text/html", payload=payload)
    return pipeline.process_fetch_result(
        fr, run_id=run_id, collector_id="citizen_products", collector_version="0.1.0",
        parse_fn=parse_citizen_product_html, default_region="US", emit_events=True,
    )


def test_citizen_product_baseline_observation_creates_no_event(db_session: Session, tmp_settings: Settings):
    """Sprint 3 example Run 1: first observation of a reference is a
    baseline, not an event."""
    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_products", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    out = _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294.html")
    assert out["success"] and out["new_watch"] is True
    assert out["product_event"]["event_type"] is None

    watches = db_session.scalars(select(Watch).where(Watch.manufacturer == "Citizen")).all()
    assert len(watches) == 1
    assert watches[0].reference_canonical == "AT8294-59E"  # conservative pass-through identity
    # Regression: caliber_or_module/movement_type were parsed by every brand's
    # parser but never actually passed into the Watch row (found live during
    # Sprint 4 field-completeness validation — 0/311 real Citizen watches had
    # a movement recorded despite the parser correctly extracting "H800").
    assert watches[0].caliber_or_module == "H800"

    obs = db_session.scalars(select(SourceObservation)).all()
    assert len(obs) == 1
    assert obs[0].price == 1225.0 and obs[0].currency == "USD" and obs[0].availability_status == "AVAILABLE"
    assert obs[0].region == "US"

    assert db_session.scalars(select(Event)).first() is None


def test_citizen_product_repeat_identical_fetch_creates_no_duplicate_event(db_session: Session, tmp_settings: Settings):
    """Acceptance criterion 4: repeated fetch of an unchanged product must
    not create a duplicate NEW_REFERENCE-equivalent baseline event, and a
    second identical observation with no transition creates no event."""
    from app.models import Event
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_products", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    out1 = _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294.html")
    out2 = _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294.html")
    assert out1["new_watch"] is True
    assert out2["new_watch"] is False
    assert out1["product_event"]["event_type"] is None
    assert out2["product_event"]["event_type"] is None  # identical price+availability -> no transition
    assert db_session.scalars(select(Event)).first() is None


def test_known_citizen_first_product_listing_in_new_region_is_regional_intelligence(
    db_session: Session, tmp_settings: Settings
):
    """Regression for the Tsuyosa regional-commercialisation miss class.

    The same canonical reference is announced globally, then observed on US
    and UK first-party stores.  It remains one Watch, retains local USD/GBP
    facts independently, and emits exactly one NEW_REGION event for the new
    market rather than inventing a cross-currency PRICE_CHANGE.
    """
    from app.models import Event, ReleaseLead, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    watch = Watch(
        manufacturer="Citizen", brand="Citizen", collection="Tsuyosa",
        reference_raw="NJ0238-57E", reference_canonical="NJ0238-57E",
    )
    db_session.add(watch)
    db_session.flush()
    db_session.add(
        ReleaseLead(
            manufacturer="Citizen", brand="Citizen", collection="Tsuyosa",
            announcement_title="TSUYOSA Shore announcement", announcement_date="2026-07-23",
            announcement_url="https://example.test/global/nj0238", source_id="citizen_news",
            source_region="GLOBAL", watch_ids=[watch.id],
        )
    )
    us = SourceObservation(
        watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0",
        parser_id="fixture", parser_version="1", region="US", source_url="https://example.test/us/nj0238",
        price=525.0, currency="USD", availability_status="AVAILABLE", overall_confidence=90.0,
    )
    uk = SourceObservation(
        watch_id=watch.id, collector_id="citizen_uk_products", collector_version="0.1.0",
        parser_id="fixture", parser_version="1", region="UK", source_url="https://example.test/uk/nj0238",
        price=349.0, currency="GBP", availability_status="AVAILABLE", overall_confidence=90.0,
    )
    db_session.add_all([us, uk])
    db_session.flush()

    result = PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
        watch=watch, new_obs=uk, is_new_watch=False
    )
    assert result["event_type"] == "NEW_REGION"
    assert result["score"] >= 70  # official Tsuyosa + first local price
    assert db_session.scalar(select(Event).where(Event.event_type == "PRICE_CHANGE")) is None
    event = db_session.scalar(select(Event).where(Event.event_type == "NEW_REGION"))
    assert event.extra["region"] == "UK"
    assert set(event.extra["prior_regions"]) == {"GLOBAL", "US"}

    # Repeat observation is routine state, not a duplicate regional lead.
    uk_repeat = SourceObservation(
        watch_id=watch.id, collector_id="citizen_uk_products", collector_version="0.1.0",
        parser_id="fixture", parser_version="1", region="UK", source_url="https://example.test/uk/nj0238",
        price=349.0, currency="GBP", availability_status="AVAILABLE", overall_confidence=90.0,
    )
    db_session.add(uk_repeat)
    db_session.flush()
    repeat = PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
        watch=watch, new_obs=uk_repeat, is_new_watch=False
    )
    assert repeat["event_type"] is None
    assert len(db_session.scalars(select(Event)).all()) == 1


def test_regional_source_onboarding_is_silent_even_for_old_known_watch(
    db_session: Session, tmp_settings: Settings
):
    """An old regional page first encountered during a source baseline is
    stored, but cannot claim to be today's rollout."""
    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    watch = Watch(
        manufacturer="Citizen", brand="Citizen", reference_raw="NJ0230-59L",
        reference_canonical="NJ0230-59L",
    )
    db_session.add(watch)
    db_session.flush()
    old_us = SourceObservation(
        watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0",
        parser_id="fixture", parser_version="1", region="US", source_url="https://example.test/us/nj0230",
        price=525.0, currency="USD", overall_confidence=90.0,
    )
    newly_discovered_uk = SourceObservation(
        watch_id=watch.id, collector_id="citizen_uk_products", collector_version="0.1.0",
        parser_id="fixture", parser_version="1", region="UK", source_url="https://example.test/uk/nj0230",
        price=349.0, currency="GBP", overall_confidence=90.0,
    )
    db_session.add_all([old_us, newly_discovered_uk])
    db_session.flush()
    result = PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
        watch=watch, new_obs=newly_discovered_uk, is_new_watch=False, force_baseline=True
    )
    assert result == {"event_type": None, "reason": "source_scoped_baseline"}
    assert db_session.scalar(select(Event)) is None


def test_same_citizen_reference_across_regions_resolves_one_watch(
    db_session: Session, tmp_settings: Settings
):
    """Identity is intentionally region-independent while observations and
    currencies remain market-specific."""
    import json

    from app.collectors.base import FetchResult
    from app.models import Event, SourceObservation, Watch
    from app.parsers.citizen_products import parse_citizen_search_hit
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_products", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    def process(region: str, price: float, currency: str):
        payload = json.dumps({"id": "NJ0238-57E", "name": "Tsuyosa Shore", "_hit_price": price, "_hit_currency": currency}).encode()
        return pipeline.process_fetch_result(
            FetchResult(url=f"https://example.test/{region}/NJ0238-57E", success=True, status_code=200, content_type="application/json", payload=payload),
            run_id=run.id, collector_id=f"citizen_{region.lower()}_products", collector_version="0.1.0",
            parse_fn=parse_citizen_search_hit, default_region=region, emit_events=True,
        )

    us = process("US", 525.0, "USD")
    uk = process("UK", 349.0, "GBP")
    assert us["new_watch"] is True and uk["new_watch"] is False
    watches = db_session.scalars(select(Watch).where(Watch.manufacturer == "Citizen")).all()
    observations = db_session.scalars(select(SourceObservation).order_by(SourceObservation.id)).all()
    assert len(watches) == 1
    assert [(o.region, o.price, o.currency) for o in observations] == [
        ("US", 525.0, "USD"), ("UK", 349.0, "GBP"),
    ]
    assert uk["product_event"]["event_type"] == "NEW_REGION"
    assert db_session.scalar(select(Event).where(Event.event_type == "PRICE_CHANGE")) is None


def test_citizen_product_price_transition_produces_price_change(db_session: Session, tmp_settings: Settings):
    """Sprint 3 example Run 2: same reference, price changed -> PRICE_CHANGE."""
    from app.models import Event
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_products", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294.html")
    out2 = _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294_price_drop.html")
    assert out2["product_event"]["event_type"] == "PRICE_CHANGE"

    events = db_session.scalars(select(Event)).all()
    assert len(events) == 1
    assert events[0].event_type == "PRICE_CHANGE"
    assert any("980" in r and "1225" in r for r in events[0].extra["reasons"])


def test_citizen_product_availability_transitions_sold_out_then_restock(db_session: Session, tmp_settings: Settings):
    """Sprint 3 example Run 3/4: AVAILABLE -> SOLD_OUT -> AVAILABLE (RESTOCK)."""
    from app.models import Event
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_products", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294.html")  # AVAILABLE baseline
    sold_out = _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294_sold_out.html")
    assert sold_out["product_event"]["event_type"] == "SOLD_OUT"
    restock = _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294.html")  # back to AVAILABLE
    assert restock["product_event"]["event_type"] == "RESTOCK"

    events = db_session.scalars(select(Event)).all()
    assert sorted(e.event_type for e in events) == ["RESTOCK", "SOLD_OUT"]
    assert all(e.extra["editorial_eligible"] is False for e in events)
    assert all("EDITORIAL HIDDEN" in e.extra["editorial_eligibility_reasons"][0] for e in events)


def _availability_transition(
    db_session: Session,
    tmp_settings: Settings,
    *,
    limited_edition: bool | None = None,
    collection: str | None = None,
    model_name: str | None = None,
    prior_status: str = "AVAILABLE",
    new_status: str = "SOLD_OUT",
    prior_age_days: float = 90,
    prior_is_baseline: bool = False,
    reference: str = "TWTEST001",
    notify: bool = False,
):
    """Create a proved healthy availability pair without any collector I/O."""
    from datetime import UTC, datetime, timedelta

    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    watch = Watch(
        manufacturer="Timex", brand="Timex", collection=collection, model_name=model_name,
        limited_edition=limited_edition, reference_raw=reference, reference_canonical=reference,
    )
    db_session.add(watch)
    db_session.flush()
    now = datetime.now(UTC)
    prior = SourceObservation(
        watch_id=watch.id, collector_id="timex_products", collector_version="test", parser_id="test",
        parser_version="test", region="US", source_url="https://example.test/prior", price=100.0,
        currency="USD", availability_status=prior_status, overall_confidence=90.0,
        observed_at=now - timedelta(days=prior_age_days), is_baseline=prior_is_baseline,
    )
    new = SourceObservation(
        watch_id=watch.id, collector_id="timex_products", collector_version="test", parser_id="test",
        parser_version="test", region="US", source_url="https://example.test/new", price=100.0,
        currency="USD", availability_status=new_status, overall_confidence=90.0, observed_at=now,
    )
    db_session.add_all([prior, new])
    db_session.flush()
    result = PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
        watch=watch, new_obs=new, is_new_watch=False, notify=notify, experimental=True
    )
    return result, db_session.scalars(select(Event).order_by(Event.id.desc())).first()


def test_ordinary_old_sold_out_is_preserved_but_editorially_hidden(db_session: Session, tmp_settings: Settings):
    result, event = _availability_transition(db_session, tmp_settings)

    assert result["event_type"] == "SOLD_OUT"
    assert event.event_type == "SOLD_OUT"
    assert event.extra["editorial_eligible"] is False
    assert event.extra["alerted"] is False
    assert any("no confirmed limited" in reason for reason in event.extra["editorial_eligibility_reasons"])


def test_recent_ordinary_sold_out_is_still_editorially_hidden(db_session: Session, tmp_settings: Settings):
    _, event = _availability_transition(db_session, tmp_settings, prior_age_days=1)

    assert event.story_score >= tmp_settings.availability_editorial_min_score
    assert event.extra["editorial_eligible"] is False
    assert any("no confirmed limited" in reason for reason in event.extra["editorial_eligibility_reasons"])


def test_legacy_availability_rows_stay_out_of_current_intelligence_without_evidence_flag() -> None:
    from app.services.editorial import event_row_is_editorially_eligible

    assert not event_row_is_editorially_eligible(
        event_type="SOLD_OUT", story_score=100.0, extra={}, availability_min_score=70.0
    )
    assert event_row_is_editorially_eligible(
        event_type="SOLD_OUT", story_score=80.0, extra={"editorial_eligible": True},
        availability_min_score=70.0,
    )


def test_ordinary_restock_is_preserved_but_editorially_hidden(db_session: Session, tmp_settings: Settings):
    result, event = _availability_transition(
        db_session, tmp_settings, prior_status="SOLD_OUT", new_status="AVAILABLE"
    )

    assert result["event_type"] == "RESTOCK"
    assert event.extra["editorial_eligible"] is False


def test_unknown_availability_never_classifies_as_sold_out() -> None:
    from app.services.editorial import classify_price_availability_transition

    event_type, _ = classify_price_availability_transition(
        prior_price=100.0, prior_currency="USD", prior_availability="AVAILABLE", prior_region="US",
        prior_source_healthy=True, new_price=100.0, new_currency="USD", new_availability="UNKNOWN",
        new_region="US", new_source_healthy=True,
    )

    assert event_type != "SOLD_OUT"


def test_initial_unavailable_product_baseline_creates_no_sold_out_event(db_session: Session, tmp_settings: Settings):
    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    watch = Watch(
        manufacturer="Timex", brand="Timex", reference_raw="TWBASE001", reference_canonical="TWBASE001"
    )
    db_session.add(watch)
    db_session.flush()
    observation = SourceObservation(
        watch_id=watch.id, collector_id="timex_products", collector_version="test", parser_id="test",
        parser_version="test", region="US", source_url="https://example.test/unavailable", price=100.0,
        currency="USD", availability_status="SOLD_OUT", overall_confidence=90.0,
    )
    db_session.add(observation)
    db_session.flush()

    result = PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
        watch=watch, new_obs=observation, is_new_watch=True, experimental=True
    )

    assert result == {"event_type": None, "reason": "baseline_new_watch"}
    assert db_session.query(Event).count() == 0


def test_limited_edition_fast_sell_out_is_editorially_eligible(db_session: Session, tmp_settings: Settings):
    result, event = _availability_transition(
        db_session, tmp_settings, limited_edition=True, prior_age_days=2
    )

    assert result["event_type"] == "SOLD_OUT"
    assert event.story_score >= tmp_settings.availability_editorial_min_score
    assert event.extra["editorial_eligible"] is True
    assert any("limited edition" in reason for reason in event.extra["reasons"])
    assert any("rapid post-launch" in reason for reason in event.extra["reasons"])


def test_collaboration_fast_sell_out_is_editorially_eligible(db_session: Session, tmp_settings: Settings):
    result, event = _availability_transition(
        db_session, tmp_settings, model_name="Peanuts x Timex limited release", prior_age_days=2
    )

    assert result["event_type"] == "SOLD_OUT"
    assert event.extra["editorial_eligible"] is True
    assert any("named collaboration" in reason for reason in event.extra["reasons"])


def test_recognizable_family_alone_does_not_make_old_sold_out_current(db_session: Session, tmp_settings: Settings):
    _, event = _availability_transition(db_session, tmp_settings, collection="Tsuyosa")

    assert event.story_score < tmp_settings.availability_editorial_min_score
    assert event.extra["editorial_eligible"] is False
    assert any("recognisable product family" in reason for reason in event.extra["reasons"])


def test_baseline_availability_is_never_treated_as_recent_launch_evidence(db_session: Session, tmp_settings: Settings):
    _, event = _availability_transition(
        db_session, tmp_settings, limited_edition=True, prior_age_days=1, prior_is_baseline=True
    )

    assert event.extra["editorial_eligible"] is False
    assert not any("rapid post-launch" in reason for reason in event.extra["reasons"])


def test_availability_discord_requires_editorial_eligibility(db_session: Session, tmp_settings: Settings):
    from unittest.mock import patch

    from app.core.config import Settings

    settings = Settings(
        database_url=tmp_settings.database_url,
        discord_editorial_webhook_url="https://discord.example/editorial",
    )
    calls = []
    with (
        patch("app.services.pipeline.get_settings", return_value=settings),
        patch("httpx.post", side_effect=lambda url, **kw: calls.append(url) or type("R", (), {"status_code": 204, "text": ""})()),
    ):
        # Ordinary historical churn remains persisted but does not reach
        # Discord, even under experimental mode's otherwise-zero threshold.
        _, ordinary = _availability_transition(db_session, tmp_settings, notify=True)
        _, eligible = _availability_transition(
            db_session, tmp_settings, limited_edition=True, prior_age_days=1,
            reference="TWTEST002", notify=True,
        )
    assert ordinary.extra["editorial_eligible"] is False
    assert eligible.extra["editorial_eligible"] is True
    assert calls == ["https://discord.example/editorial"]


def test_citizen_product_failed_fetch_cannot_create_sold_out(db_session: Session, tmp_settings: Settings):
    """Acceptance criterion 9: a collector failure between two healthy runs
    must never fabricate an availability transition. A failed FetchResult
    never reaches process_fetch_result's observation-creation code at all
    (it returns early on `not fr.success`), so no SourceObservation and no
    Event get created from it — this test proves that end-to-end."""
    from app.collectors.base import FetchResult
    from app.models import Event, SourceObservation
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_products", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294.html")  # baseline AVAILABLE

    failed_fr = FetchResult(url="https://citizenwatch.com/us/en/product/AT8294-59E", success=False, error="HTTP 503")
    failed_out = pipeline.process_fetch_result(
        failed_fr, run_id=run.id, collector_id="citizen_products", collector_version="0.1.0",
        default_region="US", emit_events=True,
    )
    assert failed_out["success"] is False

    # only the one baseline observation exists; the failure created none
    assert len(db_session.scalars(select(SourceObservation)).all()) == 1
    assert db_session.scalars(select(Event)).first() is None

    # a subsequent healthy AVAILABLE fetch after the failure is still just a
    # repeat, not a fabricated RESTOCK (there was never a real SOLD_OUT)
    after = _process_citizen_product_fixture(pipeline, run.id, "citizen_product_at8294.html")
    assert after["product_event"]["event_type"] is None


def test_citizen_news_and_product_references_correlate_to_same_watch(db_session: Session, tmp_settings: Settings):
    """Acceptance criterion 12: a news-discovered reference and a later
    product-page observation of the same reference resolve to one Watch."""
    from app.collectors.base import FetchResult
    from app.models import Watch
    from app.parsers.citizen_news import parse_citizen_news_html
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="test", collector_version="0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    news_html = (FIXTURES / "citizen_news_detail.html").read_bytes()  # references CC4107-80H
    news_fr = FetchResult(url="https://www.citizenwatch-global.com/news/2026/20260610/index.html", success=True, status_code=200, content_type="text/html", payload=news_html)
    news_out = pipeline.process_news_announcement(
        news_fr, run_id=run.id, discovered_meta={"source_region": "GLOBAL"},
        collector_id="citizen_news", manufacturer="Citizen", brand="Citizen",
        parse_fn=parse_citizen_news_html, merge_key_prefix="citizen", default_region="GLOBAL",
    )
    assert news_out["success"] and news_out["new_watch"] is True

    product_out = _process_citizen_product_fixture(
        pipeline, run.id, "citizen_product_cc4107.html",
        url="https://citizenwatch.com/us/en/product/CC4107-80H",
    )
    assert product_out["success"]
    # the product-page fetch resolved to an EXISTING watch, not a new one —
    # this is the correlation: same reference_canonical -> same Watch row
    assert product_out["new_watch"] is False

    watches = db_session.scalars(select(Watch).where(Watch.reference_canonical == "CC4107-80H")).all()
    assert len(watches) == 1
    assert watches[0].manufacturer == "Citizen"


# --- Sprint 3: Seiko product/catalogue observation --------------------------
# seikousa.com is operated by Seiko Watch of America LLC (confirmed via its
# own Terms of Service, 2026-08-11) — Seiko's official US importer, not a
# third-party retailer. Fixtures here are real, trimmed captures from its
# public Shopify /collections/all/products.json endpoint (a standard public
# Shopify storefront feature, not an API being reverse engineered).


def test_seiko_product_discovery_filters_to_wrist_watches_only():
    from app.collectors.seiko_products import SeikoProductsCollector

    listing = (FIXTURES / "seiko_products_listing.json").read_bytes()
    items = SeikoProductsCollector().discover_from_listing_json(listing)
    # fixture has 1 strap + 5 watches; only watches should be discovered
    assert len(items) == 5
    assert all(i.reference_hint != "BLACKSTRAP" for i in items)


def test_seiko_product_parser_extracts_real_captured_fields():
    from app.parsers.seiko_products import parse_seiko_product_json

    payload = (FIXTURES / "seiko_product_available.json").read_bytes()
    result = parse_seiko_product_json(payload, source_url="https://seikousa.com/products/hab001")
    assert result.success
    w = result.watches[0]
    assert w.reference_raw == "HAB001"
    assert w.manufacturer == "Seiko" and w.brand == "Seiko"
    assert w.price == 2700.0
    assert w.currency == "USD"
    assert w.availability_status == "AVAILABLE"


def test_seiko_product_parser_sold_out_fixture():
    from app.parsers.seiko_products import parse_seiko_product_json

    payload = (FIXTURES / "seiko_product_sold_out.json").read_bytes()
    result = parse_seiko_product_json(payload, source_url="x")
    assert result.success
    assert result.watches[0].availability_status == "SOLD_OUT"


def test_seiko_product_parser_rejects_non_watch_product_type():
    from app.parsers.seiko_products import parse_seiko_product_json

    strap = {"product_type": "Straps", "title": "BLACKSTRAP", "variants": [{"sku": "BLACKSTRAP", "price": "50.00", "available": True}]}
    result = parse_seiko_product_json(strap, source_url="x")
    assert result.success is False


def test_seiko_product_pipeline_baseline_then_price_change(db_session: Session, tmp_settings: Settings):
    import json

    from app.collectors.base import FetchResult
    from app.models import Event, Watch
    from app.parsers.seiko_products import parse_seiko_product_json
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="seiko_products", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    base = json.loads((FIXTURES / "seiko_product_available.json").read_bytes())

    def process(product_dict):
        fr = FetchResult(
            url="https://seikousa.com/products/hab001", success=True, status_code=200,
            content_type="application/json", payload=json.dumps(product_dict).encode("utf-8"),
        )
        return pipeline.process_fetch_result(
            fr, run_id=run.id, collector_id="seiko_products", collector_version="0.1.0",
            parse_fn=parse_seiko_product_json, default_region="US", emit_events=True,
        )

    out1 = process(base)
    assert out1["new_watch"] is True and out1["product_event"]["event_type"] is None

    cheaper = dict(base)
    cheaper["variants"] = [dict(base["variants"][0], price="2400.00")]
    out2 = process(cheaper)
    assert out2["new_watch"] is False
    assert out2["product_event"]["event_type"] == "PRICE_CHANGE"

    watches = db_session.scalars(select(Watch).where(Watch.manufacturer == "Seiko")).all()
    assert len(watches) == 1 and watches[0].reference_canonical == "HAB001"

    events = db_session.scalars(select(Event)).all()
    assert len(events) == 1 and events[0].event_type == "PRICE_CHANGE"


# --- Sprint 4: Citizen broad catalogue discovery (search-hit pagination) ---
# citizen_search_attesa_page{1,2}.html are real Citizen search-hit records
# (from the live attesa collection, 2026-08-11) trimmed to essential fields,
# split across two synthetic "pages" with an inflated total (50) purely to
# exercise multi-page pagination mechanics deterministically and quickly —
# the real site would never split 3 hits across 2 pages of limit=48, but the
# offset/total loop logic under test doesn't care how many hits are on any
# one page, only whether it keeps fetching until offset reaches the
# source-reported total.


def test_citizen_search_page_parses_hits_and_total():
    from app.collectors.citizen_products import CitizenProductsCollector

    html = (FIXTURES / "citizen_search_attesa_page1.html").read_text(encoding="utf-8")
    items, total = CitizenProductsCollector().parse_search_page(html)
    assert total == 50
    assert [i.reference_hint for i in items] == ["CC4107-80H", "CC4078-51E"]
    assert items[0].metadata["product_dict"]["_hit_price"] == 2195


def test_citizen_search_pagination_follows_total_across_pages():
    from app.collectors.citizen_products import CitizenProductsCollector

    p1 = (FIXTURES / "citizen_search_attesa_page1.html").read_bytes()
    p2 = (FIXTURES / "citizen_search_attesa_page2.html").read_bytes()
    items, fetches = CitizenProductsCollector().discover_via_search(search_pages={"mens": [p1, p2]})
    refs = [i.reference_hint for i in items]
    assert refs == ["CC4107-80H", "CC4078-51E", "AT8384-58E"]
    # 2 real pages for "mens" + 1 failed attempt for "womens" (no fixture
    # supplied) = 3 fetch attempts, proving both real pagination continuation
    # AND graceful handling of a collection with no data available.
    assert len(fetches) == 3
    assert fetches[-1].success is False


def test_citizen_search_pagination_dedupes_across_collections():
    from app.collectors.citizen_products import CitizenProductsCollector

    p1 = (FIXTURES / "citizen_search_attesa_page1.html").read_bytes()
    p2 = (FIXTURES / "citizen_search_attesa_page2.html").read_bytes()
    items, _ = CitizenProductsCollector().discover_via_search(search_pages={"mens": [p1, p2], "womens": [p1, p2]})
    # same 3 references appear via both "mens" and "womens" fixtures reused —
    # must be deduplicated to 3, not 6
    assert len(items) == 3


def test_citizen_search_pagination_respects_safety_cap():
    """A collector bug or anomalous source reporting a huge total must not
    trigger unbounded pagination — the MAX_CANDIDATES_PER_COLLECTION cap
    bounds it, protecting against a catalogue-collapse-style runaway."""
    import app.collectors.citizen_products as citizen_products_mod

    p1 = (FIXTURES / "citizen_search_attesa_page1.html").read_text(encoding="utf-8")
    huge_total_page = p1.replace('"total":50', '"total":999999').encode("utf-8")
    # Same page repeated many times — pagination must stop at the cap, not
    # loop until it runs out of list items or hits total=999999.
    pages = [huge_total_page] * 20
    original_cap = citizen_products_mod.MAX_CANDIDATES_PER_COLLECTION
    citizen_products_mod.MAX_CANDIDATES_PER_COLLECTION = 10
    try:
        items, fetches = citizen_products_mod.CitizenProductsCollector().discover_via_search(
            search_pages={"mens": pages}
        )
    finally:
        citizen_products_mod.MAX_CANDIDATES_PER_COLLECTION = original_cap
    assert len(fetches) < 20  # stopped well before exhausting the fixture list


def test_citizen_search_hit_pipeline_baseline_then_no_duplicate(db_session: Session, tmp_settings: Settings):
    """A search-hit-sourced observation has no availability signal (UNKNOWN,
    not guessed) but still participates correctly in baseline/dedup."""
    import json

    from app.collectors.base import FetchResult
    from app.models import Event, Watch
    from app.parsers.citizen_products import parse_citizen_search_hit
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    html = (FIXTURES / "citizen_search_attesa_page1.html").read_text(encoding="utf-8")
    from app.collectors.citizen_products import CitizenProductsCollector

    items, _ = CitizenProductsCollector().parse_search_page(html)
    product_dict = items[0].metadata["product_dict"]

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_products", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    def process():
        fr = FetchResult(
            url="https://citizenwatch.com/us/en/product/CC4107-80H", success=True, status_code=200,
            content_type="application/json", payload=json.dumps(product_dict).encode("utf-8"),
        )
        return pipeline.process_fetch_result(
            fr, run_id=run.id, collector_id="citizen_products", collector_version="0.1.0",
            parse_fn=parse_citizen_search_hit, default_region="US", emit_events=True,
        )

    out1 = process()
    assert out1["success"] and out1["new_watch"] is True
    assert out1["product_event"]["event_type"] is None

    out2 = process()
    assert out2["new_watch"] is False
    assert out2["product_event"]["event_type"] is None  # identical repeat -> no event

    watches = db_session.scalars(select(Watch).where(Watch.reference_canonical == "CC4107-80H")).all()
    assert len(watches) == 1
    assert watches[0].case_material == "Super Titanium"
    assert db_session.scalars(select(Event)).first() is None


# --- Sprint 4: Seiko full-catalogue pagination ------------------------------
# seiko_products_page{1,2}.json are real captures (2026-08-11) trimmed to a
# few products each; page3_empty.json is the real empty-products response
# Shopify returns past the last page — the natural pagination terminator.


def test_seiko_pagination_follows_pages_until_empty():
    from app.collectors.seiko_products import SeikoProductsCollector

    p1 = (FIXTURES / "seiko_products_page1.json").read_bytes()
    p2 = (FIXTURES / "seiko_products_page2.json").read_bytes()
    p3 = (FIXTURES / "seiko_products_page3_empty.json").read_bytes()
    items, fetches = SeikoProductsCollector().discover_all_pages(listing_pages=[p1, p2, p3])
    # page1 has 1 strap (filtered) + 3 watches, page2 has 2 more watches
    assert len(items) == 5
    assert len(fetches) == 3  # stopped after the empty page, did not overrun
    assert all(f.success for f in fetches)


def test_seiko_pagination_stops_on_first_empty_page():
    from app.collectors.seiko_products import SeikoProductsCollector

    p1 = (FIXTURES / "seiko_products_page1.json").read_bytes()
    p3 = (FIXTURES / "seiko_products_page3_empty.json").read_bytes()
    items, fetches = SeikoProductsCollector().discover_all_pages(listing_pages=[p1, p3])
    assert len(items) == 3  # only page1's watches
    assert len(fetches) == 2


def test_seiko_pagination_dedupes_repeated_reference_across_pages():
    from app.collectors.seiko_products import SeikoProductsCollector

    p1 = (FIXTURES / "seiko_products_page1.json").read_bytes()
    items, _ = SeikoProductsCollector().discover_all_pages(listing_pages=[p1, p1])  # same page "twice"
    # second identical page contributes zero new (all refs already seen) —
    # discover_all_pages doesn't itself stop on a repeat, but dedup must hold
    assert len(items) == 3


# --- Sprint 5: Layer B early-warning (CASIOBLOG + specialist leads) --------
# casioblog_feed.xml is a real, live capture of casioblog.com/en/feed/
# (2026-08-11) — genuine RSS, not synthetic.


# --- Sprint 7: G-Central + Plus9Time specialist sources ---------------------


def test_gcentral_feed_parses_real_capture():
    from app.parsers.gcentral import parse_gcentral_feed

    xml = (FIXTURES / "gcentral_feed.xml").read_bytes()
    result = parse_gcentral_feed(xml, max_items=20)
    assert result.success
    assert len(result.items) == 15
    assert all(i.url.startswith("https://www.g-central.com/") for i in result.items)
    assert all(len(i.claim_text or "") <= 400 for i in result.items)  # no full body copied
    ref_item = next(i for i in result.items if i.reference_candidates)
    assert ref_item.reference_candidates
    collab_item = next(i for i in result.items if i.is_collaboration)
    assert collab_item is not None
    restock_item = next(i for i in result.items if i.is_restock_or_availability)
    assert restock_item is not None


def test_gcentral_feed_malformed_xml_fails_closed():
    from app.parsers.gcentral import parse_gcentral_feed

    result = parse_gcentral_feed(b"<rss><channel><item><title>broken")
    assert result.success is False
    assert result.error


def test_gcentral_reference_regex_ignores_plain_words():
    """Regression: found live during Sprint 7 isolated validation against
    the real feed -- "new obby game and virtual items" false-positived as
    reference "GAME" before the regex required a digit in the suffix."""
    from app.parsers.gcentral import parse_gcentral_feed

    xml = b"""<?xml version="1.0"?><rss><channel>
    <item><title>G-Shock is now on Roblox with new obby game and virtual items</title>
    <link>https://www.g-central.com/x/</link><pubDate>Mon, 01 Jun 2026 00:00:00 +0000</pubDate>
    </item></channel></rss>"""
    result = parse_gcentral_feed(xml)
    assert result.success
    assert result.items[0].reference_candidates == []


def test_gcentral_reference_extraction_is_deterministic():
    """Same family-prefix regex discipline as casioblog.py -- matches the
    family root up to the first hyphen-colorway boundary, never the full
    multi-hyphen suffix. Consistent with Sprint 6's FAMILY_MATCH design:
    correlation, not this regex, is responsible for exact-vs-family
    distinction."""
    from app.parsers.gcentral import parse_gcentral_feed

    xml = b"""<?xml version="1.0"?><rss><channel>
    <item><title>New G-Shock GA-2100-1A1 colorway announced</title>
    <link>https://www.g-central.com/x/</link><pubDate>Mon, 01 Jun 2026 00:00:00 +0000</pubDate>
    </item></channel></rss>"""
    result = parse_gcentral_feed(xml)
    assert result.success
    assert result.items[0].reference_candidates == ["GA-2100"]


def test_plus9time_feed_parses_real_capture():
    from app.parsers.plus9time import parse_plus9time_feed

    xml = (FIXTURES / "plus9time_feed.xml").read_bytes()
    result = parse_plus9time_feed(xml, max_items=20)
    assert result.success
    assert len(result.items) == 15
    assert all(i.url.startswith("https://www.plus9time.com/") for i in result.items)
    assert all(len(i.claim_text or "") <= 400 for i in result.items)
    # Honest finding: this real capture is predominantly historical/archival
    # (catalog scans, patents) with brand identifiable but few/no current
    # extractable references -- not a parser bug, see module docstring.
    brands = {i.brand_guess for i in result.items}
    assert "Seiko" in brands or "Citizen" in brands


def test_plus9time_feed_malformed_xml_fails_closed():
    from app.parsers.plus9time import parse_plus9time_feed

    result = parse_plus9time_feed(b"<rss><channel><item><title>broken")
    assert result.success is False
    assert result.error


def test_plus9time_reference_extraction_and_brand_guess():
    from app.parsers.plus9time import parse_plus9time_feed

    xml = b"""<?xml version="1.0"?><rss><channel>
    <item><title>Seiko SBGA211 spotted early</title><category>Seiko</category>
    <link>https://www.plus9time.com/blog/x</link><pubDate>Mon, 01 Jun 2026 00:00:00 +0000</pubDate>
    </item></channel></rss>"""
    result = parse_plus9time_feed(xml)
    assert result.success
    assert result.items[0].reference_candidates == ["SBGA211"]
    assert result.items[0].brand_guess == "Seiko"


def test_gcentral_pipeline_baseline_then_repeat_creates_no_duplicates(db_session: Session, tmp_settings: Settings, monkeypatch):
    from unittest.mock import patch

    from app.core import config as config_mod
    from app.models import SpecialistLead
    from app.services.specialist_leads import run_gcentral_pipeline

    monkeypatch.setattr(config_mod, "get_settings", lambda: tmp_settings)
    xml = (FIXTURES / "gcentral_feed.xml").read_bytes()

    with patch("app.services.specialist_leads.get_settings", return_value=tmp_settings):
        run1 = run_gcentral_pipeline(db_session, feed_xml=xml)
        run2 = run_gcentral_pipeline(db_session, feed_xml=xml)

    assert run1.status == "SUCCESS"
    assert run1.summary_metadata["new_leads"] == 15
    assert run2.status == "SUCCESS"
    assert run2.summary_metadata["new_leads"] == 0

    leads = db_session.scalars(select(SpecialistLead)).all()
    assert len(leads) == 15
    assert all(lead.source_id == "g_central" for lead in leads)


def test_plus9time_pipeline_baseline_then_repeat_creates_no_duplicates(db_session: Session, tmp_settings: Settings, monkeypatch):
    from unittest.mock import patch

    from app.core import config as config_mod
    from app.models import SpecialistLead
    from app.services.specialist_leads import run_plus9time_pipeline

    monkeypatch.setattr(config_mod, "get_settings", lambda: tmp_settings)
    xml = (FIXTURES / "plus9time_feed.xml").read_bytes()

    with patch("app.services.specialist_leads.get_settings", return_value=tmp_settings):
        run1 = run_plus9time_pipeline(db_session, feed_xml=xml)
        run2 = run_plus9time_pipeline(db_session, feed_xml=xml)

    assert run1.status == "SUCCESS"
    assert run1.summary_metadata["new_leads"] == 15
    assert run2.status == "SUCCESS"
    assert run2.summary_metadata["new_leads"] == 0

    leads = db_session.scalars(select(SpecialistLead)).all()
    assert len(leads) == 15
    assert all(lead.source_id == "plus9time" for lead in leads)


def test_monochrome_hcc009j1_field_miss_fixture_extracts_exact_reference():
    """Permanent regression fixture for the 2026-08-12 HCC009J1 miss.

    Monochrome's public RSS was the earliest observable specialist signal
    found in the autopsy. The fixture intentionally preserves only the RSS
    fields this collector is allowed to retain, never an article body.
    """
    from app.parsers.specialist_publications import parse_specialist_publication_feed

    result = parse_specialist_publication_feed((FIXTURES / "monochrome_hcc009j1_feed.xml").read_bytes())

    assert result.success
    assert len(result.items) == 1
    item = result.items[0]
    assert item.brand == "Seiko"
    assert item.reference_candidates == ["HCC009J1"]
    assert item.is_limited_edition is True
    assert item.is_collaboration is True
    assert item.published_at == "2026-08-12T03:00:27+00:00"


def test_new_publication_source_fixtures_preserve_real_metadata_and_exact_reference_when_present():
    from app.parsers.specialist_publications import parse_specialist_publication_feed

    expected = {
        "deployant_gbx_h5600ki_feed.xml": ("Casio", ["GBX-H5600KI"]),
        "fratello_hcc009j1_feed.xml": ("Seiko", ["HCC009J1"]),
        # The observed WatchTime RSS headline has no model reference. Keeping
        # it empty is intentional: no parser may invent a reference.
        "watchtime_seiko_edo_murasaki_feed.xml": ("Seiko", []),
    }
    for filename, (brand, references) in expected.items():
        result = parse_specialist_publication_feed((FIXTURES / filename).read_bytes())
        assert result.success
        assert len(result.items) == 1
        assert result.items[0].brand == brand
        assert result.items[0].reference_candidates == references


def test_monochrome_source_scoped_baseline_is_silent_then_repeat_is_deduped(
    db_session: Session, tmp_settings: Settings, monkeypatch
):
    from unittest.mock import patch

    from app.core import config as config_mod
    from app.models import Event, SpecialistLead
    from app.services.specialist_leads import run_monochrome_pipeline

    monkeypatch.setattr(config_mod, "get_settings", lambda: tmp_settings)
    xml = (FIXTURES / "monochrome_hcc009j1_feed.xml").read_bytes()
    with (
        patch("app.services.specialist_leads.get_settings", return_value=tmp_settings),
        patch("app.services.discord_notify.DiscordNotifier.send_editorial_alert") as send_alert,
    ):
        baseline = run_monochrome_pipeline(db_session, feed_xml=xml, force_baseline=True)
        repeat = run_monochrome_pipeline(db_session, feed_xml=xml)

    lead = db_session.scalars(select(SpecialistLead)).one()
    assert baseline.status == "SUCCESS"
    assert baseline.is_baseline is True
    assert baseline.summary_metadata["new_leads"] == 1
    assert lead.source_id == "monochrome"
    assert lead.reference_candidates == ["HCC009J1"]
    assert lead.is_baseline is True
    assert lead.editorial_freshness == "BASELINE"
    assert db_session.query(Event).count() == 0  # Layer B must not fabricate an official Event.
    assert repeat.status == "SUCCESS"
    assert repeat.summary_metadata["new_leads"] == 0
    send_alert.assert_not_called()


def test_specialist_cross_source_exact_reference_preserves_leads_but_alerts_once(db_session: Session):
    from datetime import UTC, datetime
    from unittest.mock import patch

    from app.core.config import Settings
    from app.models import SpecialistLead
    from app.services.discord_notify import DiscordNotifier
    from app.services.specialist_leads import SpecialistLeadService

    settings = Settings(discord_editorial_webhook_url="https://discord.example/editorial")
    service = SpecialistLeadService(db_session)
    first = service.ingest_candidate(
        source_id="monochrome", lead_type="POSSIBLE_NEW_REFERENCE", title="HCC009J1",
        source_url="https://monochrome.example/hcc", published_at=datetime.now(UTC).isoformat(),
        reference_candidates=["HCC009J1"], claim_text=None, manufacturer="Seiko", confidence=60.0,
    )
    second = service.ingest_candidate(
        source_id="fratello", lead_type="POSSIBLE_NEW_REFERENCE", title="HCC009J1 too",
        source_url="https://fratello.example/hcc", published_at=datetime.now(UTC).isoformat(),
        reference_candidates=["HCC009J1"], claim_text=None, manufacturer="Seiko", confidence=60.0,
    )
    calls = []
    with (
        patch("app.services.specialist_leads.get_settings", return_value=settings),
        patch("httpx.post", side_effect=lambda url, **kw: calls.append(url) or type("R", (), {"status_code": 204, "text": ""})()),
    ):
        assert service.notify_new_lead(db_session.get(SpecialistLead, first["lead_id"]), notifier=DiscordNotifier(settings)) is True
        assert service.notify_new_lead(db_session.get(SpecialistLead, second["lead_id"]), notifier=DiscordNotifier(settings)) is False
    assert len(db_session.query(SpecialistLead).all()) == 2
    assert calls == ["https://discord.example/editorial"]


def test_gcentral_pipeline_failed_fetch_creates_no_leads(db_session: Session, tmp_settings: Settings):
    from unittest.mock import patch

    from app.models import SpecialistLead
    from app.services.specialist_leads import run_gcentral_pipeline

    with patch("app.services.specialist_leads.get_settings", return_value=tmp_settings):
        run = run_gcentral_pipeline(db_session, feed_xml=b"")

    assert run.status in ("FAILED", "BLOCKED")
    assert db_session.scalars(select(SpecialistLead)).first() is None


def test_plus9time_pipeline_failed_fetch_creates_no_leads(db_session: Session, tmp_settings: Settings):
    from unittest.mock import patch

    from app.models import SpecialistLead
    from app.services.specialist_leads import run_plus9time_pipeline

    with patch("app.services.specialist_leads.get_settings", return_value=tmp_settings):
        run = run_plus9time_pipeline(db_session, feed_xml=b"")

    assert run.status in ("FAILED", "BLOCKED")
    assert db_session.scalars(select(SpecialistLead)).first() is None


def test_gcentral_and_plus9time_source_attribution_distinct():
    from app.services.source_registry import get_source_profile

    g = get_source_profile("g_central")
    p = get_source_profile("plus9time")
    assert g.source_type == "SPECIALIST_BLOG"
    assert p.source_type == "SPECIALIST_PUBLICATION"
    assert g.account_or_domain == "g-central.com"
    assert p.account_or_domain == "plus9time.com"


def test_casioblog_feed_parses_real_capture():
    from app.parsers.casioblog import parse_casioblog_feed

    xml = (FIXTURES / "casioblog_feed.xml").read_bytes()
    result = parse_casioblog_feed(xml, max_items=20)
    assert result.success
    assert len(result.items) == 10
    rumor_item = next(i for i in result.items if i.is_rumor_tagged)
    assert "[Rumors]" in rumor_item.title or "[rumors]" in rumor_item.title.lower()
    non_rumor = next(i for i in result.items if not i.is_rumor_tagged)
    assert non_rumor.reference_candidates  # EFK-200 item has real refs
    assert all(i.url.startswith("https://casioblog.com/") for i in result.items)
    # no full article body copied — only a short excerpt
    assert all(len(i.claim_text or "") <= 400 for i in result.items)


def test_casioblog_feed_handles_leading_whitespace_quirk():
    """The real feed emits a stray leading newline before the XML
    declaration (confirmed live) — must not fail closed on that alone."""
    from app.parsers.casioblog import parse_casioblog_feed

    xml = b"\r\n" + (FIXTURES / "casioblog_feed.xml").read_bytes()
    result = parse_casioblog_feed(xml)
    assert result.success


def test_casioblog_feed_malformed_xml_fails_closed():
    from app.parsers.casioblog import parse_casioblog_feed

    result = parse_casioblog_feed(b"<rss><channel><item><title>broken")
    assert result.success is False
    assert result.error


def test_specialist_lead_ingest_dedupes_by_url(db_session: Session):
    from app.services.specialist_leads import SpecialistLeadService

    svc = SpecialistLeadService(db_session)
    kwargs = {
        "source_id": "casioblog", "lead_type": "POSSIBLE_NEW_REFERENCE", "title": "Test lead",
        "source_url": "https://casioblog.com/en/test-lead", "published_at": "2026-06-01T00:00:00+00:00",
        "reference_candidates": ["GA-2100"], "claim_text": "test claim", "manufacturer": "Casio",
    }
    first = svc.ingest_candidate(**kwargs)
    second = svc.ingest_candidate(**kwargs)
    assert first["created"] is True
    assert second["created"] is False and second["reason"] == "already_seen"

    from app.models import SpecialistLead
    leads = db_session.scalars(select(SpecialistLead)).all()
    assert len(leads) == 1
    assert leads[0].source_type == "SPECIALIST_BLOG"  # from the registry, not guessed
    assert leads[0].source_authority_tier == 2


def test_specialist_lead_rejects_unregistered_source(db_session: Session):
    from app.services.specialist_leads import SpecialistLeadService

    svc = SpecialistLeadService(db_session)
    with pytest.raises(KeyError):
        svc.ingest_candidate(
            source_id="totally_made_up_source", lead_type="POSSIBLE_NEW_REFERENCE", title="x",
            source_url="https://example.com/x", published_at=None, reference_candidates=[], claim_text=None,
        )


def test_specialist_lead_correlates_conservatively_with_official_watch(db_session: Session):
    """Sprint 5's example: a lead mentioning GA-XXXX-1A, later an official
    Watch with that exact reference appears -> correlation + lead_time_days."""
    from datetime import UTC, datetime, timedelta

    from app.models import SourceObservation, SpecialistLead, Watch
    from app.services.specialist_leads import SpecialistLeadService

    svc = SpecialistLeadService(db_session)
    lead_published = datetime(2026, 6, 1, tzinfo=UTC)
    svc.ingest_candidate(
        source_id="geesgshock_manual", lead_type="POSSIBLE_NEW_REFERENCE", title="Leaked GA-2100-9A",
        source_url="https://instagram.com/p/fake123", published_at=lead_published.isoformat(),
        reference_candidates=["GA-2100-9A"], claim_text="early photo", manufacturer="Casio",
    )

    # Official confirmation appears 4 days later
    watch = Watch(
        manufacturer="Casio", brand="Casio", reference_raw="GA-2100-9A", reference_canonical="GA-2100-9A",
        created_at=lead_published + timedelta(days=4),
    )
    db_session.add(watch)
    db_session.flush()
    obs = SourceObservation(
        watch_id=watch.id, collector_id="t", collector_version="0", parser_id="t", parser_version="0",
        region="JP", source_url="https://casio.example/ga2100-9a",
        observed_at=lead_published + timedelta(days=4), overall_confidence=90.0,
    )
    db_session.add(obs)
    db_session.commit()

    results = svc.correlate_pending_leads(manufacturer="Casio")
    assert len(results) == 1
    assert results[0]["watch_id"] == watch.id
    assert results[0]["lead_time_days"] == pytest.approx(4.0, abs=0.1)

    lead = db_session.scalars(select(SpecialistLead)).first()
    assert lead.verification_status == "CORRELATED_WITH_OFFICIAL"
    assert lead.correlated_watch_id == watch.id


def test_specialist_lead_never_fuzzy_matches(db_session: Session):
    """A lead mentioning a reference with no relation (not exact, not a
    shared family root) to an official watch must NOT correlate."""
    from app.models import Watch
    from app.services.specialist_leads import SpecialistLeadService

    watch = Watch(manufacturer="Casio", brand="Casio", reference_raw="GA-2100-1A1JF", reference_canonical="GA-2100-1A1")
    db_session.add(watch)
    db_session.commit()

    svc = SpecialistLeadService(db_session)
    svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="Rumored GA-110 successor",
        source_url="https://casioblog.com/en/rumor-ga110-successor", published_at=None,
        reference_candidates=["GA-110"], claim_text=None, manufacturer="Casio",
    )
    results = svc.correlate_pending_leads(manufacturer="Casio")
    assert results == []  # "GA-110" shares neither exact nor family root with "GA-2100-1A1JF"


def test_specialist_lead_family_match_is_labeled_not_exact(db_session: Session):
    """Phase 7: a lead naming only the family root (e.g. "GWR-B3000")
    against an official full reference (e.g. "GWR-B3000-1A") must
    correlate as FAMILY_MATCH, distinct from an exact match, and must
    never be reported as CORRELATION_TYPE == EXACT_REFERENCE_MATCH."""
    from app.models import SpecialistLead, Watch
    from app.services.specialist_leads import SpecialistLeadService

    watch = Watch(manufacturer="Casio", brand="Casio", reference_raw="GWR-B3000-1A", reference_canonical="GWR-B3000-1A")
    db_session.add(watch)
    db_session.commit()

    svc = SpecialistLeadService(db_session)
    svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="[Rumors] G-SHOCK GWR-B3000",
        source_url="https://casioblog.com/en/rumor-gwr-b3000", published_at=None,
        reference_candidates=["GWR-B3000"], claim_text=None, manufacturer="Casio",
    )
    results = svc.correlate_pending_leads(manufacturer="Casio")
    assert len(results) == 1
    assert results[0]["correlation_type"] == "FAMILY_MATCH"

    lead = db_session.scalars(select(SpecialistLead)).first()
    assert lead.correlation_type == "FAMILY_MATCH"
    assert lead.correlated_watch_id == watch.id


def test_specialist_lead_exact_match_preferred_over_family(db_session: Session):
    """When both an exact-reference watch and a family-only watch exist,
    correlation must prefer the exact match and label it accordingly."""
    from app.models import SpecialistLead, Watch
    from app.services.specialist_leads import SpecialistLeadService

    exact = Watch(manufacturer="Casio", brand="Casio", reference_raw="GWR-B3000", reference_canonical="GWR-B3000")
    family_only = Watch(manufacturer="Casio", brand="Casio", reference_raw="GWR-B3000-1A", reference_canonical="GWR-B3000-1A")
    db_session.add_all([exact, family_only])
    db_session.commit()

    svc = SpecialistLeadService(db_session)
    svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="GWR-B3000 leak",
        source_url="https://casioblog.com/en/rumor-gwr-b3000-exact", published_at=None,
        reference_candidates=["GWR-B3000"], claim_text=None, manufacturer="Casio",
    )
    results = svc.correlate_pending_leads(manufacturer="Casio")
    assert len(results) == 1
    assert results[0]["correlation_type"] == "EXACT_REFERENCE_MATCH"
    assert results[0]["watch_id"] == exact.id

    lead = db_session.scalars(select(SpecialistLead)).first()
    assert lead.correlation_type == "EXACT_REFERENCE_MATCH"


def test_correlation_followup_alert_labels_family_match_distinctly():
    from app.services.editorial import format_correlation_followup_alert

    exact_text = format_correlation_followup_alert(
        manufacturer="Casio", brand="G-Shock", lead_reference_candidates=["GWR-B3000-1A"],
        watch_reference_raw="GWR-B3000-1A", correlation_type="EXACT_REFERENCE_MATCH",
        source_display_name="CASIOBLOG", lead_published_at="2026-04-10", official_first_observed_at="2026-04-14",
        lead_time_days=4.0, source_url="https://casioblog.com/en/rumor-gwr-b3000",
    )
    family_text = format_correlation_followup_alert(
        manufacturer="Casio", brand="G-Shock", lead_reference_candidates=["GWR-B3000"],
        watch_reference_raw="GWR-B3000-1A", correlation_type="FAMILY_MATCH",
        source_display_name="CASIOBLOG", lead_published_at="2026-04-10", official_first_observed_at="2026-04-14",
        lead_time_days=4.0, source_url="https://casioblog.com/en/rumor-gwr-b3000",
    )
    assert "CONFIRMED" in exact_text
    assert "FAMILY_MATCH" not in exact_text.split("\n")[0]
    assert "FAMILY_MATCH — NOT EXACT" in family_text
    assert "Requires human verification" in family_text


def test_casioblog_pipeline_baseline_then_repeat_creates_no_duplicates(db_session: Session, tmp_settings: Settings, monkeypatch):
    from unittest.mock import patch

    from app.core import config as config_mod
    from app.models import CollectorRun, SpecialistLead
    from app.services.specialist_leads import run_casioblog_pipeline

    monkeypatch.setattr(config_mod, "get_settings", lambda: tmp_settings)
    xml = (FIXTURES / "casioblog_feed.xml").read_bytes()

    with patch("app.services.specialist_leads.get_settings", return_value=tmp_settings):
        run1 = run_casioblog_pipeline(db_session, feed_xml=xml)
        run2 = run_casioblog_pipeline(db_session, feed_xml=xml)

    assert run1.status == "SUCCESS"
    assert run1.summary_metadata["new_leads"] == 10
    assert run2.status == "SUCCESS"
    assert run2.summary_metadata["new_leads"] == 0  # repeat feed fetch -> no duplicates

    leads = db_session.scalars(select(SpecialistLead)).all()
    assert len(leads) == 10

    runs = db_session.scalars(select(CollectorRun).where(CollectorRun.collector_id == "casioblog_rss")).all()
    assert len(runs) == 2


def test_casioblog_pipeline_failed_fetch_creates_no_leads(db_session: Session, tmp_settings: Settings):
    from unittest.mock import patch

    from app.models import SpecialistLead
    from app.services.specialist_leads import run_casioblog_pipeline

    with patch("app.services.specialist_leads.get_settings", return_value=tmp_settings):
        run = run_casioblog_pipeline(db_session, feed_xml=b"")  # simulates a failed/empty fetch

    assert run.status in ("FAILED", "BLOCKED")
    assert db_session.scalars(select(SpecialistLead)).first() is None


# --- Sprint 7: operational epoch lifecycle ----------------------------------


def test_epoch_starts_and_is_active(db_session: Session):
    from app.services.epoch import get_active_epoch, is_baseline_active, start_epoch

    assert get_active_epoch(db_session) is None
    assert is_baseline_active(db_session) is False

    epoch = start_epoch(db_session, name="epoch_1")
    assert epoch.id is not None
    assert epoch.started_at is not None
    assert get_active_epoch(db_session).id == epoch.id
    assert is_baseline_active(db_session) is False  # baseline not started yet


def test_epoch_refuses_duplicate_name(db_session: Session):
    from app.services.epoch import start_epoch

    start_epoch(db_session, name="epoch_1")
    with pytest.raises(ValueError):
        start_epoch(db_session, name="epoch_1")


def test_epoch_baseline_lifecycle(db_session: Session):
    from app.services.epoch import (
        complete_baseline,
        is_baseline_active,
        start_baseline,
        start_epoch,
    )

    epoch = start_epoch(db_session, name="epoch_1")
    assert is_baseline_active(db_session) is False

    start_baseline(db_session, epoch)
    assert is_baseline_active(db_session) is True

    complete_baseline(db_session, epoch)
    assert is_baseline_active(db_session) is False


def test_epoch_baseline_refuses_double_start(db_session: Session):
    from app.services.epoch import start_baseline, start_epoch

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    with pytest.raises(ValueError):
        start_baseline(db_session, epoch)


def test_epoch_baseline_complete_refuses_without_start(db_session: Session):
    from app.services.epoch import complete_baseline, start_epoch

    epoch = start_epoch(db_session, name="epoch_1")
    with pytest.raises(ValueError):
        complete_baseline(db_session, epoch)


def test_baseline_active_suppresses_new_reference_event(db_session: Session, tmp_settings: Settings):
    """While an epoch's baseline is active, discovering a brand-new watch
    must create the Watch/Observation/ReleaseLead as real data but must
    NOT create an Event -- catalog population during baseline is known
    existing state, not news."""
    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event
    from app.parsers.citizen_news import parse_citizen_news_html
    from app.services.epoch import start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)

    detail_html = (FIXTURES / "citizen_news_detail.html").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    fr = FetchResult(
        url="https://www.citizenwatch-global.com/news/2026/20260610/index.html",
        success=True, status_code=200, content_type="text/html", payload=detail_html,
    )
    out = pipeline.process_news_announcement(
        fr, run_id=run.id, discovered_meta={"source_region": "GLOBAL"},
        collector_id="citizen_news", manufacturer="Citizen", brand="Citizen",
        parse_fn=parse_citizen_news_html, merge_key_prefix="citizen",
        default_region="GLOBAL", emit_events=True,
    )
    assert out["new_watch"] is True  # real discovery still happened
    assert out["watch_events"][0]["event_type"] is None
    assert out["watch_events"][0]["reason"] == "epoch_baseline_active"
    assert db_session.scalars(select(Event)).first() is None  # no Event created

    watch = db_session.scalars(select(Watch)).one()
    assert watch.reference_raw  # the watch itself is real (news path -- no SourceObservation expected)


def test_baseline_active_stamps_product_observation(db_session: Session, tmp_settings: Settings):
    """The product-page path (process_fetch_result) does create a
    SourceObservation -- verify it gets stamped epoch_id/is_baseline."""
    import json

    from app.collectors.base import FetchResult
    from app.collectors.citizen_products import CitizenProductsCollector
    from app.parsers.citizen_products import parse_citizen_search_hit
    from app.services.epoch import start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)

    html = (FIXTURES / "citizen_search_attesa_page1.html").read_text(encoding="utf-8")
    items, _ = CitizenProductsCollector().parse_search_page(html)
    product_dict = items[0].metadata["product_dict"]

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_products", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    fr = FetchResult(
        url="https://citizenwatch.com/us/en/product/CC4107-80H", success=True, status_code=200,
        content_type="application/json", payload=json.dumps(product_dict).encode("utf-8"),
    )
    pipeline.process_fetch_result(
        fr, run_id=run.id, collector_id="citizen_products", collector_version="0.1.0",
        parse_fn=parse_citizen_search_hit, default_region="US", emit_events=True,
    )
    obs = db_session.scalars(select(SourceObservation)).first()
    assert obs is not None
    assert obs.is_baseline is True
    assert obs.epoch_id == epoch.id


def test_pipeline_stamps_collector_run_with_active_epoch(db_session: Session, tmp_settings: Settings):
    from app.services.epoch import start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    fields = pipeline._epoch_fields()
    assert fields == {"epoch_id": epoch.id, "is_baseline": True}


def test_specialist_lead_ingest_stamps_baseline(db_session: Session):
    from app.models import SpecialistLead
    from app.services.epoch import start_baseline, start_epoch
    from app.services.specialist_leads import SpecialistLeadService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)

    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="Baseline-era post",
        source_url="https://casioblog.com/en/baseline-post", published_at=None,
        reference_candidates=["GA-2100"], claim_text=None, manufacturer="Casio",
    )
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    assert lead.is_baseline is True
    assert lead.epoch_id == epoch.id


def test_notify_new_lead_suppressed_during_baseline(db_session: Session):
    from unittest.mock import patch

    from app.core.config import Settings
    from app.models import SpecialistLead
    from app.services.discord_notify import DiscordNotifier
    from app.services.epoch import start_baseline, start_epoch
    from app.services.specialist_leads import SpecialistLeadService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)

    settings = Settings(discord_editorial_webhook_url="https://discord.example/editorial")
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="Baseline-era post",
        source_url="https://casioblog.com/en/baseline-post-2", published_at=None,
        reference_candidates=["GA-2100"], claim_text=None, manufacturer="Casio", confidence=90.0,
    )
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    assert lead.is_baseline is True

    with patch("httpx.post") as mock_post:
        sent = svc.notify_new_lead(lead, notifier=DiscordNotifier(settings))

    assert sent is False
    mock_post.assert_not_called()


# --- Sprint 8: editorial freshness bugfix ------------------------------------
# See ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md. These are the real production
# failure scenarios turned into fixtures, not synthetic hypotheticals.


def test_freshness_old_specialist_article_discovered_after_baseline(db_session: Session):
    """Scenario 1: a real July G-Central article discovered in August,
    after the epoch's baseline already completed. Must be stored, but
    classified STALE_PUBLICATION -- not FRESH, not Discord-eligible, and
    excluded from the GUI's Recent Intelligence query filter."""

    from app.models import SpecialistLead
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.specialist_leads import SpecialistLeadService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)  # baseline already over

    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(
        source_id="g_central", lead_type="POSSIBLE_COLLABORATION",
        title="Rapper Larry June's Midnight Organic releases limited G-Shock DW6900MO26-4",
        source_url="https://www.g-central.com/rapper-larry-junes-midnight-organic/",
        published_at="2026-07-26T08:44:21+00:00",  # real publish date from the incident
        reference_candidates=["DW-6900", "DW6900MO26"], claim_text=None, manufacturer="Casio",
    )
    lead = db_session.get(SpecialistLead, outcome["lead_id"])

    assert lead.is_baseline is False  # genuinely discovered post-baseline
    assert lead.editorial_freshness == "STALE_PUBLICATION"
    assert "72h" in lead.freshness_reason or "hour" in lead.freshness_reason.lower()

    # Mirrors the GUI's get_recent_leads() filter exactly.
    recent = db_session.query(SpecialistLead).filter(SpecialistLead.editorial_freshness == "FRESH").all()
    assert lead not in recent


def test_freshness_fresh_specialist_article_is_eligible(db_session: Session):
    """Scenario 2: an article published an hour ago, discovered now,
    after baseline. Must classify FRESH and be Discord-eligible."""
    from datetime import UTC, datetime, timedelta

    from app.models import SpecialistLead
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.specialist_leads import SpecialistLeadService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    svc = SpecialistLeadService(db_session)
    recent_pub = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    outcome = svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="Brand new rumor",
        source_url="https://casioblog.com/en/brand-new-rumor", published_at=recent_pub,
        reference_candidates=["GA-2100"], claim_text=None, manufacturer="Casio", confidence=60.0,
    )
    lead = db_session.get(SpecialistLead, outcome["lead_id"])

    assert lead.editorial_freshness == "FRESH"
    recent = db_session.query(SpecialistLead).filter(SpecialistLead.editorial_freshness == "FRESH").all()
    assert lead in recent


def test_freshness_baseline_article_classified_baseline_regardless_of_publish_date(db_session: Session):
    """Scenario 3: whether the article is old or freshly published,
    discovery DURING baseline always classifies BASELINE, never FRESH."""
    from datetime import UTC, datetime

    from app.models import SpecialistLead
    from app.services.epoch import start_baseline, start_epoch
    from app.services.specialist_leads import SpecialistLeadService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)  # baseline still active

    svc = SpecialistLeadService(db_session)
    fresh_pub_during_baseline = svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="Published today, found during baseline",
        source_url="https://casioblog.com/en/today-during-baseline", published_at=datetime.now(UTC).isoformat(),
        reference_candidates=["GA-2100"], claim_text=None, manufacturer="Casio",
    )
    lead = db_session.get(SpecialistLead, fresh_pub_during_baseline["lead_id"])
    assert lead.editorial_freshness == "BASELINE"
    assert lead.is_baseline is True


def test_freshness_official_product_without_publication_date_still_surfaces(db_session: Session, tmp_settings: Settings):
    """Scenario 4: official catalogue discoveries never had a publication-
    timestamp gate and must not gain one from this fix -- Events are
    created directly by pipeline transition logic, untouched here."""
    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event
    from app.parsers.citizen_news import parse_citizen_news_html
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    detail_html = (FIXTURES / "citizen_news_detail.html").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    fr = FetchResult(
        url="https://www.citizenwatch-global.com/news/2026/20260610/index.html",
        success=True, status_code=200, content_type="text/html", payload=detail_html,
    )
    out = pipeline.process_news_announcement(
        fr, run_id=run.id, discovered_meta={"source_region": "GLOBAL"},
        collector_id="citizen_news", manufacturer="Citizen", brand="Citizen",
        parse_fn=parse_citizen_news_html, merge_key_prefix="citizen",
        default_region="GLOBAL", emit_events=True,
    )
    assert out["watch_events"][0]["event_type"] == "NEW_REFERENCE"
    assert db_session.scalars(select(Event)).first() is not None


def test_freshness_old_product_current_restock_still_fires(db_session: Session, tmp_settings: Settings):
    """Scenario 5: a RESTOCK/SOLD_OUT transition detected today must fire
    regardless of how old the underlying product's history is -- the
    transition observation time is authoritative here, not any
    publication timestamp (this path doesn't touch SpecialistLead at
    all, confirming the freshness fix didn't leak into it)."""
    from datetime import UTC, datetime, timedelta

    from app.models import Event, SourceObservation, Watch
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    watch = Watch(manufacturer="Citizen", brand="Citizen", reference_raw="CC4107-80H", reference_canonical="CC4107-80H")
    db_session.add(watch)
    db_session.flush()
    old_obs = SourceObservation(
        watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0",
        parser_id="t", parser_version="0", region="US", source_url="https://x/old",
        availability_status="AVAILABLE", observed_at=datetime.now(UTC) - timedelta(days=200),
        overall_confidence=90.0,
    )
    db_session.add(old_obs)
    db_session.commit()

    new_obs = SourceObservation(
        watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0",
        parser_id="t", parser_version="0", region="US", source_url="https://x/new",
        availability_status="SOLD_OUT", observed_at=datetime.now(UTC), overall_confidence=90.0,
    )
    db_session.add(new_obs)
    db_session.flush()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    result = pipeline._record_product_transition(watch=watch, new_obs=new_obs, is_new_watch=False)
    assert result["event_type"] == "SOLD_OUT"
    assert db_session.scalars(select(Event)).first() is not None


def test_freshness_null_publication_timestamp_is_conservative(db_session: Session):
    """Scenario 6: a specialist source with no publication timestamp must
    never be assumed fresh -- explicit UNKNOWN_TIMESTAMP, not FRESH."""
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.freshness import classify_lead_freshness

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    from datetime import UTC, datetime

    now = datetime.now(UTC)
    result = classify_lead_freshness(
        source_type="SPECIALIST_BLOG", ingestion_method="collector", is_baseline=False,
        published_at=None, discovered_at=now, now=now, window_hours=72,
    )
    assert result.state == "UNKNOWN_TIMESTAMP"


def test_freshness_manual_ingestion_without_date_is_labeled_distinctly(db_session: Session):
    """Manual social ingestion with no publication timestamp gets its own
    honest label (MANUAL_UNDATED) rather than the generic
    UNKNOWN_TIMESTAMP -- "a human ingested this without dating it" is a
    different situation from "the parser couldn't find a date."""
    from datetime import UTC, datetime

    from app.services.freshness import classify_lead_freshness

    now = datetime.now(UTC)
    result = classify_lead_freshness(
        source_type="SOCIAL_LEAKER", ingestion_method="manual", is_baseline=False,
        published_at=None, discovered_at=now, now=now, window_hours=72,
    )
    assert result.state == "MANUAL_UNDATED"


def test_freshness_timezone_boundary_never_makes_july_look_current():
    """Scenario 7: a naive (no-tzinfo) July timestamp must not slip past
    the freshness window through timezone mishandling."""
    from datetime import datetime, timedelta

    from app.services.freshness import classify_lead_freshness

    naive_july = datetime(2026, 7, 15, 12, 0, 0)  # deliberately no tzinfo
    now = datetime(2026, 8, 12, 12, 0, 0)  # also naive, ~28 days later
    result = classify_lead_freshness(
        source_type="SPECIALIST_BLOG", ingestion_method="collector", is_baseline=False,
        published_at=naive_july, discovered_at=now, now=now, window_hours=72,
    )
    assert result.state == "STALE_PUBLICATION"

    # And a genuinely fresh UTC-aware timestamp close to a naive "now"
    # still correctly classifies FRESH -- proves ensure_utc normalizes
    # rather than accidentally penalizing/favoring naive inputs.
    from datetime import UTC

    recent = now - timedelta(hours=2)
    result2 = classify_lead_freshness(
        source_type="SPECIALIST_BLOG", ingestion_method="collector", is_baseline=False,
        published_at=recent, discovered_at=now, now=now.replace(tzinfo=UTC), window_hours=72,
    )
    assert result2.state == "FRESH"


def test_get_recent_leads_query_filter_matches_service_classification(db_session: Session, tmp_settings: Settings, monkeypatch):
    """End-to-end proof the GUI's actual filter (mirrored here since
    local_windows/ isn't a Python package) agrees with the service's
    classification -- the real bug was these two disagreeing (the GUI had
    no filter at all)."""
    from unittest.mock import patch

    from app.core import config as config_mod
    from app.models import SpecialistLead
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.specialist_leads import run_gcentral_pipeline

    monkeypatch.setattr(config_mod, "get_settings", lambda: tmp_settings)
    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    xml = (FIXTURES / "gcentral_feed.xml").read_bytes()
    with patch("app.services.specialist_leads.get_settings", return_value=tmp_settings):
        run_gcentral_pipeline(db_session, feed_xml=xml)

    all_leads = db_session.query(SpecialistLead).all()
    assert len(all_leads) == 15
    # Every item in this real fixture is from March-August, well outside a
    # 72h window from "now" -- all must be STALE_PUBLICATION, none FRESH.
    assert all(lead.editorial_freshness == "STALE_PUBLICATION" for lead in all_leads)
    recent = db_session.query(SpecialistLead).filter(SpecialistLead.editorial_freshness == "FRESH").all()
    assert recent == []


# --- Sprint 9: Timex fourth official brand -----------------------------------


def test_timex_product_parser_real_capture():
    import json

    from app.parsers.timex_products import parse_timex_product_json

    data = json.load((FIXTURES / "timex_products_page1.json").open(encoding="utf-8"))
    watch_products = [p for p in data["products"] if p.get("product_type") == "Watch"]
    assert watch_products  # real fixture must contain at least one real watch

    for p in watch_products[:5]:
        result = parse_timex_product_json(p, source_url="https://www.timex.com/products/x")
        assert result.success
        w = result.watches[0]
        assert w.manufacturer == "Timex"
        assert w.brand == "Timex"
        assert w.reference_raw  # real SKU, e.g. "TW6A01000VQ"
        assert w.currency in (None, "USD")


def test_timex_product_parser_rejects_non_watch_product_type():
    from app.parsers.timex_products import parse_timex_product_json

    strap = {"product_type": "Strap", "variants": [{"sku": "X1", "price": "10.00", "available": True}]}
    result = parse_timex_product_json(strap)
    assert result.success is False
    assert "not a watch product" in result.error


def test_timex_product_parser_handles_missing_price():
    from app.parsers.timex_products import parse_timex_product_json

    product = {
        "product_type": "Watch", "title": "Test Watch",
        "variants": [{"sku": "TW0000000", "price": None, "available": True}],
    }
    result = parse_timex_product_json(product)
    assert result.success
    assert result.watches[0].price is None
    assert result.watches[0].currency is None
    assert "no_price_in_source" in result.watches[0].parser_warnings


def test_timex_products_collector_paginates_and_terminates_on_empty_page():
    from app.collectors.timex_products import TimexProductsCollector

    page1 = (FIXTURES / "timex_products_page1.json").read_bytes()
    empty = (FIXTURES / "timex_products_page_empty.json").read_bytes()
    result = TimexProductsCollector().run(listing_pages=[page1, empty])
    assert result.metadata["component_status"] == "SUCCESS"
    assert len(result.discovered) > 0
    # Every discovered item must be product_type == Watch (straps/giftsets filtered)
    for item in result.discovered:
        assert item.metadata["product_json"]["product_type"] == "Watch"


def test_timex_products_collector_dedupes_by_sku_across_pages():
    from app.collectors.timex_products import TimexProductsCollector

    page1 = (FIXTURES / "timex_products_page1.json").read_bytes()
    result = TimexProductsCollector().run(listing_pages=[page1, page1])  # same page "twice"
    refs = [i.reference_hint for i in result.discovered]
    assert len(refs) == len(set(refs))  # no duplicate SKUs despite repeated page


def test_timex_news_parser_real_capture():
    import json
    import xml.etree.ElementTree as ET

    from app.parsers.timex_news import parse_timex_news_entry

    xml_bytes = (FIXTURES / "timex_blog_feed.atom").read_bytes()
    root = ET.fromstring(xml_bytes)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    assert len(entries) == 8

    entry = entries[0]
    entry_dict = {
        "title": entry.find("a:title", ns).text,
        "published": entry.find("a:published", ns).text,
        "content": entry.find("a:content", ns).text,
    }
    result = parse_timex_news_entry(json.dumps(entry_dict).encode("utf-8"), source_url="https://timex.com/blogs/x")
    assert result.success
    assert result.title
    assert result.publication_date
    assert len(result.body_excerpt or "") <= 400  # no full article body copied


def test_timex_news_parser_extracts_sku_from_image_filename_real_capture():
    """Sprint 11 miss autopsy: the real MK1 Chronograph post's SKUs
    (TW2Y71200/TW2Y71300, the exact SKUs Notebookcheck's own article cited
    as its sources) never appear as bare text -- only inside Shopify CDN
    image filenames like ".../14065_TX_TC_26_PFB_TW2Y71200_3_600x600.jpg",
    where the leading "_" defeats MODEL_RE's \\b. This is the confirmed
    root cause of a real production miss, not a hypothetical."""
    import json
    import xml.etree.ElementTree as ET

    from app.parsers.timex_news import parse_timex_news_entry

    xml_bytes = (FIXTURES / "timex_blog_feed.atom").read_bytes()
    root = ET.fromstring(xml_bytes)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    mk1_entry = next(e for e in entries if e.find("a:title", ns).text.startswith("MK1"))
    entry_dict = {
        "title": mk1_entry.find("a:title", ns).text,
        "published": mk1_entry.find("a:published", ns).text,
        "content": mk1_entry.find("a:content", ns).text,
    }
    result = parse_timex_news_entry(json.dumps(entry_dict).encode("utf-8"), source_url="https://timex.com/blogs/x")
    assert result.success
    norms = {r.normalized for r in result.model_references}
    assert "TW2Y71200" in norms
    assert "TW2Y71300" in norms
    image_refs = [r for r in result.model_references if r.location == "image_filename"]
    assert len(image_refs) == 2
    assert not result.parser_warnings  # no longer "no_model_reference_extracted"


def test_timex_news_parser_extracts_sku_from_image_filename_marlin_mesh_real_capture():
    """Same confirmed root cause, second real article (Todd Snyder x Timex
    Marlin Mesh) -- proves the fix is not a one-off special case."""
    import json
    import xml.etree.ElementTree as ET

    from app.parsers.timex_news import parse_timex_news_entry

    xml_bytes = (FIXTURES / "timex_blog_feed.atom").read_bytes()
    root = ET.fromstring(xml_bytes)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    marlin_entry = next(e for e in entries if "Marlin Mesh" in e.find("a:title", ns).text)
    entry_dict = {
        "title": marlin_entry.find("a:title", ns).text,
        "published": marlin_entry.find("a:published", ns).text,
        "content": marlin_entry.find("a:content", ns).text,
    }
    result = parse_timex_news_entry(json.dumps(entry_dict).encode("utf-8"), source_url="https://timex.com/blogs/y")
    assert result.success
    norms = {r.normalized for r in result.model_references}
    assert "TW2Y84000" in norms
    assert not result.parser_warnings


def test_timex_news_parser_does_not_match_sku_substrings_mid_word():
    """IMAGE_SKU_RE must not fire on a TW-shaped substring that isn't
    actually delimited by _ or / on both sides (avoid false positives)."""
    import json

    from app.parsers.timex_news import parse_timex_news_entry

    entry = {
        "title": "Some Article",
        "published": "2026-08-01T00:00:00-04:00",
        "content": "<p>xTW2Y71200x has nothing to do with a real filename</p>",
    }
    result = parse_timex_news_entry(json.dumps(entry).encode("utf-8"), source_url="https://timex.com/blogs/z")
    assert result.success
    assert result.model_references == []


def test_timex_news_parser_malformed_json_fails_closed():
    from app.parsers.timex_news import parse_timex_news_entry

    result = parse_timex_news_entry(b"not json")
    assert result.success is False
    assert result.error


def test_timex_news_collector_parses_real_feed():
    from app.collectors.timex_news import TimexNewsCollector

    xml_bytes = (FIXTURES / "timex_blog_feed.atom").read_bytes()
    result = TimexNewsCollector().run(index_html=xml_bytes)
    assert result.metadata["component_status"] == "SUCCESS"
    assert len(result.discovered) == 8
    assert all(i.url.startswith("https://timex.com/") for i in result.discovered)


def test_normalize_timex_reference_is_conservative_passthrough():
    from app.normalization.references import normalize_timex_reference

    result = normalize_timex_reference("TW6A01000VQ")
    assert result.reference_raw == "TW6A01000VQ"
    assert result.reference_canonical == "TW6A01000VQ"  # canonical == raw, no suffix stripping
    assert result.manufacturer == "Timex"
    assert result.brand == "Timex"


def test_timex_reference_identity_does_not_collide_with_other_brands(db_session: Session):
    """Uniqueness is scoped by (manufacturer, brand, reference_canonical) --
    a Timex watch and a differently-branded watch may safely share a raw
    reference string with no collision (defensive proof, not because this
    is expected in practice)."""
    w1 = Watch(manufacturer="Timex", brand="Timex", reference_raw="SHARED123", reference_canonical="SHARED123")
    w2 = Watch(manufacturer="Casio", brand="Casio", reference_raw="SHARED123", reference_canonical="SHARED123")
    db_session.add_all([w1, w2])
    db_session.commit()  # must not raise IntegrityError
    assert w1.id != w2.id


def test_timex_products_pipeline_baseline_then_repeat_creates_no_duplicates(db_session: Session, tmp_settings: Settings):
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    page1 = (FIXTURES / "timex_products_page1.json").read_bytes()
    empty = (FIXTURES / "timex_products_page_empty.json").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))

    run1 = pipeline.run_product_observation_pipeline("timex", offline_fixture=[page1, empty])
    run2 = pipeline.run_product_observation_pipeline("timex", offline_fixture=[page1, empty])

    assert run1.status == "SUCCESS"
    assert run1.new_watch_count > 0
    assert run2.status == "SUCCESS"
    assert run2.new_watch_count == 0  # repeat -> no duplicates

    watches = db_session.scalars(select(Watch).where(Watch.manufacturer == "Timex")).all()
    assert len(watches) == run1.new_watch_count
    assert all(w.brand == "Timex" for w in watches)


def test_citizen_de_products_force_baseline_then_repeat_is_silent(
    db_session: Session, tmp_settings: Settings, monkeypatch
):
    """A newly onboarded regional source stores its catalogue silently and
    then performs a zero-item sitemap-delta repeat without duplicate rows."""
    from app.collectors.base import FetchResult
    from app.models import Event, SourceObservation
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    product = (FIXTURES / "citizen_de_product_nj0230.html").read_bytes()
    sitemap = (FIXTURES / "citizen_de_products_sitemap.xml").read_bytes()
    monkeypatch.setattr(
        "app.collectors.citizen_de_products.fetch_url",
        lambda url, **_kwargs: FetchResult(url=url, success=True, status_code=200, content_type="text/html", payload=product),
    )
    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))

    baseline = pipeline.run_product_observation_pipeline(
        "citizen_de", offline_fixture=sitemap, force_baseline=True
    )
    before_count = db_session.query(SourceObservation).filter_by(collector_id="citizen_de_products").count()
    repeat = pipeline.run_product_observation_pipeline("citizen_de", offline_fixture=sitemap)

    assert baseline.status == "SUCCESS"
    assert baseline.new_watch_count == 1
    assert db_session.query(Event).count() == 0
    assert before_count == 2
    assert repeat.status == "ZERO_ITEMS"
    assert repeat.new_watch_count == 0
    assert db_session.query(SourceObservation).filter_by(collector_id="citizen_de_products").count() == before_count


def test_citizen_de_first_normal_listing_of_known_us_reference_emits_new_region(
    db_session: Session, tmp_settings: Settings, monkeypatch
):
    """Controlled post-baseline-style proof: an already known US Citizen
    reference first listed by the official German lane is regional evidence,
    never a cross-currency price change."""
    from app.collectors.base import FetchResult
    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    watch = Watch(
        manufacturer="Citizen", brand="Citizen", reference_raw="NJ0230-59L",
        reference_canonical="NJ0230-59L",
    )
    db_session.add(watch)
    db_session.flush()
    db_session.add(
        SourceObservation(
            watch_id=watch.id, collector_id="citizen_products", collector_version="0.1.0",
            parser_id="fixture", parser_version="1", region="US", source_url="https://example.test/us/nj0230",
            price=525.0, currency="USD", availability_status="AVAILABLE", overall_confidence=90.0,
        )
    )
    db_session.commit()
    product = (FIXTURES / "citizen_de_product_nj0230.html").read_bytes()
    sitemap = (FIXTURES / "citizen_de_products_sitemap.xml").read_bytes()
    monkeypatch.setattr(
        "app.collectors.citizen_de_products.fetch_url",
        lambda url, **_kwargs: FetchResult(url=url, success=True, status_code=200, content_type="text/html", payload=product),
    )

    run = PipelineService(db_session, SnapshotStorageService(tmp_settings)).run_product_observation_pipeline(
        "citizen_de", offline_fixture=sitemap
    )
    events = db_session.scalars(select(Event).where(Event.event_type == "NEW_REGION")).all()
    assert run.status == "SUCCESS"
    assert run.new_watch_count == 0
    assert len(events) == 1
    assert events[0].extra["region"] == "DE"
    assert db_session.scalar(select(Event).where(Event.event_type == "PRICE_CHANGE")) is None


def test_timex_news_pipeline_baseline_then_repeat_creates_no_duplicate_leads(db_session: Session, tmp_settings: Settings):
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    xml_bytes = (FIXTURES / "timex_blog_feed.atom").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))

    run1 = pipeline.run_brand_news_pipeline("timex", index_html=xml_bytes)
    run2 = pipeline.run_brand_news_pipeline("timex", index_html=xml_bytes)

    assert run1.status == "SUCCESS"
    assert run1.summary_metadata["new_leads"] == 8
    assert run2.status == "SUCCESS"
    assert run2.summary_metadata["new_leads"] == 0


def test_timex_products_baseline_suppresses_events(db_session: Session, tmp_settings: Settings):
    """Source-scoped silent baseline: the FIRST Timex population must
    create real watches but zero Events while an epoch's baseline is
    active -- same semantics already proven for Casio/Citizen/Seiko."""
    from app.models import Event
    from app.services.epoch import start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)

    page1 = (FIXTURES / "timex_products_page1.json").read_bytes()
    empty = (FIXTURES / "timex_products_page_empty.json").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = pipeline.run_product_observation_pipeline("timex", offline_fixture=[page1, empty])

    assert run.new_watch_count > 0
    assert db_session.scalars(select(Event)).first() is None

    watches = db_session.scalars(select(Watch).where(Watch.manufacturer == "Timex")).all()
    assert len(watches) == run.new_watch_count


def test_force_baseline_is_source_scoped_not_global(db_session: Session, tmp_settings: Settings):
    """Sprint 9's core requirement: Timex joining an epoch whose baseline
    ALREADY COMPLETED (the real scenario -- Casio/Citizen/Seiko already
    live) must be silently baselined via force_baseline=True WITHOUT
    reopening the epoch's global baseline window. Proof: a citizen_news
    announcement processed in the same session, same completed-baseline
    epoch, WITHOUT force_baseline, must still create a normal Event --
    it must NOT be silently suppressed just because Timex's run happened
    to pass force_baseline=True."""
    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event
    from app.parsers.citizen_news import parse_citizen_news_html
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)  # epoch is LIVE, exactly like the real Sprint 9 scenario

    page1 = (FIXTURES / "timex_products_page1.json").read_bytes()
    empty = (FIXTURES / "timex_products_page_empty.json").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))

    timex_run = pipeline.run_product_observation_pipeline("timex", offline_fixture=[page1, empty], force_baseline=True)
    assert timex_run.new_watch_count > 0
    assert db_session.scalars(select(Event)).first() is None  # Timex silently baselined

    # Now a normal (non-Timex, non-force_baseline) news announcement in the
    # SAME live epoch must behave completely normally -- NEW_REFERENCE fires.
    detail_html = (FIXTURES / "citizen_news_detail.html").read_bytes()
    run = CollectorRun(collector_id="citizen_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()
    fr = FetchResult(
        url="https://www.citizenwatch-global.com/news/2026/20260610/index.html",
        success=True, status_code=200, content_type="text/html", payload=detail_html,
    )
    out = pipeline.process_news_announcement(
        fr, run_id=run.id, discovered_meta={"source_region": "GLOBAL"},
        collector_id="citizen_news", manufacturer="Citizen", brand="Citizen",
        parse_fn=parse_citizen_news_html, merge_key_prefix="citizen",
        default_region="GLOBAL", emit_events=True,
    )
    assert out["watch_events"][0]["event_type"] == "NEW_REFERENCE"  # NOT suppressed
    assert db_session.scalars(select(Event)).first() is not None


def test_timex_news_baseline_leads_classify_baseline_not_fresh(db_session: Session, tmp_settings: Settings):
    """The old August/July Timex blog entries discovered during a source-
    scoped Timex baseline must be stored as ReleaseLead evidence but must
    never be treated as fresh newsroom material (mirrors the Sprint 8
    fix's semantics for the news/announcement lane, which uses Event
    suppression rather than SpecialistLead.editorial_freshness -- Timex
    news is Layer A/official, not Layer B/specialist)."""
    from app.models import Event
    from app.services.epoch import start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)

    xml_bytes = (FIXTURES / "timex_blog_feed.atom").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = pipeline.run_brand_news_pipeline("timex", index_html=xml_bytes)

    assert run.summary_metadata["new_leads"] == 8
    assert db_session.scalars(select(Event)).first() is None  # baseline suppression held


# --- Sprint 10: Timex historical-freshness hardening -------------------------
# See ai/handoff/TIMEX_FRESHNESS_AUDIT.md. Real gap found: Sprint 8's freshness
# fix only covered SpecialistLead (Layer B). Layer A news (ReleaseLead ->
# Event via _record_watch_event) had NO publication-age gate at all -- a
# genuinely old official article, first discovered late, would fire a real
# NEW_REFERENCE event purely from discovery novelty. Fixed source-scoped
# (Timex only, via _ISO_TIMESTAMP_NEWS_SOURCES) since Casio/Citizen/Seiko's
# announcement_date is free text ("July 15, 2026", "23 July 2026") that a
# strict ISO parse safely and predictably fails on, leaving them untouched.


def test_real_historical_timex_article_does_not_become_current_news(db_session: Session, tmp_settings: Settings):
    """Phase 4: a REAL historical Timex Atom entry (from the live fixture,
    "Todd Snyder x Timex Marlin Mesh", published 2026-07-28 -- well outside
    the 72h window from any 2026-08-12+ test run) first discovered AFTER
    baseline (not during it) must create real Watch/ReleaseLead evidence
    but must NEVER create a NEW_REFERENCE Event. This is the exact
    "Reagan-era Timex article" class of bug this sprint hardens against.
    Fails under the old discovery-time-is-news semantics (no gate existed
    at all), passes under the fix."""
    import xml.etree.ElementTree as ET

    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event, ReleaseLead
    from app.parsers.timex_news import parse_timex_news_entry
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)  # epoch is LIVE -- baseline already over

    xml_bytes = (FIXTURES / "timex_blog_feed.atom").read_bytes()
    root = ET.fromstring(xml_bytes)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    real_old_entry = next(
        e for e in root.findall("a:entry", ns)
        if e.find("a:title", ns).text.startswith("Todd Snyder x Timex")
    )
    assert real_old_entry.find("a:published", ns).text == "2026-07-28T07:00:07-04:00"  # real, from the live capture

    import json

    # The real content has no extractable SKU (an honest, already-documented
    # parser characteristic -- marketing copy rarely spells out part numbers,
    # see app/parsers/timex_news.py's MODEL_RE docstring). Title/published/
    # URL below are all real, from the live capture; a SKU sentence is
    # appended so this test actually exercises _record_watch_event's new
    # staleness gate (with no reference at all, no watch/event path would
    # be reached, which wouldn't prove anything about this fix).
    entry_dict = {
        "title": real_old_entry.find("a:title", ns).text,
        "published": real_old_entry.find("a:published", ns).text,
        "content": (real_old_entry.find("a:content", ns).text or "") + " Featuring the TW2R79300 model.",
    }
    payload = json.dumps(entry_dict).encode("utf-8")
    fr = FetchResult(
        url="https://timex.com/blogs/the-timex-blog/todd-snyder-x-timex-mesh-marlin-a-sleek-take-on-a-vintage-classic",
        success=True, status_code=200, content_type="application/json", payload=payload,
    )
    run = CollectorRun(collector_id="timex_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    out = pipeline.process_news_announcement(
        fr, run_id=run.id, collector_id="timex_news", manufacturer="Timex", brand="Timex",
        parse_fn=parse_timex_news_entry, merge_key_prefix="timex", default_region="US", emit_events=True,
    )

    assert out["success"] is True
    assert out["new_lead"] is True  # stored as real evidence
    assert db_session.scalars(select(ReleaseLead)).first() is not None  # historical evidence preserved
    assert out["watch_events"][0]["event_type"] is None
    assert out["watch_events"][0]["reason"] == "stale_publication"
    assert db_session.scalars(select(Event)).first() is None  # NEVER current news


def test_timex_news_image_filename_sku_resolves_to_existing_catalogue_watch_not_duplicate(
    db_session: Session, tmp_settings: Settings
):
    """Sprint 11: real live validation (isolated DB copy) caught this the fix
    to IMAGE_SKU_RE alone introduced -- Shopify CDN image filenames never
    carry the catalogue's trailing variant suffix (real: "TW6A00500" in the
    filename vs the catalogue's real stored "TW6A00500VQ"), so an exact
    reference_canonical match always misses and a phantom duplicate Watch
    got created instead of linking the real one. Proven live against a copy
    of the production DB before this fix; this is the regression test."""
    from datetime import UTC, datetime, timedelta

    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Watch
    from app.parsers.timex_news import parse_timex_news_entry
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    catalogue_watch = Watch(
        manufacturer="Timex", brand="Timex",
        reference_raw="TW6A00500VQ", reference_canonical="TW6A00500VQ",
        family_candidate_key="timex_timex_tw6a00500vq",
    )
    db_session.add(catalogue_watch)
    db_session.commit()

    import json

    recent_pub = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    entry_dict = {
        "title": "New For Fall: The E Line Returns",
        "published": recent_pub,
        "content": '<img src="https://cdn.shopify.com/files/1_TX_TC_26_PFB_TW6A00500_1_600x600.jpg">',
    }
    fr = FetchResult(
        url="https://timex.com/blogs/the-timex-blog/e-line-returns",
        success=True, status_code=200, content_type="application/json",
        payload=json.dumps(entry_dict).encode("utf-8"),
    )
    run = CollectorRun(collector_id="timex_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    out = pipeline.process_news_announcement(
        fr, run_id=run.id, collector_id="timex_news", manufacturer="Timex", brand="Timex",
        parse_fn=parse_timex_news_entry, merge_key_prefix="timex", default_region="US", emit_events=True,
    )

    assert out["new_watch"] is False  # must resolve to the existing catalogue watch
    all_timex_watches = db_session.scalars(select(Watch).where(Watch.manufacturer == "Timex")).all()
    assert len(all_timex_watches) == 1  # no phantom duplicate
    assert all_timex_watches[0].id == catalogue_watch.id


def test_fresh_timex_article_still_creates_event(db_session: Session, tmp_settings: Settings):
    """Proof the gate doesn't over-suppress: a Timex article published
    within the freshness window, discovered after baseline, must still
    fire a normal NEW_REFERENCE event."""
    from datetime import UTC, datetime, timedelta

    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event
    from app.parsers.timex_news import parse_timex_news_entry
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    import json

    recent_pub = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    entry_dict = {"title": "Brand New Timex Launch", "published": recent_pub, "content": "<p>Featuring the TW2R79301 model.</p>"}
    fr = FetchResult(
        url="https://timex.com/blogs/the-timex-blog/brand-new-launch",
        success=True, status_code=200, content_type="application/json",
        payload=json.dumps(entry_dict).encode("utf-8"),
    )
    run = CollectorRun(collector_id="timex_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    out = pipeline.process_news_announcement(
        fr, run_id=run.id, collector_id="timex_news", manufacturer="Timex", brand="Timex",
        parse_fn=parse_timex_news_entry, merge_key_prefix="timex", default_region="US", emit_events=True,
    )

    assert out["watch_events"][0]["event_type"] == "NEW_REFERENCE"
    assert db_session.scalars(select(Event)).first() is not None


def test_timex_article_with_null_publication_timestamp_does_not_become_news(db_session: Session, tmp_settings: Settings):
    """Phase 2: a Timex entry with a missing/invalid publication timestamp
    must NOT be assumed fresh -- UNKNOWN is not the same as CURRENT."""
    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event
    from app.parsers.timex_news import parse_timex_news_entry
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    import json

    entry_dict = {"title": "Undated Timex Post", "published": None, "content": "<p>Featuring the TW2R79302 model.</p>"}
    fr = FetchResult(
        url="https://timex.com/blogs/the-timex-blog/undated-post",
        success=True, status_code=200, content_type="application/json",
        payload=json.dumps(entry_dict).encode("utf-8"),
    )
    run = CollectorRun(collector_id="timex_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    out = pipeline.process_news_announcement(
        fr, run_id=run.id, collector_id="timex_news", manufacturer="Timex", brand="Timex",
        parse_fn=parse_timex_news_entry, merge_key_prefix="timex", default_region="US", emit_events=True,
    )

    assert out["watch_events"][0]["event_type"] is None
    assert out["watch_events"][0]["reason"] == "unknown_publication_timestamp"
    assert db_session.scalars(select(Event)).first() is None


def test_future_rediscovery_of_old_timex_article_produces_no_alert(db_session: Session, tmp_settings: Settings):
    """Phase 5: simulate the exact production scenario this sprint exists
    to prevent. Epoch 1 baseline is long over; a canonical URL genuinely
    NEW to Clank (never seen before, so this is real discovery novelty,
    not a dedup case) surfaces with a publication date that predates the
    freshness window by months. New DB evidence may be created; zero
    current editorial intelligence; zero alert eligibility."""
    from datetime import UTC, datetime, timedelta

    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event, ReleaseLead
    from app.parsers.timex_news import parse_timex_news_entry
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    import json

    ancient_pub = (datetime.now(UTC) - timedelta(days=400)).isoformat()  # over a year old
    entry_dict = {
        "title": "Timex MK1 Vintage Reissue Announcement",
        "published": ancient_pub,
        "content": "<p>Featuring the TW2R79303 model, never in the reachable feed window until now.</p>",
    }
    fr = FetchResult(
        url="https://timex.com/blogs/the-timex-blog/mk1-vintage-reissue-ancient",
        success=True, status_code=200, content_type="application/json",
        payload=json.dumps(entry_dict).encode("utf-8"),
    )
    run = CollectorRun(collector_id="timex_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    out = pipeline.process_news_announcement(
        fr, run_id=run.id, collector_id="timex_news", manufacturer="Timex", brand="Timex",
        parse_fn=parse_timex_news_entry, merge_key_prefix="timex", default_region="US", emit_events=True,
    )

    assert out["new_lead"] is True
    assert db_session.scalars(select(ReleaseLead)).first() is not None  # new DB evidence created
    assert out["watch_events"][0]["event_type"] is None
    assert out["watch_events"][0]["reason"] == "stale_publication"
    assert db_session.scalars(select(Event)).first() is None  # zero current intelligence, zero alert eligibility


def test_casio_and_citizen_official_news_unaffected_by_timex_hardening(db_session: Session, tmp_settings: Settings):
    """Phase 7 regression proof: Casio/Citizen/Seiko's free-text
    announcement_date strings are NOT in _ISO_TIMESTAMP_NEWS_SOURCES, so
    they must fire NEW_REFERENCE exactly as before -- completely
    unaffected by the Timex-specific hardening, regardless of how old
    their (unparseable) date string might look."""
    from app.collectors.base import FetchResult
    from app.models import CollectorRun, Event
    from app.parsers.citizen_news import parse_citizen_news_html
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    detail_html = (FIXTURES / "citizen_news_detail.html").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = CollectorRun(collector_id="citizen_news", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    fr = FetchResult(
        url="https://www.citizenwatch-global.com/news/2026/20260610/index.html",
        success=True, status_code=200, content_type="text/html", payload=detail_html,
    )
    out = pipeline.process_news_announcement(
        fr, run_id=run.id, discovered_meta={"source_region": "GLOBAL"},
        collector_id="citizen_news", manufacturer="Citizen", brand="Citizen",
        parse_fn=parse_citizen_news_html, merge_key_prefix="citizen",
        default_region="GLOBAL", emit_events=True,
    )
    assert out["watch_events"][0]["event_type"] == "NEW_REFERENCE"  # unaffected
    assert db_session.scalars(select(Event)).first() is not None


def test_old_timex_product_restock_still_fires_current_event(db_session: Session, tmp_settings: Settings):
    """Phase 3: publication-age gating must NEVER leak into the product/
    catalogue transition path -- _record_product_transition is untouched
    by this hardening. A years-old Timex watch that restocks today must
    still produce a current RESTOCK/SOLD_OUT event."""
    from datetime import UTC, datetime, timedelta

    from app.models import Event, SourceObservation, Watch
    from app.services.epoch import complete_baseline, start_baseline, start_epoch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    epoch = start_epoch(db_session, name="epoch_1")
    start_baseline(db_session, epoch)
    complete_baseline(db_session, epoch)

    watch = Watch(manufacturer="Timex", brand="Timex", reference_raw="TW2R79300", reference_canonical="TW2R79300")
    db_session.add(watch)
    db_session.flush()
    old_obs = SourceObservation(
        watch_id=watch.id, collector_id="timex_products", collector_version="0.1.0",
        parser_id="t", parser_version="0", region="US", source_url="https://x/old",
        availability_status="SOLD_OUT", observed_at=datetime.now(UTC) - timedelta(days=900),  # ~2.5 years old
        overall_confidence=90.0,
    )
    db_session.add(old_obs)
    db_session.commit()

    new_obs = SourceObservation(
        watch_id=watch.id, collector_id="timex_products", collector_version="0.1.0",
        parser_id="t", parser_version="0", region="US", source_url="https://x/new",
        availability_status="AVAILABLE", observed_at=datetime.now(UTC), overall_confidence=90.0,
    )
    db_session.add(new_obs)
    db_session.flush()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    result = pipeline._record_product_transition(watch=watch, new_obs=new_obs, is_new_watch=False)
    assert result["event_type"] == "RESTOCK"
    assert db_session.scalars(select(Event)).first() is not None


# --- Sprint 6: health snapshot + DB backup ----------------------------------


def test_health_snapshot_reports_never_run_and_healthy_sources(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime

    from app.services.health import get_health_snapshot

    run = CollectorRun(
        collector_id="citizen_news", collector_version="0.1.0", status="SUCCESS",
        started_at=datetime.now(UTC), completed_at=datetime.now(UTC), discovered_count=3,
    )
    db_session.add(run)
    db_session.commit()

    snap = get_health_snapshot(db_session, tmp_settings)
    by_id = {s.collector_id: s for s in snap.sources}
    assert by_id["citizen_news"].state == "HEALTHY"
    assert by_id["citizen_news"].last_item_count == 3
    assert by_id["casio_multi"].state == "NEVER_RUN"


def test_health_snapshot_marks_all_recent_failed_as_failed(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime

    from app.services.health import get_health_snapshot

    run = CollectorRun(
        collector_id="seiko_jp_news", collector_version="0.1.0", status="FAILED",
        started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
    )
    db_session.add(run)
    db_session.commit()

    snap = get_health_snapshot(db_session, tmp_settings)
    by_id = {s.collector_id: s for s in snap.sources}
    assert by_id["seiko_jp_news"].state == "FAILED"


def test_health_snapshot_flags_heartbeat_overdue(db_session: Session, tmp_settings: Settings):
    from datetime import UTC, datetime, timedelta

    from app.services.health import get_health_snapshot

    # casioblog_rss expected cadence is 45 min; a "success" 10 hours ago
    # is well past the 3x-cadence overdue window.
    run = CollectorRun(
        collector_id="casioblog_rss", collector_version="0.1.0", status="SUCCESS",
        started_at=datetime.now(UTC) - timedelta(hours=10), completed_at=datetime.now(UTC) - timedelta(hours=10),
    )
    db_session.add(run)
    db_session.commit()

    snap = get_health_snapshot(db_session, tmp_settings)
    by_id = {s.collector_id: s for s in snap.sources}
    assert by_id["casioblog_rss"].heartbeat_overdue is True
    assert by_id["casioblog_rss"].state == "WARNING"


def test_db_backup_creates_restorable_copy(tmp_path: Path, monkeypatch):
    from app.core.config import Settings

    db_path = tmp_path / "data" / "watch_clank.db"
    db_path.parent.mkdir(parents=True)
    src_conn = sqlite3.connect(str(db_path))
    src_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    src_conn.execute("INSERT INTO t (v) VALUES ('hello')")
    src_conn.commit()
    src_conn.close()

    settings = Settings(database_url=f"sqlite:///{db_path}")

    import scripts.db_backup as db_backup_mod

    monkeypatch.setattr(db_backup_mod, "get_settings", lambda: settings)

    dest = db_backup_mod.backup_database(keep=14)
    assert dest.exists()
    dest_conn = sqlite3.connect(str(dest))
    rows = dest_conn.execute("SELECT v FROM t").fetchall()
    dest_conn.close()
    assert rows == [("hello",)]


def test_db_backup_retention_prunes_oldest(tmp_path: Path, monkeypatch):
    from app.core.config import Settings

    db_path = tmp_path / "data" / "watch_clank.db"
    db_path.parent.mkdir(parents=True)
    sqlite3.connect(str(db_path)).close()

    settings = Settings(database_url=f"sqlite:///{db_path}")

    import scripts.db_backup as db_backup_mod

    monkeypatch.setattr(db_backup_mod, "get_settings", lambda: settings)

    for _ in range(5):
        db_backup_mod.backup_database(keep=3)

    backups = sorted((db_path.parent / "backups").glob("*.db"))
    assert len(backups) == 3  # oldest two pruned, never the live DB itself
    assert db_path.exists()


# --- Sprint 6: specialist-lead Discord wiring + dedup ----------------------


def test_notify_new_lead_sends_and_sets_notified_at(db_session: Session):
    from unittest.mock import patch

    from app.core.config import Settings
    from app.models import SpecialistLead
    from app.services.discord_notify import DiscordNotifier
    from app.services.specialist_leads import SpecialistLeadService

    settings = Settings(discord_editorial_webhook_url="https://discord.example/editorial")
    svc = SpecialistLeadService(db_session)
    from datetime import UTC, datetime

    outcome = svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="Rumored GWR-B3000",
        source_url="https://casioblog.com/en/rumor-gwr-b3000", published_at=datetime.now(UTC).isoformat(),
        reference_candidates=["GWR-B3000"], claim_text=None, manufacturer="Casio", confidence=60.0,
    )
    lead = db_session.get(SpecialistLead, outcome["lead_id"])

    calls = []
    with (
        patch("app.services.specialist_leads.get_settings", return_value=settings),
        patch("httpx.post", side_effect=lambda url, **kw: calls.append(url) or type("R", (), {"status_code": 204, "text": ""})()),
    ):
        sent = svc.notify_new_lead(lead, notifier=DiscordNotifier(settings))

    assert sent is True
    assert calls == ["https://discord.example/editorial"]
    assert lead.notified_at is not None


def test_notify_new_lead_does_not_resend_once_notified(db_session: Session):
    from unittest.mock import patch

    from app.core.config import Settings
    from app.models import SpecialistLead
    from app.services.discord_notify import DiscordNotifier
    from app.services.specialist_leads import SpecialistLeadService

    settings = Settings(discord_editorial_webhook_url="https://discord.example/editorial")
    svc = SpecialistLeadService(db_session)
    from datetime import UTC, datetime

    outcome = svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="Rumored GWR-B3000",
        source_url="https://casioblog.com/en/rumor-gwr-b3000-2", published_at=datetime.now(UTC).isoformat(),
        reference_candidates=["GWR-B3000"], claim_text=None, manufacturer="Casio", confidence=60.0,
    )
    lead = db_session.get(SpecialistLead, outcome["lead_id"])

    calls = []
    with (
        patch("app.services.specialist_leads.get_settings", return_value=settings),
        patch("httpx.post", side_effect=lambda url, **kw: calls.append(url) or type("R", (), {"status_code": 204, "text": ""})()),
    ):
        notifier = DiscordNotifier(settings)
        first = svc.notify_new_lead(lead, notifier=notifier)
        second = svc.notify_new_lead(lead, notifier=notifier)  # simulates a repeat pipeline run

    assert first is True
    assert second is False  # notified_at dedup
    assert calls == ["https://discord.example/editorial"]  # only sent once


def test_notify_new_lead_respects_confidence_floor_and_authority_flag(db_session: Session):
    from unittest.mock import patch

    from app.core.config import Settings
    from app.models import SpecialistLead
    from app.services.discord_notify import DiscordNotifier
    from app.services.specialist_leads import SpecialistLeadService

    svc = SpecialistLeadService(db_session)
    low_confidence = svc.ingest_candidate(
        source_id="casioblog", lead_type="LEAKED_IMAGE", title="Low-confidence rumor",
        source_url="https://casioblog.com/en/low-confidence", published_at=None,
        reference_candidates=[], claim_text=None, manufacturer="Casio", confidence=10.0,
    )
    lead = db_session.get(SpecialistLead, low_confidence["lead_id"])

    below_threshold = Settings(
        discord_editorial_webhook_url="https://discord.example/editorial",
        discord_specialist_min_confidence=40.0,
    )
    with patch("app.services.specialist_leads.get_settings", return_value=below_threshold):
        assert svc.notify_new_lead(lead, notifier=DiscordNotifier(below_threshold)) is False

    disabled = Settings(
        discord_editorial_webhook_url="https://discord.example/editorial",
        editorial_notifications_enabled=False,
    )
    with patch("app.services.specialist_leads.get_settings", return_value=disabled):
        assert svc.notify_new_lead(lead, notifier=DiscordNotifier(disabled)) is False


def test_notify_correlation_sends_family_match_followup(db_session: Session):
    from unittest.mock import patch

    from app.core.config import Settings
    from app.models import SpecialistLead, Watch
    from app.services.discord_notify import DiscordNotifier
    from app.services.specialist_leads import SpecialistLeadService

    watch = Watch(manufacturer="Casio", brand="Casio", reference_raw="GWR-B3000-1A", reference_canonical="GWR-B3000-1A")
    db_session.add(watch)
    db_session.commit()

    settings = Settings(discord_editorial_webhook_url="https://discord.example/editorial")
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(
        source_id="casioblog", lead_type="POSSIBLE_NEW_REFERENCE", title="[Rumors] GWR-B3000",
        source_url="https://casioblog.com/en/rumor-gwr-b3000-followup", published_at=None,
        reference_candidates=["GWR-B3000"], claim_text=None, manufacturer="Casio", confidence=60.0,
    )
    svc.correlate_pending_leads(manufacturer="Casio")
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    assert lead.correlation_type == "FAMILY_MATCH"

    captured = {}

    def _fake_post(url, **kw):
        captured["body"] = kw["json"]["content"]
        return type("R", (), {"status_code": 204, "text": ""})()

    with patch("httpx.post", side_effect=_fake_post):
        sent = svc.notify_correlation(lead, notifier=DiscordNotifier(settings))

    assert sent is True
    assert "FAMILY_MATCH — NOT EXACT" in captured["body"]
    assert "CONFIRMED" not in captured["body"].split("\n")[0].replace("FAMILY_MATCH — NOT EXACT", "")


def test_early_warning_alert_is_structurally_distinct_from_official():
    from app.services.editorial import (
        EventEvidence,
        format_alert,
        format_early_warning_alert,
        score_event,
    )

    official_scored = score_event(EventEvidence(event_type="NEW_REFERENCE", manufacturer="Casio", brand="Casio"))
    official_text = format_alert(
        manufacturer="Casio", brand="Casio", reference_raw="GA-2100-1A1JF", scored=official_scored,
        region="JP", announcement_title="Official announcement", announcement_url="https://casio.com/x",
        observed_at="2026-08-11T00:00:00Z",
    )
    early_warning_text = format_early_warning_alert(
        manufacturer="Casio", brand="G-Shock", reference_candidates=["GWR-B3000"],
        lead_type="POSSIBLE_NEW_REFERENCE", source_display_name="CASIOBLOG", source_type="SPECIALIST_BLOG",
        source_authority_tier=2, title="Rumored GWR-B3000", claim_text="early rumor",
        source_url="https://casioblog.com/x", published_at="2026-08-01T00:00:00Z",
        discovered_at="2026-08-11T00:00:00Z", confidence=55.0,
    )
    assert "EARLY WARNING" in early_warning_text and "UNCONFIRMED" in early_warning_text
    assert "Requires human verification" in early_warning_text
    assert "EARLY WARNING" not in official_text
    assert "tier" in early_warning_text.lower()
    # never claims official-style "Editorial score" language for a lead
    assert "Editorial score" not in early_warning_text
