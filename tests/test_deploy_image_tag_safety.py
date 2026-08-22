"""Regression test for the class of incident in
ai/handoff/INCIDENT_LEGACY_FCB5E91_LAUNCHER.md and the 2026-08-22
migrate-tooling investigation: `docker-compose.staging.yml` must never let
an operator silently run against a stale image because `IMAGE_TAG` wasn't
set in the invoking shell.

Both incidents traced back to the same root cause -- `image:
watch-clank:${IMAGE_TAG:-soak-local}` resolves to a real, long-stale image
(`soak-local`, built 2026-08-10, predating migrations 008-011) whenever
`IMAGE_TAG` is unset, rather than failing loudly. This silently reproduced
what looked like a schema-check defect: `scripts/migrate.py` correctly
reported the stale image's own baked-in expectation
(`003_release_leads`) against the real database's current head
(`011_event_review_duplicate`) -- there was no bug in `check_schema()`
itself (see tests/test_schema_check.py), only a bad default one layer up,
in the deploy config.

This test guards the fix: every service's `image:` line must require
`IMAGE_TAG` explicitly (the `${VAR:?message}` shell-parameter-expansion
form), matching the rest of the fleet's convention (e.g. OEM Radar's
`docker-compose.yml`), instead of silently substituting a default.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.staging.yml"

# Matches `image: <name>:${IMAGE_TAG...}` and captures the shell
# parameter-expansion operator used inside the braces, e.g. `:-` or `:?`.
_IMAGE_TAG_PATTERN = re.compile(r"image:\s*\S+:\$\{IMAGE_TAG(:[-?])")


def test_compose_file_exists():
    assert COMPOSE_FILE.is_file()


def test_every_service_requires_image_tag_explicitly():
    """No service may fall back to a default image tag. Every occurrence
    of `${IMAGE_TAG...}` must use the `:?` (required, error-if-unset) form,
    never `:-` (silently substitute a default)."""
    text = COMPOSE_FILE.read_text()
    matches = _IMAGE_TAG_PATTERN.findall(text)

    assert matches, "expected at least one image: line referencing IMAGE_TAG"
    assert all(op == ":?" for op in matches), (
        f"found a soft IMAGE_TAG default (':-') instead of a required "
        f"one (':?') -- this is exactly the class of bug that silently "
        f"substituted a long-stale image in two separate incidents. "
        f"operators: {matches}"
    )


def test_soak_local_default_is_gone():
    """The specific stale-default value implicated in both incidents must
    not appear anywhere in the compose file."""
    text = COMPOSE_FILE.read_text()
    assert "soak-local" not in text
