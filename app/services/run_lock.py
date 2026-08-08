"""Application-level overlap protection for collector runs.

Uses a file lock with PID + timestamp metadata plus DB RUNNING records.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import CollectorRun

logger = get_logger(__name__)

COLLECTOR_ID = "casio_japan"


@dataclass
class LockResult:
    acquired: bool
    reason: str
    stale_recovered: bool = False
    active_run_id: int | None = None


class RunLockService:
    """Prevents concurrent Casio Japan pipeline runs."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.lock_path = self.settings.resolved_lock_path

    def _stale_cutoff(self) -> datetime:
        return datetime.now(UTC) - timedelta(minutes=self.settings.stale_run_threshold_minutes)

    def find_active_run(self) -> CollectorRun | None:
        """Return a non-stale RUNNING record for this collector, if any."""
        cutoff = self._stale_cutoff()
        runs = (
            self.session.query(CollectorRun)
            .filter(
                CollectorRun.collector_id == COLLECTOR_ID,
                CollectorRun.status == "RUNNING",
            )
            .order_by(CollectorRun.started_at.desc())
            .all()
        )
        for run in runs:
            started = run.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if started >= cutoff:
                return run
        return None

    def recover_stale_runs(self) -> list[int]:
        """Mark abandoned RUNNING records as FAILED. Returns recovered IDs."""
        cutoff = self._stale_cutoff()
        recovered: list[int] = []
        runs = (
            self.session.query(CollectorRun)
            .filter(
                CollectorRun.collector_id == COLLECTOR_ID,
                CollectorRun.status == "RUNNING",
            )
            .all()
        )
        for run in runs:
            started = run.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if started < cutoff:
                run.status = "FAILED"
                run.completed_at = datetime.now(UTC)
                meta = dict(run.summary_metadata or {})
                meta["stale_recovery"] = True
                meta["recovered_at"] = datetime.now(UTC).isoformat()
                run.summary_metadata = meta
                recovered.append(run.id)
                logger.warning(
                    "stale_run_recovered",
                    run_id=run.id,
                    started_at=str(run.started_at),
                )
        if recovered:
            self.session.commit()
        return recovered

    def _read_lock_file(self) -> dict[str, Any] | None:
        if not self.lock_path.exists():
            return None
        try:
            return json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _lock_is_stale(self, meta: dict[str, Any]) -> bool:
        ts = meta.get("acquired_at")
        if not ts:
            return True
        try:
            acquired = datetime.fromisoformat(ts)
            if acquired.tzinfo is None:
                acquired = acquired.replace(tzinfo=UTC)
            age = datetime.now(UTC) - acquired
            return age > timedelta(minutes=self.settings.stale_run_threshold_minutes)
        except (TypeError, ValueError):
            return True

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def acquire(self) -> LockResult:
        """Try to acquire the run lock. Recovers stale runs first."""
        self.recover_stale_runs()

        active = self.find_active_run()
        if active:
            return LockResult(
                acquired=False,
                reason="active_db_run",
                active_run_id=active.id,
            )

        meta = self._read_lock_file()
        if meta is not None:
            pid = int(meta.get("pid") or 0)
            if not self._lock_is_stale(meta) and self._pid_alive(pid):
                return LockResult(
                    acquired=False,
                    reason="active_file_lock",
                    active_run_id=meta.get("run_id"),
                )
            # Stale lock file – remove
            try:
                self.lock_path.unlink(missing_ok=True)
                logger.info("stale_lock_file_removed", path=str(self.lock_path))
            except OSError:
                pass

        # Write lock
        payload = {
            "pid": os.getpid(),
            "acquired_at": datetime.now(UTC).isoformat(),
            "collector_id": COLLECTOR_ID,
        }
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic-ish write
            tmp = self.lock_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.lock_path)
        except OSError as exc:
            logger.error("lock_acquire_failed", error=str(exc))
            return LockResult(acquired=False, reason=f"lock_write_failed:{exc}")

        return LockResult(acquired=True, reason="acquired")

    def update_run_id(self, run_id: int) -> None:
        meta = self._read_lock_file() or {}
        meta["run_id"] = run_id
        meta["pid"] = os.getpid()
        import contextlib
        with contextlib.suppress(OSError):
            self.lock_path.write_text(json.dumps(meta), encoding="utf-8")

    def release(self) -> None:
        try:
            if self.lock_path.exists():
                meta = self._read_lock_file() or {}
                if meta.get("pid") in (None, os.getpid()):
                    self.lock_path.unlink(missing_ok=True)
                    logger.info("run_lock_released", path=str(self.lock_path))
        except OSError as exc:
            logger.warning("lock_release_failed", error=str(exc))

    def is_locked(self) -> bool:
        self.recover_stale_runs()
        if self.find_active_run() is not None:
            return True
        meta = self._read_lock_file()
        if meta is None:
            return False
        if self._lock_is_stale(meta):
            return False
        return self._pid_alive(int(meta.get("pid") or 0))
