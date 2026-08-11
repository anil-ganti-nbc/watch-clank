"""Tests for Git-revision provenance (app/core/identity.py).

Mirrors the pattern already proven on OEM Radar / Chinese Tech Wire /
Feature Phone Clank / Smartwatch Clank.
"""

from __future__ import annotations

import pytest

from app.core import identity


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WATCH_CLANK_SOURCE_REVISION", raising=False)


def test_source_revision_defaults_to_unknown_without_env_var():
    assert identity._source_revision() == "unknown"
    assert identity._source_revision_short() == "unknown"


def test_source_revision_reflects_full_sha_from_env(monkeypatch):
    full_sha = "63ba72a1fb753709c18844190030e11384a597cd"
    monkeypatch.setenv("WATCH_CLANK_SOURCE_REVISION", full_sha)
    assert identity._source_revision() == full_sha
    assert identity._source_revision_short() == full_sha[:12]


def test_get_identity_includes_source_revision(monkeypatch):
    full_sha = "63ba72a1fb753709c18844190030e11384a597cd"
    monkeypatch.setenv("WATCH_CLANK_SOURCE_REVISION", full_sha)
    result = identity.get_identity()
    assert result["clank_id"] == "watch-clank"
    assert result["source_revision"] == full_sha
    assert result["source_revision_short"] == full_sha[:12]


def test_get_identity_reports_unknown_without_env_var():
    result = identity.get_identity()
    assert result["source_revision"] == "unknown"
    assert result["source_revision_short"] == "unknown"
