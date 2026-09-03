"""Measure how long a qualified event waits before it is handed off.

Track A (Seiko Rukia Liberty Fabrics trio HEG004J / HEE007J / HEA005J,
2026-09-03): discovery, freshness and angle selection were all sound, but
the draft was ready only after competing coverage appeared. No collector,
source or transport defect -- a latency loss.

**Scope boundary, stated honestly.** Watch Clank owns detection and QC. It
does NOT own the editorial workflow: `Event.status` has no publication
vocabulary (it defaults to DRAFT and stays there), and EventReview
dispositions are QC verdicts (USEFUL / NOT_USEFUL / DUPLICATE /
FALSE_POSITIVE / OUT_OF_STOCK), not publication states. So of the four
timestamps track A proposed:

  qualified_at          OWNED    -- Event.created_at
  editorial_handoff_at  OWNED    -- DeliveryReceipt.first_attempt_at (track F)
  draft_started_at      NOT OWNED -- lives in the editorial tool
  published_or_lost_at  NOT OWNED -- lives in the CMS / the market

This report measures the two Watch Clank can prove and leaves the other
two explicitly UNKNOWN rather than inferring them. Nothing here decides
whether to publish; the expiry-risk column is an observation, not an
instruction, and no automated publication decision is made anywhere.

No new columns were added: both owned timestamps already exist, so
duplicating them into Event would create two sources of truth for the same
fact.

Usage:
    python -m scripts.opportunity_latency [--limit N] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from app.db.session import session_scope
from app.models import DeliveryReceipt, Event
from app.services.alert_priority import PRIORITY_HIGH

DEFAULT_LIMIT = 200

# A limited edition or collaboration is the class that actually expires:
# by the time competing coverage lands, the opportunity is gone. Matches
# the HIGH tier operator decision of 2026-09-03 exactly rather than
# inventing a second, divergent notion of urgency.
EXPIRY_SENSITIVE_TIER = PRIORITY_HIGH


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def collect(session, *, limit: int = DEFAULT_LIMIT) -> dict:
    events = session.query(Event).order_by(Event.id.desc()).limit(limit).all()
    receipts = {
        r.entity_id: r
        for r in session.query(DeliveryReceipt)
        .filter(DeliveryReceipt.entity_type == "EVENT")
        .all()
    }

    rows, measured = [], []
    for event in events:
        extra = event.extra or {}
        priority = extra.get("priority") if isinstance(extra.get("priority"), dict) else {}
        receipt = receipts.get(str(event.id))
        qualified_at = _aware(event.created_at)
        handoff_at = _aware(receipt.first_attempt_at) if receipt else None

        latency_seconds = None
        if qualified_at and handoff_at:
            latency_seconds = (handoff_at - qualified_at).total_seconds()
            measured.append(latency_seconds)

        rows.append(
            {
                "event_id": event.id,
                "title": event.title,
                "priority_tier": priority.get("tier"),
                "qualified_at": qualified_at.isoformat() if qualified_at else None,
                "editorial_handoff_at": handoff_at.isoformat() if handoff_at else None,
                "handoff_latency_seconds": latency_seconds,
                # Deliberately UNKNOWN, never inferred: Watch Clank cannot
                # see the editorial tool or the CMS.
                "draft_started_at": "UNKNOWN",
                "published_or_lost_at": "UNKNOWN",
                "expiry_sensitive": priority.get("tier") == EXPIRY_SENSITIVE_TIER,
                "awaiting_handoff": receipt is None,
            }
        )

    expiry_sensitive_unhanded = [
        r for r in rows if r["expiry_sensitive"] and r["awaiting_handoff"]
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scanned_events": len(events),
        "measured_handoffs": len(measured),
        "median_handoff_latency_seconds": (
            sorted(measured)[len(measured) // 2] if measured else None
        ),
        "max_handoff_latency_seconds": max(measured) if measured else None,
        "expiry_sensitive_awaiting_handoff": len(expiry_sensitive_unhanded),
        "unowned_timestamps": ["draft_started_at", "published_or_lost_at"],
        "items": rows[:50],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with session_scope() as session:
        report = collect(session, limit=args.limit)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Opportunity latency @ {report['generated_at']}")
    print()
    print(f"Events scanned:                     {report['scanned_events']}")
    print(f"Handoffs actually measured:         {report['measured_handoffs']}")
    print(f"Median qualified -> handoff (s):    {report['median_handoff_latency_seconds']}")
    print(f"Worst qualified -> handoff (s):     {report['max_handoff_latency_seconds']}")
    print(f"Expiry-sensitive, no handoff yet:   {report['expiry_sensitive_awaiting_handoff']}")
    print()
    print("Not owned by Watch Clank (reported UNKNOWN, never inferred):")
    for name in report["unowned_timestamps"]:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
