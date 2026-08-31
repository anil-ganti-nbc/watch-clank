"""Durable enforcement for the collector maturity/delivery boundary.

The delivery gate remains the operational authority.  This service records
each real evaluation and fails closed when its current-epoch record disagrees
with that configured gate.  It deliberately does not infer history for rows
that predate migration 013.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.identity import get_identity
from app.models import QualificationEvidence
from app.services.delivery_gate import EXPERIMENTAL_MATURITY_COLLECTORS


def _epoch_id() -> str:
    """Identity of the configured maturity decision, not a fabricated run ID."""
    members = ",".join(sorted(EXPERIMENTAL_MATURITY_COLLECTORS))
    return hashlib.sha256(members.encode()).hexdigest()[:32]


class QualificationService:
    """Evidence-backed adapter for the existing external-delivery gate."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def delivery_allowed(self, collector_id: str | None) -> bool:
        """Persist the actual gate comparison and return a fail-closed result."""
        if not collector_id:
            return False
        epoch_id = _epoch_id()
        configured = "BLOCKED" if collector_id in EXPERIMENTAL_MATURITY_COLLECTORS else "ELIGIBLE"
        latest = (
            self.session.query(QualificationEvidence)
            .filter_by(collector_id=collector_id)
            .order_by(QualificationEvidence.id.desc())
            .first()
        )
        if latest is None or latest.epoch_id != epoch_id:
            # A change to the configured maturity set is the existing material
            # reset event.  No old epoch can qualify the new configuration.
            if latest is not None:
                self.session.add(QualificationEvidence(
                    collector_id=collector_id, epoch_id=epoch_id,
                    provenance="CONFIG", change_identity=get_identity()["source_revision"],
                    reset_reason="delivery_maturity_gate_changed",
                    intervention_treatment="RESET", eligibility_gate=configured,
                    qualification_gate="RESET",
                ))
                return False
            latest = QualificationEvidence(
                collector_id=collector_id, epoch_id=epoch_id, provenance="NATURAL",
                change_identity=get_identity()["source_revision"],
                intervention_treatment="NONE", eligibility_gate=configured,
                qualification_gate=configured,
                observed_at=datetime.now(UTC),
            )
            self.session.add(latest)
            return configured == "ELIGIBLE"

        # Do not overwrite a contradictory durable record: it is evidence of
        # gate drift and external delivery must remain silent until reconciled.
        return latest.eligibility_gate == configured and latest.qualification_gate == configured == "ELIGIBLE"

    def record_operator_promotion(self, collector_id: str, *, change_identity: str | None = None) -> None:
        """Record the existing manual promotion decision after its code review.

        A collector still listed as experimental cannot be promoted through
        this API; configuration and durable qualification must agree.
        """
        if collector_id in EXPERIMENTAL_MATURITY_COLLECTORS:
            raise ValueError("collector remains configured as experimental")
        self.session.add(QualificationEvidence(
            collector_id=collector_id, epoch_id=_epoch_id(), provenance="MANUAL",
            change_identity=change_identity or get_identity()["source_revision"],
            intervention_treatment="NONE", eligibility_gate="ELIGIBLE",
            qualification_gate="ELIGIBLE", observed_at=datetime.now(UTC),
        ))
