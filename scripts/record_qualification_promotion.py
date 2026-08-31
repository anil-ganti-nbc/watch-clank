"""Record an operator's reviewed collector promotion in the Watch database."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import session_scope
from app.services.qualification import QualificationService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("collector_id")
    parser.add_argument("--change-identity")
    args = parser.parse_args()
    with session_scope() as session:
        QualificationService(session).record_operator_promotion(
            args.collector_id, change_identity=args.change_identity
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
