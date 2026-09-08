"""Sentinel sweep orchestration.

One sweep = one 15-minute scheduler firing = one CollectorRun row
(collector_id "horology_sentinel") + per-source stats in summary_metadata.
Guarantees:

- No overlap: RunLockService kernel lock keyed to the Sentinel. A second
  firing exits SKIPPED_OVERLAP immediately.
- Per-source isolation: any adapter exception/failed fetch marks that
  source failed and the sweep continues; one broken brand never blinds
  the others.
- Baseline safety: a source that has never completed a successful poll is
  silently baselined on its first success (per-source arming latch in
  sentinel_source_state) -- a brand-new source can never replay its whole
  catalogue into Discord, and a temporary HTTP failure neither forgets
  identities nor re-baselines an armed source.
- The identity store's unique constraint is the final double-admission
  authority regardless of lock behaviour.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from app.collectors.base import FetchResult
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import CollectorRun
from app.models.sentinel import (
    ADMITTED_VIA_BASELINE,
    ADMITTED_VIA_SIGHTING,
    ALERT_CAPPED,
)
from app.sentinel.adapters import poll_source
from app.sentinel.alerting import SentinelAlerter
from app.sentinel.config import SENTINEL_SOURCES, SentinelSourceConfig, enabled_sources
from app.sentinel.identity import build_identity
from app.sentinel.store import SentinelStore

logger = get_logger(__name__)

COLLECTOR_ID = "horology_sentinel"
COLLECTOR_VERSION = "0.1.0"


class SentinelRunner:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        *,
        fetch_fn: Callable[[str], FetchResult] | None = None,
        alerter: SentinelAlerter | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.fetch_fn = fetch_fn
        self.store = SentinelStore(session)
        self.alerter = alerter or SentinelAlerter(session, self.settings)

    # ------------------------------------------------------------------

    def run_sweep(
        self,
        *,
        force_baseline: bool = False,
        only_source: str | None = None,
        now: datetime | None = None,
    ) -> CollectorRun:
        now = now or datetime.now(UTC)
        settings = self.settings

        lock_path = settings.resolved_lock_path.parent / "horology_sentinel.run.lock"
        from app.services.run_lock import RunLockService

        lock = RunLockService(
            self.session, settings, collector_id=COLLECTOR_ID, lock_path=lock_path
        )
        grant = lock.acquire()
        if not grant.acquired:
            run = self._start_run(now)
            run.status = "SKIPPED_OVERLAP"
            run.summary_metadata = {"reason": grant.reason}
            self.session.commit()
            return run

        try:
            return self._run_locked(now, force_baseline=force_baseline, only_source=only_source)
        finally:
            lock.release()

    # ------------------------------------------------------------------

    def _run_locked(
        self,
        now: datetime,
        *,
        force_baseline: bool,
        only_source: str | None,
    ) -> CollectorRun:
        settings = self.settings
        run = self._start_run(now, is_baseline=force_baseline)
        if only_source is not None and only_source not in SENTINEL_SOURCES:
            run.status = "FAILED"
            run.summary_metadata = {"error": f"unknown sentinel source {only_source!r}"}
            run.completed_at = datetime.now(UTC)
            self.session.commit()
            return run

        sources: list[SentinelSourceConfig] = (
            enabled_sources() if only_source is None else [SENTINEL_SOURCES[only_source]]
        )
        states = {s.name: self.store.source_state(s.name) for s in sources}
        due = (
            {s.name for s in sources}
            if force_baseline or only_source is not None
            else self.store.sources_due(states, {s.name: s.cadence_minutes for s in sources}, now)
        )

        known_keys = self.store.known_identity_keys()
        refresh_after = timedelta(hours=settings.sentinel_last_seen_refresh_hours)
        max_alerts = settings.sentinel_max_alerts_per_sweep

        per_source: dict[str, dict] = {}
        ok_count = failed_count = blocked_count = total_candidates = 0
        alerts_sent = alerts_failed = alerts_capped = 0
        unseen_total = suppressed_total = 0

        for source in sources:
            if source.name not in due:
                per_source[source.name] = {
                    "status": "NOT_DUE",
                    "cadence_minutes": source.cadence_minutes,
                }
                continue

            try:
                outcome = poll_source(source, self.fetch_fn)
            except Exception as exc:  # per-source isolation: never abort the sweep
                logger.exception("sentinel_source_poll_failed", source=source.name)
                self.store.mark_poll(states[source.name], ok=False, status="FAILED", now=now)
                per_source[source.name] = {"status": "FAILED", "error": str(exc)[:300]}
                failed_count += 1
                continue

            stats: dict = {
                "status": outcome.status,
                "requests": outcome.request_count,
                "candidates": len(outcome.candidates),
            }
            total_candidates += len(outcome.candidates)

            if outcome.status in ("FAILED", "BLOCKED"):
                stats["error"] = outcome.error
                self.store.mark_poll(
                    states[source.name], ok=False, status=outcome.status, now=now
                )
                if outcome.status == "FAILED":
                    failed_count += 1
                else:
                    blocked_count += 1
                per_source[source.name] = stats
                continue

            armed = states[source.name].armed_at is not None and not force_baseline
            stats["baseline_mode"] = not armed

            unseen = suppressed = 0
            for candidate in outcome.candidates:
                identity = build_identity(
                    source.manufacturer,
                    reference_raw=candidate.reference_raw,
                    url=candidate.url,
                )
                if armed and identity.identity_key in known_keys:
                    suppressed += 1
                    row = self.store.get_identity(identity.identity_key)
                    if row is not None:
                        self.store.refresh_last_seen(
                            row,
                            source=source.name,
                            title=candidate.title,
                            now=now,
                            refresh_after=refresh_after,
                        )
                    continue

                via = ADMITTED_VIA_SIGHTING if armed else ADMITTED_VIA_BASELINE
                row, created = self.store.admit(
                    identity,
                    source=source.name,
                    region=candidate.region,
                    url=candidate.url,
                    title=candidate.title,
                    via=via,
                    now=now,
                )
                if not armed:
                    # Silent baseline mode: record the identity (or refresh
                    # one another source already found) and never alert.
                    if not created:
                        self.store.refresh_last_seen(
                            row,
                            source=source.name,
                            title=candidate.title,
                            now=now,
                            refresh_after=refresh_after,
                        )
                    continue
                if not created:
                    # Lost a race: another process admitted this identity
                    # after our snapshot. It is known now; never re-alert.
                    suppressed += 1
                    continue
                known_keys.add(identity.identity_key)
                unseen += 1
                sighting = self.store.record_sighting(
                    identity_row=row,
                    source=source.name,
                    region=candidate.region,
                    url=candidate.url,
                    title=candidate.title,
                    reference=identity.reference_canonical,
                    now=now,
                    run_id=run.id,
                )
                if alerts_sent + alerts_failed >= max_alerts:
                    alerts_capped += 1
                    self.store.set_alert_state(sighting, ALERT_CAPPED, "sweep alert cap")
                    continue
                alert_state = self.alerter.send(sighting, display_name=source.display_name)
                if alert_state == "SENT":
                    alerts_sent += 1
                elif alert_state == "FAILED":
                    alerts_failed += 1
                elif alert_state == "CAPPED":
                    alerts_capped += 1

            stats["unseen_admitted"] = unseen
            stats["known_suppressed"] = suppressed
            unseen_total += unseen
            suppressed_total += suppressed

            # A successful poll arms the source (first success = its
            # baseline just completed silently).
            self.store.mark_poll(
                states[source.name], ok=True, status=outcome.status, now=now
            )
            ok_count += 1
            per_source[source.name] = stats

        run.discovered_count = total_candidates
        run.parsed_count = total_candidates
        # The Sentinel creates no Watch rows, ever -- keep the pipeline's
        # own column honest; Sentinel admission counts live in metadata.
        run.new_watch_count = 0
        run.observation_count = 0
        run.summary_metadata = {
            "component": "horology_sentinel",
            "force_baseline": force_baseline,
            "totals": {
                "sources_polled": len(per_source),
                "candidates_observed": total_candidates,
                "unseen_admitted": unseen_total,
                "known_suppressed": suppressed_total,
                "alerts_sent": alerts_sent,
                "alerts_failed": alerts_failed,
                "alerts_capped": alerts_capped,
            },
            "per_source": per_source,
        }

        attempted = ok_count + failed_count + blocked_count
        if attempted == 0:
            run.status = "SUCCESS"
            run.summary_metadata["totals"]["note"] = "no sources due this sweep"
        elif failed_count == 0 and blocked_count == 0:
            run.status = "SUCCESS"
        elif ok_count > 0:
            run.status = "PARTIAL"
        elif blocked_count > 0 and failed_count == 0:
            run.status = "BLOCKED"
        else:
            run.status = "FAILED"

        if alerts_capped:
            self._warn_alert_cap(alerts_capped, max_alerts)

        run.completed_at = datetime.now(UTC)
        self.session.commit()
        return run

    def _warn_alert_cap(self, capped: int, max_alerts: int) -> None:
        try:
            from app.services.discord_notify import DiscordNotifier

            DiscordNotifier(self.settings).send_health_alert(
                f"WATCH CLANK — SENTINEL\nSweep alert cap hit ({capped} sightings "
                f"capped at {max_alerts}/sweep). This usually means a source is "
                "replaying its catalogue -- check the horology_sentinel "
                "collector_runs summary_metadata before treating any of them "
                "as launch signals."
            )
        except Exception:  # noqa: BLE001 -- health ping is best-effort
            pass

    def _start_run(self, now: datetime, *, is_baseline: bool = False) -> CollectorRun:
        run = CollectorRun(
            collector_id=COLLECTOR_ID,
            collector_version=COLLECTOR_VERSION,
            status="RUNNING",
            started_at=now,
            is_baseline=is_baseline,
        )
        self.session.add(run)
        self.session.commit()
        return run
