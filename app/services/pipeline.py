"""Pipeline orchestration: collector → snapshot → parse → normalize → identity → observation → ledger.

Collectors and parsers never write to the database.
All persistence happens here under clear item-level transaction boundaries.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
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
from app.normalization.references import normalize_casio_reference, safe_overall_confidence
from app.parsers.casio_japan import PARSER_ID, PARSER_VERSION, parse_casio_product_html
from app.services.run_lock import RunLockService
from app.services.snapshot_storage import SnapshotStorageService

logger = get_logger(__name__)


class PipelineService:
    def __init__(self, session: Session, storage: SnapshotStorageService | None = None) -> None:
        self.session = session
        self.storage = storage or SnapshotStorageService()

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
        norm = normalize_casio_reference(
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
    ) -> dict[str, Any]:
        """Process one fetched item under a single transaction boundary."""
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
            parse_result = parse_casio_product_html(fr.payload, source_url=fr.url)
            self._ledger(
                correlation_id=correlation_id,
                run_id=run_id,
                entity_type="parse",
                entity_id=None,
                stage="parsing",
                action="success" if parse_result.success else "failed",
                input_ref=snap_meta["filepath"],
                parser_version=PARSER_VERSION,
                metadata={
                    "parser_id": PARSER_ID,
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
                "extra_specs": pw.extra_specs,
            }

            watch, is_new = self._resolve_or_create_watch(
                reference_raw=pw.reference_raw,
                manufacturer=pw.manufacturer,
                brand=pw.brand or "Casio",
                collection=pw.collection,
                model_name=pw.model_name,
                extra=extra,
                correlation_id=correlation_id,
                run_id=run_id,
            )

            overall = safe_overall_confidence(pw.field_confidence)
            obs = SourceObservation(
                watch_id=watch.id,
                fetch_id=fetch.id,
                collector_id=collector_id,
                collector_version=collector_version,
                parser_id=PARSER_ID,
                parser_version=PARSER_VERSION,
                region="JP",
                source_url=fr.url,
                availability_status=pw.availability_status,
                price=pw.price,
                currency=pw.currency,
                source_trust_score=100.0,
                overall_confidence=overall,
                field_confidence=pw.field_confidence or {},
                parser_warnings=pw.parser_warnings or [],
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
                parser_version=PARSER_VERSION,
                metadata={"watch_id": watch.id, "is_new_watch": is_new, "fetch_id": fetch.id},
            )

            self.session.commit()
            outcome.update(
                {
                    "success": True,
                    "new_watch": is_new,
                    "observation_id": obs.id,
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

    def process_news_announcement(
        self,
        fr: FetchResult,
        *,
        run_id: int | None,
        discovered_meta: dict | None = None,
        collector_id: str = "casio_intl_news",
        collector_version: str = "0.1.0",
    ) -> dict:
        """Persist a news announcement as release lead (+ optional watches)."""
        from app.models import ReleaseLead
        from app.parsers.casio_news import parse_casio_news_html

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

        parsed = parse_casio_news_html(fr.payload, source_url=fr.url)
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
            merge_key = f"casio:{parsed.model_references[0].normalized}"
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
                manufacturer="Casio",
                brand="Casio",
                collection=parsed.collection,
                announcement_title=title[:512],
                announcement_date=parsed.publication_date
                or (discovered_meta or {}).get("publication_date_text"),
                announcement_url=fr.url,
                source_id=collector_id,
                source_region=(discovered_meta or {}).get("source_region") or "INTL",
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
                manufacturer="Casio",
                brand="Casio",
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
    ) -> CollectorRun:
        """Run accessible official Casio sources + optional catalog enrichment."""
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
        lock = RunLockService(self.session, settings)
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
                            out = self.process_fetch_result(fr, run_id=run.id)
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

