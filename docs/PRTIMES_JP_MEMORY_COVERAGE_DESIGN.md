# PR TIMES: offline qualification and memory/coverage design

Status: DESIGN ONLY; no production database opened; no adapter, scheduler,
acquisition, delivery or promotion changes. Classifier and seven predecessor files
remain frozen. Existing production activation HOLD remains in force.

## Current seams (source inspection, not database inspection)

- `app/collectors/prtimes_jp_staging.py:read_known_identities` explicitly opens
  SQLite `mode=ro`, calls `SentinelStore.known_identity_keys()` and, if the
  `watches` table exists, uses `Watch.manufacturer`, `reference_raw` and
  `reference_canonical` with `app.sentinel.identity.build_identity`.
- `app/sentinel/store.py:known_identity_keys` reads `SentinelIdentity.identity_key`;
  this is observed identity memory, not evidence of newsroom publication.
- `run_feed` separately deduplicates company/exact article URL and exact resolved
  identity keys. No identity-less cross-story equivalence is implemented.
- `SpecialistLeadService.ingest_candidate` is the native URL-idempotency seam;
  `bridge_native` creates only the owned marked staging schema. Its delivery writer
  records `gated / EDITORIAL_NOTIFICATIONS_DISABLED`, not a delivery receipt.
- `app/services/qc.py` exposes `fetch_lead_queue_page`,
  `submit_lead_review`, `fetch_lead_history_page`. `SpecialistLeadReview` permits
  USEFUL, NOT_USEFUL, DUPLICATE and FALSE_POSITIVE. A verdict is about one lead and
  evidence state. USEFUL is **not published**, DUPLICATE is **not product identity**,
  NOT_USEFUL is **not a permanent reference blacklist**.
- The inspected model inventory has Watch, observations, Events, ReleaseLead,
  specialist leads/reviews and delivery records, but no newsroom publication
  coverage model/API. Nothing here pretends an existing publication adapter exists.

## Proposed immutable snapshot boundary (not implemented)

An owner-authorized exporter, outside acquisition, would produce a dated immutable
snapshot. Never point this diagnostic at the application database. Record schema
version, exporter version, source provenance, exported_at UTC, cutoff time,
row count and content SHA-256; preserve the exact source artifact separately.
The consumer validates manifest membership, timestamps and hashes before use.
Missing, malformed, incompatible, expired or incomplete snapshots result in
`MEMORY_UNKNOWN` / `COVERAGE_UNKNOWN`, never permission to label novelty clear.
A missing known key in a partial export proves nothing. An empty snapshot is not
proof that the newsroom has published nothing.

Keep three independent evidence sets and decisions:

1. **Known exact maker/reference**: full canonical maker + reference from the
   existing identity function, source row identifier and observation time. A
   positive exact match is `KNOWN_IDENTITY`, not `ALREADY_COVERED`. Preserve region,
   event/category and supporting URL. Do not family-match or merge variants.
2. **Unresolved cluster proposal**: exact member article URLs/company IDs, raw
   title/description/date hashes, hypothesized relationship, reviewer identity,
   rationale and state (`PROPOSED`, `CONFIRMED_STORY_RELATION`, `REJECTED`). This is
   an annotation for human triage, not a product identity or automatic suppression.
   Repeated titles, character names and family names are insufficient. Preserve
   mixed smartwatch/ring articles and distinct character/model branches; no invented
   maker/reference identity. New evidence can split a prior proposal.
3. **Published newsroom coverage**: require an actual published story URL, reliable
   publication time/timezone, entity identity or explicit unresolved scope,
   covered event/context (launch vs preorder vs additional order vs restock),
   region, evidence/source attribution and verification timestamp. Drafts,
   assignments, QC_USEFUL, Discord receipts and official Events do not count as
   published coverage. A supplied boolean `already_covered` is not acceptable.

Coverage lookup returns `VERIFIED_COVERED_CONTEXT`, `VERIFIED_NOT_FOUND_IN_COMPLETE_SCOPE`
(or a narrower explicit equivalent), or `UNKNOWN`, with snapshot ID, evidence URLs,
cutoff and reason. Unknown coverage is never silently cleared. Even verified prior
coverage does not erase an article; a material later event may warrant new review.
No implementation should call publication or reference novelty confirmed on issuer
prose alone. A future production adapter needs the real newsroom API and owner
permission; no mock implementation is substituted here.

## Genuine retained cross-story cases

Private feeds remain external. `PRTIMES_JP_CAPTURE_DIR` binds the authorized root;
absence explicitly skips as NOT QUALIFIED, explicit broken input fails. The replay
helper puts unchanged `<item>...</item>` bytes in new RDF framing and verifies the
canonical parsed item fields equal the original feed. Dates and URLs are never
rewritten. These are retained-item replays, not historical feed snapshots.

All URLs below use `https://prtimes.jp/main/html/rd/p/`:

| Pair suffixes | Evidence and actual result | Qualification |
|---|---|---|
| `000001831.000025284.html` → `000002031.000025284.html` | April CODE VEIN II original vs October additional order, same named weapon motif; both unresolved. Later different URL inserts and remains a review candidate. Exact repetition inserts zero. | Cross-story suppression **FAIL/HOLD**, preserved diagnostic desired assertion. No claim of proven SKU equality. |
| `000002000.000025284.html` → `000002002.000025284.html` | October 2/6 Madoka family announcements; smartwatch + ring mixture, no resolved refs. Both distinct articles retained; mixed remains sidecar-only in native transport. | Relationship proposal only; do not merge mixed models into a conventional-watch identity. |
| `000002031.000025284.html` → `000002058.000025284.html` | Code Vein vs Onimusha; distinct subject/model-family control, Onimusha title names three characters/models. | Both articles retained; unresolved identities preserved, no inferred character-to-SKU mapping. |

Pair admission clocks are explicitly counterfactual later publication +1 hour.
They do not establish when a collector would really have detected either story.
The classifier-only 452-row replay additionally records the fixed historical
observation clock and publication+1h counterfactual separately. Output counts are
classifier populations, never gold labels, precision, recall or measured lead time.
Independent relevance labels belong to a separate parent-owned sample.

## Synthetic native QC and boundary findings

The new tests create only owned fresh staging files. They demonstrate silent first
baseline → fresh new conventional/unresolved lead → native queue read → USEFUL or
NOT_USEFUL submission → new-session history read → repeated URL/bridge adds zero.
Smartwatch stays sidecar-only. All created native leads remain UNCONFIRMED and
`gated / EDITORIAL_NOTIFICATIONS_DISABLED`; notified_at remains null. Readback checks
zero watches, source observations, release leads, Events, delivery receipts and
CollectorRuns. This is synthetic workflow evidence, not actual operator feedback.

**Additional diagnostic FAIL/HOLD:** at original `2026-11-06T10:00:00+09:00`
publication +72h+1s, pure canonical classification says STALE_PUBLICATION but native
bridge readback remains FRESH. The stored native timestamp is naive
`2026-11-06 10:00:00`; the original offset has been lost in the SQLite roundtrip.
The unchanged native bridge reclassifies this as UTC. The exact +72h boundary
passes, and an explicitly UTC +72h+1s control correctly becomes stale and leaves
the queue empty. The JST desired stale assertion remains a strict xfail; a separate
characterization documents actual behavior. This is not a passed stale-boundary
qualification or permission to ship. No runtime repair was authorized or made.

## Reproduction

Use the qualified project interpreter from the repository, with inherited offline
network/.env denial harness where available:

```text
python -X utf8 -B -m pytest -p no:cacheprovider tests/test_prtimes_jp_staging.py tests/test_prtimes_jp_native.py tests/test_prtimes_jp_offline_qualification.py -q -rx
python -X utf8 -B -m scripts.qualify_prtimes_jp_offline --capture-dir <authorized-private-root> --output <new-owned-json-file> --at 2026-11-06T17:30:00+00:00
```

The tool refuses overwriting its output, requires exactly the retained 452 records,
and performs no state ingestion, source requests or database access. Only the
bounded selected tests are qualified; the historical full suite is not run.
Strict xfails are unresolved desired requirements, not green release gates. Use
`--runxfail` on those exact diagnostic nodes to see the underlying failing assertions.
