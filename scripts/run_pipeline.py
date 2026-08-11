"""CLI to run the Casio pipeline (fixture, live, or scheduled)."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.collectors.base import FetchResult
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.schema_check import check_schema
from app.db.session import get_engine, session_scope
from app.models import CollectorRun
from app.services.pipeline import PipelineService
from app.services.snapshot_storage import SnapshotStorageService

setup_logging()
logger = get_logger(__name__)

# Exit codes for scheduled / Task Scheduler consumption:
#   0 = SUCCESS, PARTIAL, ZERO_ITEMS, BLOCKED, SKIPPED_OVERLAP (nonfatal)
#   1 = pipeline FAILED
#   2 = setup / configuration / unhandled fatal exception
#   3 = migration / database failure -- schema does not match the code's
#       expected Alembic head. Run `python -m scripts.migrate` explicitly,
#       then retry. Never auto-applied here (see app/db/schema_check.py).
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_FATAL = 2
EXIT_SCHEMA_MISMATCH = 3


def run_fixture_mode(max_items: int = 5) -> int:
    fixtures_dir = ROOT / "tests" / "fixtures"
    with session_scope() as session:
        storage = SnapshotStorageService()
        pipeline = PipelineService(session, storage)
        run = CollectorRun(
            collector_id="casio_japan_fixture",
            collector_version="0.1.0",
            status="RUNNING",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()

        count = 0
        failures = 0
        for path in sorted(fixtures_dir.glob("casio_*.html")):
            if count + failures >= max_items:
                break
            payload = path.read_bytes()
            fr = FetchResult(
                url=f"file://{path}",
                success=True,
                status_code=200,
                content_type="text/html",
                payload=payload,
            )
            outcome = pipeline.process_fetch_result(fr, run_id=run.id)
            print(
                f"{path.name}: success={outcome['success']} "
                f"new_watch={outcome.get('new_watch')} error={outcome.get('error')}"
            )
            if outcome["success"]:
                count += 1
            else:
                failures += 1

        run.completed_at = datetime.now(UTC)
        run.parsed_count = count
        run.failure_count = failures
        if count and failures:
            run.status = "PARTIAL"
        elif count:
            run.status = "SUCCESS"
        elif failures:
            run.status = "FAILED"
        else:
            run.status = "ZERO_ITEMS"
        session.commit()
        print(f"Fixture pipeline finished. Run id={run.id} status={run.status}")
        return EXIT_OK if run.status != "FAILED" else EXIT_FAILED


def run_live_or_scheduled(max_items: int = 10, scheduled: bool = False) -> int:
    settings = get_settings()
    db_url = settings.resolved_database_url
    print(f"database_url={db_url}")
    logger.info("pipeline_start", database_url=db_url, scheduled=scheduled, max_items=max_items)

    schema = check_schema(get_engine())
    if not schema.matches:
        msg = (
            f"SCHEMA MISMATCH: database is at "
            f"{schema.actual_version or '(uninitialized)'}, code expects "
            f"{schema.expected_head}. Refusing to run -- this is exactly the "
            f"outage class documented in HANDOFF.md. Run "
            f"`python -m scripts.migrate` explicitly, then retry."
        )
        logger.error("schema_mismatch", expected=schema.expected_head, actual=schema.actual_version)
        print(msg)
        return EXIT_SCHEMA_MISMATCH

    with session_scope() as session:
        pipeline = PipelineService(session)
        try:
            run = pipeline.run_multi_source_pipeline(max_items=max_items)
        except Exception as exc:
            logger.exception("scheduled_run_fatal", error=str(exc))
            print(f"FATAL: {exc}")
            return EXIT_FATAL

        logger.info(
            "scheduled_run_finished",
            run_id=run.id,
            status=run.status,
            discovered=run.discovered_count,
            fetched=run.fetched_count,
            parsed=run.parsed_count,
            new_watches=run.new_watch_count,
            failures=run.failure_count,
        )
        print(
            f"Run id={run.id} status={run.status} "
            f"discovered={run.discovered_count} fetched={run.fetched_count} "
            f"parsed={run.parsed_count} new={run.new_watch_count} "
            f"fail={run.failure_count}"
        )

        # Nonfatal for scheduler: BLOCKED / SKIPPED_OVERLAP / ZERO_ITEMS / PARTIAL / SUCCESS
        if run.status in (
            "SUCCESS",
            "PARTIAL",
            "ZERO_ITEMS",
            "BLOCKED",
            "SKIPPED_OVERLAP",
        ):
            return EXIT_OK
        return EXIT_FAILED


def run_experimental_brand(brand: str, max_items: int = 10) -> int:
    """EXPERIMENTAL lane: Citizen/Seiko news discovery. Isolated overlap
    protection (own lock file + collector_id, see RunLockService), own
    collector_runs rows — cannot interact with the Casio production run
    started by run_live_or_scheduled above. Not part of --scheduled."""
    settings = get_settings()
    print(f"database_url={settings.resolved_database_url} brand={brand}")
    with session_scope() as session:
        pipeline = PipelineService(session)
        try:
            run = pipeline.run_brand_news_pipeline(brand, max_items=max_items)
        except Exception as exc:
            logger.exception("experimental_brand_run_fatal", brand=brand, error=str(exc))
            print(f"FATAL: {exc}")
            return EXIT_FATAL
        print(f"[{brand}] Run id={run.id} status={run.status} summary={run.summary_metadata}")
        if run.status in ("SUCCESS", "PARTIAL", "ZERO_ITEMS", "BLOCKED", "SKIPPED_OVERLAP"):
            return EXIT_OK
        return EXIT_FAILED


def run_experimental_product(brand: str, max_items: int = 300) -> int:
    """EXPERIMENTAL lane: Citizen/Seiko product/catalogue observation.
    Same isolation as run_experimental_brand above — own lock file +
    collector_id, own collector_runs rows, cannot touch Casio state."""
    settings = get_settings()
    print(f"database_url={settings.resolved_database_url} brand={brand}")
    with session_scope() as session:
        pipeline = PipelineService(session)
        try:
            run = pipeline.run_product_observation_pipeline(brand, max_items=max_items)
        except Exception as exc:
            logger.exception("experimental_product_run_fatal", brand=brand, error=str(exc))
            print(f"FATAL: {exc}")
            return EXIT_FATAL
        print(f"[{brand}] Run id={run.id} status={run.status} summary={run.summary_metadata}")
        if run.status in ("SUCCESS", "PARTIAL", "ZERO_ITEMS", "BLOCKED", "SKIPPED_OVERLAP"):
            return EXIT_OK
        return EXIT_FAILED


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch Clank Casio pipeline")
    parser.add_argument("--fixture-mode", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Scheduled one-shot with lock + exit codes",
    )
    parser.add_argument(
        "--experimental-brand",
        choices=["citizen", "seiko"],
        default=None,
        help="Run the EXPERIMENTAL Citizen/Seiko news-discovery lane instead of Casio",
    )
    parser.add_argument(
        "--experimental-product",
        choices=["citizen", "seiko"],
        default=None,
        help="Run the EXPERIMENTAL Citizen/Seiko product/catalogue-observation lane instead of Casio",
    )
    parser.add_argument(
        "--max-items", type=int, default=None,
        help="Override per-mode default (Casio modes default 10; --experimental-product defaults 300 to cover the full discovered catalogue)",
    )
    args = parser.parse_args()

    if args.experimental_brand:
        sys.exit(run_experimental_brand(args.experimental_brand, args.max_items or 10))
    if args.experimental_product:
        kwargs = {} if args.max_items is None else {"max_items": args.max_items}
        sys.exit(run_experimental_product(args.experimental_product, **kwargs))
    if args.fixture_mode:
        sys.exit(run_fixture_mode(args.max_items or 10))  # preserves prior argparse-default behavior (10, not the function's own 5)
    if args.live or args.scheduled:
        sys.exit(run_live_or_scheduled(args.max_items or 10, scheduled=args.scheduled))
    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
