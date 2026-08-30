"""Pipeline orchestration: collector → snapshot → parse → normalize → identity → observation → ledger.

Collectors and parsers never write to the database.
All persistence happens here under clear item-level transaction boundaries.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
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
from app.normalization.generic_reference import normalize_generic_reference
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


def _initial_delivery_outcome(
    *, notify: bool, editorial_eligible: bool, first_seen_alertable: bool, maturity_allows_delivery: bool
) -> dict[str, Any]:
    """STD-UI-COM-011 remediation (2026-08-31): record the delivery outcome
    the control flow can already determine at event-creation time, so the
    UI can distinguish sent / failed / gated from never-eligible instead of
    collapsing all non-sent events into alerted=False.

    States: "ineligible" (editorially ineligible), "gated" (delivery
    suppressed by policy, with a machine-readable reason). "sent"/"failed"
    are recorded by the send block once a delivery is actually attempted.
    Purely additive Event.extra JSON under the "delivery" key; "alerted"
    keeps its existing meaning for backward compatibility."""
    if not editorial_eligible:
        return {"delivery": {"state": "ineligible"}}
    if not maturity_allows_delivery:
        return {"delivery": {"state": "gated", "reason": "experimental_maturity"}}
    if not first_seen_alertable:
        return {"delivery": {"state": "gated", "reason": "first_seen_opt_in"}}
    if not notify:
        return {"delivery": {"state": "gated", "reason": "notify_disabled"}}
    return {}

# Dispatch table for per-manufacturer reference normalization. Casio keeps its
# exact original call path (default kwargs identical to pre-multi-brand code)
# so existing Casio behaviour is provably unchanged.
_NORMALIZERS = {
    "Casio": normalize_casio_reference,
    "Citizen": normalize_citizen_reference,
    "Seiko": normalize_seiko_reference,
    "Timex": normalize_timex_reference,
    # Sitemap-family brands (2026-08-25): conservative passthrough normalization.
    "Tissot": normalize_generic_reference,
    "Hamilton": normalize_generic_reference,
    "Longines": normalize_generic_reference,
    "Bulova": normalize_generic_reference,
    "Orient": normalize_generic_reference,
    "Swatch": normalize_generic_reference,
}

# Sprint 10 hardening (ai/handoff/TIMEX_FRESHNESS_AUDIT.md): sources whose
# announcement_date is a genuine, machine-parseable ISO-8601 timestamp get a
# *stricter* freshness policy -- a missing/unparseable timestamp is treated
# as "not current" (return "unknown_publication_timestamp"), not silently
# assumed fresh. Timex's Shopify blog feed reliably provides this.
_ISO_TIMESTAMP_NEWS_SOURCES = frozenset({"timex_news"})

# 2026-08-19 hotfix (CasioBlog EQB-1300D-5A/-2A incident: a March 28
# article resurfaced as an Event on August 17 with no freshness check at
# all, because the original Sprint 10 gate below was scoped to
# _ISO_TIMESTAMP_NEWS_SOURCES only). Casio ("July 15, 2026"), Citizen
# ("23 July 2026", sometimes glued as "2 July2026" with no space), and
# Seiko ("January 07, 2026") all confirmed live as free-text month-name
# dates -- not ISO-8601, but not unparseable either. _parse_announcement_date
# below tries ISO first, then this fixed, deterministic list of confirmed
# real formats. Deliberately NOT a general-purpose date parser (no new
# dependency, no dateutil-style guessing) -- a string that matches none of
# these known shapes stays UNKNOWN, and for every source NOT in
# _ISO_TIMESTAMP_NEWS_SOURCES, UNKNOWN still means "no change from prior
# behavior" (no suppression) -- see _stale_official_announcement. This can
# only ever *add* suppression of a confidently-parsed, genuinely stale
# rediscovery; it can never suppress a source whose date it can't parse, so
# it carries no regression risk for existing Casio/Citizen/Seiko recall.
_MONTH_NAME_DATE_FORMATS = (
    "%B %d, %Y",  # Casio: "July 15, 2026"
    "%d %B %Y",  # Citizen: "23 July 2026"
    "%B %d %Y",  # occasional no-comma variant
)


def _parse_free_text_announcement_date(raw: str) -> datetime | None:
    text = raw.strip()
    # Citizen's confirmed glued form "2 July2026" -- insert the missing
    # space between month name and a 4-digit year before matching, rather
    # than adding a fifth format that would also accept genuinely malformed
    # strings.
    text = re.sub(r"([A-Za-z])(\d{4}\b)", r"\1 \2", text)
    for fmt in _MONTH_NAME_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _parse_announcement_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass
    return _parse_free_text_announcement_date(raw)


# 2026-08-19 accessory gate; 2026-08-21 the phrase list moved to
# app.services.editorial (shared with the specialist-lead classifier,
# which had the identical hole). Kept as a thin alias here so the
# pipeline call site reads the same.
from app.services.editorial import looks_like_accessory_only as _looks_like_accessory_only


class PipelineService:
    def __init__(self, session: Session, storage: SnapshotStorageService | None = None) -> None:
        self.session = session
        self.storage = storage or SnapshotStorageService()
        # 2026-08-24 batch-complete publication-cluster evaluation: armed by
        # run_product_observation_pipeline with {collector_id: [staged parsed
        # records]} for the current run so novelty evidence sees the WHOLE
        # source batch, not insertion-order prefix state. Unarmed (None) in
        # every other path -- behavior is unchanged there.
        self._current_publication_batch: dict[str, list[dict]] | None = None

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

    def _auto_baseline_for_first_run(self, collector_id: str) -> bool:
        """Collector-initialization safety invariant.

        2026-08-17 (see ai/handoff/INCIDENT_TIMEX_CATALOGUE_BACKFILL_BURST.md's
        addendum): a genuinely first-ever run for a collector must not be able
        to flood NEW_REFERENCE/NEW_REGION events just because whoever
        triggered it (a script, a dashboard "RUN ALL SAFE COLLECTORS" click)
        didn't know to pass --force-baseline -- this is exactly how both the
        local dev database and a fresh field-test database independently
        reproduced the same Timex flood.

        2026-08-21 hostile-audit remediation (Phase 8): that protection
        previously applied ONLY when no operational epoch existed, so adding
        a new collector/brand/region to an already-running deployment
        replayed the same flood unless an operator remembered
        --force-baseline. Baseline safety must not depend on operator
        memory: ANY collector with no successful run on this database is
        auto-baselined on its first run, epoch or no epoch. A first run
        persists watches/observations/leads normally but emits no Events.

        Collectors with established successful run history are grandfathered
        by that history rather than force-re-baselined at deploy (that would
        silently cost every production source one real collection cycle).
        The residual risk -- a long-retired collector re-enabled against a
        large accumulated delta -- is handled by the Phase 6 novelty
        inversion instead: such discoveries surface as honestly-labelled
        FIRST_SEEN_BY_CLANK unless they carry affirmative publication
        evidence, so a flood becomes a visible, low-priority, correctly-
        labelled review queue rather than a wall of confident false
        NEW_REFERENCE claims.
        """
        has_successful_run = (
            self.session.query(CollectorRun)
            .filter(
                CollectorRun.collector_id == collector_id,
                CollectorRun.status.in_(("SUCCESS", "PARTIAL", "ZERO_ITEMS")),
            )
            .first()
            is not None
        )
        return not has_successful_run

    def _stale_official_announcement(self, lead) -> str | None:
        """Returns a suppression reason if `lead` (a ReleaseLead) carries a
        confidently-parseable publication date that is older than the
        configured freshness window -- generalized 2026-08-19 (CasioBlog
        EQB-1300D-5A/-2A incident) to every official news source, not just
        Timex; see _parse_announcement_date's docstring for the parsing
        contract and why this cannot regress recall for a source whose date
        format isn't recognized.

        Sources in _ISO_TIMESTAMP_NEWS_SOURCES additionally treat a missing/
        unparseable date as itself disqualifying ("unknown_publication_timestamp")
        -- the stricter Sprint 10 Timex policy, unchanged. Every other source
        keeps its pre-existing behavior when the date can't be parsed: no
        suppression, i.e. still no assumption of freshness, just no change.
        """
        pub_dt = _parse_announcement_date(lead.announcement_date)

        if pub_dt is None:
            if lead.source_id in _ISO_TIMESTAMP_NEWS_SOURCES:
                return "unknown_publication_timestamp"
            return None

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
        # 2026-08-21 Phase 8: defaults flipped False->True. The historical
        # casio_multi incident (zero Events ever, silently, for months)
        # happened because these defaults were False and one runner forgot
        # the flag. Forgetting now fails LOUD (an unexpected Event surfaces
        # in review) instead of silent (a Watch row nobody ever hears
        # about). Observation-only tooling (replay_snapshot, fixture mode)
        # passes emit_events=False explicitly, making that intent visible.
        emit_events: bool = True,
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
                    collector_id=collector_id,
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

    @staticmethod
    def _parse_extra_specs_published_at(extra_specs: dict[str, Any] | None) -> datetime | None:
        """Best-effort ISO-8601 parse of a Watch's opportunistically
        captured `published_at` (see app/parsers/timex_products.py).
        Returns None for anything missing or unparseable -- callers must
        treat that exactly like "no evidence available", never an error."""
        if not extra_specs:
            return None
        raw = extra_specs.get("published_at")
        if not raw or not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    # 2026-08-19 hotfix (TW4B20700 Expedition Field Chronograph: tagged
    # "REACTIVATED"/"Backorder Eligible" by Timex's own catalogue, first
    # observed by Watch Clank today -- a real, correct NEW_REFERENCE by this
    # system's own "first seen by Clank" semantics, but a human skimming the
    # title alone could easily misread that as a new design). REACTIVATED
    # != NEW_REFERENCE: the event_type stays NEW_REFERENCE (that label is
    # honestly about discovery, not launch date -- see the "Discovery time
    # is not publication time" banner on /intelligence), but this makes the
    # present-tense catalogue-operation evidence an explicit, visible reason
    # rather than something a reviewer has to click through to the listing
    # to find.
    _REACTIVATION_TAGS = frozenset({"reactivated", "backorder eligible", "backorder-eligible"})

    @classmethod
    def _reactivation_signal(cls, extra_specs: dict[str, Any] | None) -> str | None:
        if not extra_specs:
            return None
        tags = extra_specs.get("tags")
        if not tags or not isinstance(tags, list):
            return None
        hits = sorted({t for t in tags if isinstance(t, str) and t.strip().lower() in cls._REACTIVATION_TAGS})
        if not hits:
            return None
        return (
            f"NOTE: source tags {hits} indicate a catalogue reactivation/backorder event, "
            "not necessarily a new design -- first-seen-by-Clank still holds, but this is "
            "present-tense catalogue-operation evidence, not launch evidence"
        )

    def _new_reference_baseline_freshness(self, *, watch: Watch, new_obs: SourceObservation):
        """Would this NEW_REFERENCE still be worth alerting despite an
        active baseline, because the source's own evidence proves it's
        recent? See ai/handoff/INCIDENT_TIMEX_BASELINE_ABSORPTION.md and
        app.services.freshness.classify_baseline_product_freshness."""
        from app.services.freshness import classify_baseline_product_freshness

        published_at = self._parse_extra_specs_published_at(watch.extra_specs)
        return classify_baseline_product_freshness(
            published_at=published_at,
            observed_at=new_obs.observed_at,
            window_hours=get_settings().product_baseline_freshness_window_hours,
        )

    def _publication_cluster_shape(self, *, watch: Watch, published_at: datetime) -> dict:
        """Is a fresh `published_at` a launch signature or maintenance noise?

        Live evidence from the real Hetzner catalogue (see
        ai/handoff/INCIDENT_20260819_EMERGENCY_HOTFIX.md, "published_at is
        not launch authority"): genuine coordinated launches share
        timestamps SECONDS apart within ONE collection (Cavatina Luxe: 5
        SKUs / 6 seconds; TW6A E-Line: 3 SKUs / 3 seconds), while routine
        catalogue-sync batches touch many unrelated collections at once (a
        23-product cluster spanning Waterbury Classic, Easy Reader,
        Weekender and Q Timex Marbella off one identical timestamp). A
        source timestamp can mean launch, migration, maintenance,
        republishing or localisation -- the cluster SHAPE is what
        distinguishes them.

        Returns {"siblings": N, "collections": M} where siblings = other
        watches of the same manufacturer with a parseable published_at
        within bulk_touch_proximity_seconds (default 90, the same
        empirically-justified window find_baseline_catchup_candidates
        uses), and collections = distinct non-null collections among
        watch + siblings. Deliberately bounded work: only novelty-event
        paths call this, which are rare in steady state.
        """
        proximity = timedelta(seconds=get_settings().bulk_touch_proximity_seconds)
        # 2026-08-24 batch-complete evaluation: when the runner staged the
        # current run's parsed records, judge cluster shape against the WHOLE
        # source batch (plus already-persisted watches from earlier runs),
        # never against insertion-order prefix state.
        batch = self._current_publication_batch
        if batch is not None:
            staged = []
            for records in batch.values():
                staged.extend(records)
            rows = [
                (None, rec.get("collection"), {"published_at": rec.get("published_at")})
                for rec in staged
                if rec.get("reference_canonical") != watch.reference_canonical
            ]
            persisted = (
                self.session.query(Watch.id, Watch.collection, Watch.extra_specs)
                .filter(
                    Watch.manufacturer == watch.manufacturer,
                    Watch.id != watch.id,
                )
                .all()
            )
            return self._cluster_shape_from_rows(
                watch=watch, published_at=published_at,
                rows=rows + [(r[0], r[1], r[2]) for r in persisted],
                proximity=proximity,
            )
        rows = (
            self.session.query(Watch.id, Watch.collection, Watch.extra_specs)
            .filter(
                Watch.manufacturer == watch.manufacturer,
                Watch.id != watch.id,
            )
            .all()
        )
        return self._cluster_shape_from_rows(
            watch=watch, published_at=published_at, rows=rows, proximity=proximity
        )

    def _cluster_shape_from_rows(
        self,
        *,
        watch: Watch,
        published_at: datetime,
        rows,
        proximity: timedelta,
    ) -> dict:
        """Shared cluster-shape computation for persisted-row and
        batch-payload sources. ``rows`` is an iterable of
        (watch_id, collection, extra_specs) EXCLUDING the subject watch."""
        siblings = 0
        collections = {watch.collection}
        for other_id, other_collection, other_specs in rows:
            other_published = self._parse_extra_specs_published_at(other_specs)
            if other_published is None:
                continue
            if abs((other_published - published_at).total_seconds()) > proximity.total_seconds():
                continue
            siblings += 1
            collections.add(other_collection)
        return {"siblings": siblings, "collections": len({c for c in collections if c})}

    def _publication_batch_payload(self) -> dict[str, list[dict]] | None:
        """The current run's staged parsed records, if a batch evaluation was
        armed by run_product_observation_pipeline (2026-08-24 repair).

        Why this exists: _record_product_transition runs DURING sequential
        ingest, so the persisted-Watch query in _publication_cluster_shape
        cannot yet see later members of the same source bulk-touch batch --
        the first item of a 100-product maintenance sync looked like an
        isolated launch (live case: Timex's 2026-08-21T05:48-06:11 catalogue
        touch produced 26 "launch-shaped" FIRST_SEEN events). When the
        runner arms this payload, evidence-strength classification sees the
        COMPLETE batch instead of insertion-order prefix state.
        """
        return self._current_publication_batch

    def _publication_evidence_strength(
        self, *, watch: Watch, published_at: datetime | None, reactivation_note: str | None,
        observed_at=None,
    ) -> tuple[str, dict]:
        """Classify the strength of a first sighting's novelty evidence.

        STRONG -- reserved for the official-news path (a first-party
            announcement article IS a launch claim); handled in
            _record_watch_event, never returned here.
        MEDIUM -- a fresh source publication timestamp whose cluster shape
            looks like a coordinated family launch (small sibling count,
            or all siblings in one collection).
        WEAK -- everything else: local absence only, a reactivation tag,
            or a fresh timestamp embedded in a large cross-collection
            sync batch (bulk-touch noise). WEAK never claims
            NEW_REFERENCE.
        """
        if reactivation_note:
            return "WEAK", {
                "evidence_strength": "WEAK",
                "cluster": None,
                "strength_reason": "source-declared reactivation/backorder contradicts novelty",
            }
        if published_at is None:
            return "WEAK", {
                "evidence_strength": "WEAK",
                "cluster": None,
                "strength_reason": "no publication evidence; local absence is not launch evidence",
            }
        # 2026-08-24: a future-dated source timestamp is bulk-touch/clock
        # noise, not launch evidence -- see
        # app.services.freshness.publication_timestamp_is_usable. The raw
        # value stays in novelty_evidence.source_published_at as provenance.
        from app.services.freshness import publication_timestamp_is_usable

        if not publication_timestamp_is_usable(
            published_at=published_at, observed_at=observed_at
        ):
            return "WEAK", {
                "evidence_strength": "WEAK",
                "cluster": None,
                "strength_reason": (
                    f"source published_at ({published_at.isoformat()}) is after the observation "
                    "-- rejected as freshness evidence (bulk-catalogue-touch or clock artifact)"
                ),
            }
        cluster = self._publication_cluster_shape(watch=watch, published_at=published_at)
        suspicious = (
            cluster["siblings"] + 1 >= get_settings().bulk_touch_cluster_min_size
            and cluster["collections"] >= get_settings().bulk_touch_cluster_min_collections
        )
        if suspicious:
            return "WEAK", {
                "evidence_strength": "WEAK",
                "cluster": cluster,
                "strength_reason": (
                    f"fresh published_at sits in a {cluster['siblings'] + 1}-product "
                    f"{cluster['collections']}-collection same-source sync batch -- "
                    "maintenance noise, not a launch signature"
                ),
            }
        return "MEDIUM", {
            "evidence_strength": "MEDIUM",
            "cluster": cluster,
            "strength_reason": (
                f"fresh published_at with launch-like cluster shape "
                f"({cluster['siblings'] + 1} product(s), "
                f"{cluster['collections']} collection(s) within "
                f"{get_settings().bulk_touch_proximity_seconds}s)"
            ),
        }

    def find_baseline_catchup_candidates(
        self, *, manufacturer: str | None = None, as_of: datetime | None = None
    ) -> list[dict]:
        """Identify Watches a baseline sweep correctly silenced (no Event,
        by design -- see _record_product_transition's baseline guard) but
        whose own captured published_at proves they were still genuinely
        recent, just outside the tight
        product_baseline_freshness_window_hours bar applied *at baseline
        time*. Read-only: creates nothing, notifies nothing. See
        create_baseline_catchup_events for the second, explicit-opt-in step
        -- this is deliberately two functions, not one, so a human reviews
        the candidate list before anything gets a belated Event (§18: "do
        not retro-alert an entire baseline").

        A candidate must have:
        - zero Events (never re-evaluates a watch that already got one,
          belated or otherwise -- naturally idempotent across repeat runs)
        - a first SourceObservation flagged is_baseline
        - a parseable extra_specs.published_at
        - age (as_of - published_at) within baseline_catchup_window_days,
          non-negative

        No published_at, unparseable, negative age, or too old -> excluded,
        silently. This can only ever surface a candidate with real
        first-party dating evidence; it never guesses.

        Each candidate also carries nearby_published_at_count: how many
        OTHER returned candidates for the same manufacturer have a
        published_at within 90 seconds (proximity, not exact match -- a
        live check found genuine multi-SKU families, e.g. Cavatina Luxe/
        TW6A, share timestamps seconds apart, not identical). A large count
        is not auto-rejected (that would just be a different unproven
        magic threshold, and the same live check found large clusters that
        genuinely were "everything in this release wave," not only sync
        noise) -- it's surfaced so create_baseline_catchup_events's
        required explicit watch_ids list is an informed human decision.
        """
        from app.models import Event, EventWatch

        now = ensure_utc(as_of) if as_of is not None else datetime.now(UTC)
        window = timedelta(days=get_settings().baseline_catchup_window_days)

        # 2026-08-21: exclude only watches with an existing NOVELTY-CLAIMING
        # event (NEW_REFERENCE / NEW_REGION). The original blanket "any
        # Event" exclusion made sense when every catalogue discovery was
        # either silent or a NEW_REFERENCE claim, but after the Phase 6
        # novelty inversion an uncertain discovery carries FIRST_SEEN_BY_CLANK
        # -- which is exactly the state catch-up exists to promote. Blocking
        # promotion because of the honest label would have permanently locked
        # every post-inversion 73-hour launch out of recovery (found by the
        # Phase 2 semantic specimen corpus).
        has_claim = (
            self.session.query(EventWatch.event_id)
            .join(Event, Event.id == EventWatch.event_id)
            .filter(
                EventWatch.watch_id == Watch.id,
                Event.event_type.in_(("NEW_REFERENCE", "NEW_REGION")),
            )
            .exists()
        )
        query = self.session.query(Watch).filter(~has_claim)
        if manufacturer:
            query = query.filter(Watch.manufacturer == manufacturer)

        candidates: list[dict] = []
        for watch in query.all():
            first_obs = (
                self.session.query(SourceObservation)
                .filter(SourceObservation.watch_id == watch.id)
                .order_by(SourceObservation.observed_at.asc())
                .first()
            )
            if first_obs is None or not first_obs.is_baseline:
                continue
            published_at = self._parse_extra_specs_published_at(watch.extra_specs)
            if published_at is None:
                continue
            age = now - ensure_utc(published_at)
            if age < timedelta(0) or age > window:
                continue
            candidates.append(
                {
                    "watch_id": watch.id,
                    "manufacturer": watch.manufacturer,
                    "reference_canonical": watch.reference_canonical,
                    "published_at": published_at.isoformat(),
                    "first_observed_at": ensure_utc(first_obs.observed_at).isoformat(),
                    "age_days": round(age.total_seconds() / 86400.0, 2),
                    "source_observation_id": first_obs.id,
                }
            )

        # Proximity clustering, not exact match: a live check against the
        # real Hetzner catalogue found genuine multi-SKU families (Cavatina
        # Luxe, the TW6A E-Line) share published_at values seconds apart,
        # not bit-identical -- exact-match counting missed them entirely.
        # It also found that same 90-second window sometimes spans several
        # *unrelated* collections at once (a routine catalogue-sync batch
        # touching many SKUs together, not one coordinated launch). Both
        # are real, confirmed shapes in the live data; this field only
        # reports the raw cluster size for a human to weigh -- it does not
        # attempt to auto-classify "genuine family" vs "sync batch" itself,
        # since that would just be a different unproven guess.
        by_manufacturer: dict[str, list[dict]] = {}
        for c in candidates:
            by_manufacturer.setdefault(c["manufacturer"], []).append(c)
        for group in by_manufacturer.values():
            group.sort(key=lambda c: c["published_at"])
            parsed = [datetime.fromisoformat(c["published_at"]) for c in group]
            for i, c in enumerate(group):
                nearby = sum(
                    1
                    for j, t in enumerate(parsed)
                    if j != i and abs((t - parsed[i]).total_seconds()) <= 90
                )
                c["nearby_published_at_count"] = nearby
        return candidates

    def create_baseline_catchup_events(
        self, *, watch_ids: list[int], notify: bool = False, experimental: bool = True
    ) -> list[dict]:
        """Create a belated NEW_REFERENCE Event for each given watch_id --
        never for "every candidate" implicitly; the caller (a human, via a
        reviewed find_baseline_catchup_candidates() list) must name exactly
        which watches. Idempotent per watch: a watch that has gained an
        Event since candidacy was checked (e.g. two operators running this
        concurrently) is skipped, not double-fired. notify defaults False
        -- a launch that's already 1-30 days old surfacing as a fresh
        Discord "breaking" alert would misrepresent its own age; the belated
        Event is reachable through the normal QC queue either way. Reuses
        the exact same scoring/persistence path as a live NEW_REFERENCE
        (_persist_product_event) so it is not a special, less-trusted kind
        of Event -- only the reasons and an extra.belated_baseline_catchup
        flag distinguish it, both purely for human transparency.
        """
        from app.models import Event, EventWatch
        from app.services.editorial import EventEvidence, score_event

        results: list[dict] = []
        for watch_id in watch_ids:
            watch = self.session.get(Watch, watch_id)
            if watch is None:
                results.append({"watch_id": watch_id, "created": False, "reason": "watch_not_found"})
                continue
            # Same Phase 6 reconciliation as find_baseline_catchup_candidates:
            # only a prior NOVELTY CLAIM blocks a belated NEW_REFERENCE. A
            # FIRST_SEEN_BY_CLANK event is the honest-uncertainty state this
            # function promotes, never a reason to refuse.
            existing_claim = (
                self.session.query(EventWatch)
                .join(Event, Event.id == EventWatch.event_id)
                .filter(
                    EventWatch.watch_id == watch_id,
                    Event.event_type.in_(("NEW_REFERENCE", "NEW_REGION")),
                )
                .first()
            )
            if existing_claim is not None:
                results.append({"watch_id": watch_id, "created": False, "reason": "already_has_event"})
                continue
            first_obs = (
                self.session.query(SourceObservation)
                .filter(SourceObservation.watch_id == watch_id)
                .order_by(SourceObservation.observed_at.asc())
                .first()
            )
            if first_obs is None:
                results.append({"watch_id": watch_id, "created": False, "reason": "no_observation"})
                continue
            published_at = self._parse_extra_specs_published_at(watch.extra_specs)
            if published_at is None:
                results.append({"watch_id": watch_id, "created": False, "reason": "no_published_at"})
                continue

            evidence = EventEvidence(
                event_type="NEW_REFERENCE",
                manufacturer=watch.manufacturer,
                brand=watch.brand,
                collection=watch.collection,
                region=first_obs.region,
                is_first_party=True,
                reference_raw=watch.reference_raw,
                price=first_obs.price,
                currency=first_obs.currency,
                availability_status=first_obs.availability_status,
                **self._availability_event_character(watch),
            )
            reasons = [
                "belated baseline catch-up (2026-08-19): discovered during an "
                "epoch baseline sweep, correctly silent at the time, but the "
                f"source's own published_at ({published_at.isoformat()}) proves "
                "this was still a genuinely recent launch -- "
                "see ai/handoff/INCIDENT_TIMEX_BASELINE_ABSORPTION.md",
            ]
            outcome = self._persist_product_event(
                watch=watch,
                new_obs=first_obs,
                scored=score_event(evidence),
                reasons=reasons,
                prior_observation=None,
                notify=notify,
                experimental=experimental,
                prior_regions=None,
            )
            from app.models import Event

            event = self.session.get(Event, outcome["event_id"])
            event.extra = {**event.extra, "belated_baseline_catchup": True}
            self.session.commit()
            results.append({"watch_id": watch_id, "created": True, **outcome})
        return results

    def _annotate_new_reference_burst(
        self, *, new_reference_event_ids: list[int], discovered_count: int
    ) -> dict[str, Any] | None:
        """Stamp same-run burst context onto every NEW_REFERENCE Event this
        run created -- never suppresses, never rescopes, never rescoreds
        anything. See ai/handoff/INCIDENT_TIMEX_CATALOGUE_BACKFILL_BURST.md.

        A single run's NEW_REFERENCE events are individually scored/
        persisted/notified inside the fetch loop above, before the run's
        true final count is known -- this runs once, after that loop, and
        patches the Event rows already created rather than restructuring
        the live per-item notify path (which real, non-burst runs also use
        every day in production; not touched here). Discord alerts for
        events in this run were already sent by the time this executes, so
        this only affects what a human reviewing the DB/dashboard sees
        after the fact -- a real, disclosed limitation, not an oversight.
        """
        count = len(new_reference_event_ids)
        if count == 0:
            return None

        settings = get_settings()
        ratio = count / discovered_count if discovered_count else 0.0
        probable_backfill = (
            count >= settings.catalogue_backfill_burst_min_count
            and ratio >= settings.catalogue_backfill_burst_min_ratio
        )
        context = {
            "same_run_new_reference_count": count,
            "same_run_discovered_count": discovered_count,
            "probable_catalogue_backfill": probable_backfill,
        }
        if not probable_backfill:
            return context

        from app.models import Event

        rows = self.session.query(Event).filter(Event.id.in_(new_reference_event_ids)).all()
        for event in rows:
            event.extra = {**(event.extra or {}), **context}
        return context

    def _record_product_transition(
        self, *, watch: Watch, new_obs: SourceObservation, is_new_watch: bool, notify: bool = False,
        experimental: bool = False, force_baseline: bool = False,
        collector_id: str | None = None,
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

        Narrow exception (see ai/handoff/INCIDENT_TIMEX_BASELINE_ABSORPTION.md):
        a brand-new reference (is_new_watch) discovered while baseline is
        active is still allowed through if the source captured a structured
        `published_at` proving it's genuinely recent -- see
        app.services.freshness.classify_baseline_product_freshness. Every
        other transition type, and every source without that evidence,
        remains unconditionally silent during baseline exactly as before.
        """
        from app.services.editorial import (
            EventEvidence,
            classify_price_availability_transition,
            score_event,
        )
        from app.services.epoch import is_baseline_active

        baseline_active = force_baseline or is_baseline_active(self.session)
        baseline_freshness = None
        if is_new_watch:
            # 2026-08-21 Phase 6/7: source-publication evidence is now
            # evaluated for EVERY first sighting, not only while a baseline
            # is active. It serves two roles with one implementation: the
            # pre-existing baseline override (INCIDENT_TIMEX_BASELINE_
            # ABSORPTION), and affirmative novelty evidence in normal
            # operation -- see the classification rule below.
            baseline_freshness = self._new_reference_baseline_freshness(watch=watch, new_obs=new_obs)

        if baseline_active and not (baseline_freshness is not None and baseline_freshness.state == "FRESH"):
            if force_baseline:
                # Sprint 9: source-scoped silent baseline (e.g. Timex joining
                # an already-baselined epoch) -- independent of the epoch's
                # own baseline window, see _epoch_fields' docstring.
                return {"event_type": None, "reason": "source_scoped_baseline"}
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
            # (guards above) UNLESS baseline_freshness proved FRESH, so it
            # otherwise only fires for a catalogue's first non-baseline
            # sighting of a reference -- exactly the discipline already
            # required before any collector is scheduled.
            # 2026-08-19 second hotfix pass (TW4B20700 blocker): a
            # REACTIVATED/backorder catalogue tag is stronger, source-
            # supplied counter-evidence against novelty than the absence of
            # a prior local observation is evidence *for* it.
            # FIRST_SEEN_BY_CLANK != NEW_REFERENCE -- see VALID_EVENT_TYPES'
            # docstring in app/services/editorial.py. This is deliberately
            # evaluated regardless of baseline state (unlike
            # baseline_freshness, which only exists during an active
            # baseline): the very first non-baseline sighting of a
            # reactivated reference is exactly the case that must not
            # silently become "NEW_REFERENCE" merely because baseline had
            # already ended.
            reactivation_note = self._reactivation_signal(watch.extra_specs)
            # 2026-08-21 Phase 6 -- NOVELTY INVERSION. The default for a
            # first local sighting is now FIRST_SEEN_BY_CLANK: "this
            # database has never seen this reference" is a discovery
            # milestone, not launch evidence. NEW_REFERENCE must be EARNED
            # by affirmative novelty evidence. The qualifying evidence
            # implemented here is the same bar already trusted for the
            # baseline override: the source's own structured publication
            # timestamp, within product_baseline_freshness_window_hours of
            # this observation (classify_baseline_product_freshness) AND a
            # launch-like cluster shape -- a fresh timestamp embedded in a
            # large cross-collection sync batch is maintenance noise, not a
            # launch signature (_publication_evidence_strength). A source-
            # declared REACTIVATED/backorder tag is affirmative
            # counter-evidence and always wins. Absence of prior local
            # rows, a new URL, and current stock are explicitly NOT
            # novelty evidence. Recall is preserved, not suppressed: a
            # FIRST_SEEN_BY_CLANK Event is still created, queued (tier 3),
            # scored and reviewable -- it just no longer claims a launch
            # it cannot prove. The official-news path (_record_watch_event)
            # keeps NEW_REFERENCE for is_new_watch because a first-party
            # announcement article IS affirmative launch evidence.
            published_at = self._parse_extra_specs_published_at(watch.extra_specs)
            strength, strength_detail = self._publication_evidence_strength(
                watch=watch, published_at=published_at, reactivation_note=reactivation_note,
                observed_at=new_obs.observed_at,
            )
            publication_fresh = (
                baseline_freshness is not None
                and baseline_freshness.state == "FRESH"
                and strength == "MEDIUM"
            )
            if reactivation_note:
                event_type = "FIRST_SEEN_BY_CLANK"
            elif publication_fresh:
                event_type = "NEW_REFERENCE"
            else:
                event_type = "FIRST_SEEN_BY_CLANK"

            novelty_evidence = {
                "collector_id": new_obs.collector_id,
                "region": new_obs.region,
                "local_first_seen_at": ensure_utc(new_obs.observed_at).isoformat(),
                "source_published_at": published_at.isoformat() if published_at else None,
                "existed_locally_before": False,
                "source_reactivation_signal": bool(reactivation_note),
                "publication_freshness_state": baseline_freshness.state if baseline_freshness else None,
                "baseline_state": "ACTIVE" if baseline_active else "INACTIVE",
                "official_article_corroboration": None,
                "evidence_strength": strength_detail["evidence_strength"],
                "cluster_shape": strength_detail["cluster"],
                "classification_reason": (
                    strength_detail["strength_reason"]
                    if not publication_fresh
                    else f"affirmative source publication evidence ({baseline_freshness.reason}; "
                    f"{strength_detail['strength_reason']})"
                ),
            }

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
                availability_status=new_obs.availability_status,
                **self._availability_event_character(watch),
            )
            reasons = [
                "first-ever product-catalogue observation of this reference; "
                "no prior region or announcement existed"
            ]
            if publication_fresh:
                reasons.append(
                    f"affirmative novelty evidence: {baseline_freshness.reason}"
                    + (" (baseline override)" if baseline_active else "")
                )
            if reactivation_note:
                reasons.append(reactivation_note)
            return self._persist_product_event(
                watch=watch,
                new_obs=new_obs,
                scored=score_event(evidence),
                reasons=reasons,
                prior_observation=None,
                notify=notify,
                experimental=experimental,
                prior_regions=None,
                novelty_evidence=novelty_evidence,
                collector_id=collector_id,
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
                collector_id=collector_id,
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
            collector_id=collector_id,
        )

    def _persist_product_event(
        self,
        *,
        watch: Watch,
        collector_id: str | None = None,
        new_obs: SourceObservation,
        scored,
        reasons: list[str],
        prior_observation: SourceObservation | None,
        notify: bool,
        experimental: bool,
        prior_regions: frozenset[str] | None,
        novelty_evidence: dict | None = None,
    ) -> dict:
        """Persist a product-state Event after the caller proved its facts.

        novelty_evidence (2026-08-21 Phase 7): the structured provenance of
        a novelty classification -- source, region, local first-seen time,
        source publication timestamp, reactivation signal, publication
        freshness, baseline state, and the final classification reason.
        Purely additive JSON in Event.extra; every field is data this
        module already computed, so nothing is duplicated or re-derived."""
        from app.models import Event, EventWatch
        from app.services.discord_notify import DiscordNotifier
        from app.services.editorial import editorial_eligibility, format_alert

        settings = get_settings()
        editorial_eligible, eligibility_reasons = editorial_eligibility(
            scored, availability_min_score=settings.availability_editorial_min_score
        )

        # 2026-08-24 QC-memory repair: consult prior human reviews for this
        # canonical reference. The Event is always created (recall-first --
        # annotation, never deletion); a repeat WEAK event for an already-
        # rejected reference is additionally flagged human_qc_deprioritized,
        # which keeps it out of the DEFAULT queue but fully visible via the
        # explicit opt-in filter and /qc/history.
        from app.services.qc import qc_memory_context
        from app.services.pipeline_constants import WEAK_FIRST_SEEN_QC_THRESHOLD

        qc_context, qc_deprioritize = qc_memory_context(
            self.session,
            watch=watch,
            event_type=scored.event_type,
            editorial_eligible=editorial_eligible,
        )

        # 2026-08-26 QC-volume incident repair: a WEAK first-sighting (score
        # <= WEAK_FIRST_SEEN_QC_THRESHOLD — no affirmative novelty evidence)
        # is catalogue bookkeeping, not an editorial lead. It is auto-flagged
        # human_qc_deprioritized: fully persisted and auditable, hidden from
        # the default queue, visible via the explicit opt-in filter / history.
        # Stronger FS events (named collaboration etc.) still queue normally.
        # Evidence: the 2026-08-25 flood was 581 weak-FS rows; every USEFUL FS
        # in history scored >= 25.
        weak_fs_suppressed = (
            scored.event_type == "FIRST_SEEN_BY_CLANK"
            and not qc_deprioritize
            and scored.score <= WEAK_FIRST_SEEN_QC_THRESHOLD
        )
        deprioritize_reason = (
            f"weak FIRST_SEEN_BY_CLANK (score {scored.score:g} <= "
            f"{WEAK_FIRST_SEEN_QC_THRESHOLD}): catalogue discovery without "
            "affirmative novelty evidence; reviewable via history/opt-in"
            if weak_fs_suppressed
            else (
                f"reference already reviewed {qc_context['prior_review_verdict']} "
                f"({qc_context['prior_review_count']} prior review(s)); this "
                f"{scored.event_type} is an equivalent weak repeat class"
            )
            if qc_deprioritize and qc_context
            else None
        )

        # Delivery-gate decisions hoisted above Event creation so the
        # recorded delivery outcome reflects every gate (STD-UI-COM-011).
        # 2026-08-21: FIRST_SEEN_BY_CLANK is reviewable, not audible. The
        # initial post-baseline crawl of a large catalogue emits hundreds of
        # honest first-sightings per run (live-verified with casio_jp_sitemap:
        # 400 in one run); at the experimental lane's threshold of 0 every
        # one of them would ring Discord. They stay fully visible in the
        # dashboard and QC queue; only the ping is gated behind an explicit
        # operator opt-in.
        first_seen_alertable = (
            scored.event_type != "FIRST_SEEN_BY_CLANK" or settings.discord_first_seen_enabled
        )
        # 2026-08-25 fleet-wide maturity gate (canonized per owner decision):
        # external delivery is a PROMOTION privilege. An experimental-maturity
        # collector must be externally silent for ANY event type/score; its
        # events stay visible in dashboard/QC. This replaces the incidental
        # stacking of discord_first_seen_enabled=False + initial-fill
        # suppression with an explicit, mechanical gate.
        from app.services.delivery_gate import experimental_delivery_blocked

        maturity_allows_delivery = not experimental_delivery_blocked(collector_id)

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
                **_initial_delivery_outcome(
                    notify=notify,
                    editorial_eligible=editorial_eligible,
                    first_seen_alertable=first_seen_alertable,
                    maturity_allows_delivery=maturity_allows_delivery,
                ),
                **(
                    {
                        "human_qc_deprioritized": True,
                        "human_qc_context": qc_context,
                        "human_qc_deprioritization_reason": deprioritize_reason,
                    }
                    if qc_deprioritize or weak_fs_suppressed
                    else {}
                ),
                **({"human_qc_context": qc_context} if qc_context and not qc_deprioritize else {}),
                **({"novelty_evidence": novelty_evidence} if novelty_evidence else {}),
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

        if notify and editorial_eligible and first_seen_alertable and maturity_allows_delivery:
            notifier = DiscordNotifier(settings)
            threshold = (
                settings.discord_experimental_min_score if experimental else settings.discord_official_min_score
            )
            if not notifier.editorial_enabled:
                event.extra = {
                    **event.extra,
                    "delivery": {"state": "gated", "reason": "editorial_disabled"},
                }
            elif scored.score < threshold:
                event.extra = {
                    **event.extra,
                    "delivery": {"state": "gated", "reason": "below_threshold"},
                }
            else:
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
                event.extra = {
                    **event.extra,
                    "alerted": sent,
                    "delivery": {
                        "state": "sent" if sent else "failed",
                        "attempted_at": datetime.now(UTC).isoformat(),
                    },
                }
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
            if not notifier.editorial_enabled:
                event.extra = {
                    **event.extra,
                    "delivery": {"state": "gated", "reason": "editorial_disabled"},
                }
            elif scored.score < threshold:
                event.extra = {
                    **event.extra,
                    "delivery": {"state": "gated", "reason": "below_threshold"},
                }
            else:
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
                event.extra = {
                    **event.extra,
                    "alerted": sent,
                    "delivery": {
                        "state": "sent" if sent else "failed",
                        "attempted_at": datetime.now(UTC).isoformat(),
                    },
                }
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
        # See process_fetch_result: Phase 8 default flip, same rationale.
        emit_events: bool = True,
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

        is_accessory_only = _looks_like_accessory_only(title)
        if is_accessory_only:
            notes = lead.notes or ""
            note_line = "accessory_only_title: no watch/Event created (2026-08-19 hotfix)"
            if note_line not in notes:
                lead.notes = (notes + "\n" + note_line).strip()
            lead.enrichment_status = "ACCESSORY_ONLY"
            outcome["accessory_only"] = True
            self.session.commit()
            outcome["success"] = True
            outcome["lead_id"] = lead.id
            return outcome

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

        auto_baseline (added 2026-08-17, see
        ai/handoff/INCIDENT_TIMEX_CATALOGUE_BACKFILL_BURST.md): unlike
        run_brand_news_pipeline/run_product_observation_pipeline, this
        function has never accepted an explicit force_baseline parameter
        (there is no supported way to pass one from the CLI or dashboard).
        `_auto_baseline_for_first_run("casio_multi")` closes that gap: a
        genuinely first-ever casio_multi run, on a database with no
        epoch, is treated as a silent baseline automatically -- see that
        method's docstring for the exact, narrow conditions.
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
        auto_baseline = self._auto_baseline_for_first_run("casio_multi")
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
            **self._epoch_fields(force_baseline=auto_baseline),
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
                    force_baseline=auto_baseline,
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
                                fr, run_id=run.id, emit_events=emit_events, notify=emit_events,
                                force_baseline=auto_baseline,
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
                "auto_baseline_applied": auto_baseline,
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
        Automatically also applied (2026-08-17, see
        ai/handoff/INCIDENT_TIMEX_CATALOGUE_BACKFILL_BURST.md) on a
        genuinely first-ever run for this brand's collector on a database
        with no epoch, regardless of the caller-supplied value -- see
        `_auto_baseline_for_first_run`.
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
        effective_force_baseline = force_baseline or self._auto_baseline_for_first_run(cfg["collector_id"])

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
            **self._epoch_fields(force_baseline=effective_force_baseline),
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
                    force_baseline=effective_force_baseline,
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
                "auto_baseline_applied": effective_force_baseline and not force_baseline,
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
            # Disarm the batch payload (2026-08-24): it describes THIS run
            # only and must never leak into later, unrelated evaluations.
            self._current_publication_batch = None
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
        Automatically also applied (2026-08-17, see
        ai/handoff/INCIDENT_TIMEX_CATALOGUE_BACKFILL_BURST.md) on a
        genuinely first-ever run for this brand's collector on a database
        with no epoch, regardless of the caller-supplied value -- this is
        the exact mechanism that failed both the local dev database and a
        fresh field-test database, each independently reproducing a
        several-hundred-event Timex NEW_REFERENCE flood. See
        `_auto_baseline_for_first_run`.
        """
        if not self._PRODUCT_REGISTRY:
            from app.collectors.casio_europe_sitemap import (
                COLLECTOR_ID as CASIO_EU_ID,
            )
            from app.collectors.casio_europe_sitemap import (
                COLLECTOR_VERSION as CASIO_EU_VER,
            )
            from app.collectors.casio_europe_sitemap import (
                REGION as CASIO_EU_REGION,
            )
            from app.collectors.casio_europe_sitemap import CasioEuropeSitemapCollector
            from app.collectors.casio_jp_sitemap import (
                COLLECTOR_ID as CASIO_JP_ID,
            )
            from app.collectors.casio_jp_sitemap import (
                COLLECTOR_VERSION as CASIO_JP_VER,
            )
            from app.collectors.casio_jp_sitemap import REGION as CASIO_JP_REGION
            from app.collectors.casio_jp_sitemap import CasioJPSitemapCollector
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
            from app.collectors.timex_uk_products import COLLECTOR_ID as TIMEX_UK_COLLECTOR_ID
            from app.collectors.timex_uk_products import TimexUkProductsCollector
            from app.collectors.tissot_sitemap import COLLECTOR_ID as TISSOT_COLLECTOR_ID

            # Sitemap-family expansion (2026-08-25): reusable family + first brand
            from app.collectors.tissot_sitemap import TissotSitemapCollector
            from app.parsers.casio_europe_sitemap import parse_casio_europe_sitemap_item
            from app.parsers.casio_jp_sitemap import parse_casio_jp_sitemap_item
            from app.parsers.casio_uk_sitemap import parse_casio_uk_sitemap_item
            from app.parsers.citizen_de_products import parse_citizen_de_product_html
            from app.parsers.citizen_products import parse_citizen_search_hit
            from app.parsers.seiko_jp_products import parse_seiko_jp_product_json
            from app.parsers.seiko_products import parse_seiko_product_json
            from app.parsers.sitemap_family import parse_sitemap_family_item
            from app.parsers.timex_products import parse_timex_product_json
            from app.parsers.timex_uk_products import parse_timex_uk_product_json

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
                    "casio_jp": {
                        "collector_cls": CasioJPSitemapCollector,
                        "collector_id": CASIO_JP_ID,
                        "collector_version": CASIO_JP_VER,
                        "parse_fn": parse_casio_jp_sitemap_item,
                        "default_region": CASIO_JP_REGION,
                        "offline_kwarg": "sitemap_payload",
                        "default_max_items": 300,
                        "known_urls_from_observations": True,
                    },
                    "casio_europe": {
                        "collector_cls": CasioEuropeSitemapCollector,
                        "collector_id": CASIO_EU_ID,
                        "collector_version": CASIO_EU_VER,
                        "parse_fn": parse_casio_europe_sitemap_item,
                        "default_region": CASIO_EU_REGION,
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
                        # 2026-08-24 Windows field-test repair: seiko_products'
                        # run() now accepts known_product_urls (new-first
                        # slicing, same pattern as timex/citizen). This was
                        # the only product registry entry whose collector
                        # supported the parameter but was never wired to it,
                        # which froze its 300-item slice on the same
                        # catalogue prefix forever.
                        "known_urls_from_observations": True,
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
                    # --- 2026-08-25 expansion programme: sitemap-family brands ---
                    # Each entry is configuration + the shared sitemap_family
                    # collector; brand N+1 should be another block like this.
                    "tissot": {
                        "collector_cls": TissotSitemapCollector,
                        "collector_id": TISSOT_COLLECTOR_ID,
                        "collector_version": "0.1.0",
                        "parse_fn": partial(parse_sitemap_family_item, manufacturer="Tissot"),
                        "default_region": "US",
                        "offline_kwarg": "sitemap_payload",
                        "default_max_items": 300,
                        "known_urls_from_observations": True,
                        "manufacturer": "Tissot",
                    },
                    # --- Timex UK: regional Shopify catalogue (2026-08-25) ---
                    # Regional presence lane: primary editorial product is
                    # NEW_REGION-class evidence on US-known SKUs plus UK-suffix
                    # regional variants. Identity stays SKU-based; a US+UK SKU
                    # resolves to ONE canonical watch.
                    "timex_uk": {
                        "collector_cls": TimexUkProductsCollector,
                        "collector_id": TIMEX_UK_COLLECTOR_ID,
                        "collector_version": "0.1.0",
                        "parse_fn": parse_timex_uk_product_json,
                        "default_region": "UK",
                        "offline_kwarg": "listing_pages",
                        "default_max_items": 300,
                        "known_urls_from_observations": True,
                        "manufacturer": "Timex",
                    },
                }
            )

        if brand not in self._PRODUCT_REGISTRY:
            raise ValueError(f"unsupported experimental product brand: {brand!r}")
        cfg = self._PRODUCT_REGISTRY[brand]
        # 2026-08-25 initial-catalogue-fill suppression (Law 1): a bounded-budget
        # brand still walking its FIRST catalogue pass drips FIRST_SEEN events
        # slice after slice. Gate that window here; the gate closes as soon as
        # the pass wraps (slice re-serves known URLs) — see
        # app/services/initial_fill.py.
        from app.services.initial_fill import initial_fill_active

        fill_gate = emit_events and initial_fill_active(self.session, cfg["collector_id"])
        effective_force_baseline = force_baseline or self._auto_baseline_for_first_run(cfg["collector_id"])

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
            **self._epoch_fields(force_baseline=effective_force_baseline),
        )
        self.session.add(run)
        self.session.commit()
        lock.update_run_id(run.id)

        try:
            collector = cfg["collector_cls"]()
            effective_max_items = (
                max_items
                if max_items is not None
                else (None if effective_force_baseline else cfg.get("default_max_items", 300))
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
            known_urls_this_run = None
            if cfg.get("known_urls_from_observations"):
                known_urls_this_run = {
                    source_url
                    for (source_url,) in self.session.query(SourceObservation.source_url)
                    .filter(SourceObservation.collector_id == cfg["collector_id"])
                    .distinct()
                    .all()
                }
                run_kwargs["known_product_urls"] = set(known_urls_this_run)
            result = collector.run(**run_kwargs)
            status = result.metadata.get("component_status") or "FAILED"
            self._update_component_state(cfg["collector_id"], status, len(result.discovered))

            # 2026-08-25 initial-fill gate decision: with the fill window armed
            # (under the run ceiling) AND this slice purely unseen (the pass has
            # not wrapped -- every processed URL was first-time), suppress
            # first-sighting events exactly as force_baseline does. Any known
            # URL in the slice proves the pass wrapped and closes the window.
            slice_pure_unseen = (
                known_urls_this_run is not None
                and len(result.discovered) > 0
                and all(i.url not in known_urls_this_run for i in result.discovered)
            )
            item_suppression_baseline = fill_gate and slice_pure_unseen

            # 2026-08-24 batch-complete publication-cluster evaluation: stage
            # the run's parsed records BEFORE any item is persisted, so
            # novelty evidence for the FIRST processed item already sees the
            # same complete source batch as the LAST. Deterministic:
            # independent of input order and ingest timing.
            if emit_events and getattr(result, "fetched", None):
                staged_records: list[dict] = []
                for fr_staged in result.fetched:
                    if not fr_staged.success or not fr_staged.payload:
                        continue
                    try:
                        parse_staged = cfg["parse_fn"](fr_staged.payload)
                    except Exception:
                        continue
                    for pw_staged in getattr(parse_staged, "watches", []) or []:
                        staged_records.append(
                            {
                                "reference_canonical": pw_staged.reference_raw,
                                "collection": pw_staged.collection,
                                "published_at": (pw_staged.extra_specs or {}).get("published_at"),
                            }
                        )
                self._current_publication_batch = {cfg["collector_id"]: staged_records}

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
                    force_baseline=effective_force_baseline or item_suppression_baseline,
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

            backfill_context = self._annotate_new_reference_burst(
                # 2026-08-21 Phase 6: a catalogue-backfill flood is now
                # mostly FIRST_SEEN_BY_CLANK events (the honest default),
                # so burst detection counts both first-sighting novelty
                # types -- the flood signature, not a specific label.
                new_reference_event_ids=[
                    pe["event_id"]
                    for pe in events
                    if pe.get("event_type") in ("NEW_REFERENCE", "FIRST_SEEN_BY_CLANK") and pe.get("event_id")
                ],
                discovered_count=len(result.discovered),
            )

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
                "auto_baseline_applied": effective_force_baseline and not force_baseline,
                # 2026-08-26 initial-fill qualification provenance: records the
                # invocation's bounded budget so downstream logic (and humans)
                # can distinguish a real unbounded catalogue pass from a
                # smoke/validation run with a small max_items. A run is a
                # catalogue pass iff it ran UNBOUNDED (max_items=None) or at
                # its full default budget — never if invoked with an explicit
                # small cap.
                "max_items": effective_max_items,
                **({"backfill_context": backfill_context} if backfill_context else {}),
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

