"""Add durable qualification evidence; existing history remains unknown."""
import sqlalchemy as sa
from alembic import op
revision = "013_qualification_evidence"
down_revision = "012_lead_delivery_state"
branch_labels = depends_on = None
def upgrade():
    op.create_table("qualification_evidence", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("collector_id", sa.String(64), nullable=False), sa.Column("epoch_id", sa.String(64), nullable=False), sa.Column("provenance", sa.String(16), nullable=False, server_default="UNKNOWN"), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("change_identity", sa.String(128)), sa.Column("reset_reason", sa.Text()), sa.Column("intervention_treatment", sa.String(32)), sa.Column("eligibility_gate", sa.String(16), nullable=False, server_default="UNKNOWN"), sa.Column("qualification_gate", sa.String(16), nullable=False, server_default="UNKNOWN"))
    op.create_index("ix_qualification_evidence_collector_id", "qualification_evidence", ["collector_id"])
def downgrade():
    op.drop_index("ix_qualification_evidence_collector_id", table_name="qualification_evidence"); op.drop_table("qualification_evidence")
