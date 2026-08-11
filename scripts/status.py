"""One-command health/status check -- works anywhere (Windows, cloud/Linux),
no GUI required. Wraps app.services.health.get_health_snapshot, the same
function the Windows Control Centre's RUN HEALTH CHECK button calls, so the
two can never disagree about what "healthy" means.

Usage:
    python -m scripts.status
    python -m scripts.status --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.db.session import get_engine, session_scope
from app.services.health import get_health_snapshot


def _print_text(snap) -> None:
    print(f"Watch Clank status @ {snap.generated_at}")
    print()
    print(f"Schema:       {'OK' if snap.schema.matches else 'MISMATCH'} "
          f"(actual={snap.schema.actual_version}, expected={snap.schema.expected_head})")
    print(f"DB integrity: {'OK' if snap.db_integrity_ok else 'FAILED'} ({snap.db_integrity_detail})")
    print(f"Total watches: {snap.total_watches}")
    print(f"Latest observation: {snap.latest_observation_at or 'none'}")
    print(f"Latest official event: {snap.latest_event_at or 'none'}")
    print(f"Latest specialist lead: {snap.latest_specialist_lead_at or 'none'}")
    print(f"Stale RUNNING rows: {snap.stale_running_count}")
    print(f"Active locks: {', '.join(snap.active_locks) or 'none'}")
    print()
    print("Sources:")
    for s in snap.sources:
        overdue = " [HEARTBEAT OVERDUE]" if s.heartbeat_overdue else ""
        print(f"  {s.collector_id:20s} {s.state:10s} last_success={s.last_success_at or 'never'}{overdue}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    engine = get_engine()
    with session_scope() as session:
        snap = get_health_snapshot(session, settings, engine=engine)

    if args.json:
        print(json.dumps(
            {
                "generated_at": snap.generated_at,
                "schema_matches": snap.schema.matches,
                "schema_actual": snap.schema.actual_version,
                "schema_expected": snap.schema.expected_head,
                "db_integrity_ok": snap.db_integrity_ok,
                "total_watches": snap.total_watches,
                "latest_observation_at": snap.latest_observation_at,
                "latest_event_at": snap.latest_event_at,
                "latest_specialist_lead_at": snap.latest_specialist_lead_at,
                "stale_running_count": snap.stale_running_count,
                "active_locks": snap.active_locks,
                "sources": [
                    {
                        "collector_id": s.collector_id,
                        "state": s.state,
                        "last_success_at": s.last_success_at,
                        "last_failure_at": s.last_failure_at,
                        "last_item_count": s.last_item_count,
                        "heartbeat_overdue": s.heartbeat_overdue,
                    }
                    for s in snap.sources
                ],
            },
            indent=2,
        ))
    else:
        _print_text(snap)

    unhealthy = not snap.schema.matches or not snap.db_integrity_ok or any(
        s.state == "FAILED" for s in snap.sources
    )
    return 1 if unhealthy else 0


if __name__ == "__main__":
    sys.exit(main())
