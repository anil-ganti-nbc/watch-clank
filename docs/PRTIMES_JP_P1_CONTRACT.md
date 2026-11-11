# P1 contract v2: announcement relationships and event-preserving model memory

This is an offline, manual-only staging contract. It is not a production migration,
activation, live detection claim, or independent review.

## Relevance

Retail opening/reopening and event-demonstration primary claims do not become
watch launches merely because an abstract mentions watch fans or stocked watches.
A primary watch release remains eligible even when a pop-up accompanies it.
The retained Destination Art artist-creation abstract and existing mechanical,
One Piece and TIMEX controls remain eligible. No article IDs or collection names
are used by the new rejection mechanism.

## Separate durable memories

- Article identity remains exact company + URL; altered raw evidence at an
  existing URL requires HOLD. Baseline remains silent.
- Product identity remains an explicitly labelled exact maker/reference pair.
  `KNOWN_SAME_MODEL` means precisely that, not duplicate announcement. A distinct
  article with an explicit reissue, additional preorder, preorder or restock is
  still a fresh review candidate. A known-model re-sighting without such an event
  is suppressed. Uncertain actual captured models remain `UNRESOLVED`.
- The additive marked-staging `announcement_relations` table stores immutable
  version-1 evidence for `PROPOSED_ANNOUNCEMENT_RELATION`, `support=SUPPORTED`,
  `identity_effect=NONE`. It records both URLs, companies, raw-fact SHA256 hashes,
  publication timestamps, supporting literal title/description passages, the
  common exact quoted subject, and explicit later-event literal. Neither family
  overlap nor this annotation merges products or suppresses an announcement.
- Relations require the same issuer, ordered usable publication timestamps,
  watch-subject records, exact quoted subject and a later explicit event. Generic
  quoted watch words and distinct primary quoted families do not correlate.
  Mixed smartwatch/ring families can have an editorial family relation but do
  not enter conventional product identity or native conventional-watch QC.
- Before using stored annotations, staging/bridge validate raw-fact digests,
  source attribution and regenerated relationships against current retained
  records. Missing/malformed/contradictory evidence fails closed. Native notes
  retain the current article's provenance and relationship; old baseline evidence
  is not rewritten. Repeated stage/bridge is idempotent.

## Explicit test contract maintenance

The obsolete `test_desired_genuine_code_vein_cross_story_suppression` required a
blanket drop. It was not repaired or turned green. Its exact predecessor source
and authentic failing/xfail receipts remain in immutable prior offline/P0
archives. Owner-authorized v2 prospectively replaces it with
`test_v2_genuine_code_vein_relationship_preserves_additional_preorder`: a supported
proposed relation, no SKU or NEW_REFERENCE assertion, retained additional preorder,
and repeated bridge idempotency. P0 UTC persistence code/readbacks are preserved.

## Evidence and limits

`corrective/p1-builder` in the separate mission journal contains exact RED/GREEN
source snapshots/streams, fixed-90 unchanged AI-label comparison, all 452 current
classifier rows, native counterfactual QC controls, final bounded XML/inventory,
and frozen cumulative candidate bytes. AI labels are not human truth; UNKNOWN
labels are excluded, never relabelled. Publication+1h clocks are counterfactual,
not historical live detection. Other issuers' measured unrelated errors are not
polished in this finite tranche. No real product-precision claim is made.

P2 newsroom/publication coverage remains unimplemented and separately gated.
