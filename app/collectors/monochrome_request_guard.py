"""Fail-closed, source-bound admission for MONOCHROME native feed attempts.

The existing collector_runs table is the durable attempt ledger. The caller holds
its existing per-source RunLockService lock and commits this reservation before
opening HTTP. No grant is created or renewed here.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CollectorRun


def reserve_monochrome_attempt(session: Session, run: CollectorRun, feed_url: str, lock) -> None:
    if (
        lock is None or lock.session is not session
        or lock.collector_id != "monochrome_rss"
        or lock._handle is None or lock._handle.closed
        or run.id is None or run.collector_id != "monochrome_rss"
        or session.get(CollectorRun, run.id) is not run
    ):
        raise ValueError("MONOCHROME native source lock and run required")
    # A reservation is single-use per persisted run, regardless of which grant
    # the caller presents next. Check this before reading the new admission or
    # counting other rows: rewriting metadata would undercount outbound calls.
    session.refresh(run)
    if (run.summary_metadata or {}).get("monochrome_request", {}).get("attempts_charged") == 1:
        raise ValueError("MONOCHROME current run already charged")
    path = os.environ.get("MONOCHROME_REQUEST_ADMISSION")
    pinned = os.environ.get("MONOCHROME_REQUEST_ADMISSION_SHA256")
    if not path or not pinned or len(pinned) != 64:
        raise ValueError("MONOCHROME finite admission absent")
    try:
        raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pinned:
            raise ValueError("MONOCHROME admission hash mismatch")
        grant = json.loads(raw)
        grant_id = grant["grant_id"]
        limit = grant["max_attempts"]
        start = datetime.fromisoformat(grant["not_before"])
        end = datetime.fromisoformat(grant["expires_at"])
        if (
            grant["source_id"] != "monochrome"
            or grant["feed_url"] != feed_url
            or not isinstance(grant_id, str) or not grant_id.strip()
            or type(limit) is not int or limit < 1
            or start.tzinfo is None or end.tzinfo is None
            or not start.astimezone(UTC) <= datetime.now(UTC) < end.astimezone(UTC)
        ):
            raise ValueError("MONOCHROME admission invalid or expired")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("MONOCHROME admission unreadable or incomplete") from exc

    # Existing native source lock covers the read/commit pair across concurrent
    # native processes. Failed/stale runs stay charged; no in-memory reset.
    rows = session.scalars(
        select(CollectorRun).where(CollectorRun.collector_id == "monochrome_rss")
    ).all()
    # Old native runs did not record redirect/retry-inclusive attempts. No new
    # grant may start at sequence 1 until an operator reconciles that history;
    # this component does not migrate or forgive the exhausted four-fetch cap.
    if any(
        row.id != run.id and not (
            isinstance((row.summary_metadata or {}).get("monochrome_request"), dict)
            and (
                (row.summary_metadata["monochrome_request"].get("attempts_charged") == 1
                 and row.summary_metadata["monochrome_request"].get("admission_sha256"))
                or (row.summary_metadata["monochrome_request"].get("attempts_charged") == 0
                    and row.summary_metadata["monochrome_request"].get("disposition") == "NO_TRANSPORT")
            )
        )
        for row in rows
    ):
        raise ValueError("MONOCHROME legacy request history unresolved")
    reservations = [
        (row.summary_metadata or {}).get("monochrome_request", {}) for row in rows
    ]
    if any(
        entry.get("grant_id") == grant_id and entry.get("attempts_charged") == 1
        and entry.get("admission_sha256") != pinned
        for entry in reservations
    ):
        raise ValueError("MONOCHROME grant id reused with changed admission")
    used = sum(
        entry.get("admission_sha256") == pinned and entry.get("attempts_charged") == 1
        for entry in reservations
    )
    if used >= limit:
        raise ValueError("MONOCHROME finite attempt budget exhausted")
    run.summary_metadata = {
        **(run.summary_metadata or {}),
        "monochrome_request": {
            "grant_id": grant_id,
            "admission_sha256": pinned,
            "url": feed_url,
            "attempts_charged": 1,
            "reservation_sequence": used + 1,
        },
    }
    session.commit()
