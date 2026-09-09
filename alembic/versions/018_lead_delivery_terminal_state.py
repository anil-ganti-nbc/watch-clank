"""Specialist-lead delivery terminal-state contract.

A newly ingested SpecialistLead could remain delivery_state NULL /
notified_at NULL with no receipt (Great G-Shock World lead 119). This
migration is additive and truthful:

- widen delivery_state so provider_identified / unresolved_historical fit
- add delivery_reason and delivery_receipt_id
- existing NULL rows become unresolved_historical / PRE_TERMINAL_STATE_CONTRACT
- no notification is sent; no timestamps are fabricated
- legacy 'sent'/'gated'/'failed' rows are left unchanged

Revision ID: 018_lead_delivery_terminal_state
Revises: 017_sentinel_identities
Create Date: 2026-09-09
"""

import sqlalchemy as sa

from alembic import op

revision = "018_lead_delivery_terminal_state"
down_revision = "017_sentinel_identities"
branch_labels = None
depends_on = None

_NEW_STATES = (
    "delivery_state IS NULL OR delivery_state IN ("
    "'sent','failed','gated',"
    "'provider_accepted','provider_identified','unresolved_historical')"
)
_OLD_STATES = "delivery_state IS NULL OR delivery_state IN ('sent', 'failed', 'gated')"


def upgrade() -> None:
    with op.batch_alter_table("specialist_leads") as batch:
        batch.drop_constraint("ck_specialist_lead_delivery_state", type_="check")
        batch.alter_column(
            "delivery_state",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=True,
        )
        batch.add_column(sa.Column("delivery_reason", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("delivery_receipt_id", sa.Integer(), nullable=True))
        batch.create_check_constraint("ck_specialist_lead_delivery_state", _NEW_STATES)

    op.execute(
        "UPDATE specialist_leads "
        "SET delivery_state = 'unresolved_historical', "
        "    delivery_reason = 'PRE_TERMINAL_STATE_CONTRACT' "
        "WHERE delivery_state IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE specialist_leads "
        "SET delivery_state = NULL, delivery_reason = NULL "
        "WHERE delivery_state = 'unresolved_historical' "
        "  AND delivery_reason = 'PRE_TERMINAL_STATE_CONTRACT'"
    )
    op.execute(
        "UPDATE specialist_leads SET delivery_state = 'sent' "
        "WHERE delivery_state IN ('provider_accepted','provider_identified')"
    )
    op.execute(
        "UPDATE specialist_leads SET delivery_state = 'failed' "
        "WHERE delivery_state NOT IN ('sent','failed','gated') "
        "  AND delivery_state IS NOT NULL"
    )
    with op.batch_alter_table("specialist_leads") as batch:
        batch.drop_constraint("ck_specialist_lead_delivery_state", type_="check")
        batch.drop_column("delivery_receipt_id")
        batch.drop_column("delivery_reason")
        batch.alter_column(
            "delivery_state",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=True,
        )
        batch.create_check_constraint("ck_specialist_lead_delivery_state", _OLD_STATES)
