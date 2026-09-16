"""Track H.4/H.5 tests: immutable reconciliation records that separate a
benchmark failure from a product-detection failure.

The Casio regional-availability recall (2026-08-27) could not be explained
afterwards because nothing linked an external editorial target back through
official evidence, identity, region observation, event semantics, QC review
and delivery outcome -- so "we missed it" and "the benchmark was wrong" were
indistinguishable.
"""
import pytest

from app.services.benchmark_reconciliation import (
    OUTCOME_BENCHMARK_UNVERIFIABLE,
    OUTCOME_DETECTED_AND_DELIVERED,
    OUTCOME_DETECTED_NOT_DELIVERED,
    OUTCOME_NOT_DETECTED_IN_COVERED_SOURCE,
    OUTCOME_NOT_DETECTED_SOURCE_GAP,
    OUTCOME_UNKNOWN,
    BenchmarkReconciliationStore,
    ReconciliationRecord,
)


def _store(tmp_path):
    return BenchmarkReconciliationStore(tmp_path / "bench.db")


def test_full_chain_is_recorded_link_by_link(tmp_path):
    """Each link is a separate column so a broken chain shows WHERE it
    broke -- the thing the Casio recall could not reconstruct."""
    store = _store(tmp_path)
    rid = store.append(
        ReconciliationRecord(
            benchmark_source="editorial-target-list",
            benchmark_claim="Casio MASTER IN HORIZON GOLD announced",
            benchmark_reference="GWF-D1000BC-1JF",
            official_evidence_url="https://www.casio.com/jp/watches/gshock/product.GWF-D1000BC-1JF/",
            watch_id=1, reference_canonical="GWF-D1000BC-1", region_observed="JP",
            event_id=42, event_type="NEW_REFERENCE", qc_review_disposition="USEFUL",
            delivery_receipt_id=7, delivery_lifecycle_state="PROVIDER_IDENTIFIED",
            outcome=OUTCOME_DETECTED_AND_DELIVERED,
        )
    )
    row = store.all_records()[0]
    assert row["id"] == rid
    assert row["event_type"] == "NEW_REFERENCE"
    assert row["qc_review_disposition"] == "USEFUL"
    assert row["delivery_lifecycle_state"] == "PROVIDER_IDENTIFIED"
    assert row["outcome"] == OUTCOME_DETECTED_AND_DELIVERED


def test_report_separates_benchmark_failure_from_detection_failure(tmp_path):
    """H.5: these have different owners. Collapsing them into one 'miss'
    count is precisely what made the recall unexplainable."""
    store = _store(tmp_path)
    common = {"benchmark_source": "corpus", "benchmark_claim": "c"}
    store.append(ReconciliationRecord(**common, outcome=OUTCOME_NOT_DETECTED_IN_COVERED_SOURCE))
    store.append(ReconciliationRecord(**common, outcome=OUTCOME_DETECTED_NOT_DELIVERED))
    store.append(ReconciliationRecord(**common, outcome=OUTCOME_NOT_DETECTED_SOURCE_GAP))
    store.append(ReconciliationRecord(**common, outcome=OUTCOME_BENCHMARK_UNVERIFIABLE))
    store.append(
        ReconciliationRecord(**common, outcome=OUTCOME_UNKNOWN, unknown_reason="snapshots not retained")
    )

    report = store.report()
    assert report["total"] == 5
    # Ours: missed in a source we DO cover, and found-but-not-delivered.
    assert report["product_detection_failures"] == 2
    # Not a detection defect: no lane covers that surface at all.
    assert report["coverage_limitations"] == 1
    # Theirs: the external claim could not be substantiated.
    assert report["benchmark_failures"] == 1
    assert report["unknown"] == 1


def test_unknown_must_state_why(tmp_path):
    """Fleet law: UNKNOWN stays UNKNOWN -- but a bare UNKNOWN is
    indistinguishable from an unfinished investigation and would later be
    re-read as an asserted miss."""
    with pytest.raises(ValueError, match="unknown_reason"):
        ReconciliationRecord(
            benchmark_source="corpus", benchmark_claim="c", outcome=OUTCOME_UNKNOWN
        )
    # With a reason it is accepted.
    ok = ReconciliationRecord(
        benchmark_source="corpus", benchmark_claim="c", outcome=OUTCOME_UNKNOWN,
        unknown_reason="run predates snapshot retention",
    )
    assert ok.unknown_reason


def test_invalid_outcome_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unsupported reconciliation outcome"):
        ReconciliationRecord(benchmark_source="c", benchmark_claim="c", outcome="MISS")


def test_records_are_immutable_and_corrections_append(tmp_path):
    """A reconciliation states what was known at a point in time. Rewriting
    it destroys the only evidence that the conclusion ever changed -- so a
    correction supersedes rather than edits."""
    store = _store(tmp_path)
    original = store.append(
        ReconciliationRecord(
            benchmark_source="corpus", benchmark_claim="Citizen JY8144-50E",
            benchmark_reference="JY8144-50E", outcome=OUTCOME_NOT_DETECTED_IN_COVERED_SOURCE,
            notes="believed a detection miss",
        )
    )
    corrected = store.supersede(
        original,
        ReconciliationRecord(
            benchmark_source="corpus", benchmark_claim="Citizen JY8144-50E",
            benchmark_reference="JY8144-50E", outcome=OUTCOME_DETECTED_NOT_DELIVERED,
            event_id=442, delivery_lifecycle_state="PROVIDER_ACCEPTED",
            notes="event 442 existed; delivery was accepted but never verified visible",
        ),
    )

    # There is no update/delete API at all.
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")

    live = store.all_records()
    assert len(live) == 1 and live[0]["id"] == corrected
    # The original survives, intact, with its original finding.
    everything = store.all_records(include_superseded=True)
    assert len(everything) == 2
    old = [r for r in everything if r["id"] == original][0]
    assert old["outcome"] == OUTCOME_NOT_DETECTED_IN_COVERED_SOURCE
    assert old["superseded_by"] == corrected


def test_store_is_a_separate_file_from_production(tmp_path):
    """The ADR-level choice: external truth of uncertain authority must not
    sit in, or be joinable from, the production schema."""
    path = tmp_path / "nested" / "bench.db"
    store = BenchmarkReconciliationStore(path)
    store.append(
        ReconciliationRecord(benchmark_source="c", benchmark_claim="c",
                             outcome=OUTCOME_NOT_DETECTED_SOURCE_GAP)
    )
    assert path.exists()
    # Its schema is self-contained; nothing about it is registered with the
    # production metadata/Alembic.
    from app.models import Base

    assert "benchmark_reconciliations" not in Base.metadata.tables
