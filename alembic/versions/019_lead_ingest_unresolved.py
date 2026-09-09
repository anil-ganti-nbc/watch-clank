"""Allow specialist-lead `unresolved` as an ingest-time placeholder.

Crash after insert must not leave delivery_state NULL. New leads are
created as unresolved / INGEST_UNFINALIZED; notify overwrites that with a
real gated/failed/provider outcome. Historical unresolved_historical rows
are not rewritten.

Revision ID: 019_lead_ingest_unresolved
Revises: 018_lead_delivery_terminal_state
Create Date: 2026-09-09
"""

from alembic import op

revision = "019_lead_ingest_unresolved"
down_revision = "018_lead_delivery_terminal_state"
branch_labels = None
depends_on = None

_NEW_STATES = (
    "delivery_state IS NULL OR delivery_state IN ("
    "'sent','failed','gated',"
    "'provider_accepted','provider_identified',"
    "'unresolved','unresolved_historical')"
)
_OLD_STATES = (
    "delivery_state IS NULL OR delivery_state IN ("
    "'sent','failed','gated',"
    "'provider_accepted','provider_identified','unresolved_historical')"
)


def upgrade() -> None:
    with op.batch_alter_table("specialist_leads") as batch:
        batch.drop_constraint("ck_specialist_lead_delivery_state", type_="check")
        batch.create_check_constraint("ck_specialist_lead_delivery_state", _NEW_STATES)


def downgrade() -> None:
    op.execute(
        "UPDATE specialist_leads "
        "SET delivery_state = 'failed', "
        "    delivery_reason = 'INGEST_UNFINALIZED' "
        "WHERE delivery_state = 'unresolved'"
    )
    with op.batch_alter_table("specialist_leads") as batch:
        batch.drop_constraint("ck_specialist_lead_delivery_state", type_="check")
        batch.create_check_constraint("ck_specialist_lead_delivery_state", _OLD_STATES)
