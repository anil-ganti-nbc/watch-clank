"""Application-level overlap protection for collector runs.

Uses a file lock with PID + timestamp metadata plus DB RUNNING records.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.time import ensure_utc
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
    """Prevents concurrent pipeline runs for a given collector_id.

    Defaults to COLLECTOR_ID ("casio_japan") to preserve the exact behaviour
    of every pre-existing caller that doesn't pass collector_id explicitly.
    run_multi_source_pipeline passes collector_id="casio_multi" so its
    RUNNING rows are actually covered by stale-run recovery and overlap
    detection — before this fix they used this same class but it only ever
    looked at "casio_japan" rows, so a crashed casio_multi run could get
    stuck RUNNING forever and would never be detected as an active run
    either (found live in data/watch_clank.db: run 65, orphaned process).
    """

    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        *,
        collector_id: str = COLLECTOR_ID,
        lock_path: Path | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.lock_path = lock_path or self.settings.resolved_lock_path
        self.collector_id = collector_id
        self._handle = None

    def _try_grant(self) -> bool:
        """Acquire the kernel-granted advisory lock; metadata is diagnostic only."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if handle.tell() == 0:
                    handle.write(" "); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def _drop_grant(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0); msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close(); self._handle = None

    def _stale_cutoff(self) -> datetime:
        return datetime.now(UTC) - timedelta(minutes=self.settings.stale_run_threshold_minutes)

    def find_active_run(self) -> CollectorRun | None:
        """Return a non-stale RUNNING record for this collector, if any."""
        cutoff = self._stale_cutoff()
        runs = (
            self.session.query(CollectorRun)
            .filter(
                CollectorRun.collector_id == self.collector_id,
                CollectorRun.status == "RUNNING",
            )
            .order_by(CollectorRun.started_at.desc())
            .all()
        )
        for run in runs:
            started = ensure_utc(run.started_at)
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
                CollectorRun.collector_id == self.collector_id,
                CollectorRun.status == "RUNNING",
            )
            .all()
        )
        for run in runs:
            started = ensure_utc(run.started_at)
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
            self._notify_health(
                f"WATCH CLANK — OPS\nStale RUNNING run(s) recovered for '{self.collector_id}': "
                f"{recovered}. Threshold: {self.settings.stale_run_threshold_minutes} min. "
                "A process likely crashed or was killed mid-run — check that the collector is "
                "otherwise healthy on its next scheduled run."
            )
        return recovered

    def _notify_health(self, text: str) -> None:
        """Best-effort ops alert — never allowed to affect lock/recovery
        outcomes. A Discord failure here is swallowed by DiscordNotifier
        itself; this wrapper exists only to keep import-time cost out of
        the (much more common) no-recovery path."""
        from app.services.discord_notify import DiscordNotifier

        DiscordNotifier(self.settings).send_health_alert(text)

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
            acquired = ensure_utc(datetime.fromisoformat(ts))
            age = datetime.now(UTC) - acquired
            return age > timedelta(minutes=self.settings.stale_run_threshold_minutes)
        except (TypeError, ValueError):
            return True

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return ctypes.get_last_error() == 5
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

        if not self._try_grant():
            return LockResult(acquired=False, reason="active_file_grant")

        # PID/timestamp identify the holder for diagnostics only. They never
        # decide whether a grant is live or can be reclaimed.
        payload = {
            "pid": os.getpid(),
            "acquired_at": datetime.now(UTC).isoformat(),
            "collector_id": self.collector_id,
        }
        try:
            self._handle.seek(0); self._handle.truncate(); self._handle.write(json.dumps(payload)); self._handle.flush()
        except OSError as exc:
            logger.error("lock_acquire_failed", error=str(exc))
            self._drop_grant()
            return LockResult(acquired=False, reason=f"lock_metadata_failed:{exc}")

        return LockResult(acquired=True, reason="acquired")

    def update_run_id(self, run_id: int) -> None:
        meta = self._read_lock_file() or {}
        meta["run_id"] = run_id
        meta["pid"] = os.getpid()
        import contextlib
        with contextlib.suppress(OSError):
            self.lock_path.write_text(json.dumps(meta), encoding="utf-8")

    def release(self) -> None:
        self._drop_grant()
        # Removing our diagnostic record is cleanup only; exclusivity ended
        # when the advisory grant was released.
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def is_locked(self) -> bool:
        self.recover_stale_runs()
        if self.find_active_run() is not None:
            return True
        if self._try_grant():
            self._drop_grant()
            return False
        return True
