"""initial_schema – Stage 1 authoritative migration

Revision ID: 001_initial
Revises:
Create Date: 2026-08-03

Creates all tables with portable JSON, check constraints, and required indexes.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "snapshot_blobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("filepath", sa.Text(), nullable=False),
        sa.Column("compression_type", sa.String(length=32), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("schema_version", sa.String(length=32), server_default="1", nullable=False),
        sa.Column("encoding", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_snapshot_blob_content_hash"),
    )
    op.create_index("ix_snapshot_blobs_content_hash", "snapshot_blobs", ["content_hash"])

    op.create_table(
        "snapshot_fetches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("blob_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("collector_version", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_headers", sa.JSON(), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["blob_id"], ["snapshot_blobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_snapshot_fetches_blob_id", "snapshot_fetches", ["blob_id"])
    op.create_index("ix_snapshot_fetches_source_url", "snapshot_fetches", ["source_url"])
    op.create_index("ix_snapshot_fetches_collector_id", "snapshot_fetches", ["collector_id"])

    op.create_table(
        "collector_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("collector_version", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("discovered_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fetched_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("parsed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new_watch_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("observation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warning_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("summary_metadata", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED', 'ZERO_ITEMS')",
            name="ck_run_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_collector_runs_collector_id", "collector_runs", ["collector_id"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("story_score", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("data_completeness_score", sa.Float(), nullable=True),
        sa.Column("scoring_rule_version", sa.String(length=32), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_event_type", "events", ["event_type"])

    op.create_table(
        "watch_families",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("manufacturer", sa.String(length=64), nullable=False),
        sa.Column("brand", sa.String(length=64), nullable=False),
        sa.Column("family_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="PROVISIONAL", nullable=False),
        sa.Column("grouping_rule", sa.String(length=128), nullable=True),
        sa.Column("grouping_rule_version", sa.String(length=32), nullable=True),
        sa.Column("grouping_confidence", sa.Float(), nullable=True),
        sa.Column("fields_compared", sa.JSON(), nullable=True),
        sa.Column("manual_override", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PROVISIONAL', 'RULE_MATCHED', 'CONFIRMED', 'MANUAL_REVIEW')",
            name="ck_family_status",
        ),
        sa.CheckConstraint(
            "grouping_confidence IS NULL OR (grouping_confidence >= 0 AND grouping_confidence <= 100)",
            name="ck_family_confidence_range",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("manufacturer", "brand", "family_key", name="uq_family_key"),
    )

    op.create_table(
        "watches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("manufacturer", sa.String(length=64), nullable=False),
        sa.Column("brand", sa.String(length=64), nullable=False),
        sa.Column("collection", sa.String(length=128), nullable=True),
        sa.Column("model_name", sa.String(length=256), nullable=True),
        sa.Column("reference_raw", sa.String(length=128), nullable=False),
        sa.Column("reference_canonical", sa.String(length=128), nullable=False),
        sa.Column("family_candidate_key", sa.String(length=256), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("movement_type", sa.String(length=64), nullable=True),
        sa.Column("caliber_or_module", sa.String(length=64), nullable=True),
        sa.Column("solar", sa.Boolean(), nullable=True),
        sa.Column("bluetooth", sa.Boolean(), nullable=True),
        sa.Column("radio_sync", sa.Boolean(), nullable=True),
        sa.Column("gps", sa.Boolean(), nullable=True),
        sa.Column("case_material", sa.String(length=128), nullable=True),
        sa.Column("crystal", sa.String(length=128), nullable=True),
        sa.Column("water_resistance_m", sa.Integer(), nullable=True),
        sa.Column("limited_edition", sa.Boolean(), nullable=True),
        sa.Column("extra_specs", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint(
            "water_resistance_m IS NULL OR water_resistance_m >= 0",
            name="ck_water_resistance_non_negative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manufacturer", "brand", "reference_canonical",
            name="uq_watch_manufacturer_brand_canonical",
        ),
    )
    op.create_index("ix_watches_manufacturer", "watches", ["manufacturer"])
    op.create_index("ix_watches_brand", "watches", ["brand"])
    op.create_index("ix_watches_reference_canonical", "watches", ["reference_canonical"])
    op.create_index("ix_watches_family_candidate_key", "watches", ["family_candidate_key"])

    op.create_table(
        "pipeline_ledger",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("input_ref", sa.Text(), nullable=True),
        sa.Column("output_ref", sa.Text(), nullable=True),
        sa.Column("collector_version", sa.String(length=32), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("scoring_rule_version", sa.String(length=32), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["collector_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_ledger_correlation_id", "pipeline_ledger", ["correlation_id"])
    op.create_index("ix_pipeline_ledger_run_id", "pipeline_ledger", ["run_id"])
    op.create_index("ix_pipeline_ledger_stage", "pipeline_ledger", ["stage"])

    op.create_table(
        "source_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("watch_id", sa.Integer(), nullable=False),
        sa.Column("fetch_id", sa.Integer(), nullable=True),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("collector_version", sa.String(length=32), nullable=False),
        sa.Column("parser_id", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("availability_status", sa.String(length=64), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("source_trust_score", sa.Float(), nullable=False),
        sa.Column("overall_confidence", sa.Float(), nullable=False),
        sa.Column("field_confidence", sa.JSON(), nullable=True),
        sa.Column("parser_warnings", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "source_trust_score >= 0 AND source_trust_score <= 100",
            name="ck_source_trust_range",
        ),
        sa.CheckConstraint(
            "overall_confidence >= 0 AND overall_confidence <= 100",
            name="ck_overall_confidence_range",
        ),
        sa.CheckConstraint("price IS NULL OR price >= 0", name="ck_price_non_negative"),
        sa.ForeignKeyConstraint(["watch_id"], ["watches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fetch_id"], ["snapshot_fetches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_observations_watch_id", "source_observations", ["watch_id"])
    op.create_index("ix_source_observations_fetch_id", "source_observations", ["fetch_id"])
    op.create_index("ix_source_observations_observed_at", "source_observations", ["observed_at"])

    op.create_table(
        "family_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("watch_id", sa.Integer(), nullable=False),
        sa.Column("family_id", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("assignment_rule", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["watch_id"], ["watches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["family_id"], ["watch_families.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("watch_id", "family_id", name="uq_watch_family"),
    )
    op.create_index("ix_family_memberships_watch_id", "family_memberships", ["watch_id"])
    op.create_index("ix_family_memberships_family_id", "family_memberships", ["family_id"])

    op.create_table(
        "event_watches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("watch_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["watch_id"], ["watches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_watches_event_id", "event_watches", ["event_id"])
    op.create_index("ix_event_watches_watch_id", "event_watches", ["watch_id"])


def downgrade() -> None:
    op.drop_table("event_watches")
    op.drop_table("family_memberships")
    op.drop_table("source_observations")
    op.drop_table("pipeline_ledger")
    op.drop_table("watches")
    op.drop_table("watch_families")
    op.drop_table("events")
    op.drop_table("collector_runs")
    op.drop_table("snapshot_fetches")
    op.drop_table("snapshot_blobs")
