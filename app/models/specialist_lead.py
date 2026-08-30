"""Layer B: early-warning leads from specialist/non-official sources.

Deliberately a SEPARATE table from ReleaseLead (Layer A / official).
ReleaseLead is shaped around official manufacturer announcements
(manufacturer defaults "Casio", enrichment_status vocabulary is about
official enrichment) — reusing it for third-party rumors/leaks would risk
exactly what this sprint explicitly forbids: a specialist lead becoming
indistinguishable from official evidence. This table's rows are never
promoted into ReleaseLead/Watch/SourceObservation automatically; they are
only ever *correlated* with an official Watch once one independently
appears (see correlated_watch_id), preserving the distinction and letting
lead-time be measured.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base

# Source type vocabulary (Phase "SOURCE TYPES" in the sprint brief).
SOURCE_TYPES = frozenset(
    {
        "SPECIALIST_PUBLICATION",
        "SPECIALIST_BLOG",
        "RETAILER_EARLY_LISTING",
        "SOCIAL_LEAKER",
        "COMMUNITY_SIGNAL",
    }
)

# Lead type vocabulary (Phase 11). These are LEADS, never official
# product-state transitions.
#
# EDITORIAL_MENTION (2026-08-19 QC + classifier hardening pass): real
# coverage of a tracked brand's product that makes no specific new-
# reference/leak/collaboration/availability claim -- roundups, reviews,
# mod tutorials, retrospectives. Added instead of one new type per
# editorial sub-genre specifically because LEAKED_IMAGE had become a
# silent default for "no reference number extracted", which is not leak
# evidence. See app/services/specialist_leads.py::classify_lead_type.
LEAD_TYPES = frozenset(
    {
        "POSSIBLE_NEW_REFERENCE",
        "POSSIBLE_COLLABORATION",
        "POSSIBLE_NEW_REGION",
        "POSSIBLE_PRICE",
        "POSSIBLE_RELEASE_DATE",
        "POSSIBLE_DISCONTINUATION",
        "POSSIBLE_LIMITED_EDITION",
        "LEAKED_IMAGE",
        "EARLY_RETAIL_LISTING",
        "EDITORIAL_MENTION",
    }
)

VERIFICATION_STATUSES = frozenset({"UNCONFIRMED", "CORRELATED_WITH_OFFICIAL", "REJECTED"})

# Sprint 8 freshness bugfix (see ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md).
# DISCOVERY NOVELTY ("have we seen this before" -- is_baseline/dedup) and
# EDITORIAL FRESHNESS ("is this current enough to show a journalist as
# news") are different questions; this is the latter.
#   FRESH             - published_at (or, for RETAILER_EARLY_LISTING with
#                        no publication concept, discovered_at) within the
#                        configured freshness window. Eligible for Recent
#                        Intelligence / Discord.
#   STALE_PUBLICATION - older than the freshness window. Kept as
#                        historical/correlation evidence, never alerted.
#   BASELINE          - discovered during an epoch's baseline run. Always
#                        historical, regardless of publication age.
#   UNKNOWN_TIMESTAMP - no publication timestamp and the source class
#                        requires one. Never assumed fresh.
#   MANUAL_UNDATED    - manually ingested with no publication timestamp
#                        supplied. Distinct from UNKNOWN_TIMESTAMP so the
#                        record honestly says "a human ingested this
#                        without dating it" rather than "the parser
#                        couldn't find a date."
EDITORIAL_FRESHNESS_STATES = frozenset(
    {"FRESH", "STALE_PUBLICATION", "BASELINE", "UNKNOWN_TIMESTAMP", "MANUAL_UNDATED"}
)

# Phase 7 (Sprint 6): how a correlated lead matched an official Watch.
# EXACT_REFERENCE_MATCH = full reference string matched, e.g. "GWR-B3000-1A".
# FAMILY_MATCH = only the family root matched (e.g. lead "GWR-B3000" against
# official "GWR-B3000-1A") — deterministic, never a similarity score, and
# must never be presented as an exact-reference confirmation.
CORRELATION_TYPES = frozenset({"EXACT_REFERENCE_MATCH", "FAMILY_MATCH"})


class SpecialistLead(Base):
    """One discovered piece of early-warning evidence from a non-official
    source. Never authoritative on its own — see module docstring."""

    __tablename__ = "specialist_leads"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('SPECIALIST_PUBLICATION','SPECIALIST_BLOG',"
            "'RETAILER_EARLY_LISTING','SOCIAL_LEAKER','COMMUNITY_SIGNAL')",
            name="ck_specialist_lead_source_type",
        ),
        CheckConstraint(
            "source_authority_tier >= 1 AND source_authority_tier <= 4",
            name="ck_specialist_lead_tier_range",
        ),
        CheckConstraint(
            "verification_status IN ('UNCONFIRMED','CORRELATED_WITH_OFFICIAL','REJECTED')",
            name="ck_specialist_lead_verification_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_specialist_lead_confidence_range",
        ),
        CheckConstraint(
            "correlation_type IS NULL OR correlation_type IN ('EXACT_REFERENCE_MATCH','FAMILY_MATCH')",
            name="ck_specialist_lead_correlation_type",
        ),
        CheckConstraint(
            "editorial_freshness IS NULL OR editorial_freshness IN "
            "('FRESH','STALE_PUBLICATION','BASELINE','UNKNOWN_TIMESTAMP','MANUAL_UNDATED')",
            name="ck_specialist_lead_editorial_freshness",
        ),
        CheckConstraint(
            "delivery_state IS NULL OR delivery_state IN ('sent', 'failed', 'gated')",
            name="ck_specialist_lead_delivery_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 1=official (not used here, reserved), 2=highly reliable specialist/
    # retailer with demonstrated history, 3=credible specialist/social
    # requiring verification, 4=community signal/unverified. See
    # app/services/source_registry.py for the source_id -> tier mapping.
    source_authority_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    account_or_domain: Mapped[str | None] = mapped_column(String(256), nullable=True)

    lead_type: Mapped[str] = mapped_column(String(32), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_candidates: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    claim_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=30.0)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNCONFIRMED")

    # Correlation with Layer A (official). Set only by deterministic exact
    # reference-string matching against an existing Watch — never fuzzy.
    correlated_watch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("watches.id", ondelete="SET NULL"), nullable=True
    )
    correlated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    official_first_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lead_time_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    correlation_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    ingestion_method: Mapped[str] = mapped_column(String(16), nullable=False, default="collector")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 2026-08-19 QC + classifier hardening pass: the CollectorRun that
    # discovered this lead, when the ingesting pipeline has one in scope
    # (every current pipeline does -- see run_*_pipeline in
    # app/services/specialist_leads.py). Nullable/SET NULL because this is
    # provenance, not identity -- losing the run row must never cascade
    # into losing the lead. Historical pre-existing rows are correctly
    # NULL (the run association was never captured for them).
    collector_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("collector_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Discord dedup (Sprint 6 Phase 4): set once an alert is actually sent so
    # a repeat pipeline run never re-notifies for the same lead.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # STD-UI-COM-011 remediation (2026-08-31): coarse delivery outcome so the
    # UI can distinguish attempted-and-failed / gated-by-policy from
    # never-attempted, instead of collapsing all of those into
    # notified_at IS NULL. NULL = never considered for delivery. An
    # early-warning 'sent' row also carries notified_at; a correlation-
    # follow-up 'sent' row does not (notified_at is the early-warning dedup
    # guard and is never set by the follow-up path). 'sent' is never
    # downgraded: policy skips only mark fields that are still NULL.
    delivery_state: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Sprint 7 epoch/baseline tracking -- see app/models/epoch.py. A lead
    # discovered during Epoch 1's baseline is real data (correlation/
    # lead-time math should still work on it later) but must never alert.
    epoch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("operational_epochs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    # Sprint 8 freshness bugfix -- see EDITORIAL_FRESHNESS_STATES above and
    # app/services/freshness.py. Nullable because it's computed at ingest
    # time (or backfilled by migration 007 for pre-existing rows), never
    # because "unset" is a meaningful state to query against.
    editorial_freshness: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    freshness_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    freshness_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
