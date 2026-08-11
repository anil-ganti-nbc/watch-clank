"""Explicit, deliberate migration application. Never run automatically by the
pipeline or dashboard -- an operator (or deploy script) invokes this
separately, once, before switching a deployment over to a new schema
version. Never run this while another writer (pipeline/dashboard) may be
active against the same database file.

Exit codes:
  0 = already at expected head, or upgrade applied successfully
  1 = upgrade failed
  2 = repository problem (ambiguous/branched migration heads)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic.config import Config

from alembic import command
from app.db.schema_check import check_schema
from app.db.session import get_engine


def main() -> int:
    try:
        status = check_schema(get_engine())
    except RuntimeError as exc:
        print(f"REPOSITORY PROBLEM: {exc}")
        return 2

    if status.matches:
        print(f"already at expected head ({status.expected_head}); nothing to do")
        return 0

    print(
        f"upgrading: current={status.actual_version or '(uninitialized)'} "
        f"-> expected={status.expected_head}"
    )
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
    except Exception as exc:  # noqa: BLE001 - report clearly, exit non-zero
        print(f"MIGRATION FAILED: {exc}")
        return 1

    status_after = check_schema(get_engine())
    if not status_after.matches:
        print(
            f"MIGRATION DID NOT REACH EXPECTED HEAD: now at "
            f"{status_after.actual_version}, expected {status_after.expected_head}"
        )
        return 1

    print(f"migration successful, now at {status_after.actual_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
