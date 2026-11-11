"""Finite P0 native UTC write-boundary regressions, using only owned state."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.collectors import prtimes_jp_staging as staging
from app.models import Base, SpecialistLead
from app.services.qc import QueueFilters, fetch_lead_queue_page
from app.services.specialist_leads import SpecialistLeadService
from tests.test_prtimes_jp_staging import feed


@pytest.mark.parametrize(
    "stamp,seconds,expected",
    [
        ("2026-11-06T00:30:00+09:00", 0, "FRESH"),
        ("2026-11-06T00:30:00+09:00", 1, "STALE_PUBLICATION"),
        ("2026-11-06T23:30:00-07:00", 1, "STALE_PUBLICATION"),
        ("2026-11-06T00:15:00+05:30", 0, "FRESH"),
        ("2026-11-06T01:00:00+00:00", 0, "FRESH"),
    ],
)
def test_native_offset_instants_survive_commit_and_retry(tmp_path, stamp, seconds, expected):
    publication = datetime.fromisoformat(stamp)
    observed = (publication + timedelta(hours=1)).astimezone(timezone(timedelta(hours=9)))
    evaluated = (publication + timedelta(hours=72, seconds=seconds)).astimezone(
        timezone(timedelta(hours=-7))
    )
    staging.run_feed(tmp_path, "25284", feed(stamp=stamp), now=observed)
    staging.run_feed(tmp_path, "25284", feed(serial=2, stamp=stamp), now=observed)
    assert staging.bridge_native(tmp_path, "25284", now=evaluated)["created"] == 2
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        # A new session after bridge commit, not its aware identity-map values.
        with Session(engine) as db:
            lead = db.query(SpecialistLead).filter_by(is_baseline=False).one()
            assert lead.published_at == publication.astimezone(UTC).replace(tzinfo=None)
            assert lead.discovered_at == observed.astimezone(UTC).replace(tzinfo=None)
            assert lead.freshness_evaluated_at == evaluated.astimezone(UTC).replace(tzinfo=None)
            assert lead.editorial_freshness == expected
            assert len(fetch_lead_queue_page(db, QueueFilters())) == int(expected == "FRESH")
            raw = json.loads(lead.notes)
            assert raw["published_at_raw"] == stamp
            assert raw["observed_at"] == observed.isoformat()
            assert lead.delivery_reason == "EDITORIAL_NOTIFICATIONS_DISABLED"
            assert lead.notified_at is None
        assert staging.bridge_native(tmp_path, "25284", now=evaluated)["created"] == 0
        with Session(engine) as db:
            assert db.query(SpecialistLead).count() == 2
            assert len(fetch_lead_queue_page(db, QueueFilters())) == int(expected == "FRESH")
    finally:
        engine.dispose()


def test_shared_ingestion_normalizes_observation_and_publication_before_write(tmp_path):
    publication = datetime.fromisoformat("2026-11-06T00:30:00+09:00")
    observation = datetime.fromisoformat("2026-11-08T10:30:00-05:00")
    engine = create_engine("sqlite:///" + str(tmp_path / "owned-service.sqlite3"))
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            result = SpecialistLeadService(db).ingest_candidate(
                source_id="casioblog", lead_type="EDITORIAL_MENTION", title="owned positive",
                source_url="https://example.invalid/owned", published_at=publication.isoformat(),
                observed_at=observation, reference_candidates=[], claim_text=None,
                notes="preserve caller provenance unchanged", freshness_window_hours=72,
            )
            db.commit()
            lead_id = result["lead_id"]
        with Session(engine) as db:
            lead = db.get(SpecialistLead, lead_id)
            assert lead.published_at == publication.astimezone(UTC).replace(tzinfo=None)
            assert lead.discovered_at == observation.astimezone(UTC).replace(tzinfo=None)
            assert lead.freshness_evaluated_at == observation.astimezone(UTC).replace(tzinfo=None)
            assert lead.editorial_freshness == "FRESH"
            assert lead.notes == "preserve caller provenance unchanged"
    finally:
        engine.dispose()
