"""Operational epoch lifecycle CLI (Sprint 7). No manual SQL required.

Usage:
    python -m scripts.epoch start --name epoch_1 --notes "..."
    python -m scripts.epoch baseline-start
    python -m scripts.epoch baseline-complete
    python -m scripts.epoch status
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import session_scope
from app.services.epoch import complete_baseline, get_active_epoch, start_baseline, start_epoch


def cmd_start(args) -> int:
    with session_scope() as session:
        if get_active_epoch(session) is not None and not args.force:
            existing = get_active_epoch(session)
            print(f"REFUSING: an epoch already exists ('{existing.name}', id={existing.id}). Use --force to add another.")
            return 1
        epoch = start_epoch(session, name=args.name, notes=args.notes)
        print(f"Started epoch '{epoch.name}' (id={epoch.id}) at {epoch.started_at}")
    return 0


def cmd_baseline_start(_args) -> int:
    with session_scope() as session:
        epoch = get_active_epoch(session)
        if epoch is None:
            print("REFUSING: no active epoch. Run `python -m scripts.epoch start` first.")
            return 1
        try:
            start_baseline(session, epoch)
        except ValueError as exc:
            print(f"REFUSING: {exc}")
            return 1
        print(f"Baseline started for epoch '{epoch.name}' at {epoch.baseline_started_at}")
    return 0


def cmd_baseline_complete(_args) -> int:
    with session_scope() as session:
        epoch = get_active_epoch(session)
        if epoch is None:
            print("REFUSING: no active epoch.")
            return 1
        try:
            complete_baseline(session, epoch)
        except ValueError as exc:
            print(f"REFUSING: {exc}")
            return 1
        print(f"Baseline completed for epoch '{epoch.name}' at {epoch.baseline_completed_at}")
    return 0


def cmd_status(_args) -> int:
    with session_scope() as session:
        epoch = get_active_epoch(session)
        if epoch is None:
            print("No epoch exists yet.")
            return 0
        print(f"Active epoch: {epoch.name} (id={epoch.id})")
        print(f"  started_at:            {epoch.started_at}")
        print(f"  baseline_started_at:   {epoch.baseline_started_at or '(not started)'}")
        print(f"  baseline_completed_at: {epoch.baseline_completed_at or '(not completed)'}")
        if epoch.baseline_started_at and not epoch.baseline_completed_at:
            print("  STATE: BASELINE IN PROGRESS -- events/alerts are suppressed")
        elif epoch.baseline_completed_at:
            print("  STATE: LIVE -- normal event/alert rules apply")
        else:
            print("  STATE: baseline not yet started")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--name", default="epoch_1")
    p_start.add_argument("--notes", default=None)
    p_start.add_argument("--force", action="store_true")

    sub.add_parser("baseline-start")
    sub.add_parser("baseline-complete")
    sub.add_parser("status")

    args = parser.parse_args()
    handlers = {
        "start": cmd_start,
        "baseline-start": cmd_baseline_start,
        "baseline-complete": cmd_baseline_complete,
        "status": cmd_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
