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

    def record_execution(self, run, provenance: str) -> QualificationEvidence:
        """Attach one durable qualification record to one terminal execution.

        The caller supplies provenance from its real entrypoint.  UNKNOWN is
        intentional where legacy wrappers have not established authority.
        """
        if provenance not in {"SCHEDULED", "MANUAL", "DEPLOY", "RECOVERY", "UNKNOWN"}:
            raise ValueError(f"unsupported qualification provenance: {provenance}")
        # A reset is an event associated with this execution, not its terminal
        # evidence.  Deduplicate terminal evidence only against another
        # terminal record, so both durable facts can coexist for one run.
        existing = (self.session.query(QualificationEvidence)
                    .filter_by(execution_id=run.id)
                    .filter(QualificationEvidence.reset_reason.is_(None))
                    .first())
        if existing is not None:
            return existing
        material = "|".join((get_identity()["source_revision"], run.collector_id, run.collector_version, _epoch_id()))
        current_epoch = _epoch_id()
        evidence = QualificationEvidence(
            collector_id=run.collector_id, epoch_id=current_epoch,
            provenance=provenance, change_identity=get_identity()["source_revision"], material_identity=material,
            intervention_treatment="NONE", eligibility_gate="ELIGIBLE" if provenance == "SCHEDULED" else "UNKNOWN",
            qualification_gate="ELIGIBLE" if provenance == "SCHEDULED" else "UNKNOWN",
            execution_id=run.id, outcome=run.status, observed_at=datetime.now(UTC),
        )
        self.session.add(evidence)
        return evidence

    def prepare_epoch_for_run(self, run, provenance: str) -> QualificationEvidence | None:
        """Fail-closed pre-event material reset; idempotent for this run."""
        if provenance not in {"SCHEDULED", "MANUAL", "DEPLOY", "RECOVERY", "UNKNOWN"}:
            raise ValueError(f"unsupported qualification provenance: {provenance}")
        material = "|".join((get_identity()["source_revision"], run.collector_id, run.collector_version, _epoch_id()))
        existing = self.session.query(QualificationEvidence).filter_by(execution_id=run.id, reset_reason="CODE_OR_COLLECTOR_CHANGE").first()
        if existing is not None:
            return existing
        prior = (self.session.query(QualificationEvidence).filter_by(collector_id=run.collector_id)
                 .filter(QualificationEvidence.material_identity.is_not(None))
                 .order_by(QualificationEvidence.id.desc()).first())
        if prior is None or prior.material_identity == material:
            return None
        reset = QualificationEvidence(
            collector_id=run.collector_id, epoch_id=_epoch_id(), provenance=provenance,
            change_identity=material, material_identity=material, execution_id=run.id,
            prior_material_identity=prior.material_identity, prior_epoch_id=prior.epoch_id,
            reset_reason="CODE_OR_COLLECTOR_CHANGE", intervention_treatment="RESET",
            eligibility_gate="UNKNOWN", qualification_gate="RESET", outcome="RUNNING",
            observed_at=datetime.now(UTC),
        )
        self.session.add(reset)
        return reset

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
                    qualification_gate="RESET", observed_at=datetime.now(UTC),
                ))
                return False
            # Delivery is not an execution authority.  Absence of durable,
            # authority-supplied qualification remains unknown and cannot
            # authorize external delivery.
            return False

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
