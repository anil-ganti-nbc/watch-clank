"""Print runtime identity (clank ID + Git-revision provenance) as JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.identity import get_identity


def main() -> int:
    print(json.dumps(get_identity(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
