"""Sentinel persistence: known-identity store, sightings, source arming.

Transaction discipline matches the fleet's item-level pattern: the runner
commits once per source, so a crash mid-sweep loses at most the in-flight
source's progress and never leaves a half-alerted source. The unique
constraint on sentinel_identities.identity_key is the final authority
against double admission -- admit() treats IntegrityError as "already
known" so even two racing processes cannot duplicate an identity.
"""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.sentinel import (
    SentinelIdentity,
    SentinelSighting,
    SentinelSourceState,
)
from app.sentinel.identity import SentinelIdentityResult

logger = get_logger(__name__)


class SentinelStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- identities --------------------------------------------------------

    def known_identity_keys(self) -> set[str]:
        rows = self.session.execute(select(SentinelIdentity.identity_key)).all()
        return {r[0] for r in rows}

    def get_identity(self, identity_key: str) -> SentinelIdentity | None:
        return (
            self.session.query(SentinelIdentity)
            .filter_by(identity_key=identity_key)
            .first()
        )

    def admit(
        self,
        identity: SentinelIdentityResult,
        *,
        source: str,
        region: str | None,
        url: str,
        title: str | None,
        via: str,
        now: datetime,
    ) -> tuple[SentinelIdentity, bool]:
        """Insert the identity if unseen. Returns (row, created).

        IntegrityError-safe: under any race the unique constraint is the
        authority, and the pre-existing row is returned with created=False.
        """
        existing = self.get_identity(identity.identity_key)
        if existing is not None:
            return existing, False
        row = SentinelIdentity(
            identity_key=identity.identity_key,
            identity_type=identity.identity_type,
            manufacturer=identity.manufacturer,
            reference_raw=identity.reference_raw,
            reference_canonical=identity.reference_canonical,
            fallback_url=identity.fallback_url,
            title=title,
            admitted_via=via,
            first_source=source,
            first_region=region,
            first_source_url=url,
            first_seen_at=now,
            last_seen_at=now,
            last_seen_source=source,
        )
        # SAVEPOINT: a race-loss rollback must not discard the sweep's
        # outer transaction (the CollectorRun row and earlier sources'
        # admissions all live there).
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            with contextlib.suppress(Exception):  # already detached is fine
                self.session.expunge(row)
            existing = self.get_identity(identity.identity_key)
            if existing is None:
                raise
            return existing, False
        return row, True

    def refresh_last_seen(
        self,
        row: SentinelIdentity,
        *,
        source: str,
        title: str | None,
        now: datetime,
        refresh_after: timedelta,
    ) -> None:
        """Re-touch a known identity only past the refresh threshold so a
        15-minute poll never becomes full-table write churn. A richer title
        (sources upgrade from sitemap-None to feed titles) is adopted any
        time it appears."""
        if title and not row.title:
            row.title = title
        if now - _as_utc(row.last_seen_at) >= refresh_after:
            row.last_seen_at = now
            row.last_seen_source = source

    # -- sightings ---------------------------------------------------------

    def record_sighting(
        self,
        *,
        identity_row: SentinelIdentity,
        source: str,
        region: str | None,
        url: str,
        title: str | None,
        reference: str | None,
        now: datetime,
        run_id: int | None,
        extra: dict[str, Any] | None = None,
    ) -> SentinelSighting:
        sighting = SentinelSighting(
            identity_id=identity_row.id,
            identity_key=identity_row.identity_key,
            source=source,
            region=region,
            source_url=url,
            title=title,
            reference=reference or identity_row.reference_canonical,
            detected_at=now,
            run_id=run_id,
            extra=extra,
        )
        self.session.add(sighting)
        self.session.flush()
        return sighting

    def set_alert_state(self, sighting: SentinelSighting, state: str, error: str | None = None) -> None:
        sighting.alert_state = state
        sighting.alert_error = error

    # -- per-source state --------------------------------------------------

    def source_state(self, source: str) -> SentinelSourceState:
        row = self.session.query(SentinelSourceState).filter_by(source=source).first()
        if row is None:
            row = SentinelSourceState(source=source)
            self.session.add(row)
            self.session.flush()
        return row

    def mark_poll(self, state: SentinelSourceState, *, ok: bool, status: str, now: datetime) -> None:
        state.last_poll_at = now
        state.last_status = status
        if ok:
            state.last_success_at = now
            if state.armed_at is None:
                # First successful poll arms the source: everything before
                # this point was its silent per-source baseline.
                state.armed_at = now
                logger.info("sentinel_source_armed", source=state.source)

    # -- sweeps ------------------------------------------------------------

    def sources_due(self, states: dict[str, SentinelSourceState], cadences: dict[str, int], now: datetime) -> set[str]:
        """Names of sources whose cadence has elapsed (or never ran)."""
        due: set[str] = set()
        for name, cadence in cadences.items():
            state = states.get(name)
            if state is None or state.last_success_at is None:
                due.add(name)
                continue
            if now - _as_utc(state.last_success_at) >= timedelta(minutes=cadence):
                due.add(name)
        return due


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
