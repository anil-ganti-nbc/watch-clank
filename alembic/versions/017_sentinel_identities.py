"""Horology Sentinel: known-identity store, sightings, source state.

Purely additive -- three new tables, no change to any existing table. The
Sentinel (see ai/handoff/SENTINEL_RUNBOOK.md) is a lightweight first-stage
tripwire; it never writes watches/observations/events, so it needs no FK
into any pipeline table except optional run-correlation handles.

NOTE (2026-09-03 drift lesson, same as 016): every NOT NULL column carries a
server_default matching the ORM model, so a row written by any path can
never fail a default the model promises.
"""
import sqlalchemy as sa

from alembic import op

revision = "017_sentinel_identities"
down_revision = "016_delivery_receipts"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "sentinel_identities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("identity_key", sa.String(320), nullable=False),
        sa.Column("identity_type", sa.String(16), nullable=False, server_default="REFERENCE"),
        sa.Column("manufacturer", sa.String(64), nullable=False),
        sa.Column("reference_raw", sa.String(128), nullable=True),
        sa.Column("reference_canonical", sa.String(128), nullable=True),
        sa.Column("fallback_url", sa.Text(), nullable=True),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("admitted_via", sa.String(16), nullable=False, server_default="SIGHTING"),
        sa.Column("first_source", sa.String(64), nullable=False),
        sa.Column("first_region", sa.String(32), nullable=True),
        sa.Column("first_source_url", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_seen_source", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("identity_key", name="uq_sentinel_identity_key"),
        sa.CheckConstraint(
            "identity_type IN ('REFERENCE', 'URL')", name="ck_sentinel_identity_type"
        ),
        sa.CheckConstraint(
            "admitted_via IN ('BASELINE', 'SIGHTING')", name="ck_sentinel_admitted_via"
        ),
    )
    op.create_index("ix_sentinel_identities_manufacturer", "sentinel_identities", ["manufacturer"])
    op.create_index(
        "ix_sentinel_identities_reference_canonical", "sentinel_identities", ["reference_canonical"]
    )

    op.create_table(
        "sentinel_sightings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "identity_id",
            sa.Integer(),
            sa.ForeignKey("sentinel_identities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("identity_key", sa.String(320), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("region", sa.String(32), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("reference", sa.String(128), nullable=True),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("alert_state", sa.String(16), nullable=False, server_default="SENT"),
        sa.Column("alert_error", sa.Text(), nullable=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("collector_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "alert_state IN ('SENT', 'FAILED', 'DISABLED', 'CAPPED')",
            name="ck_sentinel_alert_state",
        ),
    )
    op.create_index("ix_sentinel_sightings_identity_id", "sentinel_sightings", ["identity_id"])
    op.create_index("ix_sentinel_sightings_identity_key", "sentinel_sightings", ["identity_key"])
    op.create_index("ix_sentinel_sightings_run_id", "sentinel_sightings", ["run_id"])

    op.create_table(
        "sentinel_source_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("armed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(32), nullable=True),
        sa.UniqueConstraint("source", name="uq_sentinel_source_state_source"),
    )


def downgrade():
    op.drop_table("sentinel_source_state")
    op.drop_index("ix_sentinel_sightings_run_id", table_name="sentinel_sightings")
    op.drop_index("ix_sentinel_sightings_identity_key", table_name="sentinel_sightings")
    op.drop_index("ix_sentinel_sightings_identity_id", table_name="sentinel_sightings")
    op.drop_table("sentinel_sightings")
    op.drop_index(
        "ix_sentinel_identities_reference_canonical", table_name="sentinel_identities"
    )
    op.drop_index("ix_sentinel_identities_manufacturer", table_name="sentinel_identities")
    op.drop_table("sentinel_identities")
