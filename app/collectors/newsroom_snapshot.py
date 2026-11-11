"""Bounded offline Notebookcheck snapshot contract, never a publisher truth oracle.

The reviewed hash authenticates supplied bytes only. No network or application DB.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

MAX_SNAPSHOT_BYTES = 512_000
MAX_STORIES = 500
MAX_AGE = timedelta(hours=24)
EVENTS = {"LAUNCH_REVIEW", "PREORDER", "ADDITIONAL_PREORDER", "REISSUE", "RESTOCK", "EVENT", "NON_WATCH"}


def _refuse(message="invalid coverage snapshot"):
    raise ValueError(message)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _refuse("duplicate coverage authority key")
        result[key] = value
    return result


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        _refuse("nonfinite coverage number")
    return result


def strict_json(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SNAPSHOT_BYTES:
        _refuse("coverage snapshot byte bound")
    try:
        return json.loads(raw.decode("utf8"), object_pairs_hook=_pairs, parse_float=_float,
                          parse_constant=lambda value: _refuse("nonfinite coverage number"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid coverage JSON") from exc


def _shape(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        _refuse("incompatible coverage object fields")


def _text(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(c) < 32 for c in value):
        _refuse("invalid coverage text")


def _clock(value):
    _text(value)
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            _refuse("aware coverage timestamp required")
        return stamp.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid aware coverage timestamp") from exc


def _url(value, hosts=None):
    _text(value)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port is not None or parsed.fragment or parsed.query
            or not parsed.path or parsed.path == "/" or "\\" in value
            or (hosts is not None and parsed.hostname not in hosts)):
        _refuse("invalid coverage URL scope")


def _article(value):
    if not isinstance(value, str) or not re.fullmatch(r"https://prtimes\.jp/main/html/rd/p/[0-9]{9}\.[0-9]{9}\.html", value):
        _refuse("invalid coverage article witness")


def _urls(value, *, articles=False):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_STORIES or len(set(value)) != len(value):
        _refuse("invalid coverage URL list")
    for url in value:
        _article(url) if articles else _url(url)


def validate(raw):
    doc = strict_json(raw)
    _shape(doc, "schema_version snapshot_id newsroom provenance_kind test_only exported_at cutoff expires_at complete_scope stories")
    if type(doc["schema_version"]) is not int or doc["schema_version"] != 1 or doc["newsroom"] != "Notebookcheck":
        _refuse("incompatible coverage schema/newsroom")
    _text(doc["snapshot_id"])
    if doc["provenance_kind"] not in {"OWNER_EXPORT", "TEST_FIXTURE"} or type(doc["test_only"]) is not bool:
        _refuse("invalid coverage provenance")
    if doc["test_only"] != (doc["provenance_kind"] == "TEST_FIXTURE"):
        _refuse("test fixture must be explicitly test_only")
    exported, cutoff, expiry = map(_clock, (doc["exported_at"], doc["cutoff"], doc["expires_at"]))
    if not cutoff <= exported < expiry or expiry - exported > MAX_AGE:
        _refuse("inconsistent coverage clock bounds")
    scope = doc["complete_scope"]
    if scope is not None:
        _shape(scope, "region article_urls verified")
        if scope["region"] != "JP" or type(scope["verified"]) is not bool:
            _refuse("invalid complete coverage scope")
        _urls(scope["article_urls"], articles=True)
    stories = doc["stories"]
    if not isinstance(stories, list) or len(stories) > MAX_STORIES:
        _refuse("coverage story count bound")
    ids = set()
    for story in stories:
        _shape(story, "story_id state story_url published_at verified_at verification source_evidence_urls identity_status article_url event_scope region manufacturer reference")
        _text(story["story_id"])
        if story["story_id"] in ids:
            _refuse("duplicate coverage story ID")
        ids.add(story["story_id"])
        if story["state"] not in {"PUBLISHED", "DRAFT", "QC_USEFUL", "DUPLICATE", "UNKNOWN"} or story["verification"] not in {"VERIFIED", "UNVERIFIED"}:
            _refuse("invalid coverage publication state")
        _url(story["story_url"], {"notebookcheck.test"} if doc["test_only"] else {"www.notebookcheck.net", "www.notebookcheck.com"})
        if story["article_url"] is not None:
            _url(story["article_url"])
        _urls(story["source_evidence_urls"])
        if story["event_scope"] not in EVENTS or story["region"] != "JP":
            _refuse("incompatible coverage event/region")
        if story["identity_status"] == "UNRESOLVED":
            if story["manufacturer"] is not None or story["reference"] is not None:
                _refuse("unresolved coverage identity cannot assert maker/ref")
        elif story["identity_status"] == "EXACT":
            _text(story["manufacturer"])
            _text(story["reference"])
            from app.sentinel.identity import build_identity
            identity = build_identity(story["manufacturer"], reference_raw=story["reference"], url="")
            if identity.manufacturer != story["manufacturer"] or identity.reference_canonical != story["reference"] or not re.search(r"[0-9]", story["reference"]):
                _refuse("noncanonical exact coverage identity")
        else:
            _refuse("invalid coverage identity status")
        for name in ("published_at", "verified_at"):
            if story[name] is not None and _clock(story[name]) > exported:
                _refuse("coverage story timestamp beyond export")
        if story["published_at"] and story["verified_at"] and _clock(story["published_at"]) > _clock(story["verified_at"]):
            _refuse("coverage verification predates publication")
    return doc


def load_snapshot(path: Path | None, sha256: str | None):
    if path is None and sha256 is None:
        return None
    if path is None or not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        _refuse("explicit coverage file and reviewed SHA256 required")
    path = Path(path)
    if path.is_symlink() or path.suffix.lower() != ".json" or "data" in {p.lower() for p in path.resolve().parts}:
        _refuse("coverage requires an explicit regular JSON snapshot, not an application DB")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
    except OSError as exc:
        raise ValueError("configured coverage snapshot unreadable") from exc
    if hashlib.sha256(raw).hexdigest() != sha256:
        _refuse("coverage snapshot hash mismatch")
    validate(raw)
    return {"raw_json": raw.decode("utf8"), "sha256": sha256}


def source_context(record):
    # Recompute identity/category from retained literal source, not derived annotations.
    from app.collectors.base import DiscoveredItem
    from app.collectors.prtimes_jp_staging import _raw_digest, classify_item
    facts = classify_item(DiscoveredItem(url=record["url"], title=record["title_raw"], metadata={
        name: record[name] for name in ("description_raw", "publisher_raw", "published_at_raw")
    }), now=_clock(record["observed_at"]))
    event = facts["category"]
    if event == "EVENT" and re.search(r"予約開始|受注開始", facts["title_normalized"]):
        event = "PREORDER"
    return {"article_url": facts["url"], "event_scope": event, "region": "JP",
            "manufacturer": facts["manufacturer"], "reference": facts["reference_canonical"],
            "raw_sha256": _raw_digest(record)}


def assess(record, binding, *, now):
    if now.tzinfo is None:
        _refuse("aware coverage assessment clock required")
    context = source_context(record)
    result = {"status": "COVERAGE_UNKNOWN", "reason": "NO_SNAPSHOT", "source_context": context,
              "snapshot_id": None, "snapshot_sha256": None, "as_of": now.isoformat(),
              "cutoff": None, "provenance_kind": None, "test_only": False, "story_ids": [],
              "story_urls": [], "truth_limit": "Provided bytes only; no external publisher verification"}
    if binding is None:
        return result
    _shape(binding, "raw_json sha256")
    raw = binding["raw_json"].encode("utf8")
    if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        _refuse("retained coverage snapshot hash mismatch")
    doc = validate(raw)
    result.update(snapshot_id=doc["snapshot_id"], snapshot_sha256=binding["sha256"],
                  cutoff=doc["cutoff"], provenance_kind=doc["provenance_kind"], test_only=doc["test_only"])
    exported, cutoff, expiry = map(_clock, (doc["exported_at"], doc["cutoff"], doc["expires_at"]))
    scope = doc["complete_scope"]
    if not exported <= now <= expiry or now - cutoff > MAX_AGE:
        result["reason"] = "EXPIRED_STALE_OR_FUTURE_SNAPSHOT"
        return result
    if (scope is None or not scope["verified"] or context["article_url"] not in scope["article_urls"]
            or cutoff < _clock(record["published_at_raw"])):
        result["reason"] = "PARTIAL_UNVERIFIED_OR_OUTSIDE_SCOPE"
        return result
    # No uncertain record can create a negative clearance, even if its key is close.
    if any(story["state"] != "PUBLISHED" or story["verification"] != "VERIFIED"
           or not story["published_at"] or not story["verified_at"] or not story["article_url"]
           or _clock(story["published_at"]) > cutoff
           or exported - _clock(story["verified_at"]) > MAX_AGE
           or story["article_url"] not in story["source_evidence_urls"] for story in doc["stories"]):
        result["reason"] = "UNCERTAIN_PUBLICATION_RECORD"
        return result
    keys = ("article_url", "event_scope", "region", "manufacturer", "reference")
    if any(story["article_url"] == context["article_url"] and any(story[k] != context[k] for k in keys) for story in doc["stories"]):
        result["reason"] = "CONTRADICTORY_MATCHING_ARTICLE_CONTEXT"
        return result
    matches = [story for story in doc["stories"] if all(story[k] == context[k] for k in (
        "article_url", "event_scope", "region", "manufacturer", "reference"))]
    if matches:
        result.update(status="TEST_ONLY_COVERED_CONTEXT" if doc["test_only"] else "VERIFIED_AGAINST_PROVIDED_SNAPSHOT",
                      reason="EXACT_ARTICLE_EVENT_WITNESS", story_ids=[r["story_id"] for r in matches],
                      story_urls=[r["story_url"] for r in matches])
    else:
        result.update(status="NOT_FOUND_IN_PROVIDED_COMPLETE_SCOPE", reason="ONLY_LISTED_ARTICLE_SCOPE_AS_OF_CUTOFF")
    return result


def validate_native_notes(lead):
    """Authenticate coverage against native scalar/source facts before QC exposes notes."""
    if not lead.source_id.startswith("prtimes_jp_") or not lead.notes:
        return
    notes = json.loads(lead.notes)
    if "newsroom_coverage" not in notes:
        return  # Genuine pre-P2 history carries no publication claim.
    if (notes["url"] != lead.source_url or notes["title_raw"] != lead.title
            or notes["description_raw"] != lead.claim_text or lead.region != "JP"):
        _refuse("coverage native article/source mismatch")
    context = source_context(notes)
    if context["manufacturer"] != lead.manufacturer or ([context["reference"]] if context["reference"] else []) != lead.reference_candidates:
        _refuse("coverage native entity mismatch")
    coverage = notes["newsroom_coverage"]
    expected = assess(notes, notes.get("coverage_input"), now=_clock(coverage["as_of"]))
    if coverage != expected:
        _refuse("contradictory derived coverage annotation")
