"""Durable, redacted delivery receipts (track F).

Purely additive: one new table, no change to events/specialist_leads. The
existing Event.extra["delivery"] and SpecialistLead.delivery_state keep their
current meaning so nothing downstream breaks; this table is the evidence
they always lacked.

NOTE (2026-09-03 drift lesson): every NOT NULL column here carries a
server_default matching the ORM model, so a row written by any path -- ORM,
raw SQL, a future backfill -- can never fail the way
qualification_evidence.observed_at did, where the model declared
server_default=func.now() and the migration silently did not.
"""
import sqlalchemy as sa
from alembic import op

revision = "016_delivery_receipts"
down_revision = "015_qualification_reset_lineage"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "delivery_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="discord"),
        sa.Column("destination_alias", sa.String(64), nullable=True),
        sa.Column("lifecycle_state", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("provider_status", sa.Integer(), nullable=True),
        sa.Column("provider_message_id", sa.String(64), nullable=True),
        sa.Column("provider_channel_id", sa.String(64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_note", sa.Text(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_delivery_receipt_idempotency"),
    )
    op.create_index("ix_delivery_receipts_entity_type", "delivery_receipts", ["entity_type"])
    op.create_index("ix_delivery_receipts_entity_id", "delivery_receipts", ["entity_id"])
    op.create_index("ix_delivery_receipts_lifecycle_state", "delivery_receipts", ["lifecycle_state"])
    op.create_index("ix_delivery_receipts_destination_alias", "delivery_receipts", ["destination_alias"])


def downgrade():
    op.drop_index("ix_delivery_receipts_destination_alias", table_name="delivery_receipts")
    op.drop_index("ix_delivery_receipts_lifecycle_state", table_name="delivery_receipts")
    op.drop_index("ix_delivery_receipts_entity_id", table_name="delivery_receipts")
    op.drop_index("ix_delivery_receipts_entity_type", table_name="delivery_receipts")
    op.drop_table("delivery_receipts")
