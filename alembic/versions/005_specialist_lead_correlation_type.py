"""specialist lead correlation_type + notified_at

Revision ID: 005_specialist_lead_correlation_type
Revises: 004_specialist_leads
Create Date: 2026-08-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "005_specialist_lead_correlation_type"
down_revision: Union[str, None] = "004_specialist_leads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("specialist_leads") as batch_op:
        batch_op.add_column(sa.Column("correlation_type", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint(
            "ck_specialist_lead_correlation_type",
            "correlation_type IS NULL OR correlation_type IN ('EXACT_REFERENCE_MATCH','FAMILY_MATCH')",
        )


def downgrade() -> None:
    with op.batch_alter_table("specialist_leads") as batch_op:
        batch_op.drop_constraint("ck_specialist_lead_correlation_type", type_="check")
        batch_op.drop_column("notified_at")
        batch_op.drop_column("correlation_type")
