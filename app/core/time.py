"""Single source of truth for operational timestamp handling.

Policy: all operational timestamps are persisted and compared in UTC.
SQLite does not actually preserve timezone offsets even for columns declared
``DateTime(timezone=True)``, so values reloaded from the database come back
naive. Any code comparing a persisted timestamp against a fresh
timezone-aware ``now()`` must normalize the persisted value first via
``ensure_utc``.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current time, timezone-aware, in UTC."""
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Normalize a possibly-naive persisted datetime to aware UTC.

    Naive values (e.g. reloaded from SQLite) are assumed to already be UTC,
    since that is the only policy this codebase writes under.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
