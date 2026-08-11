"""operational epochs (Sprint 7 baseline/epoch tracking)

Revision ID: 006_operational_epochs
Revises: 005_specialist_lead_correlation_type
Create Date: 2026-08-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "006_operational_epochs"
down_revision: Union[str, None] = "005_specialist_lead_correlation_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operational_epochs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    with op.batch_alter_table("collector_runs") as batch_op:
        batch_op.add_column(sa.Column("epoch_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("is_baseline", sa.Boolean(), server_default="0", nullable=False))
        batch_op.create_foreign_key(
            "fk_collector_runs_epoch_id", "operational_epochs", ["epoch_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_collector_runs_epoch_id", "collector_runs", ["epoch_id"])

    with op.batch_alter_table("source_observations") as batch_op:
        batch_op.add_column(sa.Column("epoch_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("is_baseline", sa.Boolean(), server_default="0", nullable=False))
        batch_op.create_foreign_key(
            "fk_source_observations_epoch_id", "operational_epochs", ["epoch_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_source_observations_epoch_id", "source_observations", ["epoch_id"])

    with op.batch_alter_table("specialist_leads") as batch_op:
        batch_op.add_column(sa.Column("epoch_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("is_baseline", sa.Boolean(), server_default="0", nullable=False))
        batch_op.create_foreign_key(
            "fk_specialist_leads_epoch_id", "operational_epochs", ["epoch_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_specialist_leads_epoch_id", "specialist_leads", ["epoch_id"])


def downgrade() -> None:
    op.drop_index("ix_specialist_leads_epoch_id", table_name="specialist_leads")
    with op.batch_alter_table("specialist_leads") as batch_op:
        batch_op.drop_constraint("fk_specialist_leads_epoch_id", type_="foreignkey")
        batch_op.drop_column("is_baseline")
        batch_op.drop_column("epoch_id")

    op.drop_index("ix_source_observations_epoch_id", table_name="source_observations")
    with op.batch_alter_table("source_observations") as batch_op:
        batch_op.drop_constraint("fk_source_observations_epoch_id", type_="foreignkey")
        batch_op.drop_column("is_baseline")
        batch_op.drop_column("epoch_id")

    op.drop_index("ix_collector_runs_epoch_id", table_name="collector_runs")
    with op.batch_alter_table("collector_runs") as batch_op:
        batch_op.drop_constraint("fk_collector_runs_epoch_id", type_="foreignkey")
        batch_op.drop_column("is_baseline")
        batch_op.drop_column("epoch_id")

    op.drop_table("operational_epochs")
