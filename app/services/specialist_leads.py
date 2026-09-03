"""Layer B (early-warning) lead persistence and correlation.

Deliberately a separate service from PipelineService (Layer A) — see
app/models/specialist_lead.py's module docstring for why. This service
never writes to watches/source_observations/release_leads; it only reads
Watch rows for conservative, exact-reference-string correlation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.time import ensure_utc
from app.models import CollectorRun, SourceObservation, SpecialistLead, Watch
from app.services.delivery_receipts import (
    ENTITY_SPECIALIST_LEAD,
    PURPOSE_LEAD_CORRELATION,
    PURPOSE_LEAD_EARLY_WARNING,
    DeliveryReceiptService,
)
from app.services.discord_notify import DeliveryAttempt, DiscordNotifier
from app.services.editorial import (
    format_correlation_followup_alert,
    format_early_warning_alert,
    looks_like_accessory_only,
)
from app.services.epoch import get_active_epoch, is_baseline_active
from app.services.freshness import classify_lead_freshness
from app.services.run_lock import RunLockService
from app.services.source_registry import get_source_profile

logger = get_logger(__name__)


def _deliver_lead_alert(
    session: Session, lead: SpecialistLead, text: str, *, notifier: DiscordNotifier, purpose: str
) -> bool:
    """Send one specialist-lead alert and persist durable delivery evidence.

    Mirrors PipelineService._deliver_editorial_alert. Leads legitimately
    deliver twice (early warning, then correlation follow-up), so the two
    purposes carry separate receipts and must never dedup against each
    other -- see delivery_receipts.PURPOSE_LEAD_*.
    """
    receipts = DeliveryReceiptService(session)
    if receipts.already_delivered(ENTITY_SPECIALIST_LEAD, lead.id, purpose):
        logger.info("lead_alert_already_delivered", lead_id=lead.id, purpose=purpose)
        return True

    sent = bool(notifier.send_editorial_alert(text))
    attempt = getattr(notifier, "last_editorial_attempt", None)
    if not isinstance(attempt, DeliveryAttempt):
        attempt = DeliveryAttempt(accepted=sent, attempt_count=1)
    receipts.record(
        entity_type=ENTITY_SPECIALIST_LEAD, entity_id=lead.id, purpose=purpose, attempt=attempt
    )
    return sent

# 2026-08-19 QC + classifier hardening pass (real production data: 43+
# casioblog leads and dozens of gear_patrol/fratello/great_gshock_world/
# watchtime leads -- roundups, dress-watch listicles, mod tutorials,
# retailer deals, "favorite watches this week" posts -- were all silently
# defaulting to LEAKED_IMAGE purely because they had no leak claim). The
# old rule per pipeline was a bare `"POSSIBLE_NEW_REFERENCE" if
# item.reference_candidates else "LEAKED_IMAGE"` fallback: the mere
# ABSENCE of an extractable reference number was treated as evidence of a
# leak, when it is not evidence of anything. LEAKED_IMAGE now requires
# actual leak language (leaked/unreleased/spy shot/before announcement/
# accidentally published/unannounced); everything else that isn't a
# reference, collaboration, limited edition, or availability signal falls
# to EDITORIAL_MENTION -- an honest "this is real coverage of a tracked
# brand, but makes no specific new/leak/collab/price claim" bucket -- per
# the brief's explicit instruction to prefer gating + the closest valid
# type over exploding the enum with one type per editorial sub-genre
# (deal/mod/historical/etc. all become either EARLY_RETAIL_LISTING, if
# the article itself uses sale/discount language, or EDITORIAL_MENTION).
_LEAK_EVIDENCE_RE = re.compile(
    r"\b(?:leak(?:ed|s|ing)?|un-?released|spy shot|spied|"
    r"before (?:its |the )?(?:official )?announcement|"
    r"ahead of (?:its |the )?(?:official )?announcement|"
    r"accidentally (?:published|posted|revealed|leaked)|"
    r"unannounced|not yet announced|"
    r"(?:listing|database)(?: page| entry)? (?:reveals|exposes))\b",
    re.IGNORECASE | re.ASCII,
)
_DEAL_RE = re.compile(
    r"\b(?:on sale|for sale|% ?off|percent off|discount(?:ed)?|\bdeal\b|clearance|"
    r"below retail|marked down|price drop|now available|available now|back in stock)\b",
    re.IGNORECASE | re.ASCII,
)


def classify_lead_type(
    *,
    title: str,
    claim_text: str | None = None,
    reference_candidates: list[str] | None = None,
    is_limited_edition: bool = False,
    is_collaboration: bool = False,
    is_restock_or_availability: bool = False,
) -> str:
    """Single shared lead_type decision, used by every specialist source
    pipeline below (previously four near-identical inline ternary chains,
    one per pipeline, that had silently drifted into treating "no
    reference number found" as leak evidence). Order matters: a more
    specific structured signal (limited edition / collaboration /
    availability) always wins over a plain reference match, and genuine
    leak language always wins over "a reference number happens to be
    present" -- a leaked photo of a specific unreleased reference is
    still a leak, not merely a new-reference sighting."""
    if is_limited_edition:
        return "POSSIBLE_LIMITED_EDITION"
    if is_collaboration:
        return "POSSIBLE_COLLABORATION"
    if is_restock_or_availability:
        return "EARLY_RETAIL_LISTING"
    blob = f"{title} {claim_text or ''}"
    if _LEAK_EVIDENCE_RE.search(blob):
        return "LEAKED_IMAGE"
    # 2026-08-21 (Phase 2 specimen corpus): accessory-only posts must be
    # classified BEFORE reference presence is allowed to decide. The
    # Atelier NBR strap sale carried a real-looking SKU, and the previous
    # order let that SKU outrank the sale language -- producing a
    # POSSIBLE_NEW_REFERENCE lead for a strap. Same phrase contract as the
    # official-news gate (editorial.looks_like_accessory_only).
    if looks_like_accessory_only(title):
        return "EARLY_RETAIL_LISTING"
    if reference_candidates:
        return "POSSIBLE_NEW_REFERENCE"
    if _DEAL_RE.search(blob):
        return "EARLY_RETAIL_LISTING"
    return "EDITORIAL_MENTION"


def _reference_family(reference: str) -> str:
    """Deterministic family root of a hyphenated reference: strip the final
    hyphen-separated segment. "GWR-B3000-1A" -> "GWR-B3000". References with
    no hyphen have no distinct family root and return unchanged (so they can
    only ever produce an exact match, never a spurious family match)."""
    upper = reference.upper()
    if "-" not in upper:
        return upper
    return upper.rsplit("-", 1)[0]


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
        force_baseline: bool = False,
        collector_run_id: int | None = None,
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

        active_epoch = get_active_epoch(self.session)
        # A new source joining an already-live epoch is quietly backfilled
        # without reopening the global baseline.  The flag is deliberately
        # scoped to this ingestion call, matching official-lane semantics.
        is_baseline = bool(force_baseline or (active_epoch and is_baseline_active(self.session)))
        now = datetime.now(UTC)
        discovered_at = now
        freshness = classify_lead_freshness(
            source_type=profile.source_type,
            ingestion_method=ingestion_method,
            is_baseline=is_baseline,
            published_at=pub_dt,
            discovered_at=discovered_at,
            now=now,
            window_hours=get_settings().specialist_freshness_window_hours,
        )

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
            epoch_id=active_epoch.id if active_epoch else None,
            is_baseline=is_baseline,
            editorial_freshness=freshness.state,
            freshness_reason=freshness.reason,
            freshness_evaluated_at=now,
            collector_run_id=collector_run_id,
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
        Watch whose reference matches. Two match kinds, both deterministic
        (never a similarity score):

        - EXACT_REFERENCE_MATCH: reference_raw or reference_canonical
          equals a candidate exactly (case-insensitive).
        - FAMILY_MATCH: a candidate equals the watch's "family root" —
          the reference with its final hyphen-separated colorway segment
          stripped, e.g. official "GWR-B3000-1A" has family root
          "GWR-B3000". Only tried if no exact match was found. This must
          never be presented as an exact-reference confirmation — see
          correlation_type on the stored lead and
          format_early_warning_alert's CONFIRMED/FAMILY_MATCH follow-up.

        Sets correlated_watch_id, correlation_type, official_first_observed_at
        (earliest SourceObservation for that watch, falling back to the
        watch's own created_at), and
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
            watches = watch_query.all()

            matched_watch = None
            correlation_type = None
            for watch in watches:
                if watch.reference_raw.upper() in candidates or watch.reference_canonical.upper() in candidates:
                    matched_watch = watch
                    correlation_type = "EXACT_REFERENCE_MATCH"
                    break
            if matched_watch is None:
                for watch in watches:
                    if (
                        _reference_family(watch.reference_raw) in candidates
                        or _reference_family(watch.reference_canonical) in candidates
                    ):
                        matched_watch = watch
                        correlation_type = "FAMILY_MATCH"
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
            lead.correlation_type = correlation_type

            lead_time_days = None
            lead_published = ensure_utc(lead.published_at)
            if lead_published and official_at:
                lead_time_days = round((official_at - lead_published).total_seconds() / 86400.0, 2)
            lead.lead_time_days = lead_time_days

            logger.info(
                "specialist_lead_correlated",
                lead_id=lead.id,
                watch_id=matched_watch.id,
                correlation_type=correlation_type,
                lead_time_days=lead_time_days,
            )
            results.append(
                {
                    "lead_id": lead.id,
                    "watch_id": matched_watch.id,
                    "correlation_type": correlation_type,
                    "lead_time_days": lead_time_days,
                }
            )

        return results

    def _mark_delivery(self, lead: SpecialistLead, state: str) -> None:
        """Record the coarse delivery outcome (STD-UI-COM-011 remediation).
        First determination wins: a lead already marked 'sent' (notified_at
        set) must never be downgraded to 'gated' by a later dedupe pass,
        and a recorded outcome is never silently re-classified."""
        if lead.delivery_state is None:
            lead.delivery_state = state

    def notify_new_lead(self, lead: SpecialistLead, *, notifier: DiscordNotifier | None = None) -> bool:
        """Send the EARLY WARNING — UNCONFIRMED alert for one freshly
        created lead, if editorial notifications are enabled, the lead
        clears the confidence floor, and it hasn't already been sent
        (notified_at dedup — a repeat pipeline run never re-notifies for
        the same lead, since ingest_candidate itself dedups by source_url
        and this checks notified_at on top of that as a second guard).
        Discord failures are swallowed by DiscordNotifier; this never
        raises and never blocks lead persistence.

        Every policy skip records lead.delivery_state='gated' (first
        determination wins — a 'sent' outcome is never downgraded) and the
        dispatch attempt records 'sent'/'failed', so the UI can distinguish
        policy suppression from failure from never-attempted
        (STD-UI-COM-011)."""
        settings = get_settings()
        if not settings.editorial_notifications_enabled:
            self._mark_delivery(lead, "gated")
            return False
        if lead.is_baseline:
            self._mark_delivery(lead, "gated")
            return False
        # STALE_PUBLICATION/UNKNOWN_TIMESTAMP/MANUAL_UNDATED must never
        # alert as current news -- see ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md.
        if lead.editorial_freshness != "FRESH":
            self._mark_delivery(lead, "gated")
            return False
        if lead.notified_at is not None:
            return False
        if lead.confidence < settings.discord_specialist_min_confidence:
            self._mark_delivery(lead, "gated")
            return False
        # Preserve every independent article for provenance, but do not send
        # four identical early-warning alerts when several publications name
        # the same exact reference. This is intentionally a compact,
        # deterministic exact-string check, not story clustering.
        references = {ref.upper() for ref in lead.reference_candidates or []}
        if references:
            already_alerted = self.session.query(SpecialistLead).filter(
                SpecialistLead.id != lead.id,
                SpecialistLead.notified_at.is_not(None),
                SpecialistLead.editorial_freshness == "FRESH",
            ).all()
            if any(references.intersection({ref.upper() for ref in other.reference_candidates or []}) for other in already_alerted):
                self._mark_delivery(lead, "gated")
                return False

        notifier = notifier or DiscordNotifier(settings)
        if not notifier.editorial_enabled:
            self._mark_delivery(lead, "gated")
            return False

        profile = get_source_profile(lead.source_id)
        text = format_early_warning_alert(
            manufacturer=lead.manufacturer,
            brand=lead.brand,
            reference_candidates=lead.reference_candidates or [],
            lead_type=lead.lead_type,
            source_display_name=profile.display_name,
            source_type=lead.source_type,
            source_authority_tier=lead.source_authority_tier,
            title=lead.title,
            claim_text=lead.claim_text,
            source_url=lead.source_url,
            published_at=lead.published_at.isoformat() if lead.published_at else None,
            discovered_at=lead.discovered_at.isoformat() if lead.discovered_at else "",
            confidence=lead.confidence,
        )
        sent = _deliver_lead_alert(
            self.session, lead, text, notifier=notifier, purpose=PURPOSE_LEAD_EARLY_WARNING
        )
        if sent:
            lead.notified_at = datetime.now(UTC)
            lead.delivery_state = "sent"
        else:
            self._mark_delivery(lead, "failed")
        return sent

    def notify_correlation(self, lead: SpecialistLead, *, notifier: DiscordNotifier | None = None) -> bool:
        """Send the follow-up CONFIRMED/FAMILY_MATCH alert immediately
        after correlate_pending_leads() correlates a lead. Naturally
        deduped: correlate_pending_leads only ever queries UNCONFIRMED
        leads, so a lead can be correlated (and therefore notified here)
        at most once.

        2026-08-19 hotfix (CasioBlog EQB-1300D-5A/-2A incident,
        live-confirmed on Hetzner: lead id 10, a 2026-03-28 article,
        editorial_freshness=STALE_PUBLICATION, correlated 2026-08-17 with a
        baseline-suppressed official Watch observation): unlike
        notify_new_lead just above -- which has always refused to alert
        anything that isn't editorial_freshness == "FRESH" -- this method
        had no freshness check at all. Correlation with an official Watch
        proves the *reference is real*, not that the original article is
        current; a lead published 142 days before Watch Clank happened to
        observe the matching product must not become a "CONFIRMED"/
        "FAMILY_MATCH" news alert. This is a distinct, second gap from the
        one fixed in app.services.pipeline._stale_official_announcement
        (which only ever covered leads created directly as official news,
        not this correlation-followup path)."""
        settings = get_settings()
        if not settings.editorial_notifications_enabled:
            self._mark_delivery(lead, "gated")
            return False
        if lead.is_baseline:
            self._mark_delivery(lead, "gated")
            return False
        if lead.editorial_freshness != "FRESH":
            self._mark_delivery(lead, "gated")
            return False
        notifier = notifier or DiscordNotifier(settings)
        if not notifier.editorial_enabled or lead.correlated_watch_id is None:
            self._mark_delivery(lead, "gated")
            return False

        watch = self.session.get(Watch, lead.correlated_watch_id)
        if watch is None:
            self._mark_delivery(lead, "gated")
            return False
        profile = get_source_profile(lead.source_id)
        text = format_correlation_followup_alert(
            manufacturer=lead.manufacturer,
            brand=lead.brand,
            lead_reference_candidates=lead.reference_candidates or [],
            watch_reference_raw=watch.reference_raw,
            correlation_type=lead.correlation_type or "FAMILY_MATCH",
            source_display_name=profile.display_name,
            lead_published_at=lead.published_at.isoformat() if lead.published_at else None,
            official_first_observed_at=(
                lead.official_first_observed_at.isoformat() if lead.official_first_observed_at else None
            ),
            lead_time_days=lead.lead_time_days,
            source_url=lead.source_url,
        )
        sent = _deliver_lead_alert(
            self.session, lead, text, notifier=notifier, purpose=PURPOSE_LEAD_CORRELATION
        )
        if sent:
            # Correlation follow-up: record the state only. notified_at is
            # notify_new_lead's dedup guard for the EARLY WARNING alert and
            # must not be touched by this second delivery path
            # (STD-UI-COM-011: a correlation-alerted lead must not read as
            # "not delivered").
            lead.delivery_state = "sent"
        else:
            self._mark_delivery(lead, "failed")
        return sent


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
    active_epoch = get_active_epoch(session)
    run = CollectorRun(
        collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, started_at=started, status="RUNNING",
        epoch_id=active_epoch.id if active_epoch else None,
        is_baseline=bool(active_epoch and is_baseline_active(session)),
    )
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
                notifier = DiscordNotifier(settings)
                for item in parsed.items:
                    outcome = svc.ingest_candidate(
                        source_id="casioblog",
                        lead_type=classify_lead_type(
                            title=item.title,
                            claim_text=item.claim_text,
                            reference_candidates=item.reference_candidates,
                        ),
                        title=item.title,
                        source_url=item.url,
                        published_at=item.published_at,
                        reference_candidates=item.reference_candidates,
                        claim_text=item.claim_text,
                        manufacturer="Casio",
                        collector_run_id=run.id,
                        confidence=(
                            40.0
                            + (15.0 if item.reference_candidates else 0.0)
                            + (0.0 if item.is_rumor_tagged else 10.0)
                        ),
                        notes="rumor-tagged" if item.is_rumor_tagged else None,
                    )
                    if outcome["created"]:
                        new_leads += 1
                        new_lead = session.get(SpecialistLead, outcome["lead_id"])
                        svc.notify_new_lead(new_lead, notifier=notifier)
                correlated = svc.correlate_pending_leads(manufacturer="Casio")
                for c in correlated:
                    correlated_lead = session.get(SpecialistLead, c["lead_id"])
                    svc.notify_correlation(correlated_lead, notifier=notifier)
                session.commit()
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


def run_gcentral_pipeline(session: Session, *, feed_xml: bytes | None = None, max_items: int = 20) -> CollectorRun:
    """Experimental Layer B collector run for G-Central (independent
    G-Shock fan site, real confirmed RSS feed) -- same isolated-lock
    pattern as run_casioblog_pipeline, own collector_id/lock file, cannot
    interact with Casio production or CASIOBLOG's lane."""
    from app.collectors.gcentral import COLLECTOR_ID, COLLECTOR_VERSION, GCentralCollector
    from app.parsers.gcentral import parse_gcentral_feed

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
    active_epoch = get_active_epoch(session)
    run = CollectorRun(
        collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, started_at=started, status="RUNNING",
        epoch_id=active_epoch.id if active_epoch else None,
        is_baseline=bool(active_epoch and is_baseline_active(session)),
    )
    session.add(run)
    session.commit()
    lock.update_run_id(run.id)

    try:
        result = GCentralCollector().run(feed_xml=feed_xml)
        status = result.metadata.get("component_status") or "FAILED"

        new_leads = 0
        if status == "SUCCESS":
            fr = result.fetched[0]
            parsed = parse_gcentral_feed(fr.payload, max_items=max_items)
            if not parsed.success:
                status = "FAILED"
            else:
                svc = SpecialistLeadService(session)
                notifier = DiscordNotifier(settings)
                for item in parsed.items:
                    lead_type = classify_lead_type(
                        title=item.title,
                        claim_text=item.claim_text,
                        reference_candidates=item.reference_candidates,
                        is_collaboration=item.is_collaboration,
                        is_restock_or_availability=item.is_restock_or_availability,
                    )
                    outcome = svc.ingest_candidate(
                        source_id="g_central",
                        lead_type=lead_type,
                        title=item.title,
                        source_url=item.url,
                        published_at=item.published_at,
                        reference_candidates=item.reference_candidates,
                        claim_text=item.claim_text,
                        manufacturer="Casio",
                        collector_run_id=run.id,
                        confidence=(
                            35.0
                            + (15.0 if item.reference_candidates else 0.0)
                            + (10.0 if item.is_restock_or_availability else 0.0)
                        ),
                    )
                    if outcome["created"]:
                        new_leads += 1
                        new_lead = session.get(SpecialistLead, outcome["lead_id"])
                        svc.notify_new_lead(new_lead, notifier=notifier)
                correlated = svc.correlate_pending_leads(manufacturer="Casio")
                for c in correlated:
                    correlated_lead = session.get(SpecialistLead, c["lead_id"])
                    svc.notify_correlation(correlated_lead, notifier=notifier)
                session.commit()
                status = "ZERO_ITEMS" if not parsed.items else "SUCCESS"

        completed = datetime.now(UTC)
        run.completed_at = completed
        run.status = status
        run.discovered_count = new_leads
        run.parsed_count = new_leads
        run.duration_ms = int((completed - started).total_seconds() * 1000)
        run.summary_metadata = {"new_leads": new_leads, "component_status": status}
        session.commit()
        logger.info("gcentral_pipeline_completed", run_id=run.id, status=run.status, new_leads=new_leads)
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


def run_plus9time_pipeline(session: Session, *, feed_xml: bytes | None = None, max_items: int = 20) -> CollectorRun:
    """Experimental Layer B collector run for Plus9Time (Seiko/Citizen
    industry publication, real confirmed RSS feed). Same isolated-lock
    pattern as the other specialist lanes. Honest expectation, documented
    in app/parsers/plus9time.py: most items will have zero extractable
    references (historical/archival content) -- that is not a bug."""
    from app.collectors.plus9time import COLLECTOR_ID, COLLECTOR_VERSION, Plus9TimeCollector
    from app.parsers.plus9time import parse_plus9time_feed

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
    active_epoch = get_active_epoch(session)
    run = CollectorRun(
        collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION, started_at=started, status="RUNNING",
        epoch_id=active_epoch.id if active_epoch else None,
        is_baseline=bool(active_epoch and is_baseline_active(session)),
    )
    session.add(run)
    session.commit()
    lock.update_run_id(run.id)

    try:
        result = Plus9TimeCollector().run(feed_xml=feed_xml)
        status = result.metadata.get("component_status") or "FAILED"

        new_leads = 0
        if status == "SUCCESS":
            fr = result.fetched[0]
            parsed = parse_plus9time_feed(fr.payload, max_items=max_items)
            if not parsed.success:
                status = "FAILED"
            else:
                svc = SpecialistLeadService(session)
                notifier = DiscordNotifier(settings)
                for item in parsed.items:
                    outcome = svc.ingest_candidate(
                        source_id="plus9time",
                        lead_type=classify_lead_type(
                            title=item.title,
                            claim_text=item.claim_text,
                            reference_candidates=item.reference_candidates,
                        ),
                        title=item.title,
                        source_url=item.url,
                        published_at=item.published_at,
                        reference_candidates=item.reference_candidates,
                        claim_text=item.claim_text,
                        manufacturer=item.brand_guess,
                        collector_run_id=run.id,
                        confidence=30.0 + (20.0 if item.reference_candidates else 0.0),
                    )
                    if outcome["created"]:
                        new_leads += 1
                        new_lead = session.get(SpecialistLead, outcome["lead_id"])
                        svc.notify_new_lead(new_lead, notifier=notifier)
                # Correlate separately per brand -- never cross-brand, same
                # discipline as the rest of correlate_pending_leads' callers.
                for mfr in ("Seiko", "Citizen"):
                    correlated = svc.correlate_pending_leads(manufacturer=mfr)
                    for c in correlated:
                        correlated_lead = session.get(SpecialistLead, c["lead_id"])
                        svc.notify_correlation(correlated_lead, notifier=notifier)
                session.commit()
                status = "ZERO_ITEMS" if not parsed.items else "SUCCESS"

        completed = datetime.now(UTC)
        run.completed_at = completed
        run.status = status
        run.discovered_count = new_leads
        run.parsed_count = new_leads
        run.duration_ms = int((completed - started).total_seconds() * 1000)
        run.summary_metadata = {"new_leads": new_leads, "component_status": status}
        session.commit()
        logger.info("plus9time_pipeline_completed", run_id=run.id, status=run.status, new_leads=new_leads)
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


def run_publication_pipeline(
    session: Session,
    *,
    source_id: str,
    feed_xml: bytes | None = None,
    max_items: int = 20,
    force_baseline: bool = False,
) -> CollectorRun:
    """Run one approved general-publication RSS lane.

    This intentionally sits beside, rather than rewrites, the older
    source-specific pipelines above. The four new sources share a bounded
    RSS and exact-reference contract, while retaining a unique collector id,
    lock, run row, and source-scoped baseline flag.
    """
    from app.collectors.specialist_publications import (
        PUBLICATION_SOURCES,
        SpecialistPublicationCollector,
    )
    from app.parsers.specialist_publications import parse_specialist_publication_feed

    source = PUBLICATION_SOURCES[source_id]
    settings = get_settings()
    lock_path = settings.resolved_lock_path.parent / f"{source.collector_id}.run.lock"
    lock = RunLockService(session, settings, collector_id=source.collector_id, lock_path=lock_path)
    lock_result = lock.acquire()
    if not lock_result.acquired:
        started = datetime.now(UTC)
        skip_run = CollectorRun(
            collector_id=source.collector_id,
            collector_version=SpecialistPublicationCollector.collector_version,
            started_at=started,
            completed_at=started,
            status="SKIPPED_OVERLAP",
            summary_metadata={"reason": lock_result.reason, "active_run_id": lock_result.active_run_id},
        )
        session.add(skip_run)
        session.commit()
        return skip_run

    started = datetime.now(UTC)
    active_epoch = get_active_epoch(session)
    run = CollectorRun(
        collector_id=source.collector_id,
        collector_version=SpecialistPublicationCollector.collector_version,
        started_at=started,
        status="RUNNING",
        epoch_id=active_epoch.id if active_epoch else None,
        is_baseline=bool(force_baseline or (active_epoch and is_baseline_active(session))),
    )
    session.add(run)
    session.commit()
    lock.update_run_id(run.id)

    try:
        result = SpecialistPublicationCollector(source_id).run(feed_xml=feed_xml)
        status = result.metadata.get("component_status") or "FAILED"
        new_leads = 0
        if status == "SUCCESS":
            parsed = parse_specialist_publication_feed(
                result.fetched[0].payload,
                max_items=max_items,
                feed_format=source.feed_format,
                required_category=source.required_category,
            )
            if not parsed.success:
                status = "FAILED"
            else:
                service = SpecialistLeadService(session)
                notifier = DiscordNotifier(settings)
                manufacturers: set[str] = set()
                for item in parsed.items:
                    lead_type = classify_lead_type(
                        title=item.title,
                        claim_text=item.claim_text,
                        reference_candidates=item.reference_candidates,
                        is_limited_edition=item.is_limited_edition,
                        is_collaboration=item.is_collaboration,
                    )
                    # Collaborator (e.g. "Windup Watch Shop") is preserved as
                    # structured-enough context without expanding the schema
                    # for one field -- see app/models/specialist_lead.py's
                    # `notes` column, already used for free-text annotations
                    # (e.g. "rumor-tagged").
                    notes = f"collaborator: {item.collaborator}" if item.collaborator else None
                    outcome = service.ingest_candidate(
                        source_id=source_id,
                        lead_type=lead_type,
                        title=item.title,
                        source_url=item.url,
                        published_at=item.published_at,
                        reference_candidates=item.reference_candidates,
                        claim_text=item.claim_text,
                        manufacturer=item.brand,
                        brand=item.brand,
                        confidence=35.0 + (20.0 if item.reference_candidates else 0.0),
                        force_baseline=force_baseline,
                        notes=notes,
                        collector_run_id=run.id,
                    )
                    if outcome["created"]:
                        new_leads += 1
                        manufacturers.add(item.brand)
                        lead = session.get(SpecialistLead, outcome["lead_id"])
                        service.notify_new_lead(lead, notifier=notifier)
                for manufacturer in manufacturers:
                    for correlation in service.correlate_pending_leads(manufacturer=manufacturer):
                        lead = session.get(SpecialistLead, correlation["lead_id"])
                        service.notify_correlation(lead, notifier=notifier)
                session.commit()
                status = "SUCCESS" if parsed.items else "ZERO_ITEMS"

        completed = datetime.now(UTC)
        run.completed_at = completed
        run.status = status
        run.discovered_count = new_leads
        run.parsed_count = new_leads
        run.duration_ms = int((completed - started).total_seconds() * 1000)
        run.summary_metadata = {"new_leads": new_leads, "component_status": status, "force_baseline": force_baseline}
        session.commit()
        logger.info("publication_pipeline_completed", source_id=source_id, run_id=run.id, status=run.status, new_leads=new_leads)
        return run
    except Exception as exc:
        session.rollback()
        run.status = "FAILED"
        run.completed_at = datetime.now(UTC)
        run.summary_metadata = {"fatal_error": str(exc), "force_baseline": force_baseline}
        session.add(run)
        session.commit()
        raise
    finally:
        lock.release()


def run_monochrome_pipeline(session: Session, *, feed_xml: bytes | None = None, max_items: int = 20, force_baseline: bool = False) -> CollectorRun:
    return run_publication_pipeline(session, source_id="monochrome", feed_xml=feed_xml, max_items=max_items, force_baseline=force_baseline)


def run_deployant_pipeline(session: Session, *, feed_xml: bytes | None = None, max_items: int = 20, force_baseline: bool = False) -> CollectorRun:
    return run_publication_pipeline(session, source_id="deployant", feed_xml=feed_xml, max_items=max_items, force_baseline=force_baseline)


def run_fratello_pipeline(session: Session, *, feed_xml: bytes | None = None, max_items: int = 20, force_baseline: bool = False) -> CollectorRun:
    return run_publication_pipeline(session, source_id="fratello", feed_xml=feed_xml, max_items=max_items, force_baseline=force_baseline)


def run_watchtime_pipeline(session: Session, *, feed_xml: bytes | None = None, max_items: int = 20, force_baseline: bool = False) -> CollectorRun:
    return run_publication_pipeline(session, source_id="watchtime", feed_xml=feed_xml, max_items=max_items, force_baseline=force_baseline)


def run_great_gshock_world_pipeline(session: Session, *, feed_xml: bytes | None = None, max_items: int = 20, force_baseline: bool = False) -> CollectorRun:
    return run_publication_pipeline(session, source_id="great_gshock_world", feed_xml=feed_xml, max_items=max_items, force_baseline=force_baseline)


def run_gear_patrol_pipeline(session: Session, *, feed_xml: bytes | None = None, max_items: int = 60, force_baseline: bool = False) -> CollectorRun:
    """max_items defaults higher than the other publication sources (20):
    Gear Patrol's feed is site-wide, not watches-only -- ~79% of items in
    a real captured sample were non-watch categories (Motorcycles, Audio,
    Footwear, Outdoors, Motoring, Style, Deals). 60 raw items covers
    roughly the same real-world watches-relevant window (~2-3 days) that
    20 items gives a 100%-watches source, rather than silently truncating
    the effective watch coverage to a handful of items per run."""
    return run_publication_pipeline(session, source_id="gear_patrol", feed_xml=feed_xml, max_items=max_items, force_baseline=force_baseline)
