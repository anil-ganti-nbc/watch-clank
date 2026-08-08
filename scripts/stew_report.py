"""Seven-day (or N-day) stew-period operational report."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import session_scope
from app.models import CollectorRun, SnapshotBlob, SourceObservation, Watch

setup_logging()


def build_report(days: int = 7) -> dict:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as session:
        runs = (
            session.scalars(
                select(CollectorRun)
                .where(CollectorRun.started_at >= cutoff)
                .order_by(CollectorRun.started_at)
            )
            .all()
        )
        status_counts = Counter(r.status for r in runs)
        total_new_watches = sum(r.new_watch_count or 0 for r in runs)
        total_observations = sum(r.observation_count or 0 for r in runs)
        total_warnings = sum(r.warning_count or 0 for r in runs)
        durations = [r.duration_ms for r in runs if r.duration_ms]
        longest_ms = max(durations) if durations else None

        successful = [r for r in runs if r.status == "SUCCESS"]
        first_success = successful[0].started_at.isoformat() if successful else None
        last_success = successful[-1].started_at.isoformat() if successful else None

        # Consecutive failure streak (from most recent backwards)
        streak = 0
        for r in reversed(runs):
            if r.status in ("FAILED", "BLOCKED"):
                streak += 1
            elif r.status == "SKIPPED_OVERLAP":
                continue
            else:
                break

        # Failure types from summary_metadata
        failure_types: Counter[str] = Counter()
        for r in runs:
            if r.status in ("FAILED", "BLOCKED"):
                meta = r.summary_metadata or {}
                if r.status == "BLOCKED":
                    failure_types["BLOCKED_403"] += 1
                elif meta.get("fatal_error"):
                    failure_types["fatal"] += 1
                elif meta.get("stale_recovery"):
                    failure_types["stale_recovery"] += 1
                else:
                    failure_types["other_failed"] += 1

        blob_count = session.scalar(select(func.count()).select_from(SnapshotBlob)) or 0
        blob_bytes = session.scalar(select(func.coalesce(func.sum(SnapshotBlob.byte_size), 0))) or 0
        watch_count = session.scalar(select(func.count()).select_from(Watch)) or 0
        obs_total = session.scalar(select(func.count()).select_from(SourceObservation)) or 0

        db_path = settings.resolved_database_url.replace("sqlite:///", "")
        db_size = Path(db_path).stat().st_size if Path(db_path).exists() else 0

        useful = total_new_watches > 0 or total_observations > 0 or status_counts.get("SUCCESS", 0) > 0

        return {
            "period_days": days,
            "cutoff": cutoff.isoformat(),
            "generated_at": datetime.now(UTC).isoformat(),
            "total_runs": len(runs),
            "status_counts": dict(status_counts),
            "successful_runs": status_counts.get("SUCCESS", 0),
            "partial_runs": status_counts.get("PARTIAL", 0),
            "blocked_runs": status_counts.get("BLOCKED", 0),
            "failed_runs": status_counts.get("FAILED", 0),
            "overlap_skips": status_counts.get("SKIPPED_OVERLAP", 0),
            "zero_item_runs": status_counts.get("ZERO_ITEMS", 0),
            "watches_discovered": total_new_watches,
            "source_observations_created": total_observations,
            "total_watches_in_db": watch_count,
            "total_observations_in_db": obs_total,
            "parser_warnings": total_warnings,
            "top_failure_types": failure_types.most_common(10),
            "snapshot_blob_count": blob_count,
            "snapshot_payload_bytes": int(blob_bytes),
            "database_size_bytes": db_size,
            "first_successful_run": first_success,
            "last_successful_run": last_success,
            "longest_run_duration_ms": longest_ms,
            "consecutive_failure_streak": streak,
            "produced_useful_data": useful,
            "schedule_interval_minutes": settings.schedule_interval_minutes,
        }


def format_human(report: dict) -> str:
    lines = [
        f"Watch Clank stew report — last {report['period_days']} days",
        f"Generated: {report['generated_at']}",
        "",
        f"Total runs:            {report['total_runs']}",
        f"  SUCCESS:             {report['successful_runs']}",
        f"  PARTIAL:             {report['partial_runs']}",
        f"  BLOCKED:             {report['blocked_runs']}",
        f"  FAILED:              {report['failed_runs']}",
        f"  SKIPPED_OVERLAP:     {report['overlap_skips']}",
        f"  ZERO_ITEMS:          {report['zero_item_runs']}",
        "",
        f"Watches discovered:    {report['watches_discovered']}",
        f"Observations created:  {report['source_observations_created']}",
        f"Total watches in DB:   {report['total_watches_in_db']}",
        f"Parser warnings:       {report['parser_warnings']}",
        f"Snapshot blobs:        {report['snapshot_blob_count']} ({report['snapshot_payload_bytes']} bytes)",
        f"Database size:         {report['database_size_bytes']} bytes",
        "",
        f"First successful run:  {report['first_successful_run']}",
        f"Last successful run:   {report['last_successful_run']}",
        f"Longest run (ms):      {report['longest_run_duration_ms']}",
        f"Failure streak:        {report['consecutive_failure_streak']}",
        f"Produced useful data:  {report['produced_useful_data']}",
        f"Expected interval:     {report['schedule_interval_minutes']} minutes",
        "",
        "Top failure types:",
    ]
    for name, cnt in report["top_failure_types"]:
        lines.append(f"  {name}: {cnt}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch Clank stew-period report")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()
    report = build_report(args.days)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_human(report))


if __name__ == "__main__":
    main()
