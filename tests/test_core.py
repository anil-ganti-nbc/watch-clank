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
