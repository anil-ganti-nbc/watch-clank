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
        from app.services.discord_notify import DiscordNotifier

        DiscordNotifier(settings).send_health_alert(f"WATCH CLANK — OPS\n{msg}")
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


def run_experimental_brand(brand: str, max_items: int = 10, *, force_baseline: bool = False) -> int:
    """EXPERIMENTAL lane: Citizen/Seiko/Timex news discovery. Isolated overlap
    protection (own lock file + collector_id, see RunLockService), own
    collector_runs rows — cannot interact with the Casio production run
    started by run_live_or_scheduled above. Not part of --scheduled.

    force_baseline (Sprint 9): source-scoped silent baseline for a brand
    joining an already-baselined epoch -- see PipelineService._epoch_fields."""
    settings = get_settings()
    print(f"database_url={settings.resolved_database_url} brand={brand} force_baseline={force_baseline}")
    with session_scope() as session:
        pipeline = PipelineService(session)
        try:
            run = pipeline.run_brand_news_pipeline(brand, max_items=max_items, force_baseline=force_baseline)
        except Exception as exc:
            logger.exception("experimental_brand_run_fatal", brand=brand, error=str(exc))
            print(f"FATAL: {exc}")
            return EXIT_FATAL
        print(f"[{brand}] Run id={run.id} status={run.status} summary={run.summary_metadata}")
        if run.status in ("SUCCESS", "PARTIAL", "ZERO_ITEMS", "BLOCKED", "SKIPPED_OVERLAP"):
            return EXIT_OK
        return EXIT_FAILED


def run_experimental_product(brand: str, max_items: int | None = None, *, force_baseline: bool = False) -> int:
    """EXPERIMENTAL lane: Citizen (US/DE), Seiko, or Timex catalogue observation.
    Same isolation as run_experimental_brand above — own lock file +
    collector_id, own collector_runs rows, cannot touch Casio state.

    force_baseline (Sprint 9): source-scoped silent baseline -- see
    run_experimental_brand's docstring."""
    settings = get_settings()
    print(f"database_url={settings.resolved_database_url} brand={brand} force_baseline={force_baseline}")
    with session_scope() as session:
        pipeline = PipelineService(session)
        try:
            run = pipeline.run_product_observation_pipeline(brand, max_items=max_items, force_baseline=force_baseline)
        except Exception as exc:
            logger.exception("experimental_product_run_fatal", brand=brand, error=str(exc))
            print(f"FATAL: {exc}")
            return EXIT_FATAL
        print(f"[{brand}] Run id={run.id} status={run.status} summary={run.summary_metadata}")
        if run.status in ("SUCCESS", "PARTIAL", "ZERO_ITEMS", "BLOCKED", "SKIPPED_OVERLAP"):
            return EXIT_OK
        return EXIT_FAILED


def run_experimental_specialist(source: str, max_items: int = 20) -> int:
    """EXPERIMENTAL Layer B lane: specialist/early-warning source
    collectors (casioblog, gcentral, plus9time). Own lock file +
    collector_id per source, own collector_runs rows, writes only to
    specialist_leads — never to watches/source_observations/release_leads
    directly. See app/services/specialist_leads.py."""
    from app.services.specialist_leads import (
        run_casioblog_pipeline,
        run_gcentral_pipeline,
        run_plus9time_pipeline,
    )

    runners = {
        "casioblog": run_casioblog_pipeline,
        "gcentral": run_gcentral_pipeline,
        "plus9time": run_plus9time_pipeline,
    }

    settings = get_settings()
    print(f"database_url={settings.resolved_database_url} source={source}")
    with session_scope() as session:
        try:
            if source in runners:
                run = runners[source](session)
            else:
                print(f"FATAL: unknown specialist source {source!r}")
                return EXIT_FATAL
        except Exception as exc:
            logger.exception("experimental_specialist_run_fatal", source=source, error=str(exc))
            print(f"FATAL: {exc}")
            return EXIT_FATAL
        print(f"[{source}] Run id={run.id} status={run.status} summary={run.summary_metadata}")
        if run.status in ("SUCCESS", "PARTIAL", "ZERO_ITEMS", "BLOCKED", "SKIPPED_OVERLAP"):
            return EXIT_OK
        return EXIT_FAILED


def ingest_manual_lead(args: argparse.Namespace) -> int:
    """Manual ingestion endpoint for sources that cannot be safely
    automated (e.g. Instagram/@geesgshock — see HANDOFF.md Sprint 5:
    automated Instagram collection was deliberately not built to avoid
    bypassing authentication/anti-bot protections). A human pastes the
    post URL and what it claims; this stores it exactly like a collector
    would, with ingestion_method="manual" for traceability."""
    from app.services.specialist_leads import SpecialistLeadService

    if not args.lead_title or not args.lead_url:
        print("FATAL: --lead-title and --lead-url are required")
        return EXIT_FATAL

    with session_scope() as session:
        svc = SpecialistLeadService(session)
        outcome = svc.ingest_candidate(
            source_id=args.lead_source_id,
            lead_type=args.lead_type,
            title=args.lead_title,
            source_url=args.lead_url,
            published_at=args.lead_published_at,
            reference_candidates=args.lead_reference,
            claim_text=args.lead_claim,
            manufacturer=args.lead_manufacturer,
            confidence=args.lead_confidence,
            ingestion_method="manual",
        )
        session.commit()
        print(outcome)
        return EXIT_OK


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
        choices=["citizen", "seiko", "timex"],
        default=None,
        help="Run the EXPERIMENTAL Citizen/Seiko news-discovery lane instead of Casio",
    )
    parser.add_argument(
        "--experimental-product",
        choices=["citizen", "citizen_de", "seiko", "timex"],
        default=None,
        help="Run an EXPERIMENTAL product/catalogue-observation lane instead of Casio",
    )
    parser.add_argument(
        "--experimental-specialist",
        choices=["casioblog", "gcentral", "plus9time"],
        default=None,
        help="Run an EXPERIMENTAL Layer B (early-warning) specialist-source lane",
    )
    parser.add_argument(
        "--ingest-manual-lead",
        action="store_true",
        help="Manually ingest one early-warning lead (e.g. a @geesgshock post) — see --lead-* flags",
    )
    parser.add_argument("--lead-source-id", default="geesgshock_manual")
    parser.add_argument(
        "--lead-type",
        default="POSSIBLE_NEW_REFERENCE",
        choices=[
            "POSSIBLE_NEW_REFERENCE", "POSSIBLE_COLLABORATION", "POSSIBLE_NEW_REGION", "POSSIBLE_PRICE",
            "POSSIBLE_RELEASE_DATE", "POSSIBLE_DISCONTINUATION", "POSSIBLE_LIMITED_EDITION",
            "LEAKED_IMAGE", "EARLY_RETAIL_LISTING",
        ],
    )
    parser.add_argument("--lead-title", default=None)
    parser.add_argument("--lead-url", default=None)
    parser.add_argument("--lead-reference", action="append", default=[])
    parser.add_argument("--lead-claim", default=None)
    parser.add_argument("--lead-published-at", default=None, help="ISO 8601 timestamp, if known")
    parser.add_argument("--lead-manufacturer", default="Casio")
    parser.add_argument("--lead-confidence", type=float, default=35.0)
    parser.add_argument(
        "--max-items", type=int, default=None,
        help="Override per-mode default (Casio modes default 10; product lanes use their documented per-source limits)",
    )
    parser.add_argument(
        "--force-baseline",
        action="store_true",
        help="Sprint 9: source-scoped silent baseline for a brand joining an already-baselined "
        "epoch (e.g. Timex joining Epoch 1) -- creates watches/observations/leads normally but "
        "suppresses Event creation and Discord alerts for this run only. Only meaningful with "
        "--experimental-brand/--experimental-product.",
    )
    args = parser.parse_args()

    if args.ingest_manual_lead:
        sys.exit(ingest_manual_lead(args))
    if args.experimental_specialist:
        sys.exit(run_experimental_specialist(args.experimental_specialist, args.max_items or 20))
    if args.experimental_brand:
        sys.exit(run_experimental_brand(args.experimental_brand, args.max_items or 10, force_baseline=args.force_baseline))
    if args.experimental_product:
        kwargs = {} if args.max_items is None else {"max_items": args.max_items}
        sys.exit(run_experimental_product(args.experimental_product, force_baseline=args.force_baseline, **kwargs))
    if args.fixture_mode:
        sys.exit(run_fixture_mode(args.max_items or 10))  # preserves prior argparse-default behavior (10, not the function's own 5)
    if args.live or args.scheduled:
        sys.exit(run_live_or_scheduled(args.max_items or 10, scheduled=args.scheduled))
    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
