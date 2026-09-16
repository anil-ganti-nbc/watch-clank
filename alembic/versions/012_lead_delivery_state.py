"""specialist_leads gains delivery_state (STD-UI-COM-011 remediation)

The UI must be able to distinguish an attempted-and-failed or gated-by-
policy lead delivery from a never-attempted one; until now both read as
notified_at IS NULL. This adds one nullable column:

    delivery_state  NULL   = never considered for delivery
                    'sent' = alert dispatched (notified_at also set)
                    'failed' = dispatch attempted, Discord did not accept
                    'gated'  = policy skip (disabled, baseline, stale/
                               undated freshness, below-confidence floor,
                               duplicate-reference suppression)

'sent' rows are backfilled from notified_at so existing delivery history
stays visible. notified_at keeps its exact existing meaning (the dedup
guard); this column is display/triage metadata only.

Revision ID: 012_lead_delivery_state
Revises: 011_event_review_duplicate
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "012_lead_delivery_state"
down_revision: Union[str, None] = "011_event_review_duplicate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("specialist_leads") as batch:
        batch.add_column(
            sa.Column("delivery_state", sa.String(16), nullable=True)
        )
        batch.create_check_constraint(
            "ck_specialist_lead_delivery_state",
            "delivery_state IS NULL OR delivery_state IN ('sent', 'failed', 'gated')",
        )
    # Existing delivery history: anything that has notified_at was sent.
    op.execute(
        "UPDATE specialist_leads SET delivery_state = 'sent' WHERE notified_at IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("specialist_leads") as batch:
        batch.drop_constraint("ck_specialist_lead_delivery_state", type_="check")
        batch.drop_column("delivery_state")
