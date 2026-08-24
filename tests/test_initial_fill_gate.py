"""Phase 2 gate: initial-catalogue-fill suppression regression test.

Live finding (2026-08-25 Tissot repeated-run validation): a bounded-budget
brand drips FIRST_SEEN events for every catalogue slice after run 1
(120 events/run observed). The initial-fill window extends Law 1's
no-flood guarantee across the whole first catalogue pass.
"""
from sqlalchemy import func, select

from app.models import SourceObservation
from app.services.pipeline import PipelineService
from app.services.snapshot_storage import SnapshotStorageService
from tests.test_core import db_session, tmp_settings  # noqa: F401 -- shared fixtures


class _FakeSitemapCollector:
    """Deterministic 30-SKU catalogue; new-first slicing by known URLs."""

    CATALOGUE = [f"TSKU{i:03d}" for i in range(30)]

    def run(self, *, max_items=10, sitemap_payload=None, known_product_urls=None):
        from app.collectors.base import CollectorRunResult, FetchResult

        result = CollectorRunResult(
            collector_id="tissot_sitemap", collector_version="test", region="US", trust_score=70.0
        )
        urls = [f"https://www.tissotwatches.com/en-us/{sku}.html" for sku in self.CATALOGUE]
        known = known_product_urls or set()
        unseen = [u for u in urls if u not in known]
        ordered = unseen + [u for u in urls if u in known]
        batch = ordered[: max_items or 10]

        result.metadata = {
            "component_status": "SUCCESS",
            "candidate_count": len(urls),
            "discovered_count": len(batch),
        }
        for url in batch:
            sku = url.rsplit("/", 1)[-1][:-5]
            result.discovered.append(
                type("D", (), {"url": url, "title": None, "reference_hint": sku, "metadata": {}})()
            )
            result.fetched.append(FetchResult(
                url=url, success=True, status_code=200, content_type="application/json",
                payload=f'{{"reference": "{sku}", "lastmod": "2026-08-25"}}'.encode(),
            ))
        return result


def test_initial_fill_suppresses_events_until_catalogue_wraps(db_session, tmp_settings):
    from functools import partial

    from app.parsers.sitemap_family import parse_sitemap_family_item

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    original = dict(pipeline._PRODUCT_REGISTRY.get("tissot", {}))
    try:
        cfg = pipeline._PRODUCT_REGISTRY.setdefault("tissot", {})
        cfg.update({
            "collector_cls": _FakeSitemapCollector,
            "collector_id": "tissot_sitemap",
            "collector_version": "test",
            "parse_fn": partial(parse_sitemap_family_item, manufacturer="Tissot"),
            "default_region": "US",
            "offline_kwarg": "sitemap_payload",
            "default_max_items": 10,
            "known_urls_from_observations": True,
            "manufacturer": "Tissot",
        })

        for run_no in range(1, 6):
            run = pipeline.run_product_observation_pipeline("tissot", max_items=10)
            meta = run.summary_metadata or {}
            n_ev = len(meta.get("events", []))
            assert n_ev == 0, (
                f"run {run_no} emitted {n_ev} events during initial catalogue "
                "fill — Law 1 slow-drip flood regression"
            )

        # Traversal progressed: all 30 SKUs observed across the runs.
        distinct = db_session.scalar(
            select(func.count(func.distinct(SourceObservation.watch_id)))
            .select_from(SourceObservation)
            .where(SourceObservation.collector_id == "tissot_sitemap")
        )
        assert distinct == 30, f"traversal stalled at {distinct}/30"

        # Honest limitation held everywhere.
        bad = db_session.scalar(
            select(func.count()).select_from(SourceObservation)
            .where(SourceObservation.collector_id == "tissot_sitemap")
            .where(SourceObservation.price.isnot(None))
        )
        assert bad == 0
    finally:
        if original:
            pipeline._PRODUCT_REGISTRY["tissot"] = original
