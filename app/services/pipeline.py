"""Pipeline orchestration: collector → snapshot → parse → normalize → identity → observation → ledger.

Collectors and parsers never write to the database.
All persistence happens here under clear item-level transaction boundaries.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.collectors.base import FetchResult
from app.collectors.casio_japan import COLLECTOR_ID, COLLECTOR_VERSION, CasioJapanCollector
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import ensure_utc
from app.models import (
    CollectorRun,
    FamilyMembership,
    PipelineLedger,
    SnapshotBlob,
    SnapshotFetch,
    SourceObservation,
    Watch,
    WatchFamily,
)
from app.normalization.references import (
    normalize_casio_reference,
    normalize_citizen_reference,
    normalize_seiko_reference,
    normalize_timex_reference,
    safe_overall_confidence,
)
from app.parsers.casio_japan import PARSER_VERSION, parse_casio_product_html
from app.services.editorial import EventEvidence, score_event
from app.services.run_lock import RunLockService
from app.services.snapshot_storage import SnapshotStorageService

logger = get_logger(__name__)

# Dispatch table for per-manufacturer reference normalization. Casio keeps its
# exact original call path (default kwargs identical to pre-multi-brand code)
# so existing Casio behaviour is provably unchanged.
_NORMALIZERS = {
    "Casio": normalize_casio_reference,
    "Citizen": normalize_citizen_reference,
    "Seiko": normalize_seiko_reference,
    "Timex": normalize_timex_reference,
}

# Sprint 10 hardening (ai/handoff/TIMEX_FRESHNESS_AUDIT.md): official news
# sources whose announcement_date is a genuine, machine-parseable ISO-8601
# timestamp -- as opposed to Casio ("July 15, 2026"), Citizen ("23 July
# 2026", sometimes even "2 July2026" with no space), and Seiko ("January
# 07, 2026"), all confirmed live as free-text strings that a strict
# ISO parse will safely and predictably fail on. Only sources in this set
# are eligible for the publication-freshness gate in _record_watch_event
# below -- this is what keeps the hardening scoped to Timex (which can
# genuinely provide a reliable "how old is this article" signal) without
# any risk of silently suppressing a real Casio/Citizen/Seiko NEW_REFERENCE
# event because their date strings don't parse as ISO-8601.
_ISO_TIMESTAMP_NEWS_SOURCES = frozenset({"timex_news"})


class PipelineService:
    def __init__(self, session: Session, storage: SnapshotStorageService | None = None) -> None:
        self.session = session
        self.storage = storage or SnapshotStorageService()

    def _epoch_fields(self, *, force_baseline: bool = False) -> dict:
        """epoch_id/is_baseline kwargs for a new CollectorRun -- see
        app/services/epoch.py. Every real (non-skip) CollectorRun creation
        site should spread this in so baseline runs are auditable.

        force_baseline (Sprint 9): a SOURCE-SCOPED silent baseline for a
        brand joining an already-running (already-baselined) epoch -- e.g.
        Timex joining Epoch 1 after Casio/Citizen/Seiko already went live.
        Deliberately independent of the epoch's own baseline_started_at/
        baseline_completed_at window (which cannot be reopened once
        completed, and reopening it would incorrectly baseline every other
        source's concurrent scheduled runs too, not just the new one).
        """
        from app.services.epoch import get_active_epoch, is_baseline_active

        epoch = get_active_epoch(self.session)
        return {
            "epoch_id": epoch.id if epoch else None,
            "is_baseline": force_baseline or bool(epoch and is_baseline_active(self.session)),
        }

    def _stale_official_announcement(self, lead) -> str | None:
        """Sprint 10 hardening: returns a suppression reason if `lead`
        (a ReleaseLead) is from a source in _ISO_TIMESTAMP_NEWS_SOURCES
        and its announcement_date is either unparseable/missing or older
        than the configured freshness window. Returns None (no
        suppression) for every other source -- see the module-level
        constant's docstring for why this is deliberately source-scoped.

        Deliberately conservative in the direction that matters: a source
        NOT in the allowlist is completely unaffected regardless of what
        its announcement_date string looks like (Casio/Citizen/Seiko keep
        their exact pre-existing behavior). Only within the allowlist does
        "can't parse this timestamp" become "treat as not current" -- per
        this sprint's explicit instruction: NULL/invalid publication
        timestamp on an ISO-timestamp source is NOT assumed fresh.
        """
        if lead.source_id not in _ISO_TIMESTAMP_NEWS_SOURCES:
            return None

        pub_dt = None
        if lead.announcement_date:
            try:
                pub_dt = datetime.fromisoformat(lead.announcement_date)
            except ValueError:
                pub_dt = None

        if pub_dt is None:
            return "unknown_publication_timestamp"

        from app.core.time import ensure_utc

        window_hours = get_settings().specialist_freshness_window_hours
        age = datetime.now(UTC) - ensure_utc(pub_dt)
        if age > timedelta(hours=window_hours):
            return "stale_publication"
        return None

    def _ledger(
        self,
        *,
        correlation_id: str,
        run_id: int | None,
        entity_type: str,
        entity_id: str | None,
        stage: str,
        action: str,
        input_ref: str | None = None,
        output_ref: str | None = None,
        collector_version: str | None = None,
        parser_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PipelineLedger:
        entry = PipelineLedger(
            correlation_id=correlation_id,
            run_id=run_id,
            entity_type=entity_type,
            entity_id=entity_id,
            stage=stage,
            action=action,
            input_ref=input_ref,
            output_ref=output_ref,
            collector_version=collector_version,
            parser_version=parser_version,
            metadata_=metadata,
        )
        self.session.add(entry)
        return entry

    def _resolve_or_create_watch(
        self,
        *,
        reference_raw: str,
        manufacturer: str,
        brand: str,
        collection: str | None,
        model_name: str | None,
        extra: dict[str, Any],
        correlation_id: str,
        run_id: int | None,
    ) -> tuple[Watch, bool]:
        """Return (watch, is_new). Identity: manufacturer + brand + reference_canonical."""
        normalizer = _NORMALIZERS.get(manufacturer, normalize_casio_reference)
        norm = normalizer(
            reference_raw,
            manufacturer=manufacturer,
            brand_hint=brand,
            collection_hint=collection,
        )

        existing = (
            self.session.query(Watch)
            .filter_by(
                manufacturer=norm.manufacturer,
                brand=norm.brand,
                reference_canonical=norm.reference_canonical,
            )
            .one_or_none()
        )
        if existing:
            if model_name and not existing.model_name:
                existing.model_name = model_name
            if collection and not existing.collection:
                existing.collection = collection
            self._ledger(
                correlation_id=correlation_id,
                run_id=run_id,
                entity_type="watch",
                entity_id=str(existing.id),
                stage="identity_resolution",
                action="resolved_existing",
                output_ref=norm.reference_canonical,
                collector_version=COLLECTOR_VERSION,
                parser_version=PARSER_VERSION,
            )
            return existing, False

        if norm.manufacturer == "Timex":
            # Sprint 11 miss autopsy: Timex news posts only ever leak a SKU
            # via Shopify CDN image filenames (see app/parsers/timex_news.py
            # IMAGE_SKU_RE), and those filenames never carry the catalogue's
            # trailing variant suffix (e.g. "TW2Y71200" vs the catalogue's
            # "TW2Y71200VQ") -- confirmed on real captured posts. Timex's
            # own normalize_timex_reference is a deliberate conservative
            # passthrough (no suffix-stripping, unlike Casio's JDM
            # allowlist), so an exact-match miss here would otherwise create
            # a phantom duplicate Watch instead of linking the real one.
            # Prefix match, scoped to Timex only, and only auto-linked when
            # it resolves to exactly one existing watch -- ambiguous or zero
            # matches fall through to normal creation, same conservative
            # bar as everywhere else in this function.
            prefix_matches = (
                self.session.query(Watch)
                .filter(
                    Watch.manufacturer == norm.manufacturer,
                    Watch.brand == norm.brand,
                    Watch.reference_canonical.like(f"{norm.reference_canonical}%"),
                )
                .all()
            )
            if len(prefix_matches) == 1:
                existing = prefix_matches[0]
                self._ledger(
                    correlation_id=correlation_id,
                    run_id=run_id,
                    entity_type="watch",
                    entity_id=str(existing.id),
                    stage="identity_resolution",
                    action="resolved_existing_by_prefix",
                    output_ref=norm.reference_canonical,
                    collector_version=COLLECTOR_VERSION,
                    parser_version=PARSER_VERSION,
                )
                return existing, False

        watch = Watch(
            manufacturer=norm.manufacturer,
            brand=norm.brand,
            collection=norm.collection or collection,
            model_name=model_name,
            reference_raw=norm.reference_raw,
            reference_canonical=norm.reference_canonical,
            family_candidate_key=norm.family_candidate_key,
            solar=extra.get("solar"),
            bluetooth=extra.get("bluetooth"),
            radio_sync=extra.get("radio_sync"),
            gps=extra.get("gps"),
            case_material=extra.get("case_material"),
            crystal=extra.get("crystal"),
            water_resistance_m=extra.get("water_resistance_m"),
            limited_edition=extra.get("limited_edition"),
            movement_type=extra.get("movement_type"),
            caliber_or_module=extra.get("caliber_or_module"),
            extra_specs=extra.get("extra_specs") or {},
        )
        self.session.add(watch)
        self.session.flush()

        self._ledger(
            correlation_id=correlation_id,
            run_id=run_id,
            entity_type="watch",
            entity_id=str(watch.id),
            stage="identity_resolution",
            action="created",
            output_ref=norm.reference_canonical,
            collector_version=COLLECTOR_VERSION,
            parser_version=PARSER_VERSION,
            metadata={"family_candidate_key": norm.family_candidate_key},
        )

        family = (
            self.session.query(WatchFamily)
            .filter_by(
                manufacturer=norm.manufacturer,
                brand=norm.brand,
                family_key=norm.family_candidate_key,
            )
            .one_or_none()
        )
        if not family:
            family = WatchFamily(
                manufacturer=norm.manufacturer,
                brand=norm.brand,
                family_key=norm.family_candidate_key,
                status="PROVISIONAL",
                grouping_rule="prefix_series_number_v1",
                grouping_rule_version="0.1.0",
                grouping_confidence=60.0,
            )
            self.session.add(family)
            self.session.flush()

        membership = FamilyMembership(
            watch_id=watch.id,
            family_id=family.id,
            assignment_rule="family_candidate_key",
            confidence=60.0,
            is_primary=True,
        )
        self.session.add(membership)

        self._ledger(
            correlation_id=correlation_id,
            run_id=run_id,
            entity_type="family",
            entity_id=str(family.id),
            stage="family_candidate_assignment",
            action="assigned",
            output_ref=norm.family_candidate_key,
            metadata={"watch_id": watch.id},
        )
        return watch, True

    def process_fetch_result(
        self,
        fr: FetchResult,
        *,
        run_id: int,
        collector_id: str = COLLECTOR_ID,
        collector_version: str = COLLECTOR_VERSION,
        parse_fn=None,
        default_region: str = "JP",
        emit_events: bool = False,
        notify: bool = False,
        experimental: bool = False,
        force_baseline: bool = False,
    ) -> dict[str, Any]:
        """Process one fetched item (product/catalogue page) under a single
        transaction boundary.

        parse_fn/default_region default to the exact original Casio-only
        behaviour (parse_casio_product_html, region="JP"), so every
        pre-existing caller (run_casio_pipeline, run_multi_source_pipeline's
        catalog enrichment, fixture-mode CLI) is unaffected. Other brands
        pass their own parser via these kwargs (see
        run_product_observation_pipeline).

        emit_events/notify mirror process_news_announcement's Sprint 2
        pattern: default False, so the Casio production path never emits
        Event rows or Discord alerts from this method either, unless a
        future session explicitly opts it in after evidence review.
        """
        correlation_id = str(uuid.uuid4())
        outcome: dict[str, Any] = {
            "correlation_id": correlation_id,
            "url": fr.url,
            "success": False,
            "new_watch": False,
            "observation_id": None,
            "error": None,
        }

        if not fr.success or not fr.payload:
            outcome["error"] = fr.error or "No payload"
            try:
                self._ledger(
                    correlation_id=correlation_id,
                    run_id=run_id,
                    entity_type="url",
                    entity_id=None,
                    stage="fetch",
                    action="failed",
                    input_ref=fr.url,
                    metadata={"error": outcome["error"], "status_code": fr.status_code},
                    collector_version=collector_version,
                )
                self.session.commit()
            except Exception:
                self.session.rollback()
            return outcome

        try:
            # Snapshot storage (blob + fetch)
            snap_meta = self.storage.store(
                fr.payload,
                source_url=fr.url,
                content_type=fr.content_type,
                collector_id=collector_id,
                collector_version=collector_version,
            )
            self._ledger(
                correlation_id=correlation_id,
                run_id=run_id,
                entity_type="snapshot",
                entity_id=snap_meta["content_hash"],
                stage="snapshot_storage",
                action="stored" if not snap_meta.get("reused") else "reused",
                input_ref=fr.url,
                output_ref=snap_meta["filepath"],
                collector_version=collector_version,
                metadata={"byte_size": snap_meta["byte_size"], "reused": snap_meta.get("reused")},
            )

            # Blob (deduplicated)
            blob = (
                self.session.query(SnapshotBlob)
                .filter_by(content_hash=snap_meta["content_hash"])
                .one_or_none()
            )
            if not blob:
                blob = SnapshotBlob(
                    content_hash=snap_meta["content_hash"],
                    filepath=snap_meta["filepath"],
                    compression_type=snap_meta.get("compression_type"),
                    byte_size=snap_meta["byte_size"],
                    content_type=snap_meta.get("content_type"),
                    schema_version=snap_meta["schema_version"],
                    encoding=snap_meta.get("encoding"),
                )
                self.session.add(blob)
                self.session.flush()

            # Fetch (always new, preserves URL + collector metadata)
            fetch = SnapshotFetch(
                blob_id=blob.id,
                source_url=fr.url,
                collector_id=collector_id,
                collector_version=collector_version,
                http_status=fr.status_code,
                extra_metadata={"elapsed_ms": fr.elapsed_ms} if fr.elapsed_ms else None,
            )
            self.session.add(fetch)
            self.session.flush()

            # Parse offline
            active_parse_fn = parse_fn or parse_casio_product_html
            parse_result = active_parse_fn(fr.payload, source_url=fr.url)
            self._ledger(
                correlation_id=correlation_id,
                run_id=run_id,
                entity_type="parse",
                entity_id=None,
                stage="parsing",
                action="success" if parse_result.success else "failed",
                input_ref=snap_meta["filepath"],
                parser_version=parse_result.parser_version,
                metadata={
                    "parser_id": parse_result.parser_id,
                    "error": parse_result.error,
                    "watch_count": len(parse_result.watches),
                },
            )

            if not parse_result.success or not parse_result.watches:
                outcome["error"] = parse_result.error or "No watches extracted"
                self.session.commit()
                return outcome

            pw = parse_result.watches[0]
            extra = {
                "solar": pw.solar,
                "bluetooth": pw.bluetooth,
                "radio_sync": pw.radio_sync,
                "gps": pw.gps,
                "case_material": pw.case_material,
                "crystal": pw.crystal,
                "water_resistance_m": pw.water_resistance_m,
                "limited_edition": pw.limited_edition,
                "movement_type": pw.movement_type,
                "caliber_or_module": pw.caliber_or_module,
                "extra_specs": pw.extra_specs,
            }

            watch, is_new = self._resolve_or_create_watch(
                reference_raw=pw.reference_raw,
                manufacturer=pw.manufacturer,
                brand=pw.brand or pw.manufacturer,
                collection=pw.collection,
                model_name=pw.model_name,
                extra=extra,
                correlation_id=correlation_id,
                run_id=run_id,
            )

            region = default_region
            overall = safe_overall_confidence(pw.field_confidence)
            epoch_fields = self._epoch_fields(force_baseline=force_baseline)
            obs = SourceObservation(
                watch_id=watch.id,
                fetch_id=fetch.id,
                collector_id=collector_id,
                collector_version=collector_version,
                parser_id=parse_result.parser_id,
                parser_version=parse_result.parser_version,
                region=region,
                source_url=fr.url,
                availability_status=pw.availability_status,
                price=pw.price,
                currency=pw.currency,
                source_trust_score=100.0,
                overall_confidence=overall,
                field_confidence=pw.field_confidence or {},
                parser_warnings=pw.parser_warnings or [],
                epoch_id=epoch_fields["epoch_id"],
                is_baseline=epoch_fields["is_baseline"],
            )
            self.session.add(obs)
            self.session.flush()

            self._ledger(
                correlation_id=correlation_id,
                run_id=run_id,
                entity_type="observation",
                entity_id=str(obs.id),
                stage="observation_creation",
                action="created",
                output_ref=str(obs.id),
                collector_version=collector_version,
                parser_version=parse_result.parser_version,
                metadata={"watch_id": watch.id, "is_new_watch": is_new, "fetch_id": fetch.id},
            )

            product_event = None
            if emit_events:
                product_event = self._record_product_transition(
                    watch=watch,
                    new_obs=obs,
                    is_new_watch=is_new,
                    notify=notify,
                    experimental=experimental,
                    force_baseline=force_baseline,
                )

            self.session.commit()
            outcome.update(
                {
                    "success": True,
                    "new_watch": is_new,
                    "observation_id": obs.id,
                    "product_event": product_event,
                    "watch_id": watch.id,
                    "fetch_id": fetch.id,
                    "blob_id": blob.id,
                }
            )
            return outcome

        except Exception as exc:
            self.session.rollback()
            logger.exception("item_processing_failed", url=fr.url, error=str(exc))
            outcome["error"] = str(exc)
            try:
                self._ledger(
                    correlation_id=correlation_id,
                    run_id=run_id,
                    entity_type="url",
                    entity_id=None,
                    stage="pipeline",
                    action="failed",
                    input_ref=fr.url,
                    metadata={"error": str(exc)},
                    collector_version=collector_version,
                )
                self.session.commit()
            except Exception:
                self.session.rollback()
            return outcome

    def run_casio_pipeline(
        self,
        *,
        max_items: int | None = 10,
        known_product_urls: list[str] | None = None,
        discovery_urls: list[str] | None = None,
        skip_lock: bool = False,
    ) -> CollectorRun:
        """Full end-to-end run for Casio Japan with overlap protection."""
        settings = get_settings()
        lock = RunLockService(self.session, settings)

        if not skip_lock:
            lock_result = lock.acquire()
            if not lock_result.acquired:
                # Record SKIPPED_OVERLAP run for visibility
                started = datetime.now(UTC)
                skip_run = CollectorRun(
                    collector_id=COLLECTOR_ID,
                    collector_version=COLLECTOR_VERSION,
                    started_at=started,
                    completed_at=started,
                    status="SKIPPED_OVERLAP",
                    summary_metadata={
                        "reason": lock_result.reason,
                        "active_run_id": lock_result.active_run_id,
                    },
                )
                self.session.add(skip_run)
                self.session.commit()
                logger.info(
                    "pipeline_skipped_overlap",
                    reason=lock_result.reason,
                    active_run_id=lock_result.active_run_id,
                    skip_run_id=skip_run.id,
                )
                return skip_run

        collector = CasioJapanCollector()
        started = datetime.now(UTC)
        deadline = started.timestamp() + settings.max_run_duration_seconds

        run = CollectorRun(
            collector_id=COLLECTOR_ID,
            collector_version=COLLECTOR_VERSION,
            started_at=started,
            status="RUNNING",
            **self._epoch_fields(),
        )
        self.session.add(run)
        self.session.commit()
        if not skip_lock:
            lock.update_run_id(run.id)

        try:
            coll_result = collector.run(
                max_items=max_items,
                known_product_urls=known_product_urls,
                discovery_urls=discovery_urls,
            )

            run.discovered_count = len(coll_result.discovered)
            self.session.commit()

            # Detect BLOCKED: discovery-level or product-level upstream denial
            component_status = coll_result.metadata.get("component_status")
            blocked_count = 0
            for fr in coll_result.fetched:
                err = (fr.error or "").lower()
                if fr.status_code in (401, 403) or "access denied" in err or "403" in err:
                    blocked_count += 1
            disc_fetches = coll_result.metadata.get("discovery_fetches") or []
            disc_blocked = bool(disc_fetches) and all(
                d.get("blocked") or d.get("status") in (401, 403) for d in disc_fetches
            )
            total_fetched_attempts = len(coll_result.fetched)
            is_blocked = component_status == "BLOCKED" or disc_blocked or (
                total_fetched_attempts > 0
                and blocked_count >= max(1, total_fetched_attempts // 2)
                and blocked_count > 0
                and sum(1 for f in coll_result.fetched if f.success) == 0
            )

            new_watch_count = 0
            observation_count = 0
            warning_count = len(coll_result.warnings)
            failure_count = 0
            parsed_count = 0

            if not is_blocked:
                for fr in coll_result.fetched:
                    if time.time() > deadline:
                        logger.warning("max_run_duration_exceeded", run_id=run.id)
                        failure_count += 1
                        break
                    outcome = self.process_fetch_result(fr, run_id=run.id)
                    if outcome["success"]:
                        parsed_count += 1
                        observation_count += 1
                        if outcome.get("new_watch"):
                            new_watch_count += 1
                    else:
                        failure_count += 1

            completed = datetime.now(UTC)
            duration_ms = int((completed - started).total_seconds() * 1000)

            run.completed_at = completed
            run.fetched_count = sum(1 for f in coll_result.fetched if f.success)
            run.parsed_count = parsed_count
            run.new_watch_count = new_watch_count
            run.observation_count = observation_count
            run.warning_count = warning_count
            run.failure_count = failure_count
            run.duration_ms = duration_ms
            run.summary_metadata = {
                "discovered": run.discovered_count,
                "healthy": coll_result.metadata.get("healthy", False),
                "errors_sample": coll_result.errors[:5],
                "blocked_count": blocked_count,
            }

            if is_blocked:
                run.status = "BLOCKED"
            elif run.discovered_count == 0:
                run.status = "ZERO_ITEMS"
            elif failure_count == 0 and parsed_count > 0:
                run.status = "SUCCESS"
            elif parsed_count > 0:
                run.status = "PARTIAL"
            else:
                run.status = "FAILED"

            self.session.commit()
            logger.info(
                "pipeline_completed",
                run_id=run.id,
                status=run.status,
                duration_ms=duration_ms,
                new_watches=new_watch_count,
                observations=observation_count,
            )
            return run

        except Exception as exc:
            self.session.rollback()
            run.status = "FAILED"
            run.completed_at = datetime.now(UTC)
            run.summary_metadata = {"fatal_error": str(exc)}
            self.session.add(run)
            self.session.commit()
            logger.exception("pipeline_fatal", run_id=run.id, error=str(exc))
            raise
        finally:
            if not skip_lock:
                lock.release()

    def replay_snapshot(
        self,
        fetch_id: int,
        *,
        run_id: int | None = None,
    ) -> dict[str, Any]:
        """Replay a stored fetch through the current parser (no network)."""
        fetch = self.session.get(SnapshotFetch, fetch_id)
        if not fetch:
            raise ValueError(f"Fetch {fetch_id} not found")
        blob = fetch.blob
        if not blob:
            raise ValueError(f"Blob missing for fetch {fetch_id}")

        try:
            payload = self.storage.read(blob.filepath, blob.compression_type)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        fr = FetchResult(
            url=fetch.source_url,
            success=True,
            status_code=fetch.http_status or 200,
            content_type=blob.content_type,
            payload=payload,
        )
        if run_id is None:
            run = CollectorRun(
                collector_id="replay",
                collector_version=COLLECTOR_VERSION,
                status="RUNNING",
                **self._epoch_fields(),
            )
            self.session.add(run)
            self.session.commit()
            run_id = run.id

        return self.process_fetch_result(
            fr,
            run_id=run_id,
            collector_id=fetch.collector_id,
            collector_version=fetch.collector_version,
        )


    def _get_or_create_component_state(self, source_id: str):
        from app.models import SourceComponentState

        state = self.session.query(SourceComponentState).filter_by(source_id=source_id).one_or_none()
        if not state:
            state = SourceComponentState(source_id=source_id, consecutive_blocks=0, last_item_count=0)
            self.session.add(state)
            self.session.flush()
        return state

    def _update_component_state(self, source_id: str, status: str, item_count: int = 0) -> None:
        from datetime import timedelta

        state = self._get_or_create_component_state(source_id)
        state.last_status = status
        state.last_item_count = item_count
        now = datetime.now(UTC)
        if status == "BLOCKED":
            state.last_blocked_at = now
            state.consecutive_blocks = (state.consecutive_blocks or 0) + 1
            # progressive backoff: 3h, 6h, 12h, cap 24h
            hours = min(24, 3 * (2 ** max(0, state.consecutive_blocks - 1)))
            state.backoff_until = now + timedelta(hours=hours)
        elif status in ("SUCCESS", "PARTIAL", "ZERO_ITEMS"):
            state.last_success_at = now
            state.consecutive_blocks = 0
            state.backoff_until = None
        self.session.flush()

    def _should_skip_backed_off(self, source_id: str) -> bool:
        from app.models import SourceComponentState

        state = self.session.query(SourceComponentState).filter_by(source_id=source_id).one_or_none()
        if not state or not state.backoff_until:
            return False
        backoff_until = ensure_utc(state.backoff_until)
        return backoff_until > datetime.now(UTC)

    def _prior_regions_for_watch(self, watch_id: int, *, exclude_lead_id: int | None) -> frozenset[str]:
        """Regions this watch has previously been announced/observed in.

        Looked up from ReleaseLead history (JSON watch_ids column) rather
        than SourceObservation, since news-announcement leads do not create
        observations. Deliberately conservative: only counts leads with a
        stored source_region, and never counts the lead currently being
        processed.
        """
        from app.models import ReleaseLead

        regions: set[str] = set()
        candidates = self.session.query(ReleaseLead).filter(
            ReleaseLead.watch_ids.isnot(None)
        )
        for lead in candidates:
            if exclude_lead_id is not None and lead.id == exclude_lead_id:
                continue
            if not lead.source_region:
                continue
            if watch_id in (lead.watch_ids or []):
                regions.add(lead.source_region)
        return frozenset(regions)

    def _prior_product_regions_for_watch(
        self, watch_id: int, *, exclude_observation_id: int | None
    ) -> frozenset[str]:
        """Regions supported by prior first-party product observations.

        A regional product listing is a different fact from an announcement:
        one Watch can legitimately accumulate US/USD, DE/EUR, and UK/GBP
        observations.  This query deliberately excludes the just-created
        observation so the caller can identify a first listing in its region
        without ever comparing currencies or markets.
        """
        query = self.session.query(SourceObservation.region).filter(
            SourceObservation.watch_id == watch_id,
            SourceObservation.region.isnot(None),
        )
        if exclude_observation_id is not None:
            query = query.filter(SourceObservation.id != exclude_observation_id)
        return frozenset(region for (region,) in query.distinct().all() if region)

    def _extract_product_character(self, title: str) -> dict:
        """Conservative keyword extraction from an announcement title only —
        never guesses. Feeds the recall-tuning bonuses in editorial.score_event.
        """
        import re

        t = title or ""
        is_limited = bool(re.search(r"\blimited[\s-]edition\b", t, re.IGNORECASE))
        is_collab = bool(re.search(r"\bcollaboration\b|\bx\s+[A-Z]", t))
        material = None
        m = re.search(
            r"\b(recrystallised titanium|titanium|sapphire|ceramic|carbon(?:\s+fiber)?|gold)\b",
            t,
            re.IGNORECASE,
        )
        if m:
            material = m.group(1).lower()
        return {"is_limited_edition": is_limited or None, "is_collaboration": is_collab or None, "unusual_material": material}

    def _availability_event_character(self, watch: Watch) -> dict:
        """Use only persisted product facts for availability relevance.

        Product pages do not have a separate collaboration field.  A stored
        product/collection name may explicitly say ``x`` or
        ``collaboration``; that is deterministic textual evidence, not a
        demand prediction.  Limited edition is the parser-backed Watch field.
        """
        import re

        label = f"{watch.collection or ''} {watch.model_name or ''}"
        return {
            "is_limited_edition": watch.limited_edition is True,
            "is_collaboration": bool(
                re.search(r"\bcollaboration\b|\bx\s+[A-Za-z0-9]", label, re.IGNORECASE)
            ) or None,
        }

    def _days_since_first_nonbaseline_availability(
        self, *, watch_id: int, region: str, observed_at: datetime
    ) -> float | None:
        """Return age of real post-baseline availability evidence, if known.

        Baseline observations are intentionally excluded: they prove the
        catalogue state we inherited, not when the product launched.
        """
        first_available = (
            self.session.query(SourceObservation)
            .filter(
                SourceObservation.watch_id == watch_id,
                SourceObservation.region == region,
                SourceObservation.availability_status == "AVAILABLE",
                SourceObservation.is_baseline.is_(False),
            )
            .order_by(SourceObservation.observed_at.asc(), SourceObservation.id.asc())
            .first()
        )
        if first_available is None:
            return None
        age_seconds = (ensure_utc(observed_at) - ensure_utc(first_available.observed_at)).total_seconds()
        if age_seconds < 0:
            return None
        days = round(age_seconds / 86400.0, 2)
        if days > get_settings().availability_recent_launch_window_days:
            return None
        return days

    def _record_product_transition(
        self, *, watch: Watch, new_obs: SourceObservation, is_new_watch: bool, notify: bool = False,
        experimental: bool = False, force_baseline: bool = False,
    ) -> dict:
        """Classify and persist a deterministic PRICE_CHANGE/AVAILABILITY_
        CHANGE/SOLD_OUT/RESTOCK event by comparing new_obs against the most
        recent PRIOR observation of the same watch in the same region.

        Safety, by construction rather than by a health flag we could get
        wrong: process_fetch_result only ever reaches this call after a
        successful fetch + successful parse + a persisted SourceObservation.
        A failed fetch, a blocked source, or a parse failure returns early
        (see above) and never creates a SourceObservation at all — so any
        two SourceObservation rows this method compares are both, by
        definition, healthy. That is what makes it safe to always pass
        source_healthy=True to the classifier here (Sprint 3 requirement:
        a failed fetch between two runs must never fabricate a transition).

        Baseline rule: a source's initial crawl is silent.  After that first
        crawl, a known Watch's first official listing in an additional region
        is a NEW_REGION event, even if its announcement is old: novelty belongs
        to the observed commercial transition, not to the Watch's birth date.
        """
        from app.services.editorial import (
            EventEvidence,
            classify_price_availability_transition,
            score_event,
        )
        from app.services.epoch import is_baseline_active

        if force_baseline:
            # Sprint 9: source-scoped silent baseline (e.g. Timex joining an
            # already-baselined epoch) -- independent of the epoch's own
            # baseline window, see _epoch_fields' docstring.
            return {"event_type": None, "reason": "source_scoped_baseline"}
        if is_baseline_active(self.session):
            # Epoch 1 (or any epoch's) baseline: the Watch/SourceObservation
            # rows already got created by the caller -- that's real discovery
            # data. But a transition detected purely because the fresh DB has
            # never seen this watch/region before is not news; never create
            # an Event (and therefore never notify) while baselining.
            return {"event_type": None, "reason": "epoch_baseline_active"}

        if is_new_watch:
            # Hall-of-shame autopsy finding: a genuinely new-to-Clank SKU
            # discovered directly through a product catalogue (no prior news
            # announcement, no prior region) previously produced a Watch row
            # and a SourceObservation but NEVER an Event -- only the news
            # pipeline's _record_watch_event could emit NEW_REFERENCE. That
            # made real new-product launches (e.g. a SKU that simply appears
            # in a Shopify catalogue with no accompanying press release)
            # structurally invisible to Recent Intelligence/Discord even
            # though Watch Clank had genuinely just discovered it. Safe by
            # the same construction as the NEW_REGION branch below: this
            # line is unreachable during an active epoch/force_baseline
            # (guards above), so it only fires for a catalogue's first
            # non-baseline sighting of a reference -- exactly the discipline
            # already required before any collector is scheduled.
            evidence = EventEvidence(
                event_type="NEW_REFERENCE",
                manufacturer=watch.manufacturer,
                brand=watch.brand,
                collection=watch.collection,
                region=new_obs.region,
                is_first_party=True,
                reference_raw=watch.reference_raw,
                price=new_obs.price,
                currency=new_obs.currency,
                availability_status=new_obs.availability_status,
                **self._availability_event_character(watch),
            )
            return self._persist_product_event(
                watch=watch,
                new_obs=new_obs,
                scored=score_event(evidence),
                reasons=[
                    "first-ever product-catalogue observation of this reference; "
                    "no prior region or announcement existed"
                ],
                prior_observation=None,
                notify=notify,
                experimental=experimental,
                prior_regions=None,
            )

        prior_product_regions = self._prior_product_regions_for_watch(
            watch.id, exclude_observation_id=new_obs.id
        )
        prior_announcement_regions = self._prior_regions_for_watch(
            watch.id, exclude_lead_id=None
        )
        prior_regions = prior_product_regions | prior_announcement_regions

        # A separate regional collector must always be force-baselined before
        # it is scheduled.  Once that is true, first observation in a market
        # is evidence of a current commercial transition.  The source-level
        # baseline guards above make an old page discovered during onboarding
        # silent rather than misrepresenting discovery time as rollout time.
        if new_obs.region not in prior_product_regions and prior_regions:
            event_type = "NEW_REGION"
            evidence = EventEvidence(
                event_type=event_type,
                manufacturer=watch.manufacturer,
                brand=watch.brand,
                collection=watch.collection,
                region=new_obs.region,
                is_first_party=True,
                prior_regions=prior_regions,
                reference_raw=watch.reference_raw,
                price=new_obs.price,
                currency=new_obs.currency,
                availability_status=new_obs.availability_status,
            )
            return self._persist_product_event(
                watch=watch,
                new_obs=new_obs,
                scored=score_event(evidence),
                reasons=[
                    "first successful first-party product observation in a new region; "
                    "price and currency are regional facts, not a cross-market price change"
                ],
                prior_observation=None,
                notify=notify,
                experimental=experimental,
                prior_regions=prior_regions,
            )

        prior = (
            self.session.query(SourceObservation)
            .filter(
                SourceObservation.watch_id == watch.id,
                SourceObservation.region == new_obs.region,
                SourceObservation.id != new_obs.id,
            )
            .order_by(SourceObservation.observed_at.desc(), SourceObservation.id.desc())
            .first()
        )
        if prior is None:
            return {"event_type": None, "reason": "baseline_first_observation_in_region"}

        event_type, reasons = classify_price_availability_transition(
            prior_price=prior.price,
            prior_currency=prior.currency,
            prior_availability=prior.availability_status,
            prior_region=prior.region,
            prior_source_healthy=True,  # see docstring: only healthy obs are ever persisted
            new_price=new_obs.price,
            new_currency=new_obs.currency,
            new_availability=new_obs.availability_status,
            new_region=new_obs.region,
            new_source_healthy=True,
        )
        if event_type is None:
            return {"event_type": None, "reason": "no_transition", "detail_reasons": reasons}

        price_delta_pct = None
        if event_type == "PRICE_CHANGE" and prior.price:
            price_delta_pct = round((new_obs.price - prior.price) / prior.price * 100, 1)

        evidence = EventEvidence(
            event_type=event_type,
            manufacturer=watch.manufacturer,
            brand=watch.brand,
            collection=watch.collection,
            region=new_obs.region,
            is_first_party=True,
            reference_raw=watch.reference_raw,
            price=new_obs.price,
            currency=new_obs.currency,
            prior_price=prior.price,
            prior_currency=prior.currency,
            price_delta_pct=price_delta_pct,
            days_since_first_nonbaseline_availability=(
                self._days_since_first_nonbaseline_availability(
                    watch_id=watch.id, region=new_obs.region, observed_at=new_obs.observed_at
                )
                if event_type in {"SOLD_OUT", "RESTOCK"}
                else None
            ),
            **self._availability_event_character(watch),
        )
        scored = score_event(evidence)

        return self._persist_product_event(
            watch=watch,
            new_obs=new_obs,
            scored=scored,
            reasons=reasons,
            prior_observation=prior,
            notify=notify,
            experimental=experimental,
            prior_regions=None,
        )

    def _persist_product_event(
        self,
        *,
        watch: Watch,
        new_obs: SourceObservation,
        scored,
        reasons: list[str],
        prior_observation: SourceObservation | None,
        notify: bool,
        experimental: bool,
        prior_regions: frozenset[str] | None,
    ) -> dict:
        """Persist a product-state Event after the caller proved its facts."""
        from app.models import Event, EventWatch
        from app.services.discord_notify import DiscordNotifier
        from app.services.editorial import editorial_eligibility, format_alert

        settings = get_settings()
        editorial_eligible, eligibility_reasons = editorial_eligibility(
            scored, availability_min_score=settings.availability_editorial_min_score
        )

        event = Event(
            event_type=scored.event_type,
            title=f"{watch.manufacturer} {watch.reference_raw}: {scored.event_type}",
            status="DRAFT",
            story_score=scored.score,
            confidence_score={"HIGH": 90.0, "MEDIUM": 60.0, "LOW": 30.0}[scored.confidence],
            data_completeness_score=new_obs.overall_confidence,
            scoring_rule_version=scored.scoring_rule_version,
            extra={
                "reasons": scored.reasons + reasons,
                "confidence_label": scored.confidence,
                "prior_observation_id": prior_observation.id if prior_observation else None,
                "new_observation_id": new_obs.id,
                "region": new_obs.region,
                "prior_regions": sorted(prior_regions) if prior_regions else None,
                "experimental": experimental,
                "alerted": False,
                "editorial_eligible": editorial_eligible,
                "editorial_eligibility_reasons": eligibility_reasons,
            },
        )
        self.session.add(event)
        self.session.flush()
        self.session.add(EventWatch(event_id=event.id, watch_id=watch.id, role="subject"))

        logger.info(
            "product_transition_event_recorded",
            event_id=event.id,
            event_type=scored.event_type,
            watch_id=watch.id,
            score=scored.score,
        )

        if notify and editorial_eligible:
            notifier = DiscordNotifier(settings)
            threshold = (
                settings.discord_experimental_min_score if experimental else settings.discord_official_min_score
            )
            if notifier.editorial_enabled and scored.score >= threshold:
                text = format_alert(
                    manufacturer=watch.manufacturer,
                    brand=watch.brand,
                    reference_raw=watch.reference_raw,
                    scored=scored,
                    region=new_obs.region,
                    announcement_title=f"{watch.model_name or watch.reference_raw} — {scored.event_type}",
                    announcement_url=new_obs.source_url,
                    observed_at=datetime.now(UTC).isoformat(),
                    experimental=experimental,
                )
                sent = notifier.send_editorial_alert(text)
                event.extra = {**event.extra, "alerted": sent}
                self.session.commit()

        return {"event_type": scored.event_type, "event_id": event.id, "score": scored.score, "confidence": scored.confidence}

    def _record_watch_event(
        self, *, watch: Watch, is_new_watch: bool, lead, region: str | None, notify: bool = False,
        experimental: bool = False, force_baseline: bool = False,
    ) -> dict:
        """Classify and persist a deterministic Event for a resolved watch.

        Only NEW_REFERENCE and NEW_REGION are implemented from this call
        site — these are the two transitions we currently have real evidence
        for from a news announcement (PRICE_CHANGE/AVAILABILITY_CHANGE/
        SOLD_OUT/RESTOCK exist in app.services.editorial but require a
        healthy before/after SourceObservation pair, which only a product-
        page/catalog collector produces — see classify_price_availability_
        transition and its call site expectations). Routine repeat-region
        observations create no event and are stored silently, matching the
        "baseline is not news" rule.
        """
        from app.models import Event, EventWatch
        from app.services.discord_notify import DiscordNotifier
        from app.services.editorial import format_alert
        from app.services.epoch import is_baseline_active

        if force_baseline:
            return {"event_type": None, "reason": "source_scoped_baseline"}
        if is_baseline_active(self.session):
            # See the matching guard in _record_product_transition: baseline
            # discovery of a watch/region is known-existing-state, not news.
            return {"event_type": None, "reason": "epoch_baseline_active"}

        prior_regions = self._prior_regions_for_watch(watch.id, exclude_lead_id=lead.id)

        if is_new_watch or (region and prior_regions and region not in prior_regions):
            # Hardening (Sprint 10): a NEW_REFERENCE/NEW_REGION event must
            # reflect genuinely current news, not an old official article
            # discovered late (e.g. Timex's large historical blog archive
            # becoming newly reachable). Same principle as Sprint 8's
            # SpecialistLead fix, applied here to the first-party news path
            # that creates Events directly -- see _ISO_TIMESTAMP_NEWS_SOURCES.
            stale_reason = self._stale_official_announcement(lead)
            if stale_reason:
                return {"event_type": None, "reason": stale_reason}

        if is_new_watch:
            event_type = "NEW_REFERENCE"
        elif region and prior_regions and region not in prior_regions:
            event_type = "NEW_REGION"
        else:
            return {"event_type": None, "reason": "no_new_evidence"}

        character = self._extract_product_character(lead.announcement_title or "")
        evidence = EventEvidence(
            event_type=event_type,
            manufacturer=watch.manufacturer,
            brand=watch.brand,
            collection=watch.collection or lead.collection,
            region=region,
            is_first_party=True,
            prior_regions=prior_regions,
            reference_raw=watch.reference_raw,
            **character,
        )
        scored = score_event(evidence)

        event = Event(
            event_type=scored.event_type,
            title=f"{watch.manufacturer} {watch.reference_raw}: {scored.event_type}",
            status="DRAFT",
            story_score=scored.score,
            confidence_score={"HIGH": 90.0, "MEDIUM": 60.0, "LOW": 30.0}[scored.confidence],
            data_completeness_score=lead.completeness_score,
            scoring_rule_version=scored.scoring_rule_version,
            extra={
                "reasons": scored.reasons,
                "confidence_label": scored.confidence,
                "lead_id": lead.id,
                "announcement_url": lead.announcement_url,
                "region": region,
                "prior_regions": sorted(prior_regions),
                "experimental": experimental,
                "alerted": False,
            },
        )
        self.session.add(event)
        self.session.flush()
        self.session.add(EventWatch(event_id=event.id, watch_id=watch.id, role="subject"))

        logger.info(
            "editorial_event_recorded",
            event_id=event.id,
            event_type=scored.event_type,
            watch_id=watch.id,
            score=scored.score,
            confidence=scored.confidence,
        )

        if notify:
            settings = get_settings()
            notifier = DiscordNotifier(settings)
            threshold = (
                settings.discord_experimental_min_score if experimental else settings.discord_official_min_score
            )
            if notifier.editorial_enabled and scored.score >= threshold:
                text = format_alert(
                    manufacturer=watch.manufacturer,
                    brand=watch.brand,
                    reference_raw=watch.reference_raw,
                    scored=scored,
                    region=region,
                    announcement_title=lead.announcement_title,
                    announcement_url=lead.announcement_url,
                    observed_at=datetime.now(UTC).isoformat(),
                    experimental=experimental,
                )
                sent = notifier.send_editorial_alert(text)
                event.extra = {**event.extra, "alerted": sent}
                self.session.commit()

        return {
            "event_type": scored.event_type,
            "event_id": event.id,
            "score": scored.score,
            "confidence": scored.confidence,
        }

    def process_news_announcement(
        self,
        fr: FetchResult,
        *,
        run_id: int | None,
        discovered_meta: dict | None = None,
        collector_id: str = "casio_intl_news",
        collector_version: str = "0.1.0",
        manufacturer: str = "Casio",
        brand: str = "Casio",
        parse_fn=None,
        merge_key_prefix: str | None = None,
        default_region: str = "INTL",
        emit_events: bool = False,
        notify: bool = False,
        experimental: bool = False,
        force_baseline: bool = False,
    ) -> dict:
        """Persist a news announcement as release lead (+ optional watches).

        manufacturer/brand/parse_fn/merge_key_prefix default to the original
        Casio-only behaviour exactly, so existing callers (the production
        Casio pipeline) are unaffected. Other brands pass their own parser
        and identity via these kwargs (see run_brand_news_pipeline).
        """
        from app.models import ReleaseLead

        if parse_fn is None:
            from app.parsers.casio_news import parse_casio_news_html as parse_fn
        if merge_key_prefix is None:
            merge_key_prefix = manufacturer.lower()

        outcome = {
            "success": False,
            "new_lead": False,
            "new_watch": False,
            "lead_id": None,
            "error": None,
            "merged": False,
        }
        if not fr.success or not fr.payload:
            outcome["error"] = fr.error or "fetch failed"
            return outcome

        correlation_id = str(uuid.uuid4())
        snap_meta = self.storage.store(
            fr.payload,
            source_url=fr.url,
            content_type=fr.content_type,
            collector_id=collector_id,
            collector_version=collector_version,
        )
        blob = (
            self.session.query(SnapshotBlob)
            .filter_by(content_hash=snap_meta["content_hash"])
            .one_or_none()
        )
        if not blob:
            blob = SnapshotBlob(
                content_hash=snap_meta["content_hash"],
                filepath=snap_meta["filepath"],
                compression_type=snap_meta.get("compression_type"),
                byte_size=snap_meta["byte_size"],
                content_type=snap_meta.get("content_type"),
                schema_version=snap_meta["schema_version"],
                encoding=snap_meta.get("encoding"),
            )
            self.session.add(blob)
            self.session.flush()
        fetch = SnapshotFetch(
            blob_id=blob.id,
            source_url=fr.url,
            collector_id=collector_id,
            collector_version=collector_version,
            http_status=fr.status_code,
            extra_metadata={"elapsed_ms": fr.elapsed_ms, "kind": "news_announcement"}
            if fr.elapsed_ms
            else {"kind": "news_announcement"},
        )
        self.session.add(fetch)
        self.session.flush()

        parsed = parse_fn(fr.payload, source_url=fr.url)
        if not parsed.success:
            outcome["error"] = parsed.error
            self.session.commit()
            return outcome

        title = parsed.title or (discovered_meta or {}).get("title") or fr.url
        refs_payload = [
            {
                "raw": r.raw,
                "normalized": r.normalized,
                "location": r.location,
                "confidence": r.confidence,
                "warning": r.warning,
            }
            for r in parsed.model_references
        ]

        # merge key: preferred first model ref, else normalized URL path
        merge_key = None
        if parsed.model_references:
            merge_key = f"{merge_key_prefix}:{parsed.model_references[0].normalized}"
        else:
            merge_key = f"url:{fr.url.rstrip('/').lower()}"

        existing = (
            self.session.query(ReleaseLead)
            .filter(
                (ReleaseLead.announcement_url == fr.url)
                | (ReleaseLead.merge_key == merge_key)
            )
            .first()
        )
        if existing and existing.announcement_url != fr.url:
            outcome["merged"] = True
            outcome["lead_id"] = existing.id
            outcome["success"] = True
            # record secondary observation note
            notes = existing.notes or ""
            note_line = f"merged_from={fr.url}"
            if note_line not in notes:
                existing.notes = (notes + "\n" + note_line).strip()
            self.session.commit()
            return outcome

        if existing:
            lead = existing
            outcome["new_lead"] = False
        else:
            lead = ReleaseLead(
                manufacturer=manufacturer,
                brand=brand,
                collection=parsed.collection,
                announcement_title=title[:512],
                announcement_date=parsed.publication_date
                or (discovered_meta or {}).get("publication_date_text"),
                announcement_url=fr.url,
                source_id=collector_id,
                source_region=(discovered_meta or {}).get("source_region") or default_region,
                source_language=(discovered_meta or {}).get("source_language") or "en",
                model_references=refs_payload,
                product_urls=parsed.product_urls,
                image_urls=parsed.image_urls
                or ([discovered_meta.get("image_url")] if discovered_meta and discovered_meta.get("image_url") else []),
                snapshot_fetch_id=fetch.id,
                confidence_score=80.0 if refs_payload else 45.0,
                completeness_score=60.0 if refs_payload else 25.0,
                enrichment_status="ANNOUNCEMENT_ONLY" if refs_payload else "ANNOUNCEMENT_ONLY",
                merge_key=merge_key,
            )
            self.session.add(lead)
            self.session.flush()
            outcome["new_lead"] = True

        watch_ids = list(lead.watch_ids or [])
        for ref in parsed.model_references:
            if ref.confidence < 0.7:
                continue
            watch, is_new = self._resolve_or_create_watch(
                reference_raw=ref.raw,
                manufacturer=manufacturer,
                brand=brand,
                collection=parsed.collection,
                model_name=None,
                extra={},
                correlation_id=correlation_id,
                run_id=run_id,
            )
            if watch.id not in watch_ids:
                watch_ids.append(watch.id)
            if is_new:
                outcome["new_watch"] = True
            if emit_events:
                outcome.setdefault("watch_events", []).append(
                    self._record_watch_event(
                        watch=watch,
                        is_new_watch=is_new,
                        lead=lead,
                        region=lead.source_region,
                        notify=notify,
                        experimental=experimental,
                        force_baseline=force_baseline,
                    )
                )
        lead.watch_ids = watch_ids
        if watch_ids and not refs_payload:
            lead.enrichment_status = "ANNOUNCEMENT_ONLY"
        elif watch_ids:
            lead.enrichment_status = "PARTIALLY_ENRICHED"
        self.session.commit()
        outcome["success"] = True
        outcome["lead_id"] = lead.id
        return outcome

    def run_multi_source_pipeline(
        self,
        *,
        max_items: int | None = 10,
        skip_lock: bool = False,
        include_catalog: bool = True,
        news_index_html: bytes | None = None,
        emit_events: bool = True,
    ) -> CollectorRun:
        """Run accessible official Casio sources + optional catalog enrichment.

        emit_events (added 2026-08-15, see
        ai/handoff/INCIDENT_SILENT_SCHEDULED_NOTIFICATIONS.md): this is the
        production entrypoint for both the manual `--live` and scheduled
        `--scheduled` CLI paths (scripts/run_pipeline.py::run_live_or_scheduled
        calls this identically either way -- there is no separate "scheduler"
        code path to keep in sync). Defaults True, matching the same
        established contract run_brand_news_pipeline/
        run_product_observation_pipeline already use for every other
        production lane: real Event creation and Discord-eligibility on
        real production runs, with the pre-existing epoch-baseline check
        inside _record_watch_event/_record_product_transition (unrelated to
        this parameter) remaining the actual protection against a historical
        backlog being misclassified as news. Before this change, this
        function never passed emit_events to process_news_announcement/
        process_fetch_result at all, so both silently took their own
        emit_events=False default -- Casio's production path could
        discover a genuinely new watch and never create an Event for it,
        regardless of Discord configuration.
        """
        from app.collectors.casio_intl_news import (
            COLLECTOR_ID as NEWS_ID,
        )
        from app.collectors.casio_intl_news import (
            COLLECTOR_VERSION as NEWS_VER,
        )
        from app.collectors.casio_intl_news import (
            CasioIntlNewsCollector,
        )
        from app.collectors.casio_japan import COLLECTOR_ID as CAT_ID

        settings = get_settings()
        lock = RunLockService(self.session, settings, collector_id="casio_multi")
        if not skip_lock:
            lock_result = lock.acquire()
            if not lock_result.acquired:
                started = datetime.now(UTC)
                skip_run = CollectorRun(
                    collector_id="casio_multi",
                    collector_version="0.2.0",
                    started_at=started,
                    completed_at=started,
                    status="SKIPPED_OVERLAP",
                    summary_metadata={
                        "reason": lock_result.reason,
                        "active_run_id": lock_result.active_run_id,
                    },
                )
                self.session.add(skip_run)
                self.session.commit()
                return skip_run

        started = datetime.now(UTC)
        run = CollectorRun(
            collector_id="casio_multi",
            collector_version="0.2.0",
            started_at=started,
            status="RUNNING",
            **self._epoch_fields(),
        )
        self.session.add(run)
        self.session.commit()
        if not skip_lock:
            lock.update_run_id(run.id)

        components: dict[str, Any] = {}
        new_leads = 0
        new_watches = 0
        failures = 0
        parsed = 0

        try:
            # 1) International news (primary accessible source)
            news = CasioIntlNewsCollector()
            news_result = news.run(max_items=max_items, index_html=news_index_html)
            news_status = news_result.metadata.get("component_status") or "FAILED"
            components[NEWS_ID] = {
                "status": news_status,
                "discovered": len(news_result.discovered),
                "fetched_ok": sum(1 for f in news_result.fetched if f.success),
            }
            self._update_component_state(NEWS_ID, news_status, len(news_result.discovered))

            meta_by_url = {i.url: i.metadata | {"title": i.title} for i in news_result.discovered}
            for fr in news_result.fetched:
                if not fr.success:
                    failures += 1
                    continue
                out = self.process_news_announcement(
                    fr,
                    run_id=run.id,
                    discovered_meta=meta_by_url.get(fr.url),
                    collector_id=NEWS_ID,
                    collector_version=NEWS_VER,
                    emit_events=emit_events,
                    notify=emit_events,
                )
                if out["success"]:
                    parsed += 1
                    if out.get("new_lead"):
                        new_leads += 1
                    if out.get("new_watch"):
                        new_watches += 1
                else:
                    failures += 1

            # 2) Catalog enrichment (optional, with backoff)
            catalog_status = "SKIPPED"
            if include_catalog:
                if self._should_skip_backed_off(CAT_ID):
                    catalog_status = "BACKED_OFF"
                    components[CAT_ID] = {"status": catalog_status, "discovered": 0}
                else:
                    from app.collectors.casio_japan import CasioJapanCollector

                    cat = CasioJapanCollector()
                    cat_result = cat.run(max_items=max_items)
                    catalog_status = cat_result.metadata.get("component_status") or "FAILED"
                    components[CAT_ID] = {
                        "status": catalog_status,
                        "discovered": len(cat_result.discovered),
                        "discovery_fetches": cat_result.metadata.get("discovery_fetches"),
                    }
                    self._update_component_state(
                        CAT_ID, catalog_status, len(cat_result.discovered)
                    )
                    if catalog_status not in ("BLOCKED", "ZERO_ITEMS", "FAILED"):
                        for fr in cat_result.fetched:
                            if not fr.success:
                                failures += 1
                                continue
                            out = self.process_fetch_result(
                                fr, run_id=run.id, emit_events=emit_events, notify=emit_events
                            )
                            if out["success"]:
                                parsed += 1
                                if out.get("new_watch"):
                                    new_watches += 1
                            else:
                                failures += 1

            # Combined status
            statuses = [c.get("status") for c in components.values()]
            if any(s in ("SUCCESS", "PARTIAL") for s in statuses) and any(
                s == "BLOCKED" for s in statuses
            ):
                combined = "PARTIAL"
            elif any(s == "SUCCESS" for s in statuses) and all(
                s in ("SUCCESS", "ZERO_ITEMS", "BACKED_OFF", "SKIPPED") for s in statuses
            ):
                combined = "SUCCESS"
            elif any(s == "PARTIAL" for s in statuses):
                combined = "PARTIAL"
            elif all(s == "BLOCKED" for s in statuses if s not in ("SKIPPED", "BACKED_OFF")):
                combined = "BLOCKED"
            elif all(s in ("ZERO_ITEMS", "SKIPPED", "BACKED_OFF") for s in statuses):
                combined = "ZERO_ITEMS"
            elif any(s in ("SUCCESS", "PARTIAL") for s in statuses):
                combined = "PARTIAL"
            else:
                combined = "FAILED"

            completed = datetime.now(UTC)
            run.completed_at = completed
            run.status = combined
            run.discovered_count = sum(c.get("discovered", 0) for c in components.values())
            run.fetched_count = sum(c.get("fetched_ok", 0) for c in components.values())
            run.parsed_count = parsed
            run.new_watch_count = new_watches
            run.failure_count = failures
            run.duration_ms = int((completed - started).total_seconds() * 1000)
            run.summary_metadata = {
                "components": components,
                "new_leads": new_leads,
                "new_watches": new_watches,
            }
            self.session.commit()
            logger.info(
                "multi_pipeline_completed",
                run_id=run.id,
                status=run.status,
                components=components,
                new_leads=new_leads,
            )
            return run
        except Exception as exc:
            self.session.rollback()
            run.status = "FAILED"
            run.completed_at = datetime.now(UTC)
            run.summary_metadata = {"fatal_error": str(exc)}
            self.session.add(run)
            self.session.commit()
            raise
        finally:
            if not skip_lock:
                lock.release()

    # --- Experimental multi-brand news discovery (Citizen, Seiko) -----------
    # Deliberately NOT called from scripts/run_pipeline.py's --scheduled path
    # (that stays Casio-only). Has its own overlap protection: distinct
    # collector_id + distinct lock file per brand (see run_brand_news_pipeline
    # below), so it cannot collide with the Casio lock in either direction and
    # is safe to put on its own independent schedule (Sprint 2 requirement).
    _BRAND_REGISTRY: dict[str, dict[str, Any]] = {}

    def run_brand_news_pipeline(
        self,
        brand: str,
        *,
        max_items: int | None = 10,
        index_html: bytes | None = None,
        emit_events: bool = True,
        force_baseline: bool = False,
    ) -> CollectorRun:
        """Run an experimental single-brand news-discovery pipeline.

        brand: "citizen", "seiko", or "timex". Casio is intentionally not
        supported here — its production path is run_multi_source_pipeline/
        run_casio_pipeline and must not be duplicated or bypassed.

        force_baseline (Sprint 9): source-scoped silent baseline for a
        brand joining an already-baselined epoch -- see _epoch_fields.
        """
        if not self._BRAND_REGISTRY:
            from app.collectors.citizen_news import (
                COLLECTOR_ID as CITIZEN_ID,
            )
            from app.collectors.citizen_news import (
                COLLECTOR_VERSION as CITIZEN_VER,
            )
            from app.collectors.citizen_news import (
                CitizenNewsCollector,
            )
            from app.collectors.seiko_news import (
                COLLECTOR_ID as SEIKO_ID,
            )
            from app.collectors.seiko_news import (
                COLLECTOR_VERSION as SEIKO_VER,
            )
            from app.collectors.seiko_news import (
                SeikoNewsCollector,
            )
            from app.collectors.timex_news import (
                COLLECTOR_ID as TIMEX_NEWS_ID,
            )
            from app.collectors.timex_news import (
                COLLECTOR_VERSION as TIMEX_NEWS_VER,
            )
            from app.collectors.timex_news import (
                TimexNewsCollector,
            )
            from app.parsers.citizen_news import parse_citizen_news_html
            from app.parsers.seiko_news import parse_seiko_news_html
            from app.parsers.timex_news import parse_timex_news_entry

            self._BRAND_REGISTRY.update(
                {
                    "citizen": {
                        "collector_cls": CitizenNewsCollector,
                        "collector_id": CITIZEN_ID,
                        "collector_version": CITIZEN_VER,
                        "parse_fn": parse_citizen_news_html,
                        "manufacturer": "Citizen",
                        "brand": "Citizen",
                        "default_region": "GLOBAL",
                    },
                    "seiko": {
                        "collector_cls": SeikoNewsCollector,
                        "collector_id": SEIKO_ID,
                        "collector_version": SEIKO_VER,
                        "parse_fn": parse_seiko_news_html,
                        "manufacturer": "Seiko",
                        "brand": "Seiko",
                        "default_region": "JP",
                    },
                    "timex": {
                        "collector_cls": TimexNewsCollector,
                        "collector_id": TIMEX_NEWS_ID,
                        "collector_version": TIMEX_NEWS_VER,
                        "parse_fn": parse_timex_news_entry,
                        "manufacturer": "Timex",
                        "brand": "Timex",
                        "default_region": "US",
                    },
                }
            )

        if brand not in self._BRAND_REGISTRY:
            raise ValueError(f"unsupported experimental brand: {brand!r}")
        cfg = self._BRAND_REGISTRY[brand]

        # Isolated overlap protection: distinct collector_id AND distinct
        # lock file per brand, so this can never interact with the Casio
        # lock (shared casio_japan.run.lock) in either direction, while
        # still being safe to put on its own schedule (Sprint 2 requirement:
        # an experimental scheduled lane must not be able to double-run).
        settings = get_settings()
        lock_path = settings.resolved_lock_path.parent / f"{cfg['collector_id']}.run.lock"
        lock = RunLockService(
            self.session, settings, collector_id=cfg["collector_id"], lock_path=lock_path
        )
        lock_result = lock.acquire()
        if not lock_result.acquired:
            started = datetime.now(UTC)
            skip_run = CollectorRun(
                collector_id=cfg["collector_id"],
                collector_version=cfg["collector_version"],
                started_at=started,
                completed_at=started,
                status="SKIPPED_OVERLAP",
                summary_metadata={"reason": lock_result.reason, "active_run_id": lock_result.active_run_id},
            )
            self.session.add(skip_run)
            self.session.commit()
            return skip_run

        started = datetime.now(UTC)
        run = CollectorRun(
            collector_id=cfg["collector_id"],
            collector_version=cfg["collector_version"],
            started_at=started,
            status="RUNNING",
            **self._epoch_fields(force_baseline=force_baseline),
        )
        self.session.add(run)
        self.session.commit()
        lock.update_run_id(run.id)

        try:
            collector = cfg["collector_cls"]()
            result = collector.run(max_items=max_items, index_html=index_html)
            status = result.metadata.get("component_status") or "FAILED"
            self._update_component_state(cfg["collector_id"], status, len(result.discovered))

            new_leads = new_watches = parsed = failures = 0
            events: list[dict] = []
            meta_by_url = {i.url: i.metadata | {"title": i.title} for i in result.discovered}
            for fr in result.fetched:
                if not fr.success:
                    failures += 1
                    continue
                out = self.process_news_announcement(
                    fr,
                    run_id=run.id,
                    discovered_meta=meta_by_url.get(fr.url),
                    collector_id=cfg["collector_id"],
                    collector_version=cfg["collector_version"],
                    manufacturer=cfg["manufacturer"],
                    brand=cfg["brand"],
                    parse_fn=cfg["parse_fn"],
                    merge_key_prefix=brand,
                    default_region=cfg["default_region"],
                    emit_events=emit_events,
                    notify=emit_events,
                    experimental=True,
                    force_baseline=force_baseline,
                )
                if out["success"]:
                    parsed += 1
                    if out.get("new_lead"):
                        new_leads += 1
                    if out.get("new_watch"):
                        new_watches += 1
                    events.extend(e for e in out.get("watch_events", []) if e.get("event_type"))
                else:
                    failures += 1

            completed = datetime.now(UTC)
            run.completed_at = completed
            run.status = status
            run.discovered_count = len(result.discovered)
            run.fetched_count = sum(1 for f in result.fetched if f.success)
            run.parsed_count = parsed
            run.new_watch_count = new_watches
            run.failure_count = failures
            run.duration_ms = int((completed - started).total_seconds() * 1000)
            run.summary_metadata = {
                "brand": brand,
                "component_status": status,
                "new_leads": new_leads,
                "new_watches": new_watches,
                "events": events,
            }
            self.session.commit()
            logger.info(
                "brand_news_pipeline_completed",
                run_id=run.id,
                brand=brand,
                status=run.status,
                new_leads=new_leads,
                events=len(events),
            )
            return run
        except Exception as exc:
            self.session.rollback()
            run.status = "FAILED"
            run.completed_at = datetime.now(UTC)
            run.summary_metadata = {"fatal_error": str(exc)}
            self.session.add(run)
            self.session.commit()
            raise
        finally:
            lock.release()

    # --- Experimental multi-brand product/catalogue observation (Sprint 3) -
    # Same isolation pattern as run_brand_news_pipeline: distinct
    # collector_id + distinct lock file per brand, own collector_runs rows,
    # cannot interact with the Casio lock/state in either direction.
    _PRODUCT_REGISTRY: dict[str, dict[str, Any]] = {}

    def run_product_observation_pipeline(
        self,
        brand: str,
        *,
        max_items: int | None = None,
        offline_fixture: object = None,
        emit_events: bool = True,
        force_baseline: bool = False,
    ) -> CollectorRun:
        """Run an experimental single-brand product/catalogue observation
        pipeline. brand: "citizen", "seiko", or "timex". Casio's product
        path stays run_casio_pipeline/run_multi_source_pipeline's catalog
        enrichment — not duplicated here.

        offline_fixture is passed through to the collector's own offline
        kwarg (collection_html for Citizen, listing_pages for Seiko/Timex)
        — see each collector's run() signature.

        force_baseline (Sprint 9): source-scoped silent baseline for a
        brand joining an already-baselined epoch -- see _epoch_fields.
        """
        if not self._PRODUCT_REGISTRY:
            from app.collectors.casio_uk_sitemap import (
                COLLECTOR_ID as CASIO_UK_ID,
            )
            from app.collectors.casio_uk_sitemap import (
                COLLECTOR_VERSION as CASIO_UK_VER,
            )
            from app.collectors.casio_uk_sitemap import (
                REGION as CASIO_UK_REGION,
            )
            from app.collectors.casio_uk_sitemap import CasioUKSitemapCollector
            from app.collectors.citizen_de_products import (
                COLLECTOR_ID as CITIZEN_DE_PROD_ID,
            )
            from app.collectors.citizen_de_products import (
                COLLECTOR_VERSION as CITIZEN_DE_PROD_VER,
            )
            from app.collectors.citizen_de_products import (
                REGION as CITIZEN_DE_PROD_REGION,
            )
            from app.collectors.citizen_de_products import CitizenGermanyProductsCollector
            from app.collectors.citizen_products import (
                COLLECTOR_ID as CITIZEN_PROD_ID,
            )
            from app.collectors.citizen_products import (
                COLLECTOR_VERSION as CITIZEN_PROD_VER,
            )
            from app.collectors.citizen_products import (
                REGION as CITIZEN_PROD_REGION,
            )
            from app.collectors.citizen_products import (
                CitizenProductsCollector,
            )
            from app.collectors.seiko_jp_products import (
                COLLECTOR_ID as SEIKO_JP_PROD_ID,
            )
            from app.collectors.seiko_jp_products import (
                COLLECTOR_VERSION as SEIKO_JP_PROD_VER,
            )
            from app.collectors.seiko_jp_products import (
                REGION as SEIKO_JP_PROD_REGION,
            )
            from app.collectors.seiko_jp_products import (
                SeikoJapanProductsCollector,
            )
            from app.collectors.seiko_products import (
                COLLECTOR_ID as SEIKO_PROD_ID,
            )
            from app.collectors.seiko_products import (
                COLLECTOR_VERSION as SEIKO_PROD_VER,
            )
            from app.collectors.seiko_products import (
                REGION as SEIKO_PROD_REGION,
            )
            from app.collectors.seiko_products import (
                SeikoProductsCollector,
            )
            from app.collectors.timex_products import (
                COLLECTOR_ID as TIMEX_PROD_ID,
            )
            from app.collectors.timex_products import (
                COLLECTOR_VERSION as TIMEX_PROD_VER,
            )
            from app.collectors.timex_products import (
                REGION as TIMEX_PROD_REGION,
            )
            from app.collectors.timex_products import (
                TimexProductsCollector,
            )
            from app.parsers.casio_uk_sitemap import parse_casio_uk_sitemap_item
            from app.parsers.citizen_de_products import parse_citizen_de_product_html
            from app.parsers.citizen_products import parse_citizen_search_hit
            from app.parsers.seiko_jp_products import parse_seiko_jp_product_json
            from app.parsers.seiko_products import parse_seiko_product_json
            from app.parsers.timex_products import parse_timex_product_json

            self._PRODUCT_REGISTRY.update(
                {
                    "casio_uk": {
                        "collector_cls": CasioUKSitemapCollector,
                        "collector_id": CASIO_UK_ID,
                        "collector_version": CASIO_UK_VER,
                        "parse_fn": parse_casio_uk_sitemap_item,
                        "default_region": CASIO_UK_REGION,
                        "offline_kwarg": "sitemap_payload",
                        "default_max_items": 300,
                        "known_urls_from_observations": True,
                    },
                    "citizen": {
                        "collector_cls": CitizenProductsCollector,
                        "collector_id": CITIZEN_PROD_ID,
                        "collector_version": CITIZEN_PROD_VER,
                        "parse_fn": parse_citizen_search_hit,
                        "default_region": CITIZEN_PROD_REGION,
                        "offline_kwarg": "search_pages",
                        "default_max_items": 300,
                        "known_urls_from_observations": True,
                    },
                    "citizen_de": {
                        "collector_cls": CitizenGermanyProductsCollector,
                        "collector_id": CITIZEN_DE_PROD_ID,
                        "collector_version": CITIZEN_DE_PROD_VER,
                        "parse_fn": parse_citizen_de_product_html,
                        "default_region": CITIZEN_DE_PROD_REGION,
                        "offline_kwarg": "sitemap_payload",
                        "default_max_items": 100,
                        "known_urls_from_observations": True,
                    },
                    "seiko": {
                        "collector_cls": SeikoProductsCollector,
                        "collector_id": SEIKO_PROD_ID,
                        "collector_version": SEIKO_PROD_VER,
                        "parse_fn": parse_seiko_product_json,
                        "default_region": SEIKO_PROD_REGION,
                        "offline_kwarg": "listing_pages",
                        "default_max_items": 300,
                    },
                    "seiko_jp": {
                        "collector_cls": SeikoJapanProductsCollector,
                        "collector_id": SEIKO_JP_PROD_ID,
                        "collector_version": SEIKO_JP_PROD_VER,
                        "parse_fn": parse_seiko_jp_product_json,
                        "default_region": SEIKO_JP_PROD_REGION,
                        "offline_kwarg": "listing_pages",
                        "default_max_items": 300,
                        "known_urls_from_observations": True,
                    },
                    "timex": {
                        "collector_cls": TimexProductsCollector,
                        "collector_id": TIMEX_PROD_ID,
                        "collector_version": TIMEX_PROD_VER,
                        "parse_fn": parse_timex_product_json,
                        "default_region": TIMEX_PROD_REGION,
                        "offline_kwarg": "listing_pages",
                        "default_max_items": 300,
                        "known_urls_from_observations": True,
                    },
                }
            )

        if brand not in self._PRODUCT_REGISTRY:
            raise ValueError(f"unsupported experimental product brand: {brand!r}")
        cfg = self._PRODUCT_REGISTRY[brand]

        settings = get_settings()
        lock_path = settings.resolved_lock_path.parent / f"{cfg['collector_id']}.run.lock"
        lock = RunLockService(
            self.session, settings, collector_id=cfg["collector_id"], lock_path=lock_path
        )
        lock_result = lock.acquire()
        if not lock_result.acquired:
            started = datetime.now(UTC)
            skip_run = CollectorRun(
                collector_id=cfg["collector_id"],
                collector_version=cfg["collector_version"],
                started_at=started,
                completed_at=started,
                status="SKIPPED_OVERLAP",
                summary_metadata={"reason": lock_result.reason, "active_run_id": lock_result.active_run_id},
            )
            self.session.add(skip_run)
            self.session.commit()
            return skip_run

        started = datetime.now(UTC)
        run = CollectorRun(
            collector_id=cfg["collector_id"],
            collector_version=cfg["collector_version"],
            started_at=started,
            status="RUNNING",
            **self._epoch_fields(force_baseline=force_baseline),
        )
        self.session.add(run)
        self.session.commit()
        lock.update_run_id(run.id)

        try:
            collector = cfg["collector_cls"]()
            effective_max_items = (
                max_items
                if max_items is not None
                else (None if force_baseline else cfg.get("default_max_items", 300))
            )
            run_kwargs = {"max_items": effective_max_items}
            if offline_fixture is not None:
                run_kwargs[cfg["offline_kwarg"]] = offline_fixture
            if cfg.get("known_urls_from_observations"):
                run_kwargs["known_product_urls"] = {
                    source_url
                    for (source_url,) in self.session.query(SourceObservation.source_url)
                    .filter(SourceObservation.collector_id == cfg["collector_id"])
                    .distinct()
                    .all()
                }
            result = collector.run(**run_kwargs)
            status = result.metadata.get("component_status") or "FAILED"
            self._update_component_state(cfg["collector_id"], status, len(result.discovered))

            new_watches = parsed = failures = 0
            events: list[dict] = []
            for fr in result.fetched:
                if not fr.success:
                    failures += 1
                    continue
                out = self.process_fetch_result(
                    fr,
                    run_id=run.id,
                    collector_id=cfg["collector_id"],
                    collector_version=cfg["collector_version"],
                    parse_fn=cfg["parse_fn"],
                    default_region=cfg["default_region"],
                    emit_events=emit_events,
                    notify=emit_events,
                    experimental=True,
                    force_baseline=force_baseline,
                )
                if out["success"]:
                    parsed += 1
                    if out.get("new_watch"):
                        new_watches += 1
                    pe = out.get("product_event")
                    if pe and pe.get("event_type"):
                        events.append(pe)
                else:
                    failures += 1

            completed = datetime.now(UTC)
            run.completed_at = completed
            run.status = status
            run.discovered_count = len(result.discovered)
            run.fetched_count = sum(1 for f in result.fetched if f.success)
            run.parsed_count = parsed
            run.new_watch_count = new_watches
            run.failure_count = failures
            run.duration_ms = int((completed - started).total_seconds() * 1000)
            run.summary_metadata = {
                "brand": brand,
                "component_status": status,
                "new_watches": new_watches,
                "events": events,
            }
            self.session.commit()
            logger.info(
                "product_observation_pipeline_completed",
                run_id=run.id,
                brand=brand,
                status=run.status,
                new_watches=new_watches,
                events=len(events),
            )
            return run
        except Exception as exc:
            self.session.rollback()
            run.status = "FAILED"
            run.completed_at = datetime.now(UTC)
            run.summary_metadata = {"fatal_error": str(exc)}
            self.session.add(run)
            self.session.commit()
            raise
        finally:
            lock.release()

