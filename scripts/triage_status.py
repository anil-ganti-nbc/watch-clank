"""Report HIGH-priority alerts that nobody has reviewed yet (track G.4).

The failure this exists for: Casio Frogman GWF-D1000BC-1JF (2026-08-29) and
the MASTER IN HORIZON GOLD trio (2026-08-27) were both collected, evented
and delivered successfully -- and then buried. Nothing anywhere counted
"useful alerts you have not looked at", so a missed one left no trace.

Read-only. Sends nothing, changes nothing, and never routes or suppresses
an alert -- delivery behaviour is unchanged by design (see
app.services.alert_priority).

Usage:
    python -m scripts.triage_status [--limit N] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys

from app.db.session import session_scope
from app.models import Event, EventReview
from app.services.alert_priority import PRIORITY_HIGH

DEFAULT_SCAN_LIMIT = 500


def collect(session, *, limit: int = DEFAULT_SCAN_LIMIT) -> dict:
    """Scan the most recent events for unreviewed HIGH-priority alerts.

    Bounded scan rather than a JSON-path SQL query: priority lives in
    Event.extra JSON, and a bounded, explicit Python filter is portable and
    obvious. Events created before this policy shipped simply carry no
    priority label and are reported separately as unlabelled rather than
    being silently assumed NORMAL.
    """
    events = session.query(Event).order_by(Event.id.desc()).limit(limit).all()
    reviewed_ids = {
        row[0]
        for row in session.query(EventReview.event_id)
        .filter(EventReview.event_id.in_([e.id for e in events] or [0]))
        .all()
    }

    high, unlabelled, groups = [], 0, {}
    for event in events:
        extra = event.extra or {}
        priority = extra.get("priority")
        if not isinstance(priority, dict):
            unlabelled += 1
            continue
        group = extra.get("launch_group")
        if group:
            groups.setdefault(group, []).append(event.id)
        if priority.get("tier") != PRIORITY_HIGH:
            continue
        if event.id in reviewed_ids:
            continue
        delivery = extra.get("delivery") or {}
        high.append(
            {
                "event_id": event.id,
                "event_type": event.event_type,
                "title": event.title,
                "story_score": event.story_score,
                "priority_reasons": priority.get("reasons", []),
                "launch_group": group,
                "delivery_state": delivery.get("state"),
                "delivery_lifecycle": delivery.get("lifecycle_state"),
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
        )

    clustered = {k: v for k, v in groups.items() if len(v) > 1}
    return {
        "scanned_events": len(events),
        "unreviewed_high_priority": len(high),
        "events_without_priority_label": unlabelled,
        "launch_clusters": clustered,
        "items": high,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=DEFAULT_SCAN_LIMIT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with session_scope() as session:
        report = collect(session, limit=args.limit)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Triage status (last {report['scanned_events']} events)")
    print()
    print(f"Unreviewed HIGH priority:        {report['unreviewed_high_priority']}")
    print(f"Events with no priority label:   {report['events_without_priority_label']}")
    print(f"Launch clusters (>1 reference):  {len(report['launch_clusters'])}")
    print()
    if report["items"]:
        print("Unreviewed HIGH-priority alerts:")
        for item in report["items"]:
            reasons = ", ".join(item["priority_reasons"])
            print(f"  #{item['event_id']:<6} {item['event_type']:<20} {item['title']}")
            print(f"          why: {reasons}")
            print(f"          delivery: {item['delivery_state']} / {item['delivery_lifecycle']}")
    else:
        print("No unreviewed HIGH-priority alerts.")
    if report["launch_clusters"]:
        print()
        print("Launch clusters:")
        for key, ids in report["launch_clusters"].items():
            print(f"  {key}: events {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
