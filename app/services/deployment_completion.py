"""Injectable intended-vs-running deployment completion comparison."""
from dataclasses import dataclass
from datetime import UTC, datetime

@dataclass(frozen=True)
class DeploymentEvidence:
    target_scope: str; intended_revision: str; running_revision: str | None
    config_matches: bool = True; wiring_matches: bool = True; components_converged: bool = True
    evidence_source: str = "unknown"

def verify_completion(e: DeploymentEvidence) -> dict:
    matches = bool(e.running_revision) and e.intended_revision == e.running_revision and e.config_matches and e.wiring_matches
    # Missing or contradictory material identity is an observation failure,
    # not evidence that a deployment is merely still converging.
    state = "COMPLETE" if matches and e.components_converged else ("IN_PROGRESS" if matches else "UNVERIFIED")
    return {"state": state, "target_scope": e.target_scope, "intended_revision": e.intended_revision, "running_revision": e.running_revision, "comparison_matches": matches, "evidence_source": e.evidence_source, "verified_at": datetime.now(UTC).isoformat()}
