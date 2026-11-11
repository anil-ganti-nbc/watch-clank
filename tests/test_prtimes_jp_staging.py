"""Finite synthetic semantic tests, NOT historical feed backtests."""

from datetime import UTC, datetime
from importlib.util import find_spec
from xml.sax.saxutils import escape

import pytest

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


def feed(
    title="GARRACK 鬼武者 機械式時計 限定3モデルが登場",
    *,
    serial=1,
    description="明智左馬介、柳生十兵衛、宮本武蔵。型番の記載なし。",
    stamp="2026-11-06T10:00:00+09:00",
    company="25284",
    publisher="株式会社ウエニ貿易",
):
    return (
        f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        f'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<item><title>{escape(title)}</title><link>https://prtimes.jp/main/html/rd/p/"
        f"{serial:09d}.{int(company):09d}.html</link><description>{escape(description)}</description>"
        f"<dc:corp>{publisher}</dc:corp><dc:date>{stamp}</dc:date></item></rdf:RDF>"
    ).encode()


def test_manual_replay_persists_silent_baseline_and_is_idempotent(tmp_path):
    assert find_spec("app.collectors.prtimes_jp_staging") is not None, (
        "manual staging collector missing"
    )
    from app.collectors.prtimes_jp_staging import run_feed

    first = run_feed(tmp_path, "25284", feed(), now=NOW)
    assert first["status"] == "SUCCESS"
    assert first["is_baseline"] is True
    assert first["inserted"] == 1
    assert first["review_candidates"] == []  # even fresh baseline is silent
    second = run_feed(tmp_path, "25284", feed(), now=NOW)
    assert second["is_baseline"] is False
    assert second["inserted"] == 0
    assert second["review_candidates"] == []


@pytest.mark.parametrize(
    "bad",
    [
        b"<broken",
        b"<rss/>",
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>',
        b" " * 2_000_001,
        b'<!DOCTYPE x [<!ENTITY e "payload">]>' + feed(),
        feed(company="68154"),
        feed(publisher="wrong publisher"),
        feed().replace(b"https://prtimes.jp/", b"https://evil.test/"),
        feed().replace(b"<title>", b"<title><b>").replace(b"</title>", b"</b></title>"),
        feed().replace(b"</title>", b"</title><title>ambiguous</title>"),
    ],
    ids=[
        "malformed",
        "wrong-root",
        "empty",
        "oversize",
        "dtd",
        "wrong-company",
        "wrong-publisher",
        "evil-host",
        "nested-field",
        "ambiguous-title",
    ],
)
def test_invalid_feed_holds_without_arming_baseline(tmp_path, bad):
    from app.collectors.prtimes_jp_staging import run_feed

    result = run_feed(tmp_path, "25284", bad, now=NOW)
    assert result["status"] == "HOLD"
    assert result["review_candidates"] == []
    assert run_feed(tmp_path, "25284", feed(), now=NOW)["is_baseline"] is True


# Synthetic scenario summaries, NOT copied historical releases or SKU evidence.
SCENARIOS = [
    (
        "Onimusha",
        "GARRACK 鬼武者 日本製機械式時計 限定3モデルが登場",
        "LAUNCH_REVIEW",
        "CONVENTIONAL",
    ),
    (
        "Code Vein",
        "CODE VEIN II 機械式時計 好評につき追加受注 予約開始",
        "ADDITIONAL_PREORDER",
        "CONVENTIONAL",
    ),
    (
        "FFX",
        "Tendence FINAL FANTASY X 腕時計 コラボ 限定モデル登場",
        "LAUNCH_REVIEW",
        "CONVENTIONAL",
    ),
    (
        "Madoka",
        "魔法少女まどか☆マギカ スマートウォッチとリングウォッチが登場",
        "LAUNCH_REVIEW",
        "MIXED_WEARABLE_UNRESOLVED",
    ),
    ("Tales", "テイルズ オブ エターニア クロノグラフ腕時計が登場", "LAUNCH_REVIEW", "CONVENTIONAL"),
    ("Swatch", "Swatch Destination Art ウォッチ 新作登場", "LAUNCH_REVIEW", "WATCH_UNRESOLVED"),
    ("D51", "Seiko D51 蒸気機関車 腕時計 限定発売", "LAUNCH_REVIEW", "CONVENTIONAL"),
    ("Kaguya", "超かぐや姫！ GARRACK スマートウォッチが登場", "LAUNCH_REVIEW", "SMARTWATCH"),
    ("OnePiece", "ONE PIECE Tendence 腕時計 再販決定", "REISSUE", "CONVENTIONAL"),
    ("Noah", "Timex Noah Boatswain 腕時計 コラボ登場", "LAUNCH_REVIEW", "CONVENTIONAL"),
    (
        "Valhalla",
        "Breitling Aston Martin Valhalla 腕時計 新作登場",
        "LAUNCH_REVIEW",
        "CONVENTIONAL",
    ),
    (
        "StreetFighter",
        "Street Fighter II My Play Watch ゲーミングウォッチ発売",
        "LAUNCH_REVIEW",
        "ELECTRONIC_UNRESOLVED",
    ),
    (
        "CasioEvent",
        "Casio GDG-B100 腕時計 地下イベント開催 プロモーション",
        "EVENT",
        "CONVENTIONAL",
    ),
    ("FamilyMart", "Citizen FamilyMart 腕時計 再発売 第二弾", "REISSUE", "CONVENTIONAL"),
]


@pytest.mark.parametrize("label,title,category,kind", SCENARIOS, ids=[r[0] for r in SCENARIOS])
def test_synthetic_semantic_scenarios_keep_refs_unresolved(label, title, category, kind):
    from app.collectors import prtimes_jp_staging as staging

    assert hasattr(staging, "classify_item"), "Japanese editorial classification missing"
    record = staging.classify_item(staging.parse_feed(feed(title), "25284")[0], now=NOW)
    assert record["category"] == category
    assert record["product_kind"] == kind
    assert record["identity_key"] is None
    assert record["identity_status"] == "UNRESOLVED"
    assert record["title_raw"] == title
    assert record["freshness"] == "FRESH"


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("2026-11-03T12:00:00+00:00", True),  # inclusive 72h
        ("2026-11-03T11:59:59+00:00", False),
        ("2026-11-07T00:00:00+09:00", False),
        ("", False),
        ("2026-11-06", False),
        ("invalid", False),
    ],
)
def test_only_new_fresh_postbaseline_articles_become_review_candidates(tmp_path, stamp, expected):
    from app.collectors.prtimes_jp_staging import run_feed

    run_feed(tmp_path, "25284", feed(), now=NOW)
    payload = feed(serial=2, stamp=stamp)
    result = run_feed(tmp_path, "25284", payload, now=NOW)
    assert result["inserted"] == 1
    assert bool(result["review_candidates"]) is expected
    if expected:
        candidate = result["review_candidates"][0]
        assert candidate["novelty"] == "REVIEW_CANDIDATE"
        assert candidate["identity_key"] is None
    assert run_feed(tmp_path, "25284", payload, now=NOW)["review_candidates"] == []


def test_labeled_maker_reference_uses_existing_identity_and_persistent_crosspublisher_memory(
    tmp_path,
):
    from app.collectors.prtimes_jp_staging import classify_item, parse_feed, run_feed

    raw = "メーカー：Casio 型番：ＧＡ－２１００－１Ａ１ＪＦ"
    record = classify_item(parse_feed(feed(description=raw), "25284")[0], now=NOW)
    assert record["reference_raw"] == "ＧＡ－２１００－１Ａ１ＪＦ"
    assert record["identity_key"] == "casio:ga-2100-1a1"
    run_feed(tmp_path, "25284", feed(description=raw), now=NOW)
    # Establish the second publisher independently, then rediscover the known SKU.
    run_feed(tmp_path, "68154", feed(company="68154", publisher="株式会社 東栄"), now=NOW)
    second = feed(serial=2, company="68154", publisher="株式会社 東栄", description=raw)
    result = run_feed(tmp_path, "68154", second, now=NOW)
    assert result["inserted"] == 1
    assert result["review_candidates"] == []
    assert result["known_product_duplicates"] == 1


@pytest.mark.parametrize(
    "description,kind",
    [
        ("メーカー: Casio 型番: GA-2100-1A1JF", "SMARTWATCH"),
        ("メーカー: Casio 型番: GA-2100-1A1JF 型番: GA-2100-2A", "CONVENTIONAL"),
        ("CODE VEIN II 2026 GARRACK AB123456", "CONVENTIONAL"),
    ],
)
def test_ambiguous_or_wearable_refs_never_become_conventional_identity(description, kind):
    from app.collectors.prtimes_jp_staging import classify_item, parse_feed

    title = "スマートウォッチ 登場" if kind == "SMARTWATCH" else "機械式時計 登場"
    record = classify_item(parse_feed(feed(title, description=description), "25284")[0], now=NOW)
    assert record["identity_key"] is None


def test_conflicting_article_content_holds_atomically_and_exact_duplicates_coalesce(tmp_path):
    import sqlite3
    from contextlib import closing

    from app.collectors.prtimes_jp_staging import run_feed

    original = feed()
    duplicate = original.replace(
        b"</rdf:RDF>",
        original.split(b"<item>")[1].split(b"</rdf:RDF>")[0].join([b"<item>", b""]) + b"</rdf:RDF>",
    )
    assert run_feed(tmp_path, "25284", duplicate, now=NOW)["inserted"] == 1
    changed = feed("別の機械式時計が登場")
    mixed = feed(serial=2).replace(b"</rdf:RDF>", changed[changed.index(b"<item>") :])
    result = run_feed(tmp_path, "25284", mixed, now=NOW)
    assert result["status"] == "HOLD"
    assert result["inserted"] == 0
    with closing(sqlite3.connect(tmp_path / "prtimes-jp-staging.sqlite3")) as db:
        assert db.execute("SELECT count(*) FROM articles").fetchone()[0] == 1
    conflict_dir = tmp_path / "conflict"
    in_feed_conflict = original.replace(b"</rdf:RDF>", changed[changed.index(b"<item>") :])
    assert run_feed(conflict_dir, "25284", in_feed_conflict, now=NOW)["status"] == "HOLD"
    assert run_feed(conflict_dir, "25284", original, now=NOW)["is_baseline"] is True


def test_existing_sentinel_memory_is_read_only_and_suppresses_known_refs(tmp_path):
    import sqlite3
    from contextlib import closing

    from app.collectors import prtimes_jp_staging as staging

    assert hasattr(staging, "read_known_identities"), "read-only Sentinel memory seam missing"
    known_db = tmp_path / "known.sqlite3"
    with closing(sqlite3.connect(known_db)) as db, db:
        db.execute("CREATE TABLE sentinel_identities (identity_key TEXT)")
        db.execute("INSERT INTO sentinel_identities VALUES ('casio:ga-2100-1a1')")
    before = known_db.read_bytes()
    assert staging.read_known_identities(known_db) == {"casio:ga-2100-1a1"}
    state = tmp_path / "staging"
    staging.run_feed(state, "25284", feed(), now=NOW, known_db=known_db)
    candidate = feed(serial=2, description="メーカー: Casio 型番: GA-2100-1A1JF")
    result = staging.run_feed(state, "25284", candidate, now=NOW, known_db=known_db)
    assert result["review_candidates"] == []
    assert result["known_product_duplicates"] == 1
    assert known_db.read_bytes() == before


def policy(tmp_path):
    import hashlib
    import json

    from app.collectors.prtimes_jp_staging import SOURCES

    path = tmp_path / "reviewed-policy.json"
    path.write_text(
        json.dumps(
            {
                "mission": "WATCH-JP-DISCOVERY-01",
                "sources": [
                    {
                        "company_id": company,
                        "publisher": publisher,
                        "verdict": "ACCEPT_BOUNDED_EDITORIAL_STAGING",
                    }
                    for company, publisher in SOURCES.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("status", [200, 301, 403, 429, 500])
def test_live_requires_admission_one_request_no_redirect_retry_and_persistent_stop(
    tmp_path, status
):
    import httpx

    from app.collectors import prtimes_jp_staging as staging

    assert hasattr(staging, "run_live"), "bounded explicit live seam missing"
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(status, content=feed(), headers={"Location": "https://evil.test/"})

    transport = httpx.MockTransport(respond)
    path, digest = policy(tmp_path)
    state = tmp_path / "state"
    assert staging.run_live(state, "25284", now=NOW, transport=transport)["status"] == "HOLD"
    assert not requests
    result = staging.run_live(
        state,
        "25284",
        now=NOW,
        live=True,
        policy_receipt=path,
        policy_sha256=digest,
        transport=transport,
    )
    assert requests == [staging.FEED_URLS["25284"]]
    assert result["status"] == ("SUCCESS" if status == 200 else "HOLD")
    assert result["requests_charged"] == 1
    assert result["review_candidates"] == []
    # Same source exhausted; no retry even across process-equivalent function calls.
    repeated = staging.run_live(
        state,
        "25284",
        now=NOW,
        live=True,
        policy_receipt=path,
        policy_sha256=digest,
        transport=transport,
    )
    assert repeated["status"] == "HOLD"
    assert repeated["requests_charged"] == 0
    assert len(requests) == 1
    if status in (403, 429):
        staging.run_live(
            state,
            "68154",
            now=NOW,
            live=True,
            policy_receipt=path,
            policy_sha256=digest,
            transport=transport,
        )
        assert len(requests) == 1  # origin-wide persistent stop


def test_foreign_state_database_is_refused_without_mutation(tmp_path):
    import sqlite3
    from contextlib import closing

    from app.collectors.prtimes_jp_staging import run_feed

    path = tmp_path / "prtimes-jp-staging.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE precious (id INTEGER)")
    before = path.read_bytes()
    result = run_feed(tmp_path, "25284", feed(), now=NOW)
    assert result["status"] == "HOLD"
    assert path.read_bytes() == before


def test_real_module_entrypoint_replay_is_persistent_and_default_is_disabled(tmp_path):
    import json
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
        str(tmp_path / "cli-state"),
    ]
    disabled = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert disabled.returncode == 2
    assert json.loads(disabled.stdout)["status"] == "HOLD"
    replay = command + ["--replay", str(capture), "--at", NOW.isoformat()]
    first = subprocess.run(replay, capture_output=True, text=True, timeout=20)
    assert first.returncode == 0, first.stderr
    first_result = json.loads(first.stdout)
    assert first_result["is_baseline"] is True
    assert first_result["inserted"] == 1
    assert first_result["review_candidates"] == []
    second = subprocess.run(replay, capture_output=True, text=True, timeout=20)
    assert second.returncode == 0, second.stderr
    second_result = json.loads(second.stdout)
    assert second_result["is_baseline"] is False
    assert second_result["inserted"] == 0
    assert second_result["review_candidates"] == []


def test_review_decision_and_baseline_provenance_are_persisted(tmp_path):
    import json
    import sqlite3
    from contextlib import closing

    from app.collectors.prtimes_jp_staging import run_feed

    run_feed(tmp_path, "25284", feed(), now=NOW)
    run_feed(tmp_path, "25284", feed(serial=2), now=NOW)
    with closing(sqlite3.connect(tmp_path / "prtimes-jp-staging.sqlite3")) as db:
        rows = [
            json.loads(row[0]) for row in db.execute("SELECT evidence FROM articles ORDER BY url")
        ]
    assert rows[0]["is_baseline"] is True
    assert rows[0]["freshness"] == "BASELINE"
    assert rows[0]["novelty"] == "REVIEW_ONLY"
    assert rows[1]["is_baseline"] is False
    assert rows[1]["novelty"] == "REVIEW_CANDIDATE"
    assert rows[1]["observed_at"] == NOW.isoformat()


@pytest.mark.parametrize("failure", ["bad-hash", "missing-admission", "oversize", "network-error"])
def test_live_preservation_controls_fail_closed_and_do_not_arm(tmp_path, failure):
    import httpx

    from app.collectors.prtimes_jp_staging import run_feed, run_live

    path, digest = policy(tmp_path)
    requests = []

    def respond(request):
        requests.append(request)
        if failure == "network-error":
            raise httpx.ConnectError("synthetic offline transport failure")
        return httpx.Response(200, content=b"x" * 2_000_001 if failure == "oversize" else feed())

    if failure == "missing-admission":
        path.write_text("{}", encoding="utf-8")
        import hashlib

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = run_live(
        tmp_path / "state",
        "25284",
        now=NOW,
        live=True,
        policy_receipt=path,
        policy_sha256="0" * 64 if failure == "bad-hash" else digest,
        transport=httpx.MockTransport(respond),
    )
    assert result["status"] == "HOLD"
    assert result["requests_charged"] == (0 if failure in ("bad-hash", "missing-admission") else 1)
    assert len(requests) == (0 if failure in ("bad-hash", "missing-admission") else 1)
    assert run_feed(tmp_path / "state", "25284", feed(), now=NOW)["is_baseline"] is True


def test_namespace_only_fields_ignore_media_prose_and_classify_other_transitions():
    from app.collectors.prtimes_jp_staging import classify_item, parse_feed

    payload = feed("衣服の発売", description="衣服の告知").replace(
        b"</item>", '<media xmlns="urn:media"><title>腕時計</title></media></item>'.encode()
    )
    assert classify_item(parse_feed(payload, "25284")[0], now=NOW)["category"] == "NON_WATCH"
    for title, expected in [("腕時計 再入荷", "RESTOCK"), ("腕時計 予約開始", "PREORDER")]:
        assert classify_item(parse_feed(feed(title), "25284")[0], now=NOW)["category"] == expected
