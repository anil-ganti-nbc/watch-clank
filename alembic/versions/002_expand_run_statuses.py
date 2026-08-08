"""Expand collector_run statuses for operations pass.

Revision ID: 002_ops_statuses
Revises: 001_initial
Create Date: 2026-08-03

Adds BLOCKED and SKIPPED_OVERLAP to allowed run statuses.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002_ops_statuses"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite: recreate check constraint via batch alter
    with op.batch_alter_table("collector_runs") as batch:
        batch.drop_constraint("ck_run_status", type_="check")
        batch.create_check_constraint(
            "ck_run_status",
            "status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED', 'ZERO_ITEMS', 'BLOCKED', 'SKIPPED_OVERLAP')",
        )


def downgrade() -> None:
    with op.batch_alter_table("collector_runs") as batch:
        batch.drop_constraint("ck_run_status", type_="check")
        batch.create_check_constraint(
            "ck_run_status",
            "status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED', 'ZERO_ITEMS')",
        )
