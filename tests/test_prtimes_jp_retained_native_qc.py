"""Private, pinned November 6 captures into isolated native QC; never acquisition.

The second historical article is deliberately *counterfactually* observed at
publication+1h after a one-article baseline. This is a contract test, not a
natural discovery, human gold label, or qualification of sustained collection.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.collectors import prtimes_jp_staging as staging
from app.models import Event, SpecialistLead, Watch
from app.services.qc import QueueFilters, fetch_lead_queue_page
from scripts.qualify_prtimes_jp_offline import retained_frames

# SHA-256 of original captures, as recorded in the retained more-receipts.json.
CAPTURES = {
    "25284": (8, "f3bcdf9bd3012bd6c65baf4ac64d62bd466ccd19c6f6b8d6b1f47141329deb53"),
    "68154": (9, "a2446e34c9baa09fb21ba4762737e97499ce079bd042786afc1894ad7a4c89ef"),
    "46261": (10, "47c82a4640ba526efeb9f0d9f42a6790e31151f8ab33120b6f12afae52dc2ece"),
}


@pytest.mark.parametrize("company", list(CAPTURES))
def test_retained_issuer_to_native_qc_counterfactual(tmp_path, company):
    directory = os.environ.get("PRTIMES_JP_CAPTURE_DIR")
    if directory is None:
        pytest.skip("private original captures absent; real-fixture QC NOT RUN")
    if not directory.strip():
        raise ValueError("empty private capture binding")
    page, expected_hash = CAPTURES[company]
    capture = (Path(directory) / f"page-{page}.bin").read_bytes()
    assert hashlib.sha256(capture).hexdigest() == expected_hash
    frames = retained_frames(capture, company)
    expected_count = {"25284": 200, "68154": 52, "46261": 200}[company]
    assert len(frames) == expected_count
    candidates = []
    noise = []
    for frame, item in frames:
        try:
            published = datetime.fromisoformat(item.metadata["published_at_raw"])
        except ValueError:
            continue
        if published.tzinfo is None:
            continue
        at = published + timedelta(hours=1)
        record = staging.classify_item(item, now=at)
        if record["category"] == "LAUNCH_REVIEW" and record["product_kind"] in {
            "CONVENTIONAL", "WATCH_UNRESOLVED"
        }:
            candidates.append((frame, item, at))
        elif record["product_kind"] == "NON_WATCH":
            noise.append((frame, item, at))
    assert len(candidates) >= 2 and noise
    baseline, selected = candidates[1], candidates[0]
    # A subset of one historic item is the simulated first-run baseline.
    first = staging.run_feed(tmp_path, company, baseline[0], now=selected[2])
    assert first["is_baseline"] and first["review_candidates"] == []
    assert staging.bridge_native(tmp_path, company, now=selected[2])["created"] == 1
    second = staging.run_feed(tmp_path, company, selected[0], now=selected[2])
    assert second["inserted"] == 1
    assert len(second["review_candidates"]) == 1
    assert second["review_candidates"][0]["freshness"] == "FRESH"
    assert staging.bridge_native(tmp_path, company, now=selected[2])["created"] == 1
    assert staging.run_feed(tmp_path, company, selected[0], now=selected[2])["inserted"] == 0
    assert staging.bridge_native(tmp_path, company, now=selected[2])["created"] == 0
    third = staging.run_feed(tmp_path, company, noise[0][0], now=selected[2])
    assert third["review_candidates"] == []
    assert staging.bridge_native(tmp_path, company, now=selected[2])["created"] == 0
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            queue = fetch_lead_queue_page(db, QueueFilters())
            assert len(queue) == 1
            lead = queue[0]
            assert lead.source_id == f"prtimes_jp_{company}"
            assert lead.source_url == selected[1].url
            assert lead.delivery_state == "gated" and lead.notified_at is None
            assert json.loads(lead.notes)["publisher_raw"] == staging.SOURCES[company]
            assert db.query(SpecialistLead).count() == 2
            assert db.query(Watch).count() == db.query(Event).count() == 0
    finally:
        engine.dispose()


def test_full_retained_corpus_is_silent_baseline_not_launches(tmp_path):
    directory = os.environ.get("PRTIMES_JP_CAPTURE_DIR")
    if directory is None:
        pytest.skip("private original captures absent; real-fixture QC NOT RUN")
    if not directory.strip():
        raise ValueError("empty private capture binding")
    observed = datetime.fromisoformat("2026-11-20T16:00:00+00:00")
    # Historical parent baseline was 90/31/200 on its earlier code/capture.
    # This exact corrective 18-file classifier filters Swatch noise; do not
    # silently substitute historical 321 as the new classifier's count.
    expected_leads = {"25284": 91, "68154": 31, "46261": 40}
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        for company, (page, expected_hash) in CAPTURES.items():
            payload = (Path(directory) / f"page-{page}.bin").read_bytes()
            assert hashlib.sha256(payload).hexdigest() == expected_hash
            result = staging.run_feed(tmp_path, company, payload, now=observed)
            assert result["is_baseline"] and result["review_candidates"] == []
            assert result["inserted"] == {"25284": 200, "68154": 52, "46261": 200}[company]
            assert staging.bridge_native(tmp_path, company, now=observed)["created"] == expected_leads[company]
            assert staging.run_feed(tmp_path, company, payload, now=observed)["inserted"] == 0
            assert staging.bridge_native(tmp_path, company, now=observed)["created"] == 0
        with Session(engine) as db:
            assert db.query(SpecialistLead).count() == sum(expected_leads.values())
            assert fetch_lead_queue_page(db, QueueFilters()) == []
            for company, expected in expected_leads.items():
                leads = db.query(SpecialistLead).filter_by(source_id=f"prtimes_jp_{company}").all()
                assert len(leads) == expected
                assert all(x.is_baseline and x.delivery_state == "gated" and x.notified_at is None for x in leads)
                assert all(json.loads(x.notes)["newsroom_coverage"]["status"] == "COVERAGE_UNKNOWN" for x in leads)
            assert db.query(Watch).count() == db.query(Event).count() == 0
    finally:
        engine.dispose()


def test_retained_two_story_relation_is_review_only_not_coverage(tmp_path):
    directory = os.environ.get("PRTIMES_JP_CAPTURE_DIR")
    if directory is None:
        pytest.skip("private original capture absent; real-fixture QC NOT RUN")
    if not directory.strip():
        raise ValueError("empty private capture binding")
    capture = (Path(directory) / "page-8.bin").read_bytes()
    assert hashlib.sha256(capture).hexdigest() == CAPTURES["25284"][1]
    frames = {
        item.url: (frame, item)
        for frame, item in retained_frames(capture, "25284")
    }
    prefix = "https://prtimes.jp/main/html/rd/p/"
    first = frames[f"{prefix}000001831.000025284.html"]
    later = frames[f"{prefix}000002031.000025284.html"]
    observed = datetime.fromisoformat(later[1].metadata["published_at_raw"]) + timedelta(hours=1)
    assert staging.run_feed(tmp_path, "25284", first[0], now=observed)["is_baseline"]
    staged = staging.run_feed(tmp_path, "25284", later[0], now=observed)
    assert len(staged["review_candidates"]) == 1
    record = staged["review_candidates"][0]
    assert record["identity_status"] == "UNRESOLVED"
    assert record["novelty"] == "REVIEW_CANDIDATE"
    assert record["announcement_relations"][0]["status"] == "PROPOSED_ANNOUNCEMENT_RELATION"
    assert record["newsroom_coverage"]["status"] == "COVERAGE_UNKNOWN"
    assert staging.bridge_native(tmp_path, "25284", now=observed)["created"] == 2
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            leads = db.query(SpecialistLead).all()
            assert {lead.source_url for lead in leads} == {first[1].url, later[1].url}
            newer = next(lead for lead in leads if lead.source_url == later[1].url)
            evidence = json.loads(newer.notes)
            assert evidence["announcement_relations"] == record["announcement_relations"]
            assert evidence["newsroom_coverage"]["status"] == "COVERAGE_UNKNOWN"
            assert newer.lead_type == "EDITORIAL_MENTION"
            assert newer.delivery_state == "gated" and newer.notified_at is None
            assert db.query(Watch).count() == db.query(Event).count() == 0
    finally:
        engine.dispose()
