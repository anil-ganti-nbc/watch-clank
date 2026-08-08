"""release leads and source component state

Revision ID: 003_release_leads
Revises: 002_ops_statuses
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "003_release_leads"
down_revision: Union[str, None] = "002_ops_statuses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "release_leads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("manufacturer", sa.String(length=64), nullable=False),
        sa.Column("brand", sa.String(length=64), nullable=False),
        sa.Column("collection", sa.String(length=128), nullable=True),
        sa.Column("announcement_title", sa.String(length=512), nullable=False),
        sa.Column("announcement_date", sa.String(length=64), nullable=True),
        sa.Column("announcement_url", sa.String(length=1024), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_region", sa.String(length=32), nullable=True),
        sa.Column("source_language", sa.String(length=16), nullable=True),
        sa.Column("model_references", sa.JSON(), nullable=True),
        sa.Column("product_urls", sa.JSON(), nullable=True),
        sa.Column("image_urls", sa.JSON(), nullable=True),
        sa.Column("snapshot_fetch_id", sa.Integer(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("completeness_score", sa.Float(), nullable=False),
        sa.Column("enrichment_status", sa.String(length=32), nullable=False),
        sa.Column("merge_key", sa.String(length=256), nullable=True),
        sa.Column("merged_into_id", sa.Integer(), nullable=True),
        sa.Column("watch_ids", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("announcement_url"),
    )
    op.create_index("ix_release_leads_merge_key", "release_leads", ["merge_key"])

    op.create_table(
        "source_component_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_blocks", sa.Integer(), nullable=False),
        sa.Column("backoff_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_item_count", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
    )


def downgrade() -> None:
    op.drop_table("source_component_states")
    op.drop_index("ix_release_leads_merge_key", table_name="release_leads")
    op.drop_table("release_leads")
