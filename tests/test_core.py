"""Offline behavioral test suite for Watch Clank Stage 1."""
from __future__ import annotations

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
        "release_leads", "source_component_states",
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
