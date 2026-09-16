"""casio_multi component-health regression tests (2026-09-10 incident).

The incident: the JP product component had never succeeded from this
vantage (Akamai 403 on every HTML listing URL since the first run on
2026-08-10), a 24h backoff made most parent runs report SUCCESS while the
component was silently skipped, and PARTIAL rows showed Fail 0 because
failure_count is strictly item-level. These tests pin the repaired
contract:

- the JP product component is the official watches.xml sitemap lane
- every component exit emits one terminal structured log record
- component degradation/skips are persisted separately from item failures
- an unchanged healthy catalogue is ZERO_ITEMS, never a failure
- baseline / freshness / event semantics are unchanged by the swap

All tests are offline: collectors are patched at run() boundaries with
real discovery/parse code over fixture payloads.
"""
from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.orm import Session

from app.collectors.base import CollectorRunResult, FetchResult
from app.collectors.casio_intl_news import CasioIntlNewsCollector
from app.collectors.casio_jp_sitemap import CasioJPSitemapCollector
from app.core.config import Settings
from app.models import CollectorRun, Event, SourceObservation, Watch
from app.services.pipeline import PipelineService
from app.services.snapshot_storage import SnapshotStorageService
from tests.test_core import FIXTURES, db_session, tmp_settings  # noqa: F401 -- shared fixtures


class _CapturingLogger:
    """Deterministic stand-in for the structlog pipeline logger (caplog is
    unreliable here: setup_logging() clears root handlers mid-process)."""

    def __init__(self):
        self.records = []

    def info(self, event, **kw):
        self.records.append({"event": event, **kw})


_JP_SITEMAP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.casio.com/jp/watches/gshock/product.GBA-950-2A/</loc><lastmod>2026-09-09T10:00:00Z</lastmod></url>
<url><loc>https://www.casio.com/jp/watches/gshock/product.GW-M5610U-1JF/</loc><lastmod>2026-09-09T10:00:00Z</lastmod></url>
</urlset>"""


def _news_success(items: int = 0):
    """News component: SUCCESS with an empty item list (its own lane has
    dedicated tests; these tests are about component aggregation)."""

    def news_run(self, *, max_items=None, index_html=None):
        r = CollectorRunResult(
            collector_id="casio_intl_news",
            collector_version="0.1.0",
            region="INTL",
            trust_score=95.0,
        )
        r.metadata["component_status"] = "SUCCESS"
        r.metadata["discovered_count"] = items
        return r

    return news_run


def _jp_run_from_xml(xml: bytes, *, blocked: bool = False):
    """JP component: the collector's REAL discovery over a fixture document
    (or a blocked fetch), producing the same synthetic fetch shape live
    runs produce."""

    def jp_run(self, *, max_items=None, known_product_urls=None, sitemap_payload=None):
        r = CollectorRunResult(
            collector_id="casio_jp_sitemap",
            collector_version="0.1.0",
            region="JP",
            trust_score=70.0,
        )
        if blocked:
            r.metadata["component_status"] = "BLOCKED"
            r.metadata["discovery_fetches"] = [
                {
                    "url": "https://www.casio.com/jp/sitemap/watches.xml",
                    "status": 403,
                    "success": False,
                    "blocked": True,
                }
            ]
            return r
        items = self.discover_from_sitemap_xml(xml)
        known = known_product_urls or set()
        new_items = [i for i in items if i.url not in known]
        r.discovered = new_items
        r.metadata["component_status"] = "SUCCESS" if items else "ZERO_ITEMS"
        r.metadata["discovered_count"] = len(new_items)
        r.metadata["known_url_count"] = len(known)
        for i in new_items:
            # Mirrors the live synthetic-fetch shape: reference + lastmod.
            r.fetched.append(
                FetchResult(
                    url=i.url,
                    success=True,
                    status_code=200,
                    content_type="application/json",
                    payload=(
                        f'{{"reference": "{i.reference_hint}", "lastmod": "{i.metadata["lastmod"]}"}}'
                    ).encode(),
                )
            )
        return r

    return jp_run


def _established_multi_run(db_session: Session) -> None:  # noqa: F811 -- pytest fixture
    """A prior casio_multi run, so _auto_baseline_for_first_run is off and
    the tests exercise the normal (non-baseline) path."""
    db_session.add(CollectorRun(collector_id="casio_multi", collector_version="0.1", status="SUCCESS"))
    db_session.flush()


def _make_pipeline(db_session: Session, tmp_settings: Settings) -> PipelineService:  # noqa: F811 -- pytest fixture
    return PipelineService(db_session, SnapshotStorageService(tmp_settings))


# ---------------------------------------------------------------------------
# both components healthy; unchanged catalogue => ZERO_ITEMS, never failure


def test_unchanged_catalogue_is_zero_items_not_failure(db_session: Session, tmp_settings: Settings, monkeypatch):  # noqa: F811 -- pytest fixture
    _established_multi_run(db_session)
    pipeline = _make_pipeline(db_session, tmp_settings)
    captured = _CapturingLogger()
    monkeypatch.setattr("app.services.pipeline.logger", captured)

    with (
        patch.object(CasioIntlNewsCollector, "run", _news_success()),
        patch.object(CasioJPSitemapCollector, "run", _jp_run_from_xml(_JP_SITEMAP_XML)),
    ):
        run1 = pipeline.run_multi_source_pipeline(max_items=5, skip_lock=True, include_catalog=True)
    assert run1.status == "SUCCESS"
    jp1 = run1.summary_metadata["components"]["casio_jp_sitemap"]
    assert jp1["status"] == "SUCCESS"
    assert jp1["new_for_processing"] == 2
    assert run1.summary_metadata["component_failures"] == []
    assert db_session.query(Watch).count() == 2

    obs_after_run1 = db_session.query(SourceObservation).count()

    # Second sweep: the catalogue is unchanged -- every URL is now known.
    with (
        patch.object(CasioIntlNewsCollector, "run", _news_success()),
        patch.object(CasioJPSitemapCollector, "run", _jp_run_from_xml(_JP_SITEMAP_XML)),
    ):
        run2 = pipeline.run_multi_source_pipeline(max_items=5, skip_lock=True, include_catalog=True)
    jp2 = run2.summary_metadata["components"]["casio_jp_sitemap"]
    assert jp2["status"] == "ZERO_ITEMS"
    assert jp2["reason"] == "NO_NEW_REFERENCES"
    assert run2.status == "SUCCESS"  # healthy-unchanged is NOT a failure
    assert run2.summary_metadata["component_failures"] == []
    assert db_session.query(SourceObservation).count() == obs_after_run1  # no churn
    # every component exit left one terminal structured record
    finished = [r for r in captured.records if r["event"] == "casio_component_finished"]
    assert any(
        r.get("component") == "casio_jp_sitemap" and r.get("status") == "ZERO_ITEMS"
        and r.get("reason") == "NO_NEW_REFERENCES"
        for r in finished
    )


# ---------------------------------------------------------------------------
# backoff skip is visible, never silent


def test_backed_off_component_is_visible_not_silent(db_session: Session, tmp_settings: Settings, monkeypatch):  # noqa: F811 -- pytest fixture  # noqa: F811 -- pytest fixture
    from datetime import UTC, datetime, timedelta

    from app.models import SourceComponentState

    _established_multi_run(db_session)
    db_session.add(
        SourceComponentState(
            source_id="casio_jp_sitemap",
            last_status="BLOCKED",
            consecutive_blocks=5,
            backoff_until=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    db_session.commit()
    pipeline = _make_pipeline(db_session, tmp_settings)
    captured = _CapturingLogger()
    monkeypatch.setattr("app.services.pipeline.logger", captured)

    with (
        patch.object(CasioIntlNewsCollector, "run", _news_success()),
    ):
        run = pipeline.run_multi_source_pipeline(max_items=5, skip_lock=True, include_catalog=True)

    # The parent is SUCCESS (backoff is a designed state, the pipeline did
    # its work) -- but the skip is now explicit in three places instead of
    # being invisible.
    jp = run.summary_metadata["components"]["casio_jp_sitemap"]
    assert jp["status"] == "BACKED_OFF"
    assert jp["reason"] == "BACKOFF_ACTIVE"
    assert run.summary_metadata["component_skipped"] == [
        {"component": "casio_jp_sitemap", "status": "BACKED_OFF", "reason": "BACKOFF_ACTIVE"}
    ]
    assert "casio_jp_sitemap=BACKED_OFF" in run.summary_metadata["status_reason"]
    terminal = [r for r in captured.records if r["event"] == "casio_component_finished"]
    assert any(
        r.get("component") == "casio_jp_sitemap"
        and r.get("status") == "BACKED_OFF"
        and r.get("reason") == "BACKOFF_ACTIVE"
        for r in terminal
    )


# ---------------------------------------------------------------------------
# a genuinely new JP reference flows through the sitemap parser with
# unchanged identity/semantic contracts (sitemap lastmod is weak evidence:
# no NEW_REFERENCE from it)


def test_new_japan_reference_processed_via_sitemap_parser(db_session: Session, tmp_settings: Settings):  # noqa: F811 -- pytest fixture
    _established_multi_run(db_session)
    pipeline = _make_pipeline(db_session, tmp_settings)

    with (
        patch.object(CasioIntlNewsCollector, "run", _news_success()),
        patch.object(CasioJPSitemapCollector, "run", _jp_run_from_xml(_JP_SITEMAP_XML)),
    ):
        run = pipeline.run_multi_source_pipeline(max_items=5, skip_lock=True, include_catalog=True)

    assert run.status == "SUCCESS"
    watch = db_session.query(Watch).filter_by(reference_canonical="GBA-950-2A").one()
    assert watch.manufacturer == "Casio"
    # no false NEW_REFERENCE from a sitemap delta: lastmod is weak evidence
    assert not any(
        e.event_type == "NEW_REFERENCE" for e in db_session.query(Event).all()
    )


# ---------------------------------------------------------------------------
# a JP parse regression is a component-level failure with an explicit reason


def test_japan_parse_regression_surfaces(db_session: Session, tmp_settings: Settings):  # noqa: F811 -- pytest fixture
    _established_multi_run(db_session)
    pipeline = _make_pipeline(db_session, tmp_settings)

    def jp_run_garbage(self, *, max_items=None, known_product_urls=None, sitemap_payload=None):
        r = CollectorRunResult(
            collector_id="casio_jp_sitemap", collector_version="0.1.0", region="JP", trust_score=70.0
        )
        r.metadata["component_status"] = "SUCCESS"
        r.metadata["discovered_count"] = 1
        r.fetched.append(
            FetchResult(
                url="https://www.casio.com/jp/watches/gshock/product.GBA-950-2A/",
                success=True,
                status_code=200,
                content_type="application/json",
                payload=b"this is not the json the parser expects",
            )
        )
        return r

    with (
        patch.object(CasioIntlNewsCollector, "run", _news_success()),
        patch.object(CasioJPSitemapCollector, "run", jp_run_garbage),
    ):
        run = pipeline.run_multi_source_pipeline(max_items=5, skip_lock=True, include_catalog=True)

    jp = run.summary_metadata["components"]["casio_jp_sitemap"]
    assert jp["status"] == "FAILED"
    assert jp["reason"] == "PARSE_FAILURES"
    # item failures were ALSO counted at the item level (both truths held)
    assert run.failure_count == 1
    assert run.status == "PARTIAL"
    assert "casio_jp_sitemap=FAILED(PARSE_FAILURES)" in run.summary_metadata["status_reason"]
    assert run.summary_metadata["component_failures"] == [
        {
            "component": "casio_jp_sitemap",
            "status": "FAILED",
            "reason": "PARSE_FAILURES",
            "http_statuses": None,
        }
    ]
