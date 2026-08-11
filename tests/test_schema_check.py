"""Tests for the explicit schema-version gate (app/db/schema_check.py).

Covers the exact soak-outage class this exists to prevent: database schema
pinned at an older Alembic revision than the code expects. The pipeline must
refuse to run rather than hit "no such table"/"no such column" mid-run.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.db.schema_check import check_schema

ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def test_fresh_uninitialized_db_does_not_match(tmp_path: Path):
    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    status = check_schema(engine, alembic_ini_path=str(ROOT / "alembic.ini"))
    assert status.actual_version is None
    assert not status.matches
    assert status.expected_head  # non-empty


def test_db_at_head_matches(tmp_path: Path):
    db_path = tmp_path / "head.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    status = check_schema(engine, alembic_ini_path=str(ROOT / "alembic.ini"))
    assert status.matches
    assert status.actual_version == status.expected_head


def test_db_at_older_revision_does_not_match(tmp_path: Path):
    """Direct reproduction of the real outage class: code expects the
    current migration head, database is pinned at an older revision
    (originally reproduced with 002_ops_statuses vs 003_release_leads;
    updated to 005_specialist_lead_correlation_type as the code's head
    advanced — the outage class under test is unchanged)."""
    db_path = tmp_path / "stale.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "002_ops_statuses")

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    status = check_schema(engine, alembic_ini_path=str(ROOT / "alembic.ini"))
    assert not status.matches
    assert status.actual_version == "002_ops_statuses"
    assert status.expected_head == "005_specialist_lead_correlation_type"


def test_run_pipeline_refuses_on_schema_mismatch(tmp_path, monkeypatch):
    """End-to-end: the actual CLI entrypoint must exit EXIT_SCHEMA_MISMATCH (3)
    rather than let the pipeline hit the underlying DB error."""
    from app.core.config import Settings

    db_path = tmp_path / "stale_for_cli.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "002_ops_statuses")

    import app.db.session as session_module

    fresh_settings = Settings(database_url=f"sqlite:///{db_path}")
    monkeypatch.setattr(session_module, "_settings", fresh_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_SessionLocal", None)

    import scripts.run_pipeline as run_pipeline_module

    monkeypatch.setattr(run_pipeline_module, "get_settings", lambda: fresh_settings)

    exit_code = run_pipeline_module.run_live_or_scheduled(max_items=1, scheduled=True)
    assert exit_code == run_pipeline_module.EXIT_SCHEMA_MISMATCH


def test_run_pipeline_sends_health_alert_on_schema_mismatch(tmp_path, monkeypatch):
    """Sprint 6: schema mismatch is exactly the actionable failure class the
    health webhook exists for — verify it actually fires when configured."""
    from unittest.mock import patch

    from app.core.config import Settings

    db_path = tmp_path / "stale_for_alert.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "002_ops_statuses")

    import app.db.session as session_module

    fresh_settings = Settings(
        database_url=f"sqlite:///{db_path}",
        discord_health_webhook_url="https://discord.example/health",
    )
    monkeypatch.setattr(session_module, "_settings", fresh_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_SessionLocal", None)

    import scripts.run_pipeline as run_pipeline_module

    monkeypatch.setattr(run_pipeline_module, "get_settings", lambda: fresh_settings)

    calls = []
    with patch("httpx.post", side_effect=lambda url, **kw: calls.append(url) or type("R", (), {"status_code": 204, "text": ""})()):
        exit_code = run_pipeline_module.run_live_or_scheduled(max_items=1, scheduled=True)

    assert exit_code == run_pipeline_module.EXIT_SCHEMA_MISMATCH
    assert calls == ["https://discord.example/health"]
