"""event_reviews disposition vocabulary gains DUPLICATE

Phase 9 of the 2026-08-21 remediation: SpecialistLeadReview has always
supported USEFUL / NOT_USEFUL / DUPLICATE / FALSE_POSITIVE, but EventReview's
CHECK constraint omitted DUPLICATE -- so an operator who triages a genuine
duplicate Event (the same reference surfacing through two collectors, or a
re-sent announcement) had to file it as NOT_USEFUL or FALSE_POSITIVE, both
of which mean something different and both of which pollute the feedback
signal the QC system is meant to accumulate.

A duplicate Event is a real editorial disposition and must be recordable as
one. This migration rebuilds event_reviews via batch_alter_table purely to
swap the CHECK constraint's value list; no column changes, no data changes,
no row loss. Existing rows all satisfy the new constraint (their values are
a subset of the old one).

Revision ID: 011_event_review_duplicate
Revises: 010_review_is_corrected
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "011_event_review_duplicate"
down_revision: Union[str, None] = "010_review_is_corrected"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("event_reviews") as batch:
        batch.drop_constraint("ck_event_review_disposition", type_="check")
        batch.create_check_constraint(
            "ck_event_review_disposition",
            "disposition IN ('USEFUL', 'NOT_USEFUL', 'DUPLICATE', 'FALSE_POSITIVE', 'OUT_OF_STOCK')",
        )


def downgrade() -> None:
    # Refuse silently losing data is not a concern here: DUPLICATE rows would
    # violate the restored constraint, so migrate them back to NOT_USEFUL
    # first -- the closest available verdict under the old vocabulary.
    op.execute("UPDATE event_reviews SET disposition = 'NOT_USEFUL' WHERE disposition = 'DUPLICATE'")
    with op.batch_alter_table("event_reviews") as batch:
        batch.drop_constraint("ck_event_review_disposition", type_="check")
        batch.create_check_constraint(
            "ck_event_review_disposition",
            "disposition IN ('USEFUL', 'NOT_USEFUL', 'FALSE_POSITIVE', 'OUT_OF_STOCK')",
        )
