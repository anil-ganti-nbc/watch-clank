from pathlib import Path

from app.models import QualificationEvidence
from app.models import CollectorRun
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


def test_real_delivery_gate_does_not_fabricate_provenance_and_fails_closed_on_drift(db_session):
    service = QualificationService(db_session)
    assert not service.delivery_allowed("casio_japan")
    db_session.flush()
    assert db_session.query(QualificationEvidence).filter_by(collector_id="casio_japan").count() == 0
    # Authority-supplied scheduled evidence is consumed without rewriting it.
    run = CollectorRun(collector_id="casio_japan", collector_version="test", status="SUCCESS")
    db_session.add(run); db_session.flush()
    evidence = service.record_execution(run, "SCHEDULED")
    db_session.flush()
    assert evidence.provenance == "SCHEDULED"
    assert service.delivery_allowed("casio_japan")
    evidence.qualification_gate = "BLOCKED"
    db_session.flush()
    assert not service.delivery_allowed("casio_japan")


def test_material_gate_change_creates_reset_epoch_and_excludes_prior_evidence(db_session, monkeypatch):
    import app.services.qualification as qualification

    service = QualificationService(db_session)
    prior = QualificationEvidence(collector_id="casio_japan", epoch_id=qualification._epoch_id(), provenance="SCHEDULED", material_identity="old", eligibility_gate="ELIGIBLE", qualification_gate="ELIGIBLE")
    db_session.add(prior); db_session.flush()
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


def test_terminal_execution_records_explicit_provenance_once(db_session):
    from datetime import UTC, datetime

    run = CollectorRun(collector_id="casio_japan", collector_version="test", status="SUCCESS", started_at=datetime.now(UTC), completed_at=datetime.now(UTC))
    db_session.add(run)
    db_session.flush()
    service = QualificationService(db_session)
    evidence = service.record_execution(run, "SCHEDULED")
    assert evidence.execution_id == run.id
    assert evidence.provenance == "SCHEDULED"
    assert evidence.outcome == "SUCCESS"
    assert service.record_execution(run, "SCHEDULED").id == evidence.id


def test_unknown_execution_provenance_is_never_promoted_to_natural(db_session):
    from datetime import UTC, datetime

    run = CollectorRun(collector_id="manual_lane", collector_version="test", status="SUCCESS", started_at=datetime.now(UTC), completed_at=datetime.now(UTC))
    db_session.add(run)
    db_session.flush()
    evidence = QualificationService(db_session).record_execution(run, "UNKNOWN")
    assert evidence.provenance == "UNKNOWN"
    assert evidence.qualification_gate == "UNKNOWN"


def test_execution_authority_provenance_is_preserved_without_downstream_rewrite(db_session):
    """All recognised execution authorities retain their supplied identity."""
    from datetime import UTC, datetime

    service = QualificationService(db_session)
    for index, provenance in enumerate(("SCHEDULED", "MANUAL", "DEPLOY", "RECOVERY")):
        run = CollectorRun(
            collector_id=f"authority-{index}", collector_version="test", status="SUCCESS",
            started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
        )
        db_session.add(run); db_session.flush()
        evidence = service.record_execution(run, provenance)
        assert evidence.provenance == provenance
        assert service.delivery_allowed(run.collector_id) is (provenance == "SCHEDULED")


def test_historical_reset_without_lineage_remains_unknown_and_readable(db_session):
    row = QualificationEvidence(
        collector_id="historical", epoch_id="current", provenance="UNKNOWN",
        material_identity="new", reset_reason="CODE_OR_COLLECTOR_CHANGE",
        intervention_treatment="RESET", eligibility_gate="UNKNOWN", qualification_gate="RESET",
    )
    db_session.add(row); db_session.flush()
    assert row.prior_material_identity is None
    assert row.prior_epoch_id is None


def test_first_changed_run_resets_before_gate_can_use_prior_evidence(db_session):
    """Regression for M4B.1: pipeline pre-event boundary, not delivery side effect."""
    from datetime import UTC, datetime
    from app.services.pipeline import PipelineService

    old_run = CollectorRun(collector_id="casio_japan", collector_version="A", status="SUCCESS", started_at=datetime.now(UTC), completed_at=datetime.now(UTC))
    db_session.add(old_run); db_session.flush()
    old = QualificationService(db_session).record_execution(old_run, "SCHEDULED")
    db_session.flush()
    changed = CollectorRun(collector_id="casio_japan", collector_version="B", status="RUNNING", started_at=datetime.now(UTC))
    db_session.add(changed); db_session.commit()
    pipeline = PipelineService(db_session)
    # This is the same call site made immediately after real run creation and
    # before collector/event processing in every pipeline entrypoint.
    pipeline._prepare_qualification_epoch(changed, "SCHEDULED")
    reset = db_session.query(QualificationEvidence).filter_by(execution_id=changed.id, reset_reason="CODE_OR_COLLECTOR_CHANGE").one()
    assert reset.material_identity != old.material_identity
    assert reset.prior_material_identity == old.material_identity
    assert reset.prior_epoch_id == old.epoch_id
    assert reset.qualification_gate == "RESET"
    assert db_session.get(QualificationEvidence, old.id).execution_id == old_run.id
    assert not QualificationService(db_session).delivery_allowed("casio_japan")
    pipeline._prepare_qualification_epoch(changed, "SCHEDULED")
    assert db_session.query(QualificationEvidence).filter_by(execution_id=changed.id, reset_reason="CODE_OR_COLLECTOR_CHANGE").count() == 1
    # Reset and terminal evidence are distinct durable facts for the same run.
    changed.status = "SUCCESS"
    changed.completed_at = datetime.now(UTC)
    db_session.flush()
    terminal = QualificationService(db_session).record_execution(changed, "SCHEDULED")
    db_session.flush()
    assert terminal.id != reset.id
    assert terminal.execution_id == reset.execution_id == changed.id
    assert terminal.reset_reason is None
    assert terminal.outcome == "SUCCESS"
    assert terminal.epoch_id == reset.epoch_id
    assert terminal.material_identity == reset.material_identity
    assert QualificationService(db_session).record_execution(changed, "SCHEDULED").id == terminal.id
    assert db_session.query(QualificationEvidence).filter_by(execution_id=changed.id).count() == 2


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


def _real_migrated_session(tmp_path):
    """2026-09-03 incident regression support: the standard db_session
    fixture builds its schema via Base.metadata.create_all(), which applies
    the CURRENT model's server_default=func.now() for
    QualificationEvidence.observed_at. The real production database was
    built by actually running alembic/versions/013_qualification_evidence.py,
    whose op.create_table call for observed_at has NO server_default at all
    -- a genuine model/migration drift that only a real migrated schema can
    expose. Mirrors test_alembic_upgrade_fresh_db's pattern."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    repo_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "mig.db"
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    return sessionmaker(bind=engine)()


def test_prepare_epoch_reset_row_survives_the_real_migrated_schema(tmp_path):
    """2026-09-03 incident regression: deploying the Seiko JP provenance fix
    triggered this crash live on Hetzner the first time any collector's
    material_identity changed across a real deploy (source_revision changes
    with every commit) -- QualificationService.prepare_epoch_for_run's reset
    row never set observed_at, and the real migrated table has no DB-level
    default for it (see _real_migrated_session), so the INSERT failed
    NOT NULL and crashed every collector's first run after any code deploy.
    Must be exercised against the real migrated schema; the standard
    db_session fixture's create_all()-built schema does not reproduce it."""
    from app.models import CollectorRun, QualificationEvidence
    from app.services.qualification import QualificationService

    session = _real_migrated_session(tmp_path)
    old_run = CollectorRun(collector_id="seiko_jp_products", collector_version="A", status="SUCCESS")
    session.add(old_run)
    session.flush()
    QualificationService(session).record_execution(old_run, "SCHEDULED")
    session.commit()

    changed_run = CollectorRun(collector_id="seiko_jp_products", collector_version="B", status="RUNNING")
    session.add(changed_run)
    session.commit()

    reset = QualificationService(session).prepare_epoch_for_run(changed_run, "SCHEDULED")
    session.commit()  # this is exactly where the real deploy crashed

    assert reset is not None
    assert reset.observed_at is not None
    persisted = session.query(QualificationEvidence).filter_by(id=reset.id).one()
    assert persisted.observed_at is not None


def test_maturity_gate_reset_row_survives_the_real_migrated_schema(tmp_path):
    """Same defect, second call site: QualificationService.delivery_allowed's
    'delivery_maturity_gate_changed' reset row also never set observed_at --
    would crash the same way the first time EXPERIMENTAL_MATURITY_COLLECTORS
    changes while stale evidence exists for a collector. Fixed alongside the
    incident fix since it is the identical defect in the same file."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from app.models import QualificationEvidence
    from app.services.qualification import QualificationService, _epoch_id

    session = _real_migrated_session(tmp_path)
    # A realistic prior row: any row actually written by record_execution()
    # always has observed_at set explicitly (that call site was already
    # correct) -- this reproduces the real pre-existing-evidence shape.
    session.add(QualificationEvidence(
        collector_id="tissot_sitemap", epoch_id="stale-epoch", provenance="SCHEDULED",
        eligibility_gate="ELIGIBLE", qualification_gate="ELIGIBLE", observed_at=datetime.now(UTC),
    ))
    session.commit()

    with patch("app.services.qualification.EXPERIMENTAL_MATURITY_COLLECTORS", frozenset({"tissot_sitemap"})):
        assert _epoch_id() != "stale-epoch"
        result = QualificationService(session).delivery_allowed("tissot_sitemap")
    session.commit()  # this is exactly where the real deploy would have crashed

    assert result is False
    reset = (
        session.query(QualificationEvidence)
        .filter_by(collector_id="tissot_sitemap", reset_reason="delivery_maturity_gate_changed")
        .one()
    )
    assert reset.observed_at is not None
