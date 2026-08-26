# WATCH CLANK ARCHITECTURE NOTES — QC Volume & Run Provenance
2026-08-26. Incident-driven clarifications; governing documents unchanged
(FLEET_LAWS v1, WATCH_SOAK_CONTRACT.md, HUMAN_QC_FEEDBACK_CONTRACT.md).

## Principle: catalogue-pass status is an invocation fact

**Catalogue-pass status is an invocation fact, never an inference from
output cardinality.**

Whether a collector run "walked the real catalogue" is determined by how
the run was INVOKED (its resolved budget), not by what it happened to
discover. A bounded smoke/validation run that discovers 100 items is still
a smoke run; an unbounded pass that discovers 0 items (empty catalogue,
blocked source) is still a catalogue pass.

Rationale — learned twice the hard way:

1. 2026-08-25 QC flood: four single-item deployment-validation runs
   exhausted INITIAL_FILL_RUNS because qualification inferred "real pass"
   from successful-run count alone.
2. The first repair used `discovered_count > 1` — output cardinality
   again — which would have broken the moment a bounded validation run
   discovered 2+ items.

Correct implementation (`app/services/initial_fill.py::_is_catalogue_pass`):
qualification reads the runner-persisted `summary_metadata.max_items`:

| persisted value            | meaning                    | qualifies |
|----------------------------|----------------------------|-----------|
| explicit `null`            | known unbounded invocation | yes       |
| integer ≥ registry default | known full-budget pass     | yes       |
| smaller integer            | known bounded/smoke run    | no        |
| key absent / metadata NULL | unknown provenance         | no (conservative) |

Corollary for any future Clank work: when run behaviour must depend on how
a run was invoked, persist the invocation fact at run time and read it back
through one representation. Never reconstruct intent from result shapes.

## Queue accounting denominators (2026-08-26 incident)

Two distinct populations; never conflate them in counters or reports:

- **RAW unreviewed population**: every Event with no EventReview row.
  The audit view. Incident value: 639.
- **DEFAULT human-QC queue**: raw minus availability events lacking
  `editorial_eligible`, minus `human_qc_deprioritized` rows. What the UI's
  FIFO wall shows. Incident value: 580 pre-repair, 41 post-repair replay.

Additionally, availability events split into eligible (default queue) and
background (tiered review via event-type filter). "Reviewable somewhere"
is not "sitting on the default FIFO wall" — reports must state the tier.

## Review provenance is three-valued

`EventReview.review_metadata.mode` ∈ {individual, bulk, absent}. Absent is
its own accounting class (**unspecified**) — never silently relabelled
"individual". Reclassifying historical rows requires a separately approved
migration; analytics report three classes, not two
(`qc.reviewed_today_breakdown`).

## Weak first-sighting threshold

`FIRST_SEEN_BY_CLANK` at story_score ≤ `WEAK_FIRST_SEEN_QC_THRESHOLD`
(`app/services/pipeline_constants.py`, currently 15) carries no affirmative
novelty evidence: auto-flagged `human_qc_deprioritized` at creation.
Persisted and auditable; hidden from the default queue only. Evidence base:
every USEFUL FS in production history scored ≥ 25; the flood class scored 15.
