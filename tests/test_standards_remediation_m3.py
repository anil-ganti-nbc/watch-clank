from pathlib import Path

from app.models import QualificationEvidence
from app.services.deployment_completion import DeploymentEvidence, verify_completion
from app.services.qualification import QualificationService
from scripts.deployment_status import observed_evidence
from tests.test_core import db_session, tmp_settings  # noqa: F401


def test_deployment_completion_requires_observed_congruence(monkeypatch):
    complete = verify_completion(DeploymentEvidence("staging", "abc", "abc", evidence_source="host"))
    assert complete["state"] == "COMPLETE"
    assert complete["target_scope"] == "staging"
    assert complete["evidence_source"] == "host"
    assert verify_completion(DeploymentEvidence("staging", "abc", "old"))["state"] == "UNVERIFIED"
    assert verify_completion(DeploymentEvidence("staging", "abc", None))["state"] == "UNVERIFIED"
    assert verify_completion(DeploymentEvidence("staging", "abc", "abc", config_matches=False))["state"] == "UNVERIFIED"
    assert verify_completion(DeploymentEvidence("staging", "abc", "abc", wiring_matches=False))["state"] == "UNVERIFIED"
    assert verify_completion(DeploymentEvidence("staging", "abc", "abc", components_converged=False))["state"] == "IN_PROGRESS"
    monkeypatch.delenv("WATCH_CLANK_RUNNING_REVISION", raising=False)
    assert observed_evidence("staging", "abc").running_revision is None
    assert verify_completion(observed_evidence("staging", "abc"))["state"] == "UNVERIFIED"


def test_real_delivery_gate_persists_natural_evidence_and_fails_closed_on_drift(db_session):
    service = QualificationService(db_session)
    assert service.delivery_allowed("casio_japan")
    db_session.flush()
    evidence = db_session.query(QualificationEvidence).filter_by(collector_id="casio_japan").one()
    assert evidence.provenance == "NATURAL"
    assert evidence.eligibility_gate == evidence.qualification_gate == "ELIGIBLE"
    evidence.qualification_gate = "BLOCKED"
    db_session.flush()
    assert not service.delivery_allowed("casio_japan")


def test_material_gate_change_creates_reset_epoch_and_excludes_prior_evidence(db_session, monkeypatch):
    import app.services.qualification as qualification

    service = QualificationService(db_session)
    assert service.delivery_allowed("casio_japan")
    db_session.flush()
    prior = db_session.query(QualificationEvidence).filter_by(collector_id="casio_japan").one()
    monkeypatch.setattr(qualification, "EXPERIMENTAL_MATURITY_COLLECTORS", frozenset({"tissot_sitemap", "changed"}))
    assert not service.delivery_allowed("casio_japan")
    db_session.flush()
    current = db_session.query(QualificationEvidence).filter_by(collector_id="casio_japan").order_by(QualificationEvidence.id.desc()).first()
    assert current.epoch_id != prior.epoch_id
    assert current.reset_reason == "delivery_maturity_gate_changed"
    assert current.intervention_treatment == "RESET"
    service.record_operator_promotion("casio_japan", change_identity="review-42")
    db_session.flush()
    assert service.delivery_allowed("casio_japan")


def test_unknown_legacy_state_is_not_inferred(db_session):
    db_session.add(QualificationEvidence(
        collector_id="legacy", epoch_id="legacy", provenance="UNKNOWN",
        eligibility_gate="UNKNOWN", qualification_gate="UNKNOWN",
    ))
    db_session.flush()
    row = db_session.query(QualificationEvidence).filter_by(collector_id="legacy").one()
    assert row.provenance == row.eligibility_gate == row.qualification_gate == "UNKNOWN"


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
