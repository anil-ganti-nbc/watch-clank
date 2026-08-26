"""Three-way resolved-max_items persistence regression (owner directive).

The initial-fill window qualifies runs by INVOCATION provenance: the
runner's resolved max_items persisted in CollectorRun.summary_metadata.
This test drives the REAL runner (no stubs on the metadata path) and pins
that all three invocation modes persist their resolved value verbatim:

  1. explicit small cap   -> max_items = the cap (e.g. 10)  -> never qualifies
  2. force_baseline pass  -> max_items = null (unbounded)   -> qualifies
  3. steady default run   -> max_items = registry default   -> qualifies

A missing/absent field (legacy row) is also pinned as non-qualifying.
Without this persistence, _is_catalogue_pass has nothing to read and the
window logic silently degrades to guesswork.
"""
import json

from sqlalchemy import select

from app.models import CollectorRun
from app.services.initial_fill import INITIAL_FILL_RUNS, initial_fill_active
from tests.test_core import db_session, tmp_settings  # noqa: F401 -- pytest fixtures


class _CountingCollector:
    """Serves `discovered` items, honouring max_items like a real collector."""

    CATALOGUE = [f"SKU{i:04d}" for i in range(50)]
    collector_id = "persist_collector"
    collector_version = "test"
    region = "US"
    trust_score = 70.0

    def __init__(self):
        self._seen_urls = set()

    def run(self, *, max_items=None, sitemap_payload=None, known_product_urls=None):
        from app.collectors.base import CollectorRunResult, FetchResult

        result = CollectorRunResult(
            collector_id=self.collector_id, collector_version=self.collector_version,
            region=self.region, trust_score=self.trust_score,
        )
        urls = [f"https://x.test/{sku}.html" for sku in self.CATALOGUE]
        known = known_product_urls or set()
        unseen = [u for u in urls if u not in known]
        ordered = unseen + [u for u in urls if u in known]
        batch = ordered if max_items is None else ordered[:max_items]

        result.metadata = {
            "component_status": "SUCCESS",
            "candidate_count": len(urls),
            "discovered_count": len(batch),
        }
        for url in batch:
            ref = url.rsplit("/", 1)[-1][:-5]
            result.discovered.append(
                type("D", (), {"url": url, "title": None, "reference_hint": ref, "metadata": {}})()
            )
            result.fetched.append(FetchResult(
                url=url, success=True, status_code=200, content_type="application/json",
                payload=f'{{"reference": "{ref}"}}'.encode(),
            ))
        return result


def _register(pipeline):
    from functools import partial

    from app.parsers.sitemap_family import parse_sitemap_family_item

    cfg = pipeline._PRODUCT_REGISTRY.setdefault("persist_probe", {})
    cfg.update({
        "collector_cls": _CountingCollector,
        "collector_id": "persist_collector",
        "collector_version": "test",
        "parse_fn": partial(parse_sitemap_family_item, manufacturer="PersistProbe"),
        "default_region": "US",
        "offline_kwarg": "sitemap_payload",
        "default_max_items": 300,
        "known_urls_from_observations": True,
    })
    return cfg


def test_resolved_max_items_persisted_for_all_three_invocation_modes(
    db_session,  # noqa: F811 -- pytest fixture
    tmp_settings,  # noqa: F811 -- pytest fixture
):
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    original = dict(pipeline._PRODUCT_REGISTRY.get("persist_probe", {}))
    try:
        _register(pipeline)

        # Mode 1: explicit small cap -> resolved value == the cap itself.
        run1 = pipeline.run_product_observation_pipeline("persist_probe", max_items=10)
        assert run1.summary_metadata["max_items"] == 10, (
            f"explicit cap must persist verbatim, got {run1.summary_metadata.get('max_items')}"
        )

        # Mode 2: force_baseline pass -> unbounded -> persisted as null.
        run2 = pipeline.run_product_observation_pipeline(
            "persist_probe", force_baseline=True
        )
        assert run2.summary_metadata["max_items"] is None, (
            "force_baseline (unbounded) must persist explicit null"
        )

        # Mode 3: steady-state run with no argument -> registry default (300).
        run3 = pipeline.run_product_observation_pipeline("persist_probe")
        assert run3.summary_metadata["max_items"] == 300, (
            f"default-budget run must persist the registry default, "
            f"got {run3.summary_metadata.get('max_items')}"
        )

        # All three rows are readable from the DB exactly as the qualifier
        # reads them.
        rows = db_session.scalars(
            select(CollectorRun)
            .where(CollectorRun.collector_id == "persist_collector")
            .order_by(CollectorRun.id)
        ).all()
        def _meta(r):
            raw = r.summary_metadata
            if not raw:
                return {}
            return json.loads(raw) if isinstance(raw, str) else dict(raw)

        persisted = [_meta(r).get("max_items", "ABSENT") for r in rows]
        assert persisted == [10, None, 300], persisted

        # Qualification agrees with the persisted provenance: only the two
        # full passes qualify; the capped run does not.
        from app.services.initial_fill import _is_catalogue_pass

        qualified = sum(1 for r in rows if _is_catalogue_pass(r))
        assert qualified == 2, f"expected exactly 2 qualifying passes, got {qualified}"
        assert qualified < INITIAL_FILL_RUNS
    finally:
        if original:
            pipeline._PRODUCT_REGISTRY["persist_probe"] = original


def test_legacy_row_without_provenance_never_qualifies(db_session):  # noqa: F811 -- pytest fixture
    """Conservative fallback pin: a pre-provenance row must not count toward
    the ceiling — an unknown-history collector keeps its fill protection."""
    db_session.add(CollectorRun(
        collector_id="legacy_collector", collector_version="0.0",
        status="SUCCESS", discovered_count=500, new_watch_count=500,
        summary_metadata=None,  # predates the max_items field
    ))
    db_session.flush()

    assert initial_fill_active(db_session, "legacy_collector"), (
        "legacy row without max_items provenance must not consume the fill budget"
    )
