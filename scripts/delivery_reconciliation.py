"""Report alerts the provider accepted but nobody has proven were visible.

Track F.5. The failure this exists to make impossible: Citizen JY8144-50E
(event 442) recorded `alerted=true` / `delivery.state=sent` on 2026-09-01,
the operator saw nothing, and there was no surface anywhere that would have
flagged the discrepancy. Acceptance by Discord is evidence that a request
succeeded, not that a human can see the message.

Read-only. Sends nothing, changes nothing, and never prints a webhook URL --
destinations appear only as their redacted alias.

Usage:
    python -m scripts.delivery_reconciliation [--older-than-minutes N] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta

from app.db.session import session_scope
from app.models import UNVERIFIED_ACCEPTED_STATES, DeliveryReceipt

# Anything accepted but still unidentified after this long is worth a look:
# a webhook post that never returned a message id means the provider took it
# without telling us where it landed.
DEFAULT_OLDER_THAN_MINUTES = 60


def collect(session, *, older_than_minutes: int) -> dict:
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)

    receipts = (
        session.query(DeliveryReceipt)
        .filter(DeliveryReceipt.lifecycle_state.in_(UNVERIFIED_ACCEPTED_STATES))
        .filter(DeliveryReceipt.reconciled_at.is_(None))
        .order_by(DeliveryReceipt.id.desc())
        .all()
    )
    failed = (
        session.query(DeliveryReceipt)
        .filter(DeliveryReceipt.lifecycle_state == "FAILED")
        .order_by(DeliveryReceipt.id.desc())
        .limit(50)
        .all()
    )

    def _row(r: DeliveryReceipt) -> dict:
        return {
            "receipt_id": r.id,
            "entity": f"{r.entity_type}:{r.entity_id}",
            "purpose": r.purpose,
            "lifecycle_state": r.lifecycle_state,
            "destination_alias": r.destination_alias,
            "provider_status": r.provider_status,
            "provider_message_id": r.provider_message_id,
            "provider_channel_id": r.provider_channel_id,
            "attempt_count": r.attempt_count,
            "last_attempt_at": r.last_attempt_at.isoformat() if r.last_attempt_at else None,
        }

    aged = [r for r in receipts if r.last_attempt_at and r.last_attempt_at.replace(tzinfo=UTC) < cutoff]
    no_identity = [r for r in aged if not r.provider_message_id]

    # Distinct destinations are the single most useful operator signal: if
    # alerts split across two aliases, the webhook changed underneath and
    # half the alerts went somewhere nobody is reading.
    destinations: dict[str, int] = {}
    for r in receipts:
        destinations[r.destination_alias or "UNKNOWN"] = destinations.get(r.destination_alias or "UNKNOWN", 0) + 1

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "older_than_minutes": older_than_minutes,
        "accepted_unverified_total": len(receipts),
        "accepted_unverified_aged": len(aged),
        "accepted_without_message_identity": len(no_identity),
        "destinations_seen": destinations,
        "aged_sample": [_row(r) for r in aged[:25]],
        "recent_failures": [_row(r) for r in failed[:25]],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--older-than-minutes", type=int, default=DEFAULT_OLDER_THAN_MINUTES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with session_scope() as session:
        report = collect(session, older_than_minutes=args.older_than_minutes)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Delivery reconciliation @ {report['generated_at']}")
    print()
    print(f"Accepted but unverified (total):      {report['accepted_unverified_total']}")
    print(f"  ... older than {report['older_than_minutes']} min:            {report['accepted_unverified_aged']}")
    print(f"  ... with no provider message id:    {report['accepted_without_message_identity']}")
    print()
    print("Destinations seen (redacted aliases):")
    for alias, count in sorted(report["destinations_seen"].items(), key=lambda kv: -kv[1]):
        print(f"  {alias:28} {count}")
    if len(report["destinations_seen"]) > 1:
        print("  NOTE: more than one destination in history -- the webhook changed at some point.")
    print()
    if report["aged_sample"]:
        print("Aged accepted-but-unverified:")
        for row in report["aged_sample"]:
            print(
                f"  #{row['receipt_id']:<6} {row['entity']:<28} {row['purpose']:<20} "
                f"{row['lifecycle_state']:<20} msg_id={row['provider_message_id'] or '-'}"
            )
    if report["recent_failures"]:
        print()
        print("Recent failures:")
        for row in report["recent_failures"]:
            print(
                f"  #{row['receipt_id']:<6} {row['entity']:<28} status={row['provider_status']} "
                f"attempts={row['attempt_count']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
