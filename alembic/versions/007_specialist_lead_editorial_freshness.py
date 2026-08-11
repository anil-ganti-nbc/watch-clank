"""specialist lead editorial_freshness + backfill

Adds editorial_freshness/freshness_reason/freshness_evaluated_at to
specialist_leads and deterministically backfills every pre-existing row
(fixes the Epoch 1 stale-material-shown-as-new incident -- see
ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md). Backfill logic is intentionally
self-contained here rather than importing app.services.freshness, so this
migration's behavior never silently changes if that module is edited later.

Revision ID: 007_specialist_lead_editorial_freshness
Revises: 006_operational_epochs
Create Date: 2026-08-11
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "007_specialist_lead_editorial_freshness"
down_revision: Union[str, None] = "006_operational_epochs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FRESHNESS_WINDOW_HOURS = 72
_OBSERVATION_TIME_SOURCE_TYPES = {"RETAILER_EARLY_LISTING"}


def _classify(*, source_type, ingestion_method, is_baseline, published_at, discovered_at, now) -> tuple[str, str]:
    if is_baseline:
        return "BASELINE", "discovered during an epoch baseline run"

    reference_time = published_at
    reference_label = "published"
    if reference_time is None:
        if source_type in _OBSERVATION_TIME_SOURCE_TYPES:
            reference_time = discovered_at
            reference_label = "observed"
        elif ingestion_method == "manual":
            return "MANUAL_UNDATED", "manually ingested with no publication timestamp supplied"
        else:
            return "UNKNOWN_TIMESTAMP", "no publication timestamp available for this source class"

    age = now - reference_time
    window = timedelta(hours=FRESHNESS_WINDOW_HOURS)
    if age <= window:
        return "FRESH", f"{reference_label} {age} ago, within the {FRESHNESS_WINDOW_HOURS}h freshness window"
    return "STALE_PUBLICATION", f"{reference_label} {age} ago, exceeds the {FRESHNESS_WINDOW_HOURS}h freshness window"


def upgrade() -> None:
    with op.batch_alter_table("specialist_leads") as batch_op:
        batch_op.add_column(sa.Column("editorial_freshness", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("freshness_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("freshness_evaluated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint(
            "ck_specialist_lead_editorial_freshness",
            "editorial_freshness IS NULL OR editorial_freshness IN "
            "('FRESH','STALE_PUBLICATION','BASELINE','UNKNOWN_TIMESTAMP','MANUAL_UNDATED')",
        )

    conn = op.get_bind()
    now = datetime.now(UTC)
    rows = conn.execute(
        sa.text(
            "SELECT id, source_type, ingestion_method, is_baseline, published_at, discovered_at "
            "FROM specialist_leads"
        )
    ).fetchall()

    for row in rows:
        published_at = row.published_at
        if isinstance(published_at, str):
            published_at = datetime.fromisoformat(published_at)
        if published_at is not None and published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)

        discovered_at = row.discovered_at
        if isinstance(discovered_at, str):
            discovered_at = datetime.fromisoformat(discovered_at)
        if discovered_at is not None and discovered_at.tzinfo is None:
            discovered_at = discovered_at.replace(tzinfo=UTC)

        state, reason = _classify(
            source_type=row.source_type,
            ingestion_method=row.ingestion_method,
            is_baseline=bool(row.is_baseline),
            published_at=published_at,
            discovered_at=discovered_at,
            now=now,
        )
        conn.execute(
            sa.text(
                "UPDATE specialist_leads SET editorial_freshness = :state, freshness_reason = :reason, "
                "freshness_evaluated_at = :now WHERE id = :id"
            ),
            {"state": state, "reason": reason, "now": now.isoformat(), "id": row.id},
        )


def downgrade() -> None:
    with op.batch_alter_table("specialist_leads") as batch_op:
        batch_op.drop_constraint("ck_specialist_lead_editorial_freshness", type_="check")
        batch_op.drop_column("freshness_evaluated_at")
        batch_op.drop_column("freshness_reason")
        batch_op.drop_column("editorial_freshness")
