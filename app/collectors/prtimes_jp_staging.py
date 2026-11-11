"""Manual-only PR TIMES editorial staging. Not registered with the production pipeline."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.collectors.base import DiscoveredItem
from app.collectors.newsroom_snapshot import assess, load_snapshot

RSS = "{http://purl.org/rss/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"


MAX_BYTES = 2_000_000
SOURCES = {
    "25284": "株式会社ウエニ貿易",
    "68154": "株式会社 東栄",  # NOT 東映
    "46261": "スウォッチグループジャパン株式会社",
}
FEED_URLS = {
    company: f"https://prtimes.jp/companyrdf.php?company_id={company}" for company in SOURCES
}


def parse_feed(payload: bytes, company: str) -> list[DiscoveredItem]:
    if company not in SOURCES or not payload or len(payload) > MAX_BYTES:
        raise ValueError("unadmitted source or XML byte bound")
    text = payload.decode("utf-8-sig", errors="strict")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I) or "\x00" in text:
        raise ValueError("DTD/entities/non-UTF8 XML refused")
    root = ET.fromstring(text)
    if root.tag != "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF":
        raise ValueError("expected RSS1 RDF")
    nodes = root.findall(RSS + "item")
    if not 1 <= len(nodes) <= 250:
        raise ValueError("unexpected empty/oversized item count")
    items = []
    for node in nodes:
        fields = {}
        for name, tag in [
            ("title_raw", RSS + "title"),
            ("url", RSS + "link"),
            ("description_raw", RSS + "description"),
            ("publisher_raw", DC + "corp"),
            ("published_at_raw", DC + "date"),
        ]:
            matches = node.findall(tag)
            if len(matches) > 1 or any(len(match) for match in matches):
                raise ValueError("ambiguous/nested field")
            fields[name] = matches[0].text or "" if matches else ""
        if not fields["title_raw"].strip() or fields["publisher_raw"] != SOURCES[company]:
            raise ValueError("missing title or publisher mismatch")
        if not re.fullmatch(
            r"https://prtimes\.jp/main/html/rd/p/[0-9]{9}\." + f"{int(company):09d}" + r"\.html",
            fields["url"],
        ):
            raise ValueError("article URL/publisher scope mismatch")
        url, title = fields.pop("url"), fields.pop("title_raw")
        items.append(DiscoveredItem(url=url, title=title, metadata=fields))
    unique = {}
    for item in items:
        if item.url in unique and unique[item.url] != item:
            raise ValueError("conflicting duplicate article in feed")
        unique[item.url] = item
    return list(unique.values())


def classify_item(item: DiscoveredItem, *, now: datetime) -> dict:
    from app.services.freshness import (
        classify_lead_freshness,
        publication_timestamp_is_usable,
    )

    title = unicodedata.normalize("NFKC", item.title or "")
    description = item.metadata["description_raw"]
    publisher_prefix = f"[{item.metadata['publisher_raw']}]"
    if description.startswith(publisher_prefix):
        # Attribution is provenance, not evidence of the article's product subject.
        description = description[len(publisher_prefix) :]
    blob = title + "\n" + unicodedata.normalize("NFKC", description)
    # The Japanese brand name contains ウォッチ; it is not a product noun.
    subject = re.sub(r"スウォッチ|\bswatch\b", "", blob, flags=re.I)
    timex_gmt_release = (
        re.search(r"\btimex\b|タイメックス", title, re.I)
        and re.search(r"(?<![A-Za-z])GMT(?![A-Za-z])", title, re.I)
        and re.search(r"新作|新型", title)
        and re.search(r"\d+\s*(?:型|モデル).*発売", title)
        and not re.search(r"ベルト|ストラップ|収納|ケース|修理|アクセサリ|映画|映像|企業", blob)
    )
    swatch_time_art = (
        re.search(r"\bswatch\b|スウォッチ", title, re.I)
        and re.search(r"時(?:という|を|の)", title)
        and re.search(r"アート|作品", description)
        and "アーティスト" in description
        and "コラボレーション" in description
        and "生み出" in description
        and not re.search(r"映画|映像|サーフ|企業|ストラップ|ベルト", blob)
    )
    category = "LAUNCH_REVIEW"
    for pattern, label in [
        (r"追加受注|追加予約|再受注", "ADDITIONAL_PREORDER"),
        (r"再販|再発売|復刻|リイシュー", "REISSUE"),
        (r"再入荷|入荷再開|在庫復活", "RESTOCK"),
        (r"イベント|ポップアップ|ツアー|プロモーション|キャンペーン|展示会", "EVENT"),
        (r"予約|受注", "PREORDER"),
    ]:
        if re.search(pattern, title):
            category = label
            break
    smart = bool(re.search(r"スマートウォッチ|smart\s*watch", blob, re.I))
    ring = "リングウォッチ" in blob
    if smart and ring:
        kind = "MIXED_WEARABLE_UNRESOLVED"
    elif smart:
        kind = "SMARTWATCH"
    elif ring:
        kind = "RING_WATCH_UNRESOLVED"
    elif re.search(r"ゲーミング|gaming|my play watch", blob, re.I):
        kind = "ELECTRONIC_UNRESOLVED"
    elif re.search(r"腕時計|機械式時計|アナログ|クオーツ", blob):
        kind = "CONVENTIONAL"
    elif re.search(r"時計ブランド", blob) and not re.search(
        r"掛け時計|掛時計|置き時計|置時計|壁掛|卓上|ベルト|ストラップ|収納|ケース|修理|アクセサリ",
        blob,
    ):
        # A clock/watch brand alone is not evidence of a conventional wristwatch.
        kind = "WATCH_UNRESOLVED"
    elif (
        timex_gmt_release
        or swatch_time_art
        or re.search(r"ウォッチ|\bwatch(?:es)?\b", subject, re.I)
    ):
        kind = "WATCH_UNRESOLVED"
    else:
        kind, category = "NON_WATCH", "NON_WATCH"
    # Primary retail/event claims are not launches merely because their abstract
    # mentions a watch audience or stocked products. A real product release in
    # the primary claim remains eligible, including releases at pop-up events.
    primary_watch = re.sub(r"スウォッチ|\bswatch\b", "", title, flags=re.I)
    primary_release = re.search(r"腕時計|機械式時計|ウォッチ|\bwatch(?:es)?\b", primary_watch, re.I) and re.search(
        r"発売|新作|新型|登場|予約|受注|再入荷|復刻", title
    )
    retail_or_event = re.search(r"(?:ストア|店舗|新店).*オープン|ポップアップストア|リニューアルオープン", title) or (
        re.search(r"開催", description) and re.search(r"紹介|展示|体験", description)
    )
    if retail_or_event and not primary_release and not swatch_time_art:
        kind, category = "NON_WATCH", "NON_WATCH"
    raw_stamp = item.metadata["published_at_raw"]
    try:
        published = datetime.fromisoformat(raw_stamp) if "T" in raw_stamp else None
        if published is not None and published.tzinfo is None:
            published = None
    except ValueError:
        published = None
    freshness = classify_lead_freshness(
        source_type="PRESS_RELEASE",
        ingestion_method="feed",
        is_baseline=False,
        published_at=published,
        discovered_at=now,
        now=now,
        window_hours=72,
    ).state
    if published is not None and not publication_timestamp_is_usable(
        published_at=published,
        observed_at=now,
    ):
        freshness = "FUTURE_TIMESTAMP"
    identity = None
    raw_refs = re.findall(
        r"(?:型番|品番)\s*[:：]\s*([^\s、。;；<>「」『』]{1,64})",
        (item.title or "") + "\n" + item.metadata["description_raw"],
    )
    makers = re.findall(r"(?:メーカー|ブランド)\s*:\s*([A-Za-z]+)\b", blob)
    if kind == "CONVENTIONAL" and len(raw_refs) == 1 and len(makers) == 1:
        ref = unicodedata.normalize("NFKC", raw_refs[0])
        # Only explicitly labelled maker/ref pairs; no word/character-name extraction.
        if re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", ref) and re.search(r"[0-9]", ref):
            from app.sentinel.identity import build_identity

            identity = build_identity(makers[0], reference_raw=ref, url=item.url)
    return {
        "url": item.url,
        "title_raw": item.title,
        "title_normalized": title,
        **item.metadata,
        "category": category,
        "product_kind": kind,
        "identity_status": "EVIDENCED" if identity else "UNRESOLVED",
        "identity_key": identity.identity_key if identity else None,
        "reference_raw": raw_refs[0] if identity else None,
        "reference_canonical": identity.reference_canonical if identity else None,
        "manufacturer": identity.manufacturer if identity else None,
        "freshness": freshness,
        "novelty": "REVIEW_ONLY",
    }


def read_known_identities(path: Path) -> set[str]:
    """Existing Sentinel API over a physically read-only explicit database; no ambient settings."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool

    from app.sentinel.store import SentinelStore

    uri = Path(path).resolve().as_uri() + "?mode=ro"
    engine = create_engine(
        "sqlite://", creator=lambda: sqlite3.connect(uri, uri=True), poolclass=NullPool
    )
    try:
        with Session(engine) as session:
            keys = SentinelStore(session).known_identity_keys()
            # Exact maker + complete reference only; no family/fuzzy correlation
            # and no mutation or confirmation of the primary issuer lead.
            from sqlalchemy import inspect

            from app.models import Watch
            from app.sentinel.identity import build_identity

            if inspect(engine).has_table("watches"):
                for watch in session.query(Watch).all():
                    for reference in (watch.reference_raw, watch.reference_canonical):
                        identity = build_identity(
                            watch.manufacturer, reference_raw=reference, url=""
                        )
                        keys.add(identity.identity_key)
            return keys
    finally:
        engine.dispose()


def _raw_digest(record: dict) -> str:
    facts = {name: record[name] for name in (
        "title_raw", "description_raw", "publisher_raw", "published_at_raw"
    )}
    return hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _propose_relation(earlier: dict, later: dict) -> dict | None:
    """Family-level editorial evidence only, never a SKU or duplicate assertion."""
    if earlier["company_id"] != later["company_id"] or earlier["url"] == later["url"]:
        return None
    if any(row["product_kind"] == "NON_WATCH" for row in (earlier, later)):
        return None
    try:
        dates = [datetime.fromisoformat(row["published_at_raw"]) for row in (earlier, later)]
        if any(date.tzinfo is None for date in dates) or dates[0] > dates[1]:
            return None
    except ValueError:
        return None
    event = re.search(r"追加受注|追加予約|再受注|再販|再発売|復刻|リイシュー|再入荷|予約|受注", later["title_raw"])
    if not event:
        return None
    # Require the exact quoted subject, not a common unquoted watch word.
    def anchors(row):
        title = row["title_raw"]
        families = re.findall(r"『([^『』]{4,80})』", title)
        return {literal for literal in (families or re.findall(r"「([^「」]{4,80})」", title))
                if unicodedata.normalize("NFKC", literal).casefold() not in {"watch", "腕時計", "ウォッチ"}}
    shared = anchors(earlier) & anchors(later)
    if not shared:
        return None
    anchor = sorted(shared, key=lambda value: (-len(value), value))[0]
    return {
        "schema_version": 1, "status": "PROPOSED_ANNOUNCEMENT_RELATION",
        "support": "SUPPORTED", "identity_effect": "NONE", "anchor": anchor,
        "event_literal": event.group(),
        "records": [{"url": row["url"], "company_id": row["company_id"],
                     "published_at_raw": row["published_at_raw"], "raw_sha256": _raw_digest(row),
                     "title_passage": row["title_raw"], "description_passage": row["description_raw"]}
                    for row in (earlier, later)],
    }


def _validated_records(db, company):
    records = []
    for url, evidence, digest in db.execute("SELECT url,evidence,digest FROM articles WHERE company=? ORDER BY rowid", (company,)):
        row = json.loads(evidence)
        if row["url"] != url or row["company_id"] != company or _raw_digest(row) != digest:
            raise ValueError("contradictory retained raw evidence")
        # Validate publisher/URL/raw-field facts with the actual parser contract.
        if row["publisher_raw"] != SOURCES[company] or not re.fullmatch(
            r"https://prtimes\.jp/main/html/rd/p/[0-9]{9}\." + f"{int(company):09d}" + r"\.html", url
        ):
            raise ValueError("invalid retained source attribution")
        records.append(row)
    indexed = {row["url"]: row for row in records}
    for earlier, later, evidence in db.execute("SELECT earlier_url,later_url,evidence FROM announcement_relations WHERE company=?", (company,)):
        relation = json.loads(evidence)
        if earlier not in indexed or later not in indexed or relation != _propose_relation(indexed[earlier], indexed[later]):
            raise ValueError("malformed or contradictory announcement relation")
        indexed[later].setdefault("announcement_relations", [])
        if relation not in indexed[later]["announcement_relations"]:
            indexed[later]["announcement_relations"].append(relation)
    for row in records:
        coverage = row.get("newsroom_coverage")
        if coverage is not None and coverage != assess(row, row.get("coverage_input"), now=datetime.fromisoformat(coverage["as_of"])):
            raise ValueError("contradictory retained coverage annotation")
        annotation = db.execute("SELECT evidence FROM coverage_annotations WHERE company=? AND url=?", (company, row["url"])).fetchone()
        if annotation:
            annotation = json.loads(annotation[0])
            if not isinstance(annotation, dict) or set(annotation) != {"newsroom_coverage", "coverage_input"}:
                raise ValueError("invalid retained coverage annotation fields")
            if annotation["newsroom_coverage"] != assess(row, annotation["coverage_input"], now=datetime.fromisoformat(annotation["newsroom_coverage"]["as_of"])):
                raise ValueError("contradictory retained coverage annotation")
            row.update(annotation)
        for relation in row.get("announcement_relations", []):
            pair = relation["records"]
            expected = _propose_relation(indexed[pair[0]["url"]], row)
            stored = db.execute("SELECT evidence FROM announcement_relations WHERE company=? AND earlier_url=? AND later_url=?", (company, pair[0]["url"], row["url"])).fetchone()
            if expected != relation or not stored or json.loads(stored[0]) != relation:
                raise ValueError("unbacked announcement annotation")
    return records


def connect_state(state_dir: Path) -> sqlite3.Connection:
    state_dir = Path(state_dir).resolve()
    if "data" in {part.lower() for part in state_dir.parts}:
        raise ValueError("production/data paths refused")
    path = state_dir / "prtimes-jp-staging.sqlite3"
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as existing:
            if existing.execute("PRAGMA application_id").fetchone()[0] != 731402:
                raise ValueError("foreign/unmarked state database refused")
    state_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA application_id=731402")
        db.execute("CREATE TABLE IF NOT EXISTS sources (company TEXT PRIMARY KEY)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS articles "
            "(company TEXT, url TEXT, evidence TEXT, digest TEXT, PRIMARY KEY(company,url))"
        )
        db.execute("CREATE TABLE IF NOT EXISTS products (identity_key TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE IF NOT EXISTS coverage_annotations (company TEXT, url TEXT, evidence TEXT NOT NULL, PRIMARY KEY(company,url))")
        db.execute("CREATE TABLE IF NOT EXISTS announcement_relations (company TEXT, earlier_url TEXT, later_url TEXT, evidence TEXT NOT NULL, PRIMARY KEY(company,earlier_url,later_url))")
        db.execute("CREATE TABLE IF NOT EXISTS requests (company TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE IF NOT EXISTS stops (origin TEXT PRIMARY KEY, reason TEXT)")
        return db
    except Exception:
        db.close()
        raise


def run_feed(
    state_dir: Path, company: str, payload: bytes, *, now: datetime, known_db: Path | None = None,
    coverage_snapshot: Path | None = None, coverage_sha256: str | None = None
) -> dict:
    try:
        coverage = load_snapshot(coverage_snapshot, coverage_sha256)
        return _stage_feed(state_dir, company, payload, now=now, known_db=known_db, coverage=coverage)
    except Exception as exc:
        return {
            "company_id": company,
            "status": "HOLD",
            "error": str(exc),
            "inserted": 0,
            "review_candidates": [],
        }


def _stage_feed(
    state_dir: Path, company: str, payload: bytes, *, now: datetime, known_db: Path | None = None,
    coverage=None
) -> dict:
    known_keys = read_known_identities(known_db) if known_db is not None else set()
    items = parse_feed(payload, company)
    with closing(connect_state(state_dir)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        previous_records = _validated_records(db, company)
        digests = {}
        for item in items:
            raw = json.dumps(
                {"title_raw": item.title, **item.metadata}, ensure_ascii=False, sort_keys=True
            )
            digest = hashlib.sha256(raw.encode()).hexdigest()
            previous = db.execute(
                "SELECT digest FROM articles WHERE company=? AND url=?", (company, item.url)
            ).fetchone()
            if previous and previous[0] != digest:
                return {
                    "company_id": company,
                    "status": "HOLD",
                    "inserted": 0,
                    "error": "existing article changed; manual review required",
                    "review_candidates": [],
                }
            digests[item.url] = digest
        baseline = (
            db.execute("SELECT 1 FROM sources WHERE company=?", (company,)).fetchone() is None
        )
        inserted = 0
        duplicates = 0
        candidates = []
        for item in items:
            record = classify_item(item, now=now)
            key = record["identity_key"]
            known = bool(
                key
                and (
                    key in known_keys
                    or db.execute("SELECT 1 FROM products WHERE identity_key=?", (key,)).fetchone()
                )
            )
            record.update(is_baseline=baseline, observed_at=now.isoformat(), company_id=company)
            record["model_memory_status"] = "KNOWN_SAME_MODEL" if known else ("UNSEEN_EXACT_MODEL" if key else "UNRESOLVED")
            record["coverage_input"] = coverage
            record["newsroom_coverage"] = assess(record, coverage, now=now)
            if baseline:
                record["freshness"] = "BASELINE"
            candidate = (
                (not known or record["category"] in {"REISSUE", "ADDITIONAL_PREORDER", "PREORDER", "RESTOCK"}
                 # A material preorder can accompany an EVENT presentation label.
                 or re.search(r"予約開始|受注開始", record["title_normalized"]))
                and not baseline
                and record["freshness"] == "FRESH"
                and record["product_kind"] != "NON_WATCH"
            )
            if candidate:
                record["novelty"] = "REVIEW_CANDIDATE"
            record["announcement_relations"] = []
            for earlier in previous_records:
                relation = _propose_relation(earlier, record)
                if relation:
                    record["announcement_relations"].append(relation)
            evidence = json.dumps(record, ensure_ascii=False)
            added = db.execute(
                "INSERT OR IGNORE INTO articles VALUES (?,?,?,?)",
                (company, item.url, evidence, digests[item.url]),
            ).rowcount
            inserted += added
            retained = record if added else next(row for row in previous_records if row["url"] == item.url)
            db.execute("INSERT OR REPLACE INTO coverage_annotations VALUES (?,?,?)", (company, item.url, json.dumps({"coverage_input": coverage, "newsroom_coverage": assess(retained, coverage, now=now)}, ensure_ascii=False)))
            if added:
                for relation in record["announcement_relations"]:
                    db.execute("INSERT INTO announcement_relations VALUES (?,?,?,?)", (company, relation["records"][0]["url"], item.url, json.dumps(relation, ensure_ascii=False, sort_keys=True)))
                previous_records.append(record)
            duplicates += int(bool(added and known))
            if key:
                db.execute("INSERT OR IGNORE INTO products VALUES (?)", (key,))
            if added and candidate:
                candidates.append(record)
        db.execute("INSERT OR IGNORE INTO sources VALUES (?)", (company,))
    return {
        "company_id": company,
        "status": "SUCCESS",
        "is_baseline": baseline,
        "inserted": inserted,
        "known_product_duplicates": duplicates,
        "review_candidates": candidates,
    }


def run_live(
    state_dir: Path,
    company: str,
    *,
    now: datetime,
    live: bool = False,
    policy_receipt: Path | None = None,
    policy_sha256: str | None = None,
    known_db: Path | None = None,
    transport=None,
) -> dict:
    """One admitted GET per company per isolated mission state. No policy refetch, retries or redirects."""
    import httpx

    hold = {"company_id": company, "status": "HOLD", "inserted": 0, "review_candidates": []}
    requests_charged = 0
    try:
        if not live or company not in SOURCES or policy_receipt is None or policy_sha256 is None:
            raise ValueError(
                "acquisition disabled; explicit live and reviewed policy hash required"
            )
        receipt_bytes = Path(policy_receipt).read_bytes()
        if hashlib.sha256(receipt_bytes).hexdigest() != policy_sha256:
            raise ValueError("policy receipt hash mismatch")
        receipt = json.loads(receipt_bytes)
        admitted = [row for row in receipt.get("sources", []) if row.get("company_id") == company]
        if (
            receipt.get("mission") != "WATCH-JP-DISCOVERY-01"
            or len(admitted) != 1
            or admitted[0].get("verdict") != "ACCEPT_BOUNDED_EDITORIAL_STAGING"
            or admitted[0].get("publisher") != SOURCES[company]
        ):
            raise ValueError("source lacks bounded editorial policy admission")
        # Validate explicit memory before spending the only request. Never weaken on unreadable memory.
        if known_db is not None:
            read_known_identities(known_db)
        with closing(connect_state(state_dir)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM stops").fetchone():
                raise ValueError("persistent PR TIMES origin stop")
            if db.execute("SELECT 1 FROM requests WHERE company=?", (company,)).fetchone():
                raise ValueError("persistent per-source request budget exhausted (1)")
            db.execute("INSERT INTO requests VALUES (?)", (company,))
        # Reservation is durable before opening the transport. Count it even when
        # a redirect, failed response or uncertain transport outcome ends in HOLD.
        requests_charged = 1
        # Shared http_util follows redirects and reads ambient Settings: unsuitable for this gate.
        with (
            httpx.Client(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=20,
                headers={
                    "User-Agent": "WatchClank-Editorial-Staging/0.1",
                    "Accept": "application/rdf+xml, application/xml",
                    "Accept-Language": "ja",
                },
            ) as client,
            client.stream("GET", FEED_URLS[company]) as response,
        ):
            if response.status_code in (403, 429):
                with closing(connect_state(state_dir)) as db, db:
                    db.execute(
                        "INSERT OR REPLACE INTO stops VALUES (?,?)",
                        ("prtimes.jp", f"HTTP {response.status_code}"),
                    )
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}; no redirect/retry")
            payload = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(payload) + len(chunk) > MAX_BYTES:
                    raise ValueError("response byte limit exceeded")
                payload.extend(chunk)
        result = run_feed(state_dir, company, bytes(payload), now=now, known_db=known_db)
        result["requests_attempted"] = requests_charged
        result["requests_charged"] = requests_charged
        result["feed_sha256"] = hashlib.sha256(payload).hexdigest()
        return result
    except Exception as exc:
        return {**hold, "error": str(exc), "requests_charged": requests_charged}


def bridge_native(state_dir: Path, company: str, *, now: datetime,
                  coverage_snapshot: Path | None = None, coverage_sha256: str | None = None) -> dict:
    """Copy retained evidence to a marked, isolated native schema; never notify/promote.

    Replays the durable sidecar rather than an ephemeral result, so interrupted
    bridges recover by the native source_url uniqueness seam. No application path
    argument or ambient database Settings is accepted.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool

    from app.models import SpecialistLead
    from app.models.base import Base
    from app.models.specialist_lead import LEAD_DELIVERY_REASON_EDITORIAL_DISABLED
    from app.services.specialist_leads import SpecialistLeadService

    coverage = load_snapshot(coverage_snapshot, coverage_sha256)
    if company not in SOURCES or now.tzinfo is None:
        raise ValueError("admitted company and aware bridge clock required")
    # This clock is also persisted; preserve its instant across SQLite reloads.
    now = now.astimezone(UTC)
    state_dir = Path(state_dir).resolve()
    with closing(connect_state(state_dir)) as sidecar, sidecar:
        records = _validated_records(sidecar, company)
        for record in records:
            selected = coverage if coverage_snapshot is not None else record.get("coverage_input")
            record["coverage_input"] = selected
            record["newsroom_coverage"] = assess(record, selected, now=now)
            sidecar.execute("INSERT OR REPLACE INTO coverage_annotations VALUES (?,?,?)", (company, record["url"], json.dumps({"coverage_input": selected, "newsroom_coverage": record["newsroom_coverage"]}, ensure_ascii=False)))
    path = state_dir / "prtimes-jp-native-staging.sqlite3"
    if path.is_symlink():
        raise ValueError("linked native database refused")
    fresh = not path.exists()
    if not fresh:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA application_id").fetchone()[0] != 731403:
                raise ValueError("foreign/unmarked native database refused")
    engine = create_engine("sqlite:///" + str(path), poolclass=NullPool)
    try:
        if fresh:
            Base.metadata.create_all(engine)
            with engine.begin() as db:
                db.exec_driver_sql("PRAGMA application_id=731403")
        created = 0
        with Session(engine) as session, session.begin():
            service = SpecialistLeadService(session)
            for record in records:
                if record["product_kind"] not in {"CONVENTIONAL", "WATCH_UNRESOLVED"}:
                    continue
                if not record["is_baseline"] and record["novelty"] != "REVIEW_CANDIDATE":
                    continue
                # Reissue/restock/event/preorder are not new-reference assertions.
                lead_type = (
                    "POSSIBLE_NEW_REFERENCE"
                    if record["category"] == "LAUNCH_REVIEW" and record["reference_canonical"]
                    else "EDITORIAL_MENTION"
                )
                outcome = service.ingest_candidate(
                    source_id=f"prtimes_jp_{company}",
                    lead_type=lead_type,
                    title=record["title_raw"],
                    source_url=record["url"],
                    published_at=(
                        record["published_at_raw"]
                        if record["freshness"] in {"FRESH", "BASELINE", "STALE_PUBLICATION"}
                        and "T" in record["published_at_raw"]
                        else None
                    ),
                    reference_candidates=(
                        [record["reference_canonical"]] if record["reference_canonical"] else []
                    ),
                    manufacturer=record["manufacturer"],
                    region="JP",
                    claim_text=record["description_raw"],
                    notes=json.dumps(record, ensure_ascii=False),
                    ingestion_method="feed",
                    force_baseline=record["is_baseline"],
                    observed_at=datetime.fromisoformat(record["observed_at"]),
                    freshness_window_hours=72,
                )
                if not outcome["created"]:
                    from app.collectors.newsroom_snapshot import validate_native_notes
                    lead = session.get(SpecialistLead, outcome["lead_id"])
                    validate_native_notes(lead)
                    lead.notes = json.dumps(record, ensure_ascii=False)
                if outcome["created"]:
                    lead = session.get(SpecialistLead, outcome["lead_id"])
                    from app.services.freshness import classify_lead_freshness

                    freshness = classify_lead_freshness(
                        source_type=lead.source_type,
                        ingestion_method="feed",
                        is_baseline=lead.is_baseline,
                        published_at=lead.published_at,
                        discovered_at=lead.discovered_at,
                        now=now,
                        window_hours=72,
                    )
                    lead.editorial_freshness = freshness.state
                    lead.freshness_reason = freshness.reason
                    lead.freshness_evaluated_at = now
                    service.record_lead_delivery_outcome(
                        lead, "gated", LEAD_DELIVERY_REASON_EDITORIAL_DISABLED
                    )
                    created += 1
        return {"created": created, "database": str(path), "delivery": "EDITORIAL_DISABLED"}
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import UTC

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", required=True, choices=list(SOURCES))
    parser.add_argument("--state-dir", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--replay", type=Path, help="offline captured RSS1 XML; never fetches articles"
    )
    mode.add_argument(
        "--live", action="store_true", help="one admitted GET; NOT production activation"
    )
    parser.add_argument("--policy-receipt", type=Path)
    parser.add_argument("--policy-sha256")
    parser.add_argument("--known-db", type=Path, help="explicit read-only Sentinel identity memory")
    parser.add_argument("--coverage-snapshot", type=Path, help="explicit offline newsroom JSON; no DB or fetch")
    parser.add_argument("--coverage-sha256", help="reviewed SHA256 authenticates provided bytes, not publisher truth")
    parser.add_argument(
        "--native-staging",
        action="store_true",
        help="silent native bridge; fixed isolated application DB only",
    )
    parser.add_argument("--at", help="timezone-aware replay observation clock; forbidden with live")
    args = parser.parse_args(argv)
    try:
        now = datetime.fromisoformat(args.at) if args.at else datetime.now(UTC)
        if now.tzinfo is None or (args.live and args.at):
            raise ValueError("aware observation clock required; live clock cannot be overridden")
        if (args.coverage_snapshot is not None or args.coverage_sha256 is not None) and not args.replay:
            raise ValueError("coverage snapshots are offline replay only")
        if args.replay:
            with args.replay.open("rb") as capture:
                payload = capture.read(MAX_BYTES + 1)
            result = run_feed(
                args.state_dir, args.company, payload, now=now, known_db=args.known_db,
                coverage_snapshot=args.coverage_snapshot, coverage_sha256=args.coverage_sha256
            )
        else:
            result = run_live(
                args.state_dir,
                args.company,
                now=now,
                live=args.live,
                policy_receipt=args.policy_receipt,
                policy_sha256=args.policy_sha256,
                known_db=args.known_db,
            )
        if args.native_staging and result["status"] == "SUCCESS":
            # Native logging is diagnostic stderr; stdout remains one JSON receipt.
            import sys
            from contextlib import redirect_stdout

            with redirect_stdout(sys.stderr):
                result["native_staging"] = bridge_native(args.state_dir, args.company, now=now,
                                                        coverage_snapshot=args.coverage_snapshot, coverage_sha256=args.coverage_sha256)
    except Exception as exc:
        result = {
            "company_id": args.company,
            "status": "HOLD",
            "error": str(exc),
            "inserted": 0,
            "review_candidates": [],
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
