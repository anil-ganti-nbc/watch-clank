"""Offline publication awareness. All positive stories are SYNTHETIC TEST_FIXTURE."""
import hashlib
import json
from contextlib import closing
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.collectors import prtimes_jp_staging as s
from app.models import Event, SpecialistLead, Watch
from app.services.qc import QueueFilters, fetch_lead_queue_page
from tests.test_prtimes_jp_staging import NOW, feed


def snapshot_for(payload, *, stories=True):
    record = s.classify_item(s.parse_feed(payload, "25284")[0], now=NOW)
    context = {"article_url": record["url"], "event_scope": record["category"],
               "region": "JP", "manufacturer": record["manufacturer"],
               "reference": record["reference_canonical"]}
    return {"schema_version": 1, "snapshot_id": "SYNTHETIC-TEST-ONLY-1",
            "newsroom": "Notebookcheck", "provenance_kind": "TEST_FIXTURE", "test_only": True,
            "exported_at": NOW.isoformat(), "cutoff": NOW.isoformat(),
            "expires_at": (NOW + timedelta(hours=12)).isoformat(),
            "complete_scope": {"region": "JP", "article_urls": [record["url"]], "verified": True},
            "stories": [{"story_id": "synthetic-story-1", "state": "PUBLISHED",
                         "story_url": "https://notebookcheck.test/synthetic-story-1",
                         "published_at": (NOW - timedelta(hours=1)).isoformat(),
                         "verified_at": NOW.isoformat(), "verification": "VERIFIED",
                         "source_evidence_urls": [record["url"]],
                         "identity_status": "EXACT" if record["identity_key"] else "UNRESOLVED",
                         **context}] if stories else []}


def binding(tmp_path, snapshot):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf8")
    return {"coverage_snapshot": path, "coverage_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_fixture_same_event_native_and_snapshot_change_idempotence(tmp_path):
    payload = feed("腕時計 ブランド: Example 型番: EX-123 追加受注", serial=2)
    s.run_feed(tmp_path / "state", "25284", feed(), now=NOW)
    snap = snapshot_for(payload)
    kwargs = binding(tmp_path, snap)
    result = s.run_feed(tmp_path / "state", "25284", payload, now=NOW, **kwargs)
    assert result["status"] == "SUCCESS"
    row = result["review_candidates"][0]
    assert row["newsroom_coverage"]["status"] == "TEST_ONLY_COVERED_CONTEXT"
    assert row["novelty"] == "REVIEW_CANDIDATE"
    native = s.bridge_native(tmp_path / "state", "25284", now=NOW, **kwargs)
    engine = create_engine("sqlite:///" + native["database"])
    try:
        with Session(engine) as db:
            lead = fetch_lead_queue_page(db, QueueFilters())[0]
            assert json.loads(lead.notes)["newsroom_coverage"] == row["newsroom_coverage"]
        with closing(s.connect_state(tmp_path / "state")) as db:
            before = db.execute("SELECT url,digest,evidence FROM articles ORDER BY url").fetchall()
        snap["snapshot_id"] = "SYNTHETIC-TEST-ONLY-2"
        snap["stories"] = []
        kwargs = binding(tmp_path, snap)
        repeat = s.run_feed(tmp_path / "state", "25284", payload, now=NOW, **kwargs)
        assert repeat["status"] == "SUCCESS" and repeat["inserted"] == 0
        assert repeat["review_candidates"] == []
        assert s.bridge_native(tmp_path / "state", "25284", now=NOW, **kwargs)["created"] == 0
        with closing(s.connect_state(tmp_path / "state")) as db:
            assert db.execute("SELECT url,digest,evidence FROM articles ORDER BY url").fetchall() == before
        with Session(engine) as db:
            lead = fetch_lead_queue_page(db, QueueFilters())[0]
            cov = json.loads(lead.notes)["newsroom_coverage"]
            assert cov["status"] == "NOT_FOUND_IN_PROVIDED_COMPLETE_SCOPE"
            assert cov["test_only"] is True and cov["snapshot_id"] == snap["snapshot_id"]
            assert db.query(SpecialistLead).count() == 2
            assert db.query(Watch).count() == db.query(Event).count() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("field,value", [("article_url", "https://prtimes.jp/main/html/rd/p/000000099.000025284.html"), ("event_scope", "REISSUE"), ("manufacturer", "Casio")])
@pytest.mark.parametrize("reader", ["queue", "history"])
def test_native_qc_refuses_corrupt_derived_source_context(tmp_path, field, value, reader):
    from app.services.qc import fetch_lead_history_page, submit_lead_review
    payload = feed("腕時計 ブランド: Example 型番: EX-123 追加受注", serial=2)
    s.run_feed(tmp_path, "25284", feed(), now=NOW)
    kwargs = binding(tmp_path, snapshot_for(payload))
    s.run_feed(tmp_path, "25284", payload, now=NOW, **kwargs)
    native = s.bridge_native(tmp_path, "25284", now=NOW, **kwargs)
    engine = create_engine("sqlite:///" + native["database"])
    try:
        with Session(engine) as db:
            lead = fetch_lead_queue_page(db, QueueFilters())[0]
            if reader == "history":
                submit_lead_review(db, lead=lead, disposition="USEFUL", reason="SYNTHETIC QA, not publication")
            lead_id = lead.id
            db.commit()
        with closing(__import__("sqlite3").connect(native["database"])) as db, db:
            notes = json.loads(db.execute("SELECT notes FROM specialist_leads WHERE id=?", (lead_id,)).fetchone()[0])
            notes["newsroom_coverage"]["source_context"][field] = value
            assert db.execute("UPDATE specialist_leads SET notes=? WHERE id=?", (json.dumps(notes), lead_id)).rowcount == 1
            assert json.loads(db.execute("SELECT notes FROM specialist_leads WHERE id=?", (lead_id,)).fetchone()[0])["newsroom_coverage"]["source_context"][field] == value
        with Session(engine) as db, pytest.raises(ValueError, match="coverage"):
            (fetch_lead_queue_page if reader == "queue" else fetch_lead_history_page)(db, QueueFilters())
    finally:
        engine.dispose()


@pytest.mark.parametrize("mode", ["partial", "unverified-scope", "draft", "qc-useful", "duplicate", "missing-clock", "unverified-record", "stale-verification", "expired", "stale-cutoff", "wrong-entity"])
def test_uncertain_snapshot_never_clears(tmp_path, mode):
    payload = feed("腕時計 ブランド: Example 型番: EX-123 追加受注", serial=2)
    doc = snapshot_for(payload)
    if mode == "partial":
        doc["complete_scope"] = None
    elif mode == "unverified-scope":
        doc["complete_scope"]["verified"] = False
    elif mode in {"draft", "qc-useful", "duplicate"}:
        doc["stories"][0]["state"] = {"draft": "DRAFT", "qc-useful": "QC_USEFUL", "duplicate": "DUPLICATE"}[mode]
    elif mode == "missing-clock":
        doc["stories"][0]["published_at"] = None
    elif mode == "unverified-record":
        doc["stories"][0]["verification"] = "UNVERIFIED"
    elif mode == "stale-verification":
        doc["stories"][0]["published_at"] = (NOW - timedelta(days=4)).isoformat()
        doc["stories"][0]["verified_at"] = (NOW - timedelta(days=3)).isoformat()
    elif mode == "expired":
        doc["exported_at"] = (NOW - timedelta(days=3)).isoformat()
        doc["cutoff"] = (NOW - timedelta(days=3)).isoformat()
        doc["expires_at"] = (NOW - timedelta(days=2)).isoformat()
        doc["stories"] = []
    elif mode == "stale-cutoff":
        doc["cutoff"] = (NOW - timedelta(days=3)).isoformat()
    elif mode == "wrong-entity":
        doc["stories"][0]["manufacturer"] = "Casio"
    s.run_feed(tmp_path / "state", "25284", feed(), now=NOW)
    row = s.run_feed(tmp_path / "state", "25284", payload, now=NOW, **binding(tmp_path, doc))["review_candidates"][0]
    assert row["newsroom_coverage"]["status"] == "COVERAGE_UNKNOWN"


@pytest.mark.parametrize("mode", ["duplicate-key", "nan", "overflow", "duplicate-id", "schema", "naive", "wrong-hash", "missing-file", "oversize"])
def test_malformed_explicit_config_refuses_before_state(tmp_path, mode):
    doc = snapshot_for(feed())
    if mode == "duplicate-id":
        doc["stories"] *= 2
    elif mode == "schema":
        doc["schema_version"] = 999
    elif mode == "naive":
        doc["exported_at"] = "2026-11-06T12:00:00"
    raw = json.dumps(doc).encode()
    if mode == "duplicate-key":
        raw = raw.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
    elif mode == "nan":
        raw = raw.replace(b'"schema_version": 1', b'"schema_version": NaN')
    elif mode == "overflow":
        raw = raw.replace(b'"schema_version": 1', b'"schema_version": 1e999')
    elif mode == "oversize":
        raw = b" " * 512001
    path = tmp_path / "snapshot.json"
    if mode != "missing-file":
        path.write_bytes(raw)
    result = s.run_feed(tmp_path / "state", "25284", feed(), now=NOW, coverage_snapshot=path,
                        coverage_sha256="0" * 64 if mode == "wrong-hash" else hashlib.sha256(raw).hexdigest())
    assert result["status"] == "HOLD" and result["inserted"] == 0
    assert not (tmp_path / "state").exists()


def test_original_launch_coverage_not_additional_preorder(tmp_path):
    original = feed("腕時計 ブランド: Example 型番: EX-123 発売")
    later = feed("腕時計 ブランド: Example 型番: EX-123 追加受注", serial=2)
    doc = snapshot_for(original)
    doc["complete_scope"]["article_urls"].append(s.parse_feed(later, "25284")[0].url)
    kwargs = binding(tmp_path, doc)
    assert s.run_feed(tmp_path / "state", "25284", original, now=NOW, **kwargs)["review_candidates"] == []
    row = s.run_feed(tmp_path / "state", "25284", later, now=NOW, **kwargs)["review_candidates"][0]
    assert row["model_memory_status"] == "KNOWN_SAME_MODEL"
    assert row["newsroom_coverage"]["status"] == "NOT_FOUND_IN_PROVIDED_COMPLETE_SCOPE"
    assert row["novelty"] == "REVIEW_CANDIDATE"


def test_unresolved_fixture_requires_exact_article_not_family(tmp_path):
    payload = feed("『Alpha family』腕時計追加受注", serial=2)
    s.run_feed(tmp_path / "state", "25284", feed("『Alpha family』腕時計発売"), now=NOW)
    doc = snapshot_for(payload)
    kwargs = binding(tmp_path, doc)
    row = s.run_feed(tmp_path / "state", "25284", payload, now=NOW, **kwargs)["review_candidates"][0]
    assert row["identity_status"] == "UNRESOLVED" and row["identity_key"] is None
    assert row["newsroom_coverage"]["status"] == "TEST_ONLY_COVERED_CONTEXT"
    assert row["announcement_relations"]
    other = feed("『Alpha family』腕時計追加受注", serial=3)
    doc["complete_scope"]["article_urls"].append(s.parse_feed(other, "25284")[0].url)
    row = s.run_feed(tmp_path / "state", "25284", other, now=NOW, **binding(tmp_path, doc))["review_candidates"][0]
    assert row["newsroom_coverage"]["status"] == "NOT_FOUND_IN_PROVIDED_COMPLETE_SCOPE"


@pytest.mark.parametrize("field,value", [("article_url", "https://prtimes.jp/main/html/rd/p/000000099.000025284.html"), ("event_scope", "RESTOCK"), ("reference", "EX-999")])
def test_sidecar_corruption_refused_before_native(tmp_path, field, value):
    payload = feed("腕時計 ブランド: Example 型番: EX-123 追加受注", serial=2)
    s.run_feed(tmp_path, "25284", feed(), now=NOW)
    s.run_feed(tmp_path, "25284", payload, now=NOW, **binding(tmp_path, snapshot_for(payload)))
    url = s.parse_feed(payload, "25284")[0].url
    with closing(s.connect_state(tmp_path)) as db, db:
        note = json.loads(db.execute("SELECT evidence FROM coverage_annotations WHERE url=?", (url,)).fetchone()[0])
        note["newsroom_coverage"]["source_context"][field] = value
        assert db.execute("UPDATE coverage_annotations SET evidence=? WHERE url=?", (json.dumps(note), url)).rowcount == 1
        assert json.loads(db.execute("SELECT evidence FROM coverage_annotations WHERE url=?", (url,)).fetchone()[0])["newsroom_coverage"]["source_context"][field] == value
    with pytest.raises(ValueError, match="coverage"):
        s.bridge_native(tmp_path, "25284", now=NOW)
    assert not (tmp_path / "prtimes-jp-native-staging.sqlite3").exists()


def test_real_manual_cli_fixture_positive_and_repeat(tmp_path):
    import subprocess
    import sys
    state = tmp_path / "state"
    capture = tmp_path / "fixture.xml"
    payload = feed("腕時計 ブランド: Example 型番: EX-123 追加受注", serial=2)
    kwargs = binding(tmp_path, snapshot_for(payload))
    common = [sys.executable, "-X", "utf8", "-B", "-m", "app.collectors.prtimes_jp_staging", "--company", "25284", "--state-dir", str(state), "--replay", str(capture), "--at", NOW.isoformat(), "--native-staging", "--coverage-snapshot", str(kwargs["coverage_snapshot"]), "--coverage-sha256", kwargs["coverage_sha256"]]
    for data, expected in [(feed(), 1), (payload, 1), (payload, 0)]:
        capture.write_bytes(data)
        proc = subprocess.run(common, capture_output=True, text=True, encoding="utf8", timeout=20)
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result["inserted"] == result["native_staging"]["created"] == expected
        if result["review_candidates"]:
            assert result["review_candidates"][0]["newsroom_coverage"]["status"] == "TEST_ONLY_COVERED_CONTEXT"


def test_retained_partial_research_and_captured_pair_unknown(tmp_path):
    import os
    from datetime import datetime
    from pathlib import Path

    from scripts.qualify_prtimes_jp_offline import retained_frames
    root = os.environ.get("PRTIMES_JP_CAPTURE_DIR")
    if root is None:
        pytest.skip("private research/capture absent: NOT QUALIFIED")
    rows = json.loads((Path(root) / "research/benchmark-14.json").read_text(encoding="utf8"))["rows"]
    indexed = [r for r in rows if r["notebookcheck"]]
    assert len(rows) == 14 and len(indexed) == 8
    frames = retained_frames((Path(root) / "page-8.bin").read_bytes(), "25284")
    first, _ = next((f, i) for f, i in frames if "/000001831." in i.url)
    second, item = next((f, i) for f, i in frames if "/000002031." in i.url)
    # Counterfactual replay only, not a historical detection or publication clock.
    clock = datetime.fromisoformat(item.metadata["published_at_raw"]) + timedelta(hours=1)
    doc = snapshot_for(second)
    doc.update(snapshot_id="UNVERIFIED_RETAINED_RESEARCH_PROJECTION", provenance_kind="OWNER_EXPORT", test_only=False,
               exported_at=clock.isoformat(), cutoff=clock.isoformat(), expires_at=(clock + timedelta(hours=12)).isoformat(), complete_scope=None)
    doc["stories"] = [{**doc["stories"][0], "story_id": r["case_id"], "story_url": r["notebookcheck"]["url"],
                       "article_url": r["original_url"], "source_evidence_urls": [r["original_url"] or r["notebookcheck"]["url"]],
                       "published_at": None, "verified_at": None, "verification": "UNVERIFIED",
                       "identity_status": "UNRESOLVED", "manufacturer": None, "reference": None} for r in indexed]
    assert all(r["notebookcheck"]["publication_clock"] is None for r in indexed)
    kwargs = binding(tmp_path, doc)
    baseline = s.run_feed(tmp_path / "state", "25284", first, now=clock, **kwargs)
    assert baseline["status"] == "SUCCESS", baseline
    assert baseline["review_candidates"] == []
    result = s.run_feed(tmp_path / "state", "25284", second, now=clock, **kwargs)
    assert result["status"] == "SUCCESS", result
    row = result["review_candidates"][0]
    assert row["newsroom_coverage"]["status"] == "COVERAGE_UNKNOWN"
    assert row["identity_status"] == "UNRESOLVED" and row["announcement_relations"]
    native = s.bridge_native(tmp_path / "state", "25284", now=clock, **kwargs)
    engine = create_engine("sqlite:///" + native["database"])
    try:
        with Session(engine) as db:
            lead = fetch_lead_queue_page(db, QueueFilters())[0]
            assert json.loads(lead.notes)["newsroom_coverage"]["status"] == "COVERAGE_UNKNOWN"
            assert lead.title == item.title
    finally:
        engine.dispose()


def test_known_memory_does_not_clear_missing_coverage_native(tmp_path):
    baseline = feed("腕時計 ブランド: Example 型番: EX-123 発売")
    later = feed("腕時計 ブランド: Example 型番: EX-123 追加受注", serial=2)
    assert s.run_feed(tmp_path, "25284", baseline, now=NOW)["review_candidates"] == []
    row = s.run_feed(tmp_path, "25284", later, now=NOW)["review_candidates"][0]
    assert row["model_memory_status"] == "KNOWN_SAME_MODEL"
    assert row["newsroom_coverage"]["status"] == "COVERAGE_UNKNOWN"
    result = s.bridge_native(tmp_path, "25284", now=NOW)
    engine = create_engine("sqlite:///" + result["database"])
    try:
        with Session(engine) as db:
            lead = fetch_lead_queue_page(db, QueueFilters())[0]
            assert json.loads(lead.notes)["newsroom_coverage"] == row["newsroom_coverage"]
            assert lead.lead_type == "EDITORIAL_MENTION"
            assert lead.verification_status == "UNCONFIRMED"
            assert lead.notified_at is None
            assert db.query(Watch).count() == db.query(Event).count() == 0
    finally:
        engine.dispose()
