# WATCH SOAK CONTRACT — Experimental Collector Promotion
2026-08-25. Governing documents: FLEET_LAWS v1 (Law 1/2/3/8),
NO_PROMOTION_POLICY.md, WATCH_EVENT_SEMANTICS.md.
"a Clank is never finished" — promotion is evidence accumulation, never completion.

## Delivery-silence canon (fleet-wide maturity gate)

External delivery (Discord/editorial notifications) is a **promotion
privilege**, not a configuration default. Collectors in EXPERIMENTAL
maturity are externally silent for ANY event type and ANY score; their
events remain fully visible in dashboard/QC. This is enforced at runtime by
`app/services/delivery_gate.py::experimental_delivery_blocked`, consulted
by the notify path — not merely by `discord_first_seen_enabled=false` plus
initial-fill suppression, which were incidental mechanisms that happened to
stack. Promotion review removes the collector id from
`EXPERIMENTAL_MATURITY_COLLECTORS`; nothing is auto-added or auto-removed.
The gate set is mechanically cross-checked against this document by
`tests/test_production_wiring.py::test_soak_contract_experimental_set_matches_registry_controls`.

## Collectors entering soak

| Collector | Brand | Region | Family | Maturity |
|---|---|---|---|---|
| tissot_sitemap | Tissot | US (en-us) | Sitemap Delta | EXPERIMENTAL_READY_FOR_HETZNER |
| timex_uk_products | Timex | UK | Shopify Catalogue | EXPERIMENTAL_READY_FOR_HETZNER |

## Cadence specification (Claude configures on canonical scheduler)

- Both collectors: 360-minute cadence, matching the established
  `timex_products` pattern. Do not probe faster; sitemap-delta sources have
  no sub-6-hour signal value and aggressive polling is anti-bot bait.
- They MAY share an existing run group only if the group already sequences
  same-shape collectors with per-collector locks. Each collector keeps its
  own `<collector_id>.run.lock` — locks must never be shared.
- Backoff: existing http_util retry/backoff applies unchanged. A BLOCKED run
  exits the normal path; do NOT add manual re-runs outside schedule.
- Budget: default_max_items=300 per run. Do not raise during soak.

## Soak evidence contract (per collector)

Minimum 4 successful scheduled acquisition runs before promotion review.
Per run, record from `collector_runs` + `source_component_states`:

scheduled(y/n) · started_at · fetch attempts · successful fetches ·
discovered_count · parsed_count · observations written · new identities ·
repeated identities · events emitted · QC-eligible events · errors ·

Derived state tracked across runs:
- acquisition health trajectory (SUCCESS/ZERO_ITEMS/BLOCKED mix)
- editorial yield (events → USEFUL verdicts conversion)
- traversal progress (distinct SKUs monotonic non-decreasing)
- STAGNANT / ZERO frequency from health module
- reference collisions (distinct refs < watch rows ⇒ identity bug)
- regional duplicates (same SKU via two regional collectors = ONE watch)

A process exit code of 0 is NOT evidence. Evidence is the DB rows.

## QC during soak

All candidates reviewed through the existing QC queue with the full
disposition set (USEFUL / NOT_USEFUL / DUPLICATE / FALSE_POSITIVE).
Watch specifically for:
- old catalogue stock surfacing as first-seen;
- regional duplicates (US SKU re-listed on UK storefront);
- REACTIVATED/backorder tags misread as launches (Peanuts-class);
- sitemap resurfacing after removal/re-add;
- stale collaborations;
- Shopify bulk-touch timestamps strengthening bogus freshness;
- sold-out archaeology dominating yield;
- identical candidates recurring after negative QC (QC memory context
  should deprioritize these — recurrence is a regression).

Negative QC remains contextual deprioritization, never a blacklist.

## Promotion gate — ALL ten required

1. Acquisition repeatedly works (≥4 successful scheduled runs).
2. Traversal progresses (distinct identities grew across early runs).
3. No baseline flood occurred (initial-fill window held; run-1 zero events).
4. Identity remains stable (no duplicate watch rows per reference).
5. No identical-QC spam recurrence after negative verdicts.
6. Health state truthful and matches DB reality (acquisition vs yield).
7. Failure modes bounded (blocked/malformed cases produced honest statuses,
   not crashes or fabricated empties).
8. ≥4 successful scheduled runs exist.
9. Human QC yield acceptable (operator judgement; NOT_USEFUL-majority is a
   NOISY flag requiring review before promotion).
10. No manual DB surgery was required during the soak.

Promotion decision belongs to the operator. No auto-promotion exists or will
be added.

## Durable gate evidence

The runtime delivery boundary persists its actual maturity comparison in
`qualification_evidence`.  Each configured maturity-set change creates a new
qualification epoch and a reset record; evidence from an earlier epoch cannot
qualify the new one.  A disagreement between configured eligibility and the
current epoch's durable gate state fails closed.  After reviewed promotion,
the operator records the existing manual decision with
`python -m scripts.record_qualification_promotion <collector_id>`; it refuses
collectors still configured as experimental.  Pre-migration history is not
backfilled and remains unknown.

## Deployment claims

`python -m scripts.deployment_status --target <scope> --intended-revision <sha>`
is the only Watch deployment-completion status command.  It reports COMPLETE
only when a separately authorized host observer supplies an independently
observed `WATCH_CLANK_RUNNING_REVISION`, matching config/wiring evidence, and
component convergence.  A build or requested revision is never reused as
observation.  Host verification remains a separate operational action.

## Post-soak obligations (even after promotion)

Future source drift, new features, and additional collectors remain normal
maintenance. Promotion only certifies the current operating role.
