"""specialist (early-warning / Layer B) leads

Revision ID: 004_specialist_leads
Revises: 003_release_leads
Create Date: 2026-08-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "004_specialist_leads"
down_revision: Union[str, None] = "003_release_leads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "specialist_leads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_authority_tier", sa.Integer(), nullable=False),
        sa.Column("account_or_domain", sa.String(length=256), nullable=True),
        sa.Column("lead_type", sa.String(length=32), nullable=False),
        sa.Column("manufacturer", sa.String(length=64), nullable=True),
        sa.Column("brand", sa.String(length=64), nullable=True),
        sa.Column("region", sa.String(length=32), nullable=True),
        sa.Column("reference_candidates", sa.JSON(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("correlated_watch_id", sa.Integer(), nullable=True),
        sa.Column("correlated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("official_first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lead_time_days", sa.Float(), nullable=True),
        sa.Column("ingestion_method", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["correlated_watch_id"], ["watches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_url"),
        sa.CheckConstraint(
            "source_type IN ('SPECIALIST_PUBLICATION','SPECIALIST_BLOG',"
            "'RETAILER_EARLY_LISTING','SOCIAL_LEAKER','COMMUNITY_SIGNAL')",
            name="ck_specialist_lead_source_type",
        ),
        sa.CheckConstraint(
            "source_authority_tier >= 1 AND source_authority_tier <= 4",
            name="ck_specialist_lead_tier_range",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNCONFIRMED','CORRELATED_WITH_OFFICIAL','REJECTED')",
            name="ck_specialist_lead_verification_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_specialist_lead_confidence_range",
        ),
    )
    op.create_index("ix_specialist_leads_source_id", "specialist_leads", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_specialist_leads_source_id", table_name="specialist_leads")
    op.drop_table("specialist_leads")
