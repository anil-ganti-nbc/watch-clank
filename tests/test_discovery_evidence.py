"""Track E tests: retain what a run selected from, and why it skipped things.

The evidence gap being closed (Casio JP AQ-230ECK-3A, run 5146,
2026-09-01): the sitemap document itself was discarded and no per-candidate
exclusion reason was recorded anywhere, so "was this reference in the feed
at the time, or did it appear later?" could not be answered afterwards --
and a one-cycle deferral was indistinguishable from a coverage failure.
"""
from app.collectors.casio_jp_sitemap import MAX_CANDIDATES, CasioJPSitemapCollector

SITEMAP_HEAD = '<?xml version="1.0" encoding="UTF-8"?><urlset>'
SITEMAP_TAIL = "</urlset>"


def _sitemap(references: list[str]) -> bytes:
    body = "".join(
        f"<url><loc>https://www.casio.com/jp/watches/gshock/product.{r}/</loc>"
        f"<lastmod>2026-09-01</lastmod></url>"
        for r in references
    )
    return (SITEMAP_HEAD + body + SITEMAP_TAIL).encode("utf-8")


def test_run_reports_selection_policy_and_deferral_reason():
    payload = _sitemap([f"REF{i:04d}" for i in range(10)])
    result = CasioJPSitemapCollector().run(max_items=4, sitemap_payload=payload)

    selection = result.metadata["selection"]
    assert selection["candidate_count"] == 10
    assert selection["selected_count"] == 4
    assert selection["deferred_count"] == 6
    assert selection["deferred_reason"] == "per_run_item_budget"
    assert selection["max_items"] == 4
    # The deferred references are named, not just counted -- that is what
    # makes a later "was it in the feed?" question answerable.
    assert selection["deferred_sample"][0] == "REF0004"
    assert selection["policy"] == "document_order"


def test_unseen_first_policy_is_reported_when_known_urls_are_supplied():
    payload = _sitemap([f"REF{i:04d}" for i in range(10)])
    known = {"https://www.casio.com/jp/watches/gshock/product.REF0000/"}
    result = CasioJPSitemapCollector().run(
        max_items=4, sitemap_payload=payload, known_product_urls=known
    )
    selection = result.metadata["selection"]
    assert selection["policy"] == "unseen_first"
    # The known URL is deprioritised behind every unseen one, so it does not
    # consume budget ahead of genuinely new references.
    assert "REF0000" not in [i.reference_hint for i in result.discovered]


def test_nothing_deferred_reports_no_reason():
    payload = _sitemap(["REF0001", "REF0002"])
    result = CasioJPSitemapCollector().run(max_items=10, sitemap_payload=payload)
    selection = result.metadata["selection"]
    assert selection["deferred_count"] == 0
    assert selection["deferred_reason"] is None
    assert selection["truncated_at_max_candidates"] is False


def test_index_document_is_retained_for_later_reconstruction():
    payload = _sitemap(["REF0001"])
    result = CasioJPSitemapCollector().run(max_items=10, sitemap_payload=payload)
    assert len(result.discovery_payloads) == 1
    assert result.discovery_payloads[0].payload == payload


def test_max_candidates_ceiling_is_reported_distinctly_from_the_run_budget():
    """The ceiling that the incident archive did not identify: this caps
    what is even CONSIDERED, so candidates beyond it are invisible to every
    run regardless of per-run budget or prioritisation. It must be
    reported as its own fact, not folded into ordinary deferral."""
    payload = _sitemap([f"REF{i:05d}" for i in range(MAX_CANDIDATES + 25)])
    result = CasioJPSitemapCollector().run(max_items=None, sitemap_payload=payload)

    selection = result.metadata["selection"]
    assert selection["candidate_count"] == MAX_CANDIDATES
    assert selection["truncated_at_max_candidates"] is True
    assert selection["max_candidates"] == MAX_CANDIDATES
    # No per-run budget was applied here, so the ONLY thing that hid the
    # remaining references is the ceiling -- distinguishable in the record.
    assert selection["deferred_count"] == 0
    assert selection["deferred_reason"] is None


def test_pipeline_persists_discovery_evidence(db_session, tmp_settings):
    """The pipeline half: index snapshot stored content-addressed, and a
    ledger row naming the selection decision."""
    from app.models import CollectorRun, PipelineLedger
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    payload = _sitemap([f"REF{i:04d}" for i in range(10)])
    result = CasioJPSitemapCollector().run(max_items=4, sitemap_payload=payload)

    run = CollectorRun(collector_id="casio_jp_sitemap", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    recorded = pipeline._retain_discovery_evidence(result, run=run)
    db_session.commit()

    assert recorded is not None
    assert recorded["discovery_index_hashes"], "the index document must be retained"
    assert recorded["selection"]["deferred_count"] == 6

    entry = (
        db_session.query(PipelineLedger)
        .filter_by(run_id=run.id, stage="discovery_selection")
        .one()
    )
    assert entry.action == "candidates_selected"
    assert entry.metadata_["selection"]["deferred_reason"] == "per_run_item_budget"
    assert entry.metadata_["discovery_index_hashes"]


def test_evidence_retention_never_fails_a_run(db_session, tmp_settings, monkeypatch):
    """Retention is diagnostic, not operational: a storage failure must not
    be able to take down a collection run."""
    from app.models import CollectorRun
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    payload = _sitemap(["REF0001", "REF0002"])
    result = CasioJPSitemapCollector().run(max_items=1, sitemap_payload=payload)
    run = CollectorRun(collector_id="casio_jp_sitemap", collector_version="0.1.0", status="RUNNING")
    db_session.add(run)
    db_session.commit()

    storage = SnapshotStorageService(tmp_settings)
    monkeypatch.setattr(
        storage, "store", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full"))
    )
    pipeline = PipelineService(db_session, storage)
    recorded = pipeline._retain_discovery_evidence(result, run=run)  # must not raise

    # The selection record still survives even though the payload did not.
    assert recorded["discovery_index_hashes"] == []
    assert recorded["selection"]["selected_count"] == 1


from tests.test_core import (  # noqa: E402 -- fixture re-export, after helpers
    db_session,  # noqa: F401
    tmp_settings,  # noqa: F401
)
