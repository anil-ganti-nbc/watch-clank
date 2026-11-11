"""Offline-only retained RSS replay; no acquisition or production database inputs.

Run from the repository with python -m scripts.qualify_prtimes_jp_offline.
Counters describe classifier output, not editorial precision or historical recall.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from app.collectors import prtimes_jp_staging as staging


def retained_frames(payload: bytes, company: str):
    """New XML framing, unchanged captured item bytes; verify parsed equivalence."""
    items = staging.parse_feed(payload, company)
    opening = re.search(rb"<rdf:RDF\b[^>]*>", payload)
    if opening is None:
        raise ValueError("unsupported retained RDF framing")
    chunks = re.findall(rb"<item\b[^>]*>.*?</item\s*>", payload, flags=re.S)
    frames = []
    for chunk in chunks:
        frame = opening.group() + chunk + b"</rdf:RDF>"
        parsed = staging.parse_feed(frame, company)
        if len(parsed) != 1:
            raise ValueError("expected one unchanged retained item")
        frames.append((frame, parsed[0]))
    if [item for _, item in frames] != items:
        raise ValueError("retained framing differs from canonical parsed feed")
    return frames


def replay_rows(payload: bytes, company: str, now: datetime):
    if now.tzinfo is None:
        raise ValueError("aware replay clock required")
    rows = []
    for frame, item in retained_frames(payload, company):
        row = staging.classify_item(item, now=now)
        row.update(company_id=company, item_frame_sha256=hashlib.sha256(frame).hexdigest())
        try:
            published = datetime.fromisoformat(row["published_at_raw"])
            if published.tzinfo is None:
                raise ValueError("naive timestamp")
            clock = published + timedelta(hours=1)
            simulated = staging.classify_item(item, now=clock)
            row["counterfactual_publication_plus_1h"] = {
                "clock": clock.isoformat(),
                "freshness": simulated["freshness"],
                "is_live_detection": False,
            }
        except ValueError:
            row["counterfactual_publication_plus_1h"] = None
        rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--at", required=True)
    args = parser.parse_args(argv)

    # This process is socket-denied and does not spawn children. Audit hooks
    # are not inherited; qualification tests use a separate inherited guard.
    def deny(event, values):
        if event.startswith("socket."):
            raise PermissionError("offline replay: socket operation refused")

    sys.addaudithook(deny)
    now = datetime.fromisoformat(args.at)
    rows, inputs = [], {}
    for company, page in [("25284", 8), ("68154", 9), ("46261", 10)]:
        raw = (args.capture_dir / f"page-{page}.bin").read_bytes()
        inputs[company] = hashlib.sha256(raw).hexdigest()
        rows.extend(replay_rows(raw, company, now))
    if len(rows) != 452:
        raise ValueError(f"expected frozen 452 items, got {len(rows)}")
    summary = {}
    for company in staging.SOURCES:
        group = [row for row in rows if row["company_id"] == company]
        summary[company] = {
            "articles": len(group),
            **{
                field: dict(Counter(row[field] for row in group))
                for field in ["product_kind", "category", "freshness", "identity_status"]
            },
        }
    result = {
        "clock": now.isoformat(),
        "mode": "pure retained classification; no livedetection or gold labels",
        "input_sha256": inputs,
        "per_issuer": summary,
        "rows": rows,
    }
    with args.output.open("x", encoding="utf8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
