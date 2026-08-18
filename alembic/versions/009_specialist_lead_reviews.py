"""specialist_lead_reviews table + specialist_leads.collector_run_id

Watch Clank QC + classifier hardening pass (2026-08-19). The existing
`event_reviews` table (008) only covers official Events; Specialist leads
had no QC controls at all, violating the unified-QC-UI rule established
by that sprint. Sibling table, not a merge into event_reviews -- Event
and SpecialistLead have always been separate tables in this codebase (see
app/models/specialist_lead.py's module docstring), and extending that
separation to reviews avoids any risk to the already-shipped, already-
verified Official Events QC path while still reusing the same qc.py
service module, the same /qc/history page, and the same disposition-
persistence/correction-audit-trail pattern.

Also adds `specialist_leads.collector_run_id` (nullable, SET NULL on
delete) so a review can carry the real originating CollectorRun id rather
than an approximation -- every current specialist-lead pipeline already
has `run.id` in scope at ingest time, it was simply never persisted onto
the lead row.

Purely additive: no existing table/column is altered destructively (only
one new nullable column added to specialist_leads), no existing row is
touched. Safe on a fresh database and on an existing production database.

Revision ID: 009_specialist_lead_reviews
Revises: 008_event_reviews
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "009_specialist_lead_reviews"
down_revision: Union[str, None] = "008_event_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("specialist_leads") as batch_op:
        batch_op.add_column(
            sa.Column(
                "collector_run_id",
                sa.Integer(),
                sa.ForeignKey(
                    "collector_runs.id", ondelete="SET NULL", name="fk_specialist_leads_collector_run_id"
                ),
                nullable=True,
            )
        )
    op.create_index("ix_specialist_leads_collector_run_id", "specialist_leads", ["collector_run_id"])

    op.create_table(
        "specialist_lead_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "specialist_lead_id",
            sa.Integer(),
            sa.ForeignKey("specialist_leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lead_title", sa.String(length=512), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("manufacturer", sa.String(length=64), nullable=True),
        sa.Column("region", sa.String(length=32), nullable=True),
        sa.Column("lead_type", sa.String(length=32), nullable=True),
        sa.Column("source_authority_tier", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "collector_run_id",
            sa.Integer(),
            sa.ForeignKey("collector_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column(
            "reviewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
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
            "disposition IN ('USEFUL', 'NOT_USEFUL', 'DUPLICATE', 'FALSE_POSITIVE')",
            name="ck_specialist_lead_review_disposition",
        ),
        sa.UniqueConstraint("specialist_lead_id", name="uq_specialist_lead_review_lead_id"),
    )
    op.create_index(
        "ix_specialist_lead_reviews_specialist_lead_id", "specialist_lead_reviews", ["specialist_lead_id"]
    )
    op.create_index("ix_specialist_lead_reviews_reviewed_at", "specialist_lead_reviews", ["reviewed_at"])


def downgrade() -> None:
    op.drop_index("ix_specialist_lead_reviews_reviewed_at", table_name="specialist_lead_reviews")
    op.drop_index("ix_specialist_lead_reviews_specialist_lead_id", table_name="specialist_lead_reviews")
    op.drop_table("specialist_lead_reviews")
    op.drop_index("ix_specialist_leads_collector_run_id", table_name="specialist_leads")
    with op.batch_alter_table("specialist_leads") as batch_op:
        batch_op.drop_column("collector_run_id")
