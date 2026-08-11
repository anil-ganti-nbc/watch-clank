"""Explicit schema-version gate for unattended (container/scheduled) startup.

Policy (Option B from the Hetzner migration): the application never applies
migrations automatically and never lazily creates tables. It only ever
compares the database's current Alembic head against the code's expected
head and refuses to run on any mismatch -- including a completely fresh,
uninitialized database. Migrations are applied only via the separate,
explicit `migrate` one-shot command (`scripts/migrate.py`), matching
Dockerfile's `migrate` CMD alternative.

This exists because of a real prior outage: the database was pinned at an
older migration (002_ops_statuses) while the deployed code expected
003_release_leads, and every scheduled pipeline run failed with
"no such table: source_component_states" until someone noticed and ran the
migration by hand. A silent lazy-create or an automatic migrate-on-startup
would have masked the same class of mismatch differently (silently running
against the wrong schema, or applying a migration while another writer might
be active) rather than surfacing it immediately and loudly.
"""

from __future__ import annotations

from dataclasses import dataclass

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError


@dataclass(frozen=True)
class SchemaStatus:
    expected_head: str
    actual_version: str | None  # None means alembic_version table doesn't exist yet
    matches: bool


def _expected_head(alembic_ini_path: str = "alembic.ini") -> str:
    config = Config(alembic_ini_path)
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(
            f"expected exactly one Alembic head, found {len(heads)}: {heads} "
            "-- this is a repository problem (branched migrations), not a "
            "deployment problem; fix the migration graph before deploying."
        )
    return heads[0]


def _actual_version(engine: Engine) -> str | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            return row[0] if row else None
    except OperationalError:
        # alembic_version table doesn't exist -- a genuinely fresh/uninitialized DB.
        return None


def check_schema(engine: Engine, alembic_ini_path: str = "alembic.ini") -> SchemaStatus:
    """Compare the database's current Alembic version against the code's
    expected head. Never raises for a mismatch -- callers decide how to
    react (the CLI entrypoint exits non-zero; a health check reports
    degraded). Never applies a migration itself."""
    expected = _expected_head(alembic_ini_path)
    actual = _actual_version(engine)
    return SchemaStatus(expected_head=expected, actual_version=actual, matches=actual == expected)
