"""Bounded offline diagnostics. Capture-dependent checks never acquire inputs."""

import os
from datetime import timedelta
from pathlib import Path

import pytest

from app.collectors import prtimes_jp_staging as staging


def captures(company="25284", page=8):
    from scripts.qualify_prtimes_jp_offline import retained_frames

    directory = os.environ.get("PRTIMES_JP_CAPTURE_DIR")
    if directory is None:
        pytest.skip("private retained captures absent: NOT QUALIFIED")
    if not directory.strip():
        raise ValueError("empty capture binding")
    return {
        item.url: (frame, item)
        for frame, item in retained_frames(
            (Path(directory) / f"page-{page}.bin").read_bytes(), company
        )
    }


def genuine_pair(tmp_path, earlier, later):
    rows = captures()
    prefix = "https://prtimes.jp/main/html/rd/p/"
    a, b = [rows[f"{prefix}{serial:09d}.000025284.html"] for serial in (earlier, later)]
    assert a[1].url != b[1].url
    at = __import__("datetime").datetime.fromisoformat(
        b[1].metadata["published_at_raw"]
    ) + timedelta(hours=1)
    first = staging.run_feed(tmp_path, "25284", a[0], now=at)
    second = staging.run_feed(tmp_path, "25284", b[0], now=at)
    assert first["status"] == second["status"] == "SUCCESS"
    assert first["review_candidates"] == []
    assert staging.run_feed(tmp_path, "25284", b[0], now=at)["inserted"] == 0
    return a, b, second


@pytest.mark.parametrize(
    "earlier,later,kind,candidates",
    [
        (1831, 2031, "CONVENTIONAL", 1),
        (2000, 2002, "MIXED_WEARABLE_UNRESOLVED", 1),
        (2031, 2058, "CONVENTIONAL", 1),
    ],
)
def test_genuine_different_url_characterization(tmp_path, earlier, later, kind, candidates):
    # Clocks are counterfactual publication+1h, never historical live detection.
    a, b, result = genuine_pair(tmp_path, earlier, later)
    assert result["inserted"] == 1
    assert len(result["review_candidates"]) == candidates
    record = result["review_candidates"][0]
    assert record["product_kind"] == kind
    assert record["identity_key"] is None
    assert record["identity_status"] == "UNRESOLVED"
    assert record["title_raw"] == b[1].title
    assert record["published_at_raw"] == b[1].metadata["published_at_raw"]
    assert a[1].title != b[1].title


def test_v2_genuine_code_vein_relationship_preserves_additional_preorder(tmp_path):
    # Owner contract v2 replaces obsolete blanket suppression; original test and
    # failing receipts are immutable in offline-qualification and P0 archives.
    _, _, second = genuine_pair(tmp_path, 1831, 2031)
    row = second["review_candidates"][0]
    relation = row["announcement_relations"][0]
    assert relation["status"] == "PROPOSED_ANNOUNCEMENT_RELATION"
    assert relation["support"] == "SUPPORTED"
    assert row["identity_status"] == "UNRESOLVED"
    assert row["novelty"] != "NEW_REFERENCE"
    staging.bridge_native(tmp_path, "25284", now=NOW)
    assert staging.bridge_native(tmp_path, "25284", now=NOW)["created"] == 0


@pytest.mark.parametrize("disposition", ["USEFUL", "NOT_USEFUL"])
def test_synthetic_native_postbaseline_qc_readback(tmp_path, disposition):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base, SpecialistLead, SpecialistLeadReview
    from app.services.qc import (
        QueueFilters,
        fetch_lead_history_page,
        fetch_lead_queue_page,
        submit_lead_review,
    )

    state = tmp_path / "owned-synthetic"
    baseline = staging.run_feed(state, "25284", feed(), now=NOW)
    assert baseline["is_baseline"] and baseline["review_candidates"] == []
    staging.bridge_native(state, "25284", now=NOW)
    engine = create_engine("sqlite:///" + str(state / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            assert fetch_lead_queue_page(db, QueueFilters()) == []
        post = staging.run_feed(state, "25284", feed(serial=2), now=NOW)
        assert len(post["review_candidates"]) == 1
        staging.run_feed(state, "25284", feed("スマートウォッチ", serial=3), now=NOW)
        assert staging.bridge_native(state, "25284", now=NOW)["created"] == 1
        with Session(engine) as db:
            queue = fetch_lead_queue_page(db, QueueFilters())
            assert len(queue) == 1
            lead = queue[0]
            assert lead.delivery_state == "gated"
            assert lead.delivery_reason == "EDITORIAL_NOTIFICATIONS_DISABLED"
            assert lead.notified_at is None
            assert lead.verification_status == "UNCONFIRMED"
            submit_lead_review(
                db,
                lead=lead,
                disposition=disposition,
                reason="explicit synthetic offline demonstration",
            )
            db.commit()
        with Session(engine) as db:
            assert fetch_lead_queue_page(db, QueueFilters()) == []
            assert len(fetch_lead_history_page(db, QueueFilters())) == 1
            assert db.query(SpecialistLeadReview).one().disposition == disposition
            assert db.query(SpecialistLead).count() == 2
        assert staging.run_feed(state, "25284", feed(serial=2), now=NOW)["inserted"] == 0
        assert staging.bridge_native(state, "25284", now=NOW)["created"] == 0
        with engine.connect() as db:
            for name in [
                "watches",
                "events",
                "source_observations",
                "release_leads",
                "delivery_receipts",
                "collector_runs",
            ]:
                assert name in Base.metadata.tables, name
                assert db.exec_driver_sql(f'SELECT COUNT(*) FROM "{name}"').scalar_one() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "FRESH"),
        (1, "STALE_PUBLICATION"),
    ],
)
def test_native_exact_72h_boundary(tmp_path, seconds, expected):
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import SpecialistLead
    from app.services.qc import QueueFilters, fetch_lead_queue_page

    published = datetime.fromisoformat("2026-11-06T10:00:00+09:00")
    staging.run_feed(tmp_path, "25284", feed(), now=NOW)
    staging.run_feed(tmp_path, "25284", feed(serial=2), now=NOW)
    staging.bridge_native(tmp_path, "25284", now=published + timedelta(hours=72, seconds=seconds))
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            lead = db.query(SpecialistLead).filter_by(is_baseline=False).one()
            assert lead.editorial_freshness == expected
            assert len(fetch_lead_queue_page(db, QueueFilters())) == int(expected == "FRESH")
            assert lead.delivery_state == "gated"
    finally:
        engine.dispose()


def test_native_jst_timestamp_roundtrip_preserves_instant(tmp_path):
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import SpecialistLead
    from app.services.qc import QueueFilters, fetch_lead_queue_page

    published = datetime.fromisoformat("2026-11-06T10:00:00+09:00")
    clock = published + timedelta(hours=72, seconds=1)
    item = staging.parse_feed(feed(serial=2), "25284")[0]
    assert staging.classify_item(item, now=clock)["freshness"] == "STALE_PUBLICATION"
    staging.run_feed(tmp_path, "25284", feed(), now=NOW)
    staging.run_feed(tmp_path, "25284", feed(serial=2), now=NOW)
    staging.bridge_native(tmp_path, "25284", now=clock)
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            lead = db.query(SpecialistLead).filter_by(is_baseline=False).one()
            # Successor assertion maintenance: predecessor diagnostic retained in P0 evidence.
            assert lead.published_at == datetime(2026, 11, 6, 1)
            assert lead.published_at.tzinfo is None
            assert lead.editorial_freshness == "STALE_PUBLICATION"
            assert fetch_lead_queue_page(db, QueueFilters()) == []
            assert lead.delivery_reason == "EDITORIAL_NOTIFICATIONS_DISABLED"
    finally:
        engine.dispose()


def test_native_utc_stale_boundary_is_gated(tmp_path):
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import SpecialistLead
    from app.services.qc import QueueFilters, fetch_lead_queue_page

    stamp = "2026-11-06T01:00:00+00:00"
    clock = datetime.fromisoformat(stamp) + timedelta(hours=72, seconds=1)
    staging.run_feed(tmp_path, "25284", feed(stamp=stamp), now=NOW)
    staging.run_feed(tmp_path, "25284", feed(serial=2, stamp=stamp), now=NOW)
    staging.bridge_native(tmp_path, "25284", now=clock)
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            lead = db.query(SpecialistLead).filter_by(is_baseline=False).one()
            assert lead.editorial_freshness == "STALE_PUBLICATION"
            assert fetch_lead_queue_page(db, QueueFilters()) == []
            assert lead.delivery_state == "gated"
    finally:
        engine.dispose()


from tests.test_prtimes_jp_staging import NOW, feed


def test_retained_frame_keeps_article_bytes_and_fields():
    from app.collectors import prtimes_jp_staging as staging
    from scripts.qualify_prtimes_jp_offline import retained_frames

    payload = feed()
    frames = retained_frames(payload, "25284")
    assert len(frames) == 1
    frame, item = frames[0]
    assert payload[payload.index(b"<item>") : payload.index(b"</item>") + 7] in frame
    assert staging.parse_feed(frame, "25284") == [item]


def test_replay_preserves_dates_and_separates_counterfactual_clock():
    from scripts.qualify_prtimes_jp_offline import replay_rows

    rows = replay_rows(feed(), "25284", NOW + timedelta(days=100))
    assert rows[0]["published_at_raw"] == "2026-11-06T10:00:00+09:00"
    assert rows[0]["freshness"] == "STALE_PUBLICATION"
    assert rows[0]["counterfactual_publication_plus_1h"]["freshness"] == "FRESH"
    assert rows[0]["counterfactual_publication_plus_1h"]["is_live_detection"] is False
