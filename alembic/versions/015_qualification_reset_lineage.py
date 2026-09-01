"""Make qualification-reset transitions structurally auditable."""
import sqlalchemy as sa
from alembic import op

revision = "015_qualification_reset_lineage"
down_revision = "014_qualification_execution_identity"
branch_labels = depends_on = None


def upgrade():
    # Historical resets did not retain these facts.  NULL means unknown; do
    # not manufacture a predecessor by trying to reconstruct it later.
    op.add_column("qualification_evidence", sa.Column("prior_material_identity", sa.String(256), nullable=True))
    op.add_column("qualification_evidence", sa.Column("prior_epoch_id", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("qualification_evidence", "prior_epoch_id")
    op.drop_column("qualification_evidence", "prior_material_identity")
