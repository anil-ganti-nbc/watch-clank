"""Layer B (early-warning) lead persistence and correlation.

Deliberately a separate service from PipelineService (Layer A) — see
app/models/specialist_lead.py's module docstring for why. This service
never writes to watches/source_observations/release_leads; it only reads
Watch rows for conservative, exact-reference-string correlation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import ensure_utc
from app.models import CollectorRun, SourceObservation, SpecialistLead, Watch
from app.services.run_lock import RunLockService
from app.services.source_registry import get_source_profile

logger = get_logger(__name__)


class SpecialistLeadService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ingest_candidate(
        self,
        *,
        source_id: str,
        lead_type: str,
        title: str,
        source_url: str,
        published_at: str | None,
        reference_candidates: list[str],
        claim_text: str | None,
        manufacturer: str | None = None,
        brand: str | None = None,
        region: str | None = None,
        confidence: float = 30.0,
        ingestion_method: str = "collector",
        notes: str | None = None,
    ) -> dict:
        """Create (or silently skip, if already seen) one SpecialistLead.
        Dedup key is source_url — reprocessing the same feed/post is a
        no-op, matching the silent-baseline rule for repeat discovery."""
        existing = self.session.query(SpecialistLead).filter_by(source_url=source_url).one_or_none()
        if existing:
            return {"created": False, "lead_id": existing.id, "reason": "already_seen"}

        profile = get_source_profile(source_id)
        pub_dt = None
        if published_at:
            try:
                pub_dt = datetime.fromisoformat(published_at)
            except ValueError:
                pub_dt = None

        lead = SpecialistLead(
            source_id=source_id,
            source_type=profile.source_type,
            source_authority_tier=profile.tier,
            account_or_domain=profile.account_or_domain,
            lead_type=lead_type,
            manufacturer=manufacturer,
            brand=brand,
            region=region,
            reference_candidates=reference_candidates or [],
            title=title[:512],
            claim_text=claim_text,
            source_url=source_url,
            published_at=pub_dt,
            confidence=max(0.0, min(100.0, confidence)),
            verification_status="UNCONFIRMED",
            ingestion_method=ingestion_method,
            notes=notes,
        )
        self.session.add(lead)
        self.session.flush()
        logger.info(
            "specialist_lead_created",
            lead_id=lead.id,
            source_id=source_id,
            lead_type=lead_type,
            references=reference_candidates,
        )
        return {"created": True, "lead_id": lead.id}

    def correlate_pending_leads(self, *, manufacturer: str | None = None) -> list[dict]:
        """Conservative, deterministic correlation: for every UNCONFIRMED
        lead with at least one reference candidate, look for an existing
        Watch whose reference_raw or reference_canonical exactly matches
        (case-insensitive) — same manufacturer if the lead states one. No
        fuzzy matching, no partial-string matching. Sets correlated_watch_id,
        official_first_observed_at (earliest SourceObservation for that
        watch, falling back to the watch's own created_at), and
        lead_time_days = official_first_observed_at - lead.published_at.
        """
        results: list[dict] = []
        query = self.session.query(SpecialistLead).filter(SpecialistLead.verification_status == "UNCONFIRMED")
        if manufacturer:
            query = query.filter(SpecialistLead.manufacturer == manufacturer)

        for lead in query.all():
            if not lead.reference_candidates:
                continue
            candidates = {r.upper() for r in lead.reference_candidates}
            watch_query = self.session.query(Watch)
            if lead.manufacturer:
                watch_query = watch_query.filter(Watch.manufacturer == lead.manufacturer)
            matched_watch = None
            for watch in watch_query.all():
                if watch.reference_raw.upper() in candidates or watch.reference_canonical.upper() in candidates:
                    matched_watch = watch
                    break
            if matched_watch is None:
                continue

            first_obs = (
                self.session.query(SourceObservation)
                .filter(SourceObservation.watch_id == matched_watch.id)
                .order_by(SourceObservation.observed_at.asc())
                .first()
            )
            official_at = ensure_utc(first_obs.observed_at) if first_obs else ensure_utc(matched_watch.created_at)

            lead.correlated_watch_id = matched_watch.id
            lead.correlated_at = datetime.now(UTC)
            lead.official_first_observed_at = official_at
            lead.verification_status = "CORRELATED_WITH_OFFICIAL"

            lead_time_days = None
            lead_published = ensure_utc(lead.published_at)
            if lead_published and official_at:
                lead_time_days = round((official_at - lead_published).total_seconds() / 86400.0, 2)
            lead.lead_time_days = lead_time_days

            logger.info(
                "specialist_lead_correlated",
                lead_id=lead.id,
                watch_id=matched_watch.id,
                lead_time_days=lead_time_days,
            )
            results.append({"lead_id": lead.id, "watch_id": matched_watch.id, "lead_time_days": lead_time_days})

        return results


def run_casioblog_pipeline(session: Session, *, feed_xml: bytes | None = None, max_items: int = 20) -> CollectorRun:
    """Experimental Layer B collector run: fetch CASIOBLOG's RSS feed,
    ingest new items as SpecialistLeads, attempt conservative correlation
    against existing Casio Watches. Isolated lock (own collector_id + lock
    file), exactly like the Sprint 3/4 experimental brand/product lanes —
    cannot interact with Casio's production lock or any other lane's."""
    from app.collectors.casioblog import COLLECTOR_ID, COLLECTOR_VERSION, CasioblogCollector
    from app.parsers.casioblog import parse_casioblog_feed

    settings = get_settings()
    lock_path = settings.resolved_lock_path.parent / f"{COLLECTOR_ID}.run.lock"
    lock = RunLockService(session, settings, collector_id=COLLECTOR_ID, lock_path=lock_path)
    lock_result = lock.acquire()
    if not lock_result.acquired:
        started = datetime.now(UTC)
        skip_run = CollectorRun(
            collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, started_at=started, completed_at=started,
            status="SKIPPED_OVERLAP",
            summary_metadata={"reason": lock_result.reason, "active_run_id": lock_result.active_run_id},
        )
        session.add(skip_run)
        session.commit()
        return skip_run

    started = datetime.now(UTC)
    run = CollectorRun(collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, started_at=started, status="RUNNING")
    session.add(run)
    session.commit()
    lock.update_run_id(run.id)

    try:
        result = CasioblogCollector().run(feed_xml=feed_xml)
        status = result.metadata.get("component_status") or "FAILED"

        new_leads = 0
        if status == "SUCCESS":
            fr = result.fetched[0]
            parsed = parse_casioblog_feed(fr.payload, max_items=max_items)
            if not parsed.success:
                status = "FAILED"
            else:
                svc = SpecialistLeadService(session)
                for item in parsed.items:
                    outcome = svc.ingest_candidate(
                        source_id="casioblog",
                        lead_type="POSSIBLE_NEW_REFERENCE" if item.reference_candidates else "LEAKED_IMAGE",
                        title=item.title,
                        source_url=item.url,
                        published_at=item.published_at,
                        reference_candidates=item.reference_candidates,
                        claim_text=item.claim_text,
                        manufacturer="Casio",
                        confidence=(
                            40.0
                            + (15.0 if item.reference_candidates else 0.0)
                            + (0.0 if item.is_rumor_tagged else 10.0)
                        ),
                        notes="rumor-tagged" if item.is_rumor_tagged else None,
                    )
                    if outcome["created"]:
                        new_leads += 1
                svc.correlate_pending_leads(manufacturer="Casio")
                status = "SUCCESS" if (parsed.items and new_leads) or not parsed.items else "SUCCESS"
                if not parsed.items:
                    status = "ZERO_ITEMS"

        completed = datetime.now(UTC)
        run.completed_at = completed
        run.status = status
        run.discovered_count = new_leads
        run.parsed_count = new_leads
        run.duration_ms = int((completed - started).total_seconds() * 1000)
        run.summary_metadata = {"new_leads": new_leads, "component_status": status}
        session.commit()
        logger.info("casioblog_pipeline_completed", run_id=run.id, status=run.status, new_leads=new_leads)
        return run
    except Exception as exc:
        session.rollback()
        run.status = "FAILED"
        run.completed_at = datetime.now(UTC)
        run.summary_metadata = {"fatal_error": str(exc)}
        session.add(run)
        session.commit()
        raise
    finally:
        lock.release()
