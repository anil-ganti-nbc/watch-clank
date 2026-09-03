"""Immutable benchmark reconciliation records (track H.4 / H.5).

The Casio regional-availability recall (2026-08-27) could not be explained
afterwards: some variants produced weak `FIRST_SEEN_BY_CLANK` or
`NEW_REGION` events and some Discord acknowledgements, but nothing linked an
external editorial target back through official evidence, identity, region
observation, event semantics, QC review and delivery outcome. Without that
chain, "we missed it" and "the benchmark was wrong" are indistinguishable.

**Why a companion store, not the production schema** (the ADR-level choice
the archive flagged, decided 2026-09-03): a benchmark corpus is EXTERNAL
truth of uncertain authority. Production tables are the system's own
observations. Mixing them would let an unverified third-party claim acquire
the standing of a first-party observation, and would make the production
schema answer to a corpus whose authority has never been established. So
this lives in its own SQLite file, has its own DDL, is not managed by
Alembic, and is never joined to in production code paths.

**Immutability.** Records are append-only. There is deliberately no update
or delete API: a reconciliation is a statement about what was known at a
point in time, and rewriting it destroys the only evidence that the
conclusion ever changed. Correct a record by appending a superseding one.

**Fleet law.** An unreconstructable fact is recorded as UNKNOWN and never
backfilled as an asserted miss.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 1

# H.5: the whole point of this vocabulary is that a BENCHMARK failure and a
# PRODUCT-DETECTION failure are different findings with different owners,
# and collapsing them (as "miss") is what made the Casio recall
# unexplainable.
OUTCOME_DETECTED_AND_DELIVERED = "DETECTED_AND_DELIVERED"
OUTCOME_DETECTED_NOT_DELIVERED = "DETECTED_NOT_DELIVERED"
OUTCOME_DETECTED_DIFFERENT_SEMANTICS = "DETECTED_DIFFERENT_SEMANTICS"
OUTCOME_NOT_DETECTED_SOURCE_GAP = "NOT_DETECTED_SOURCE_GAP"
OUTCOME_NOT_DETECTED_IN_COVERED_SOURCE = "NOT_DETECTED_IN_COVERED_SOURCE"
OUTCOME_BENCHMARK_UNVERIFIABLE = "BENCHMARK_UNVERIFIABLE"
OUTCOME_UNKNOWN = "UNKNOWN"

VALID_OUTCOMES: frozenset[str] = frozenset(
    {
        OUTCOME_DETECTED_AND_DELIVERED,
        OUTCOME_DETECTED_NOT_DELIVERED,
        OUTCOME_DETECTED_DIFFERENT_SEMANTICS,
        OUTCOME_NOT_DETECTED_SOURCE_GAP,
        OUTCOME_NOT_DETECTED_IN_COVERED_SOURCE,
        OUTCOME_BENCHMARK_UNVERIFIABLE,
        OUTCOME_UNKNOWN,
    }
)

# Which side of the boundary each outcome lands on. A reconciliation report
# that cannot say "this was our failure" vs "this was the benchmark's" is
# not doing its job.
PRODUCT_DETECTION_FAILURES: frozenset[str] = frozenset(
    {OUTCOME_NOT_DETECTED_IN_COVERED_SOURCE, OUTCOME_DETECTED_NOT_DELIVERED}
)
COVERAGE_LIMITATIONS: frozenset[str] = frozenset({OUTCOME_NOT_DETECTED_SOURCE_GAP})
BENCHMARK_FAILURES: frozenset[str] = frozenset({OUTCOME_BENCHMARK_UNVERIFIABLE})

_DDL = """
CREATE TABLE IF NOT EXISTS benchmark_reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL,

    -- the external claim being reconciled
    benchmark_source TEXT NOT NULL,
    benchmark_claim TEXT NOT NULL,
    benchmark_reference TEXT,
    benchmark_observed_at TEXT,

    -- what production actually knew, each link recorded separately so a
    -- broken chain shows WHERE it broke
    official_evidence_url TEXT,
    watch_id INTEGER,
    reference_canonical TEXT,
    region_observed TEXT,
    event_id INTEGER,
    event_type TEXT,
    qc_review_disposition TEXT,
    delivery_receipt_id INTEGER,
    delivery_lifecycle_state TEXT,

    outcome TEXT NOT NULL,
    unknown_reason TEXT,
    notes TEXT,
    detail TEXT,

    superseded_by INTEGER
);
CREATE INDEX IF NOT EXISTS ix_bench_reference ON benchmark_reconciliations(benchmark_reference);
CREATE INDEX IF NOT EXISTS ix_bench_outcome ON benchmark_reconciliations(outcome);
"""


@dataclass(frozen=True)
class ReconciliationRecord:
    benchmark_source: str
    benchmark_claim: str
    outcome: str
    benchmark_reference: str | None = None
    benchmark_observed_at: str | None = None
    official_evidence_url: str | None = None
    watch_id: int | None = None
    reference_canonical: str | None = None
    region_observed: str | None = None
    event_id: int | None = None
    event_type: str | None = None
    qc_review_disposition: str | None = None
    delivery_receipt_id: int | None = None
    delivery_lifecycle_state: str | None = None
    unknown_reason: str | None = None
    notes: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(f"unsupported reconciliation outcome: {self.outcome}")
        if self.outcome == OUTCOME_UNKNOWN and not self.unknown_reason:
            # Fleet law: UNKNOWN stays UNKNOWN, but it must say WHY it is
            # unknown, otherwise it is indistinguishable from an unfinished
            # investigation and will be quietly re-read as a miss later.
            raise ValueError("UNKNOWN outcome requires an explicit unknown_reason")


class BenchmarkReconciliationStore:
    """Append-only store, in its own SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def append(self, record: ReconciliationRecord) -> int:
        """Append one immutable reconciliation. Returns its id."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO benchmark_reconciliations (
                    recorded_at, schema_version, benchmark_source, benchmark_claim,
                    benchmark_reference, benchmark_observed_at, official_evidence_url,
                    watch_id, reference_canonical, region_observed, event_id, event_type,
                    qc_review_disposition, delivery_receipt_id, delivery_lifecycle_state,
                    outcome, unknown_reason, notes, detail
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    datetime.now(UTC).isoformat(), SCHEMA_VERSION,
                    record.benchmark_source, record.benchmark_claim,
                    record.benchmark_reference, record.benchmark_observed_at,
                    record.official_evidence_url, record.watch_id,
                    record.reference_canonical, record.region_observed,
                    record.event_id, record.event_type, record.qc_review_disposition,
                    record.delivery_receipt_id, record.delivery_lifecycle_state,
                    record.outcome, record.unknown_reason, record.notes,
                    json.dumps(record.detail) if record.detail else None,
                ),
            )
            new_id = int(cur.lastrowid)
        logger.info(
            "benchmark_reconciliation_appended",
            record_id=new_id, outcome=record.outcome,
            reference=record.benchmark_reference, source=record.benchmark_source,
        )
        return new_id

    def supersede(self, record_id: int, replacement: ReconciliationRecord) -> int:
        """Correct a prior conclusion by APPENDING a new record and marking
        the old one superseded. The original row's findings are never
        rewritten -- that a conclusion changed is itself evidence.
        """
        new_id = self.append(replacement)
        with self._connect() as conn:
            conn.execute(
                "UPDATE benchmark_reconciliations SET superseded_by = ? WHERE id = ?",
                (new_id, record_id),
            )
        return new_id

    def all_records(self, *, include_superseded: bool = False) -> list[dict]:
        query = "SELECT * FROM benchmark_reconciliations"
        if not include_superseded:
            query += " WHERE superseded_by IS NULL"
        query += " ORDER BY id DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(query).fetchall()]

    def report(self) -> dict:
        """H.5: split findings by WHOSE failure they are.

        A benchmark corpus is external truth of uncertain authority, so
        "the benchmark asserted something we cannot substantiate" is a
        finding about the benchmark, not about detection. Reporting them
        together is what made the Casio recall unexplainable.
        """
        records = self.all_records()
        by_outcome: dict[str, int] = {}
        for row in records:
            by_outcome[row["outcome"]] = by_outcome.get(row["outcome"], 0) + 1

        return {
            "total": len(records),
            "by_outcome": by_outcome,
            "product_detection_failures": sum(
                by_outcome.get(o, 0) for o in PRODUCT_DETECTION_FAILURES
            ),
            "coverage_limitations": sum(by_outcome.get(o, 0) for o in COVERAGE_LIMITATIONS),
            "benchmark_failures": sum(by_outcome.get(o, 0) for o in BENCHMARK_FAILURES),
            "unknown": by_outcome.get(OUTCOME_UNKNOWN, 0),
        }
