"""event_reviews.is_corrected + specialist_lead_reviews.is_corrected

QC History correction UX addendum (2026-08-19): the operator asked for
QC History's default view to behave like a workable correction queue --
once a review's disposition has been corrected, it should drop out of
the default view (retrievable via an explicit "include corrected" filter),
not remain visible indefinitely.

`is_corrected` is a plain indexed boolean rather than re-deriving the
answer from `review_metadata.correction_history` on every request (that
JSON field already exists and is NOT removed here -- the audit trail
still lives there; this column is purely a fast, explicit "has this ever
been corrected" flag).

Data-driven backfill: any existing row whose `review_metadata` already
contains a non-empty `correction_history` array is backfilled to
`is_corrected = 1` -- real production data (the field-test DB) already
has one such row from this feature's own initial rollout, and this
migration must not silently reset it to "never corrected".

Purely additive otherwise: no existing row is deleted, no other column
altered. Safe on a fresh database and on an existing production database.

Revision ID: 010_review_is_corrected
Revises: 009_specialist_lead_reviews
Create Date: 2026-08-19
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "010_review_is_corrected"
down_revision: Union[str, None] = "009_specialist_lead_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("event_reviews", "specialist_lead_reviews")


def _backfill(conn, table: str) -> None:
    rows = conn.execute(sa.text(f"SELECT id, review_metadata FROM {table}")).fetchall()
    for row in rows:
        raw = row.review_metadata
        if not raw:
            continue
        try:
            metadata = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if isinstance(metadata, dict) and metadata.get("correction_history"):
            conn.execute(
                sa.text(f"UPDATE {table} SET is_corrected = 1 WHERE id = :id"),  # noqa: S608
                {"id": row.id},
            )


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column("is_corrected", sa.Boolean(), nullable=False, server_default="0")
            )
        op.create_index(f"ix_{table}_is_corrected", table, ["is_corrected"])

    conn = op.get_bind()
    for table in _TABLES:
        _backfill(conn, table)


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_is_corrected", table_name=table)
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("is_corrected")
