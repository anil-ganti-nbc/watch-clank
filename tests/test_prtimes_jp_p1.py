"""P1 versioned captured-evidence contracts, offline only."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.collectors import prtimes_jp_staging as s
from app.collectors.base import DiscoveredItem
from tests.test_prtimes_jp_staging import NOW, feed


def retained(company, serial):
    from scripts.qualify_prtimes_jp_offline import retained_frames
    directory = os.environ.get("PRTIMES_JP_CAPTURE_DIR")
    if directory is None:
        pytest.skip("private retained capture absent: NOT QUALIFIED")
    if not directory.strip():
        raise ValueError("empty PRTIMES_JP_CAPTURE_DIR binding")
    page = {"25284": 8, "46261": 10}[company]
    rows = retained_frames((Path(directory) / f"page-{page}.bin").read_bytes(), company)
    return next((frame, item) for frame, item in rows if f"/{serial:09d}." in item.url)


def test_absent_capture_binding_is_explicitly_not_qualified(monkeypatch):
    monkeypatch.delenv("PRTIMES_JP_CAPTURE_DIR", raising=False)
    with pytest.raises(pytest.skip.Exception, match="NOT QUALIFIED"):
        retained("25284", 1831)


@pytest.mark.parametrize("binding", ["", " ", "missing"])
def test_invalid_capture_binding_fails_not_skips(monkeypatch, tmp_path, binding):
    monkeypatch.setenv("PRTIMES_JP_CAPTURE_DIR", str(tmp_path / binding) if binding == "missing" else binding)
    with pytest.raises((ValueError, FileNotFoundError)):
        retained("25284", 1831)


@pytest.mark.parametrize("serial,expected", [(272, "NON_WATCH"), (270, "NON_WATCH"), (184, "NON_WATCH"), (273, "WATCH_UNRESOLVED")])
def test_primary_subject_captured(serial, expected):
    _, item = retained("46261", serial)
    assert s.classify_item(item, now=NOW)["product_kind"] == expected


@pytest.mark.parametrize("title,description,expected", [
    ("腕時計の新作を発売、ポップアップで先行販売", "アーティストの腕時計が登場", "CONVENTIONAL"),
    ("創作ラボ、東京へ", "11月に開催。ウォッチファンに可能性を紹介", "NON_WATCH"),
    ("新店舗リニューアルオープン", "時計ブランドのウォッチを展示", "NON_WATCH"),
])
def test_primary_subject_family(title, description, expected):
    item = s.parse_feed(feed(), "25284")[0]
    item = DiscoveredItem(url=item.url, title=title, metadata={**item.metadata, "description_raw": description})
    assert s.classify_item(item, now=NOW)["product_kind"] == expected


@pytest.mark.parametrize("event", ["追加受注", "復刻", "予約開始", "再入荷"])
def test_known_same_model_new_event_retained(tmp_path, event):
    original = feed("腕時計 ブランド: Example 型番: EX-123 発売")
    later = feed(f"腕時計 ブランド: Example 型番: EX-123 {event}", serial=2)
    assert s.run_feed(tmp_path, "25284", original, now=NOW)["review_candidates"] == []
    row = s.run_feed(tmp_path, "25284", later, now=NOW)["review_candidates"][0]
    assert row["model_memory_status"] == "KNOWN_SAME_MODEL"
    assert row["novelty"] == "REVIEW_CANDIDATE"
    assert s.run_feed(tmp_path, "25284", later, now=NOW)["inserted"] == 0
    assert s.run_feed(tmp_path, "25284", feed("腕時計 ブランド: Example 型番: EX-123 発売", serial=3), now=NOW)["review_candidates"] == []


@pytest.mark.parametrize("a,b,related", [(1831, 2031, True), (2000, 2002, True), (2031, 2058, False)])
def test_persisted_relation_and_current_attribution(tmp_path, a, b, related):
    import sqlite3
    from contextlib import closing
    first, _ = retained("25284", a)
    second, item = retained("25284", b)
    clock = datetime.fromisoformat(item.metadata["published_at_raw"]) + timedelta(hours=1)
    s.run_feed(tmp_path, "25284", first, now=clock)
    row = s.run_feed(tmp_path, "25284", second, now=clock)["review_candidates"][0]
    assert bool(row["announcement_relations"]) == related
    assert row["identity_status"] == "UNRESOLVED"
    result = s.bridge_native(tmp_path, "25284", now=clock)
    assert s.bridge_native(tmp_path, "25284", now=clock)["created"] == 0
    if related and row["product_kind"] == "CONVENTIONAL":
        with closing(sqlite3.connect(result["database"])) as db:
            notes = json.loads(db.execute("SELECT notes FROM specialist_leads WHERE source_url=?", (item.url,)).fetchone()[0])
        assert notes["announcement_relations"] == row["announcement_relations"]
        assert notes["title_raw"] == item.title
    if related:
        with closing(s.connect_state(tmp_path)) as db, db:
            count = db.execute("UPDATE announcement_relations SET evidence='{}'").rowcount
            assert count >= 1
            assert db.execute("SELECT evidence FROM announcement_relations").fetchone()[0] == "{}"
        with pytest.raises((ValueError, KeyError)):
            s.bridge_native(tmp_path, "25284", now=clock)
        assert s.run_feed(tmp_path, "25284", second, now=clock)["status"] == "HOLD"


@pytest.mark.parametrize("left,right", [("「watch」腕時計発売", "「watch」腕時計追加受注"), ("『Alpha family』「共有ブランド」腕時計発売", "『Other family』「共有ブランド」腕時計追加受注")])
def test_shared_generic_or_different_family_not_related(tmp_path, left, right):
    s.run_feed(tmp_path, "25284", feed(left), now=NOW)
    row = s.run_feed(tmp_path, "25284", feed(right, serial=2), now=NOW)["review_candidates"][0]
    assert row["announcement_relations"] == []


@pytest.mark.parametrize("event", ["予約開始", "受注開始"])
def test_known_preorder_at_popup_remains_material(tmp_path, event):
    assert s.run_feed(tmp_path, "25284", feed("腕時計 ブランド: Example 型番: EX-123 発売"), now=NOW)["review_candidates"] == []
    later = feed(f"腕時計 ブランド: Example 型番: EX-123 {event}、ポップアップストアで展示", serial=2)
    row = s.run_feed(tmp_path, "25284", later, now=NOW)["review_candidates"][0]
    assert row["category"] == "EVENT"
    assert row["model_memory_status"] == "KNOWN_SAME_MODEL"
    assert row["novelty"] == "REVIEW_CANDIDATE"
    assert s.run_feed(tmp_path, "25284", later, now=NOW)["inserted"] == 0
    assert s.run_feed(tmp_path, "25284", feed("腕時計 ブランド: Example 型番: EX-123 ポップアップストアで展示", serial=3), now=NOW)["review_candidates"] == []
    native = s.bridge_native(tmp_path, "25284", now=NOW)
    assert native["created"] == 2
    assert s.bridge_native(tmp_path, "25284", now=NOW)["created"] == 0


@pytest.mark.parametrize("word", ["watch", "Watch", "WATCH", "wAtCh", "Ｗａｔｃｈ"])
def test_generic_anchor_comparison_is_nfkc_casefolded(tmp_path, word):
    s.run_feed(tmp_path, "25284", feed(f"「{word}」腕時計発売"), now=NOW)
    row = s.run_feed(tmp_path, "25284", feed(f"「{word}」腕時計追加受注", serial=2), now=NOW)["review_candidates"][0]
    assert row["identity_status"] == "UNRESOLVED"
    assert row["announcement_relations"] == []
    assert row["title_raw"] == f"「{word}」腕時計追加受注"


def test_literal_subject_anchor_and_passages_preserved(tmp_path):
    subject = "Ａｌｐｈａ Saga"
    first = f"『{subject}』腕時計発売"
    later = f"『{subject}』腕時計追加受注"
    s.run_feed(tmp_path, "25284", feed(first), now=NOW)
    row = s.run_feed(tmp_path, "25284", feed(later, serial=2), now=NOW)["review_candidates"][0]
    relation = row["announcement_relations"][0]
    assert relation["anchor"] == subject
    assert [r["title_passage"] for r in relation["records"]] == [first, later]
    assert row["identity_status"] == "UNRESOLVED"


def test_popup_preorder_does_not_bypass_smartwatch_native_guard(tmp_path):
    s.run_feed(tmp_path, "25284", feed("スマートウォッチ発売"), now=NOW)
    result = s.run_feed(tmp_path, "25284", feed("スマートウォッチ予約開始、ポップアップストアで展示", serial=2), now=NOW)
    assert result["review_candidates"][0]["product_kind"] == "SMARTWATCH"
    assert s.bridge_native(tmp_path, "25284", now=NOW)["created"] == 0

