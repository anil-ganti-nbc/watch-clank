"""Offline native bridge contracts; no production database or acquisition."""

import json
import os
from pathlib import Path

import pytest

from app.collectors import prtimes_jp_staging as staging
from tests.test_prtimes_jp_staging import NOW, feed


def private_capture_items(name, company):
    """Unbound optional captures skip; explicit broken bindings must fail."""
    directory = os.environ.get("PRTIMES_JP_CAPTURE_DIR")
    if directory is None:
        pytest.skip("private RSS capture absent: NOT QUALIFIED; set PRTIMES_JP_CAPTURE_DIR")
    if not directory.strip():
        raise ValueError("empty PRTIMES_JP_CAPTURE_DIR binding")
    return staging.parse_feed((Path(directory) / name).read_bytes(), company)


@pytest.mark.parametrize(
    "title,description,expected",
    [
        (
            "SWATCH DESTINATION ART: 時というレンズを通して日本を讃える",
            "[スウォッチグループジャパン株式会社]\nSwatch Destination Artは、その土地のアーティストとのコラボレーションによって生み出される作品。",
            "WATCH_UNRESOLVED",
        ),
        ("TIMEX Atelier 最新作、38mm GMTの全6型を発売", "", "WATCH_UNRESOLVED"),
        ("TIMEX ブランド紹介", "", "NON_WATCH"),
        ("TIMEX GMT 交換ストラップ発売", "", "NON_WATCH"),
        ("TIMEX GMT サーフ映画公開", "", "NON_WATCH"),
        (
            "FAMILY TIES：サーフアイコンの旅",
            "[スウォッチグループジャパン株式会社]\nサーファーと父親の映画。",
            "NON_WATCH",
        ),
        (
            "スウォッチのサーフ映画公開",
            "[スウォッチグループジャパン株式会社]\n親子の旅。",
            "NON_WATCH",
        ),
        (
            "Destination Art 新作登場",
            "[スウォッチグループジャパン株式会社]\nアートを表現するウォッチを発売。",
            "WATCH_UNRESOLVED",
        ),
        ("Swatch Destination Art ウォッチ新作発売", "", "WATCH_UNRESOLVED"),
    ],
)
def test_primary_product_signal_not_publisher_or_brand(tmp_path, title, description, expected):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Event, SpecialistLead, Watch
    from app.services.qc import QueueFilters, fetch_lead_queue_page

    company = "46261"
    publisher = staging.SOURCES[company]
    item_payload = feed(
        title, description=description, serial=2, company=company, publisher=publisher
    )
    item = staging.parse_feed(item_payload, company)[0]
    record = staging.classify_item(item, now=NOW)
    assert record["product_kind"] == expected
    assert record["identity_status"] == "UNRESOLVED"
    assert item.metadata["description_raw"] == description
    baseline = staging.run_feed(
        tmp_path, company, feed(company=company, publisher=publisher), now=NOW
    )
    assert baseline["review_candidates"] == []
    post = staging.run_feed(tmp_path, company, item_payload, now=NOW)
    assert len(post["review_candidates"]) == int(expected != "NON_WATCH")
    staging.bridge_native(tmp_path, company, now=NOW)
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            queue = fetch_lead_queue_page(db, QueueFilters())
            assert len(queue) == int(expected != "NON_WATCH")
            if queue:
                assert queue[0].lead_type == "EDITORIAL_MENTION"
                assert queue[0].delivery_reason == "EDITORIAL_NOTIFICATIONS_DISABLED"
            assert db.query(Watch).count() == db.query(Event).count() == 0
            assert db.query(SpecialistLead).filter_by(source_url=item.url).count() == int(
                expected != "NON_WATCH"
            )
    finally:
        engine.dispose()


def test_capture_binding_absent_is_explicit_skip(monkeypatch):
    monkeypatch.delenv("PRTIMES_JP_CAPTURE_DIR", raising=False)
    with pytest.raises(pytest.skip.Exception, match="NOT QUALIFIED"):
        private_capture_items("page-8.bin", "25284")


@pytest.mark.parametrize("mode", ["missing", "malformed", "wrong-publisher"])
def test_configured_capture_errors_fail_not_skip(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("PRTIMES_JP_CAPTURE_DIR", str(tmp_path))
    if mode != "missing":
        (tmp_path / "page-8.bin").write_bytes(
            b"broken XML" if mode == "malformed" else feed(publisher="wrong publisher")
        )
    with pytest.raises((ValueError, FileNotFoundError, staging.ET.ParseError)):
        private_capture_items("page-8.bin", "25284")


def test_real_capture_clock_brand_reissue():
    item = next(
        x
        for x in private_capture_items("page-8.bin", "25284")
        if x.url.endswith("000002003.000025284.html")
    )
    result = staging.classify_item(item, now=NOW)
    assert result["category"] == "REISSUE"
    assert result["product_kind"] == "WATCH_UNRESOLVED"
    assert result["identity_key"] is None


@pytest.mark.parametrize(
    "title",
    [
        "掛け時計ブランドの復刻",
        "置き時計ブランド新作",
        "時計ブランドの交換ベルト再販",
        "時計ブランドの収納ケース再販",
        "時計修理サービス開始",
    ],
)
def test_clock_brand_noise_not_watch(title):
    result = staging.classify_item(
        staging.parse_feed(feed(title, description=""), "25284")[0], now=NOW
    )
    assert result["product_kind"] == "NON_WATCH"


@pytest.mark.parametrize("company", list(staging.SOURCES))
def test_native_baseline_candidate_qc_and_retry(tmp_path, company):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Event, SpecialistLead, SpecialistLeadReview, Watch
    from app.services.qc import (
        QueueFilters,
        fetch_lead_history_page,
        fetch_lead_queue_page,
        submit_lead_review,
    )

    assert hasattr(staging, "bridge_native"), "missing native staging bridge"
    state = tmp_path / "isolated"
    payload = feed(company=company, publisher=staging.SOURCES[company])
    staging.run_feed(state, company, payload, now=NOW)
    first = staging.bridge_native(state, company, now=NOW)
    assert first["created"] == 1
    engine = create_engine("sqlite:///" + str(state / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            lead = db.query(SpecialistLead).one()
            assert lead.is_baseline and lead.editorial_freshness == "BASELINE"
            assert fetch_lead_queue_page(db, QueueFilters()) == []
        staging.run_feed(
            state,
            company,
            feed(serial=2, company=company, publisher=staging.SOURCES[company]),
            now=NOW,
        )
        assert staging.bridge_native(state, company, now=NOW)["created"] == 1
        assert staging.bridge_native(state, company, now=NOW)["created"] == 0
        with Session(engine) as db:
            queue = fetch_lead_queue_page(db, QueueFilters())
            assert len(queue) == 1
            lead = queue[0]
            assert lead.reference_candidates == []
            assert lead.source_type == "ISSUER_PRESS_RELEASE"
            assert lead.verification_status == "UNCONFIRMED"
            assert lead.delivery_state == "gated"
            assert lead.delivery_reason == "EDITORIAL_NOTIFICATIONS_DISABLED"
            assert lead.notified_at is None
            assert json.loads(lead.notes)["publisher_raw"] == staging.SOURCES[company]
            assert db.query(Watch).count() == db.query(Event).count() == 0
            submit_lead_review(db, lead=lead, disposition="USEFUL", reason="staging operator test")
            db.commit()
            assert fetch_lead_queue_page(db, QueueFilters()) == []
            assert len(fetch_lead_history_page(db, QueueFilters())) == 1
            assert db.query(SpecialistLeadReview).count() == 1
    finally:
        engine.dispose()


def test_cli_native_bridge_is_silent_and_idempotent(tmp_path):
    import subprocess
    import sys

    capture = tmp_path / "synthetic.xml"
    capture.write_bytes(feed())
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-B",
        "-m",
        "app.collectors.prtimes_jp_staging",
        "--company",
        "25284",
        "--state-dir",
        str(tmp_path / "isolated"),
        "--replay",
        str(capture),
        "--at",
        NOW.isoformat(),
        "--native-staging",
    ]
    for expected in [1, 0]:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf8", timeout=20
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["native_staging"]["created"] == expected


@pytest.mark.parametrize(
    "category_title",
    ["腕時計 復刻", "腕時計 再入荷", "腕時計 イベント", "腕時計 追加予約", "腕時計 予約"],
)
def test_nonlaunch_and_smart_native_separation(tmp_path, category_title):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import SpecialistLead

    staging.run_feed(tmp_path, "25284", feed(), now=NOW)
    staging.run_feed(
        tmp_path,
        "25284",
        feed(category_title, serial=2, description="メーカー: Casio 型番: AB-123"),
        now=NOW,
    )
    staging.run_feed(tmp_path, "25284", feed("スマートウォッチ", serial=3), now=NOW)
    staging.bridge_native(tmp_path, "25284", now=NOW)
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            leads = db.query(SpecialistLead).all()
            assert len(leads) == 2
            assert all(x.lead_type != "POSSIBLE_NEW_REFERENCE" for x in leads)
    finally:
        engine.dispose()


def test_known_watch_maker_reference_is_readonly(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base, Watch

    known = tmp_path / "known.sqlite3"
    engine = create_engine("sqlite:///" + str(known))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            Watch(
                manufacturer="Casio",
                brand="Casio",
                reference_raw="AB-123",
                reference_canonical="AB-123",
            )
        )
        db.commit()
    engine.dispose()
    before = known.read_bytes()
    state = tmp_path / "isolated"
    staging.run_feed(state, "25284", feed(), now=NOW, known_db=known)
    result = staging.run_feed(
        state,
        "25284",
        feed("腕時計 新作", serial=2, description="メーカー: Casio 型番: AB-123"),
        now=NOW,
        known_db=known,
    )
    assert result["known_product_duplicates"] == 1
    assert result["review_candidates"] == []
    assert known.read_bytes() == before


def test_delayed_bridge_rechecks_canonical_freshness(tmp_path):
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import SpecialistLead
    from app.services.qc import QueueFilters, fetch_lead_queue_page

    staging.run_feed(tmp_path, "25284", feed(), now=NOW)
    staging.run_feed(tmp_path, "25284", feed(serial=2), now=NOW)
    staging.bridge_native(tmp_path, "25284", now=NOW + timedelta(days=4))
    engine = create_engine("sqlite:///" + str(tmp_path / "prtimes-jp-native-staging.sqlite3"))
    try:
        with Session(engine) as db:
            assert fetch_lead_queue_page(db, QueueFilters()) == []
            lead = db.query(SpecialistLead).filter_by(is_baseline=False).one()
            assert lead.editorial_freshness == "STALE_PUBLICATION"
    finally:
        engine.dispose()


def test_foreign_native_database_is_never_adopted(tmp_path):
    import sqlite3
    from contextlib import closing

    path = tmp_path / "prtimes-jp-native-staging.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE precious (id INTEGER)")
    before = path.read_bytes()
    staging.run_feed(tmp_path, "25284", feed(), now=NOW)
    with pytest.raises(ValueError, match="foreign/unmarked"):
        staging.bridge_native(tmp_path, "25284", now=NOW)
    assert path.read_bytes() == before
