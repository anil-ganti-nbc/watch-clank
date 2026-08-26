"""Shared pipeline tuning constants.

WEAK_FIRST_SEEN_QC_THRESHOLD: a FIRST_SEEN_BY_CLANK event at or below this
story_score carries no affirmative novelty evidence (no named collaboration,
no recognisable family, no publication corroboration) — it is catalogue
bookkeeping. Evidence base (2026-08-25 QC flood forensics): every USEFUL
FIRST_SEEN in production history scored >= 25; the flood rows all scored 15.
Threshold sits at the observed weak-class ceiling so genuine leads keep
queueing while pure catalogue discovery deprioritizes out of the default QC
view (still fully persisted and auditable).
"""

WEAK_FIRST_SEEN_QC_THRESHOLD = 15.0
