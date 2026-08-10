"""Regression test for resolved_lock_path (found during Hetzner
containerization, 2026-08-10): the lock file must live next to the actual
database file, not a hard-coded project_root/data path that ignores
DATABASE_URL overrides. A real deliberate overlap test on Hetzner proved two
separate one-shot containers each got their own private, ephemeral lock file
and both ran as full writers simultaneously before this fix.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings


def test_lock_path_follows_absolute_sqlite_database_location(tmp_path: Path):
    db_path = tmp_path / "somewhere" / "watch_clank.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")

    lock_path = settings.resolved_lock_path

    assert lock_path.parent == db_path.parent
    assert lock_path.name == settings.lock_file_name


def test_lock_path_and_database_share_directory_for_relative_url(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(database_url="sqlite:///./data/watch_clank.db")

    db_url = settings.resolved_database_url
    db_file = Path(db_url[len("sqlite:///") :])
    lock_path = settings.resolved_lock_path

    assert lock_path.parent == db_file.parent
