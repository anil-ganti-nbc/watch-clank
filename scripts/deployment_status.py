"""Verify a deployment claim from independently observed running state.

The host-side observer must set WATCH_CLANK_RUNNING_REVISION and, where
applicable, WATCH_CLANK_CONFIG_MATCHES / WATCH_CLANK_WIRING_MATCHES / 
WATCH_CLANK_COMPONENTS_CONVERGED.  This command never substitutes the
requested revision for absent host evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.deployment_completion import DeploymentEvidence, verify_completion


def _bool_from_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def observed_evidence(target_scope: str, intended_revision: str) -> DeploymentEvidence:
    """Adapter boundary for a later authorized host observer."""
    return DeploymentEvidence(
        target_scope=target_scope,
        intended_revision=intended_revision,
        running_revision=os.environ.get("WATCH_CLANK_RUNNING_REVISION"),
        config_matches=_bool_from_env("WATCH_CLANK_CONFIG_MATCHES"),
        wiring_matches=_bool_from_env("WATCH_CLANK_WIRING_MATCHES"),
        components_converged=_bool_from_env("WATCH_CLANK_COMPONENTS_CONVERGED"),
        evidence_source=os.environ.get("WATCH_CLANK_OBSERVATION_SOURCE", "unavailable"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--intended-revision", required=True)
    args = parser.parse_args()
    result = verify_completion(observed_evidence(args.target, args.intended_revision))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["state"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
