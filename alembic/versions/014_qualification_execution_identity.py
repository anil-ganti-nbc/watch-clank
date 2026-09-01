"""Link qualification evidence to actual executions and material identity."""
import sqlalchemy as sa
from alembic import op

revision = "014_qualification_execution_identity"
down_revision = "013_qualification_evidence"
branch_labels = depends_on = None


def upgrade():
    op.add_column("qualification_evidence", sa.Column("execution_id", sa.Integer(), nullable=True))
    op.add_column("qualification_evidence", sa.Column("material_identity", sa.String(256), nullable=True))
    op.add_column("qualification_evidence", sa.Column("outcome", sa.String(32), nullable=True))
    op.create_index("ix_qualification_evidence_execution_id", "qualification_evidence", ["execution_id"])
    # 013 wrote NATURAL from a delivery-side default, not scheduler evidence.
    # Preserve those rows but make the limitation explicit rather than re-label
    # them as proven scheduled qualification.
    op.execute("UPDATE qualification_evidence SET provenance = 'UNKNOWN' WHERE provenance = 'NATURAL' AND execution_id IS NULL")


def downgrade():
    op.drop_index("ix_qualification_evidence_execution_id", table_name="qualification_evidence")
    op.drop_column("qualification_evidence", "outcome")
    op.drop_column("qualification_evidence", "material_identity")
    op.drop_column("qualification_evidence", "execution_id")
