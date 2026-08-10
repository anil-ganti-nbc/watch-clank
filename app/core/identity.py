"""Runtime identity: clank ID and immutable Git-revision provenance.

Same pattern already proven on OEM Radar, Chinese Tech Wire, Feature Phone
Clank, and Smartwatch Clank: the full Git SHA is baked in at Docker build
time (never read from a .git directory at runtime) and surfaced here. Local/
non-Docker runs report "unknown" rather than a fabricated value.

No existing identity/version contract existed in this project before this
addition -- this is new, additive surface, not a change to an existing one.
"""

from __future__ import annotations

import os

CLANK_ID = "watch-clank"


def _source_revision() -> str:
    return os.environ.get("WATCH_CLANK_SOURCE_REVISION", "unknown")


def _source_revision_short() -> str:
    revision = _source_revision()
    return revision if revision == "unknown" else revision[:12]


def get_identity() -> dict[str, str]:
    return {
        "clank_id": CLANK_ID,
        "source_revision": _source_revision(),
        "source_revision_short": _source_revision_short(),
    }
