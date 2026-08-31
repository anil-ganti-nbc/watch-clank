from pathlib import Path
from app.services.deployment_completion import DeploymentEvidence, verify_completion
from tests.test_core import db_session, tmp_settings  # noqa: F401

def test_deployment_completion_requires_running_congruence():
    complete = verify_completion(DeploymentEvidence("staging", "abc", "abc", evidence_source="runtime"))
    assert complete["state"] == "COMPLETE"
    assert verify_completion(DeploymentEvidence("staging", "abc", "old"))["state"] == "UNVERIFIED"
    assert verify_completion(DeploymentEvidence("staging", "abc", None))["state"] == "UNVERIFIED"
    assert verify_completion(DeploymentEvidence("staging", "abc", "abc", config_matches=False))["state"] == "UNVERIFIED"
    assert verify_completion(DeploymentEvidence("staging", "abc", "abc", wiring_matches=False))["state"] == "UNVERIFIED"
    assert verify_completion(DeploymentEvidence("staging", "abc", "abc", components_converged=False))["state"] == "IN_PROGRESS"

def test_lock_grant_beats_stale_metadata(db_session, tmp_settings):
    from app.services.run_lock import RunLockService
    path = Path(tmp_settings.resolved_lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pid": 1, "acquired_at": "1970-01-01T00:00:00+00:00"}')
    first = RunLockService(db_session, tmp_settings, lock_path=path)
    second = RunLockService(db_session, tmp_settings, lock_path=path)
    assert first.acquire().acquired
    assert not second.acquire().acquired
    first.release()
    assert second.acquire().acquired
    second.release()
