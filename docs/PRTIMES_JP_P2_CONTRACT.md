# P2 offline publication-snapshot awareness (implemented)

P0 and P1 remain separate prerequisites. This implementation is manual/offline,
annotation-only, and does not enable a collector, production DB, notification,
scheduler or promotion. `PRTIMES_JP_MEMORY_COVERAGE_DESIGN.md` is the retained
historical design/diagnostic; this document supersedes its design-only P2 status,
not its retained earlier FAILs.

## Explicit boundary

```text
python -X utf8 -B -m app.collectors.prtimes_jp_staging --company 25284 --state-dir <owned-state> --replay <retained-or-fixture.xml> --at <aware-counterfactual-clock> --native-staging --coverage-snapshot <explicit.json> --coverage-sha256 <reviewed-lowercase-sha256>
```

Both coverage options are optional together. Genuine absence means
`COVERAGE_UNKNOWN`. An explicitly missing/unreadable/wrong-hash/malformed or
incompatible file refuses with HOLD before staging state creation/commit. Coverage
options cannot be used with live acquisition. No application DB is accepted as a
coverage input; no ambient settings, credentials, browser or HTTP is consulted.
The existing optional known-identity API is unchanged and independent.

## Version 1 strict JSON

Maximum 512,000 UTF-8 bytes, 500 stories, 500 scope URLs; exact object field sets,
unique story IDs and URL lists, no duplicate keys, NaN/Infinity or overflowing
nonfinite floats. Strings are bounded. No signing/fleet/exporter framework exists.
The executable fixture factory `snapshot_for` in `tests/test_prtimes_jp_coverage.py`
is a portable concrete schema example, explicitly SYNTHETIC TEST_FIXTURE.

Top-level fields: `schema_version=1`, `snapshot_id`, `newsroom=Notebookcheck`,
`provenance_kind=OWNER_EXPORT|TEST_FIXTURE`, `test_only` (true iff fixture),
aware `exported_at`, `cutoff`, `expires_at`, `complete_scope`, and `stories`.
Expiry is at most 24h after export; assessment/cutoff freshness is at most 24h.
A stale/expired/future snapshot cannot assert coverage or negative clearance.

`complete_scope` is null (partial/unknown) or an explicit object with region JP,
unique `article_urls` of the admitted PR TIMES format, and boolean `verified`.
Even a verified empty stories list establishes only absence within these exact
listed article contexts as of cutoff, never global "not published". A snapshot
must cover the assessed source publication instant. Partial or unverified scope
cannot give positives either.

Each story has `story_id`, actual publication `state`, canonical HTTPS `story_url`,
aware-or-null `published_at` and `verified_at`, `verification`, evidence URLs,
`identity_status`, `article_url` witness (nullable if genuinely unavailable),
`event_scope`, region JP, and full exact canonical maker/reference or both null
for explicit unresolved identity. OWNER_EXPORT story hosts are canonical
www.notebookcheck.net / www.notebookcheck.com; fixtures use only the reserved
notebookcheck.test host. A null clock/witness or unverified record remains unknown.
DRAFT, QC_USEFUL and DUPLICATE are valid uncertainty states, not publication.
Even a close matching article with contradictory entity/event data cannot clear
unknown. Malformed records refuse the explicit configuration rather than silently
being discarded into an apparently complete empty export.

## Matching and truth limits

- Known-model memory stays `KNOWN_SAME_MODEL` independently of publication.
- Positive matching requires the exact source article URL, material event scope,
  region and resolved full maker/reference (or explicit unresolved null pair).
  For an EVENT presentation with explicit reservation/order start, material scope
  is PREORDER. Shared titles/families/relationship proposals prove no publication.
- Original launch coverage cannot cover a new additional preorder article, even
  with the same exact model. Later legitimate news remains a review candidate.
- Fixtures yield `TEST_ONLY_COVERED_CONTEXT`; there is no factual positive claim.
  Actual OWNER_EXPORT matching would yield
  `VERIFIED_AGAINST_PROVIDED_SNAPSHOT`, not independent publisher verification.
  The operator hash authenticates bytes only, not external truth or ownership.
- No authenticated real Notebookcheck publication clock was supplied. The retained
  14-row research population contains eight indexed Notebookcheck URLs with null
  clocks. Its diagnostic partial projection preserves every null, uses
  UNVERIFIED and no complete scope, and remains COVERAGE_UNKNOWN. Wrapper export
  clocks are explicitly counterfactual replay clocks, not research acquisition,
  publication, publisher verification, or detection times.

## Durable/native behavior

Initial article evidence carries the as-of annotation/input binding. Separate
`coverage_annotations` rows retain the latest derived annotation and exact supplied
snapshot bytes/hash; changing a snapshot updates only this projection. Article raw
facts, digest and original evidence are never rewritten. Same article recurrence
adds no article/native lead and creates no identity conflict. Every annotation
includes snapshot ID/hash/cutoff/as-of/provenance/test-only and source context.

Before bridging, rederive the source context from literal raw source facts and
validate stored annotation equality against its retained snapshot. Native notes
carry this same evidence; QC queue/history and review submission rederive and
validate coverage against native URL/title/claim/region/entity scalars before
exposing/using notes. This is corruption detection, not tamper-proof storage or a
publisher-authentication mechanism. Coordinated replacement of all trusted input
bytes and operator authority is outside this local snapshot contract. Historical
as-of annotations remain historical; the next bridge reevaluates expiry at its
explicit evaluation clock. Genuine pre-P2 notes have no publication claim.

No coverage status suppresses/drops/deprioritizes a lead or relation. Existing
baseline, freshness, native URL-idempotence, verification and notification gates
are unchanged. No NEW_REFERENCE, Watch, Event or DeliveryReceipt is created by
this path. All demonstrations are owned staging/fixture/research replays.

## Qualification scope

Vertical RED/GREEN covers missing awareness, real snapshot/native roundtrip and
reader corruption refusal; preservation controls cover bounded strict JSON,
uncertainty/expiry, distinct events, unresolved identities, recurrence and actual
manual CLI. Final gate is the exact predecessor 133 identities plus finite P2
additions, not the historical repository suite. Independent exact-candidate review
belongs to the parent after builder freeze; no self-review PASS is asserted.
