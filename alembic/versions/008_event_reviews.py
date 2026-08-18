"""event_reviews table -- human QC feedback on Events

Adds a durable, appendable record of human editorial verdicts on Events,
built in response to the 2026-08-18 Citizen stale/out-of-stock flood
autopsy (ai/handoff/HUMAN_QC_FEEDBACK_CONTRACT.md). Purely additive: no
existing table is altered, no existing row is touched. Safe on a fresh
database (table simply starts empty) and on an existing production
database (every current Event is implicitly "unreviewed" until an
operator acts on it).

Revision ID: 008_event_reviews
Revises: 007_specialist_lead_editorial_freshness
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "008_event_reviews"
down_revision: Union[str, None] = "007_specialist_lead_editorial_freshness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "watch_id",
            sa.Integer(),
            sa.ForeignKey("watches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("manufacturer", sa.String(length=64), nullable=True),
        sa.Column("reference_canonical", sa.String(length=128), nullable=True),
        sa.Column("source_collector_id", sa.String(length=64), nullable=True),
        sa.Column("region", sa.String(length=16), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column(
            "reviewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("evidence_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("availability_status", sa.String(length=64), nullable=True),
        sa.Column("provenance_url", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("review_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "disposition IN ('USEFUL', 'NOT_USEFUL', 'FALSE_POSITIVE', 'OUT_OF_STOCK')",
            name="ck_event_review_disposition",
        ),
        sa.UniqueConstraint("event_id", name="uq_event_review_event_id"),
    )
    op.create_index("ix_event_reviews_event_id", "event_reviews", ["event_id"])
    op.create_index("ix_event_reviews_watch_id", "event_reviews", ["watch_id"])
    op.create_index("ix_event_reviews_reviewed_at", "event_reviews", ["reviewed_at"])


def downgrade() -> None:
    op.drop_index("ix_event_reviews_reviewed_at", table_name="event_reviews")
    op.drop_index("ix_event_reviews_watch_id", table_name="event_reviews")
    op.drop_index("ix_event_reviews_event_id", table_name="event_reviews")
    op.drop_table("event_reviews")
