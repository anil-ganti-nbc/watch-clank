# Human QC feedback contract

Built 2026-08-18 in response to the Citizen stale/out-of-stock flood
autopsy (see `CITIZEN_STALE_FLOOD_AUTOPSY_20260818.md`). Two independent
deliverables live here: the forensic root cause of that specific incident,
and a general, permanent human-QC review system for every future Event
Watch Clank ever surfaces.

## The core distinction

**EVENT != REVIEW. OBSERVATION != REVIEW. WATCH != REVIEW.**

- `Event` — Watch Clank's own machine-generated claim ("this reference was
  first observed", "this availability changed"). Immutable editorial
  history once created; never edited, never deleted by the QC system.
- `SourceObservation` — one successful parse of one source fetch. Raw
  provenance. Never touched by review actions.
- `Watch` — the canonical reference/identity row. Never touched by review
  actions.
- `EventReview` (`app/models/review.py`) — **human editorial feedback
  about one Event, under one evidence state, at one point in time.** It is
  not a correction to the Event, not a fact about the Watch, and not a
  permanent verdict on the reference.

A Review answers: who/what was reviewed, what was the verdict, when,
under what evidence (`evidence_observed_at`, `availability_status`,
`provenance_url` snapshot at review time), and — via
`review_metadata.correction_history` — whether a later human decision
superseded the first one for that *same* Event.

## Why a Review is not a permanent blacklist

Watch Clank's whole editorial posture is **recall-first**: a missed story
is expensive, a shown-but-uninteresting one is cheap (thirty seconds of a
human's attention). The QC system exists to make that cheap cost even
cheaper to triage, not to quietly convert it into a suppression list.
Concretely:

- `EventReview.event_id` is unique — a review is tied to **one specific
  Event row**, never to a Watch or a reference string.
- A later Event for the same watch/reference (a genuine new NEW_REFERENCE,
  a RESTOCK after a SOLD_OUT, a PRICE_CHANGE) is a **different row with
  its own event_id**. It has no review yet, so it is never pre-suppressed
  by a past verdict on a different Event — see
  `test_later_event_for_same_reference_not_permanently_suppressed` and
  `test_out_of_stock_review_does_not_suppress_later_restock_event` in
  `tests/test_web.py`.
- Nothing in `app/services/qc.py` filters, hides, or scores future Events
  based on past reviews. `prior_reviews_for_reference()` exists purely as
  **read-only, advisory structured context** a future ranking pass could
  choose to surface ("this reference has 2 prior OUT_OF_STOCK reviews") —
  it is called nowhere in the current pipeline. No behavior changes from
  its existence today.

## Dispositions

`USEFUL`, `NOT_USEFUL`, `FALSE_POSITIVE`, `OUT_OF_STOCK`
(`app.models.review.DISPOSITIONS`). A disposition applies to the Event
instance, not the reference. Corrections re-submit through the same
`POST /api/qc/review/{event_id}` endpoint used for the first review: the
row is updated in place and the prior verdict is appended to
`review_metadata.correction_history` (timestamped), so a correction is
auditable without a second table or a duplicate row per Event.

## The active queue

`/intelligence`'s Official Events section is the QC active queue:
editorially-eligible Events (same rule as before this sprint,
`event_row_is_editorially_eligible`, now expressed in SQL via
`app.services.qc._eligibility_clause`) with no `EventReview` row yet,
true cursor-paginated (`before_id`, `Event.id` descending) rather than the
old fixed top-40 slice that caused the Citizen incident's "additional
entries beyond the viewport" complaint. Reviewing a row removes it from
the queue immediately (client-side DOM removal + one replacement row
pulled in via `/api/qc/queue`) and is reflected in the `Unreviewed: N` /
`Reviewed today: N` counters. `/qc/history` is the permanent archive,
independently paginated, with inline correction support.

## Future-evidence contract (what this data is/isn't for yet)

No ML, no opaque scoring, no automatic suppression. If a future session
wires review data into ranking/classification, it must stay within the
same conservative envelope already proven safe by this sprint's tests:

**Acceptable** (annotate/deprioritize, never hide):
- Surface "this reference/source has prior OUT_OF_STOCK reviews" as
  context next to a new Event.
- Recognize an *exact* duplicate Event/evidence instance already reviewed
  as NOT_USEFUL and deprioritize its repeat in ranking.
- Always let a RESTOCK Event surface regardless of a prior OUT_OF_STOCK
  review on an earlier SOLD_OUT/NEW_REFERENCE Event for the same watch.

**Unacceptable** (never implement without a full new task/review):
- "Once OUT_OF_STOCK, never alert on this reference again."
- One FALSE_POSITIVE verdict lowering score for an entire model family
  (e.g. all Promaster).
- Any global per-manufacturer or per-collection score penalty derived from
  review data.
- Silently hiding unavailable products from discovery.

## Expected future uses (not built yet, named so a later session doesn't
have to rediscover the intent)

- Ranking evaluation / precision-recall benchmarking against real human
  verdicts.
- Source-quality measurement (which collectors' first observations get
  marked USEFUL most often).
- Repeated-noise detection (the same exact evidence re-surfacing after
  already being triaged).
- Eventually: a learned ranking/classification signal, and Diagnostic
  Clank-style periodic analysis of the review corpus. Neither exists
  today — this file documents intent, not a claim that either is live.

## Schema

`alembic/versions/008_event_reviews.py` — purely additive (`CREATE TABLE
event_reviews`), no existing table altered. Verified forward-safe against
both a fresh database and a real production-shaped copy of the field-test
DB (3,887 watches / 70 events / 9,518 observations, all counts unchanged
post-migration, `PRAGMA integrity_check` `ok`), and reversible
(`downgrade` drops only what `upgrade` created).
