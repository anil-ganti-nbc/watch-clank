# Incident: baseline absorption of a genuinely recent product-catalogue launch

**Discovered:** 2026-08-17, "how many fucking Timexes?" surgical failure
autopsy, triggered by a competitor cluster of Timex/Citizen stories none
of which reached the user via Watch Clank. **HEAD at discovery:** `2d15275`.

## Mandatory specimen: Timex Weekender New England (TW2Y86600 / TW2Y86500)

### Timeline (all times UTC unless noted)

| # | Point | Evidence |
|---|---|---|
| 1 | Earliest credible first-party public evidence | Timex's own Shopify `published_at`: **2026-08-04T00:00:13-04:00** (TW2Y86600) / **T00:00:12-04:00** (TW2Y86500) |
| 2 | Shopify `created_at` | not captured by this collector (not used as evidence, per this investigation's own instruction not to treat it as proof of public availability) |
| 3 | First Watch Clank discovery | **2026-08-14 18:33:57** — `timex_products` collector, `run_id` from the Hetzner-redeploy force-baseline sweep |
| 4 | First Watch row | id 2112 (TW2Y86600VQ) / id 2113 (TW2Y86500VQ), `created_at` 2026-08-14 18:33:57 |
| 5 | First SourceObservation | id 2135 / 2136, `is_baseline=1`, `availability_status=AVAILABLE` |
| 6 | First Event | **none, ever** — zero Events exist for either watch as of this investigation |
| 7 | First SpecialistLead | **none** — no specialist source (CasioBlog/G-Central/Plus9Time/Monochrome/Deployant/Fratello/WatchTime) ever mentioned "Weekender" or "New England"; this specimen was never in reach of the Layer B correlation path at all |
| 8 | Notification eligibility | never reached — no Event existed to evaluate |
| 9 | Notification attempt | none |
| 10 | Notification delivery | none |
| 11 | Current state | 11 further observations since baseline, every 6h through 2026-08-17 00:40:56, always `AVAILABLE`, zero transitions, zero Events, still unknown to the user |

### Exact production-path trace and divergence point

```
official surface (Shopify /products.json, timex.com)
    -> discovery (timex_products collector, force-baseline sweep, 2026-08-14 18:33:57)   OK
    -> parser (app/parsers/timex_products.py)                                            OK -- reference, price,
                                                                                            availability_status, AND
                                                                                            published_at all correctly
                                                                                            extracted into extra_specs
    -> identity (Watch created, reference_canonical=TW2Y86600VQ)                          OK
    -> Watch / SourceObservation persisted, is_baseline=1                                 OK -- correct, real evidence
    -> transition detection (_record_product_transition)                                  <-- DIVERGES HERE
    -> Event/Lead                                                                          never created
    -> scoring / freshness / notification eligibility / Discord                           never reached
```

**The divergence is precise and single-point**: `_record_product_transition`'s
baseline guard (`force_baseline or is_baseline_active(...)`) returned early
and unconditionally, before ever looking at `is_new_watch` or anything else
— including the `published_at` evidence the parser had *already correctly
captured two lines earlier in the same pipeline run*. Nothing upstream was
broken. The data existed, correctly, in `Watch.extra_specs`. It was simply
never read by anything.

### Why this specific gap existed

`app/services/freshness.py`'s own module docstring (written for a different
incident, Sprint 8) stated as an explicit, deliberate scoping decision:
*"Official Events already have correct freshness semantics without this
concept... there is no 'old official event discovered late' failure mode
to fix, because Events don't carry an independent publication timestamp
the way a blog article does."* That was **true when written**. It became
false, silently, the moment `timex_products`' parser started
opportunistically capturing Shopify's own `published_at` (a later,
unrelated addition) without anyone revisiting the freshness module's
now-outdated claim. This is exactly the "collector healthy does not mean
business outcome works" lesson this project has learned before, one layer
deeper: this time the code wasn't even wrong about anything it checked —
it just never checked something it already had.

### Failure classification

**BASELINE ABSORPTION** (primary and sufficient classification). The
`is_new_watch`/NEW_REFERENCE path is unconditionally silenced during any
baseline run — by design, correctly, for the common case (most of a
freshly onboarded catalogue is old, already-existing inventory) — but with
no mechanism to distinguish that from a product that happens to have
launched shortly before the baseline ran. Not a DISCOVERY GAP (discovery
worked), not a PARSER FAILURE (parsing worked, captured more than was
used), not a SCORING FAILURE (scoring was never reached), not a
NOTIFICATION-DELIVERY FAILURE (delivery was never attempted). One clean
compounding factor: the evidence needed to resolve the ambiguity
(`published_at`) was captured but never consulted.

## Control-group results (Phase 5/6)

| Story | Exact reference(s) | Watch Clank discovery | `created_at` | `is_baseline` | Event? | Outcome |
|---|---|---|---|---|---|---|
| Weekender New England | TW2Y86600VQ / TW2Y86500VQ | 2026-08-14 18:33:57 | same baseline sweep | 1 | none | **D** |
| Expedition Sierra Chronograph | TW2Y89300VQ / TW2Y89200VQ | 2026-08-14 18:33:56 | same baseline sweep | 1 | none | **D** |
| Peanuts x Expedition Acadia | TW2Y84700JT / TW2Y84600JT | 2026-08-14 18:33:57 | same baseline sweep | 1 | none | **D** |
| Deepwater Meridian 300 Titanium | TW2Y48300VQ / TW2W82100VQ | 2026-08-14 18:33:58 | same baseline sweep | 1 | none | **D** |
| New Timex automatic GMT watches | ambiguous -- dozens of GMT references exist, all created in the same 2026-08-14 baseline sweep; the story as reported doesn't name one specific SKU | 2026-08-14 (whichever) | same baseline sweep | 1 | none | **F**, but if any specific one is meant, mechanism is identical to the above |
| Citizen Luke Skywalker AW1910-48W | AW1910-48W | **2026-08-15 18:40:09** (non-baseline, live `citizen_products` run 361) | after baseline; `is_baseline=0` | 0 | **id 38, NEW_REFERENCE, `alerted: true`, real HTTP 2xx delivery confirmed via `DiscordNotifier._post`'s own success-only-on-`status<300` contract** | **A -- UNRELATED CONTROL CASE** |

**Every genuinely evaluable Timex control case shares the exact same
mechanism as the mandatory specimen** — same collector, same 2026-08-14
force-baseline sweep, same silent divergence point, same fix.

**Citizen's case is not the same failure and is not a Watch Clank
failure at all.** `AW1910-48W` was a genuine live, non-baseline discovery
on 2026-08-15, correctly scored (experimental lane, `story_score=40`,
threshold 0), correctly judged editorially eligible, and the notifier's
own return value confirms a real, successful Discord delivery two days
before the competitor's coverage. If this was still an editorial loss, it
was a loss of *human attention to an alert Watch Clank already sent*, not
a system failure — a materially different, and much better, story than
the Timex cases. **UNRELATED CONTROL CASE.**

## An important correction made during this investigation

The first design considered for the fix used `published_at` directly with
a generous 30-day "recent launch" window (reusing the existing
`availability_recent_launch_window_days` setting, chosen for consistency
with prior precedent). Before shipping it, it was checked against the
real Hetzner catalogue (1485 Timex watches, the one real baseline run that
has ever happened) rather than trusted on the strength of the Weekender
example alone:

- At a 30-day window: **240 of 1485 products (16%)** would have qualified
  as "fresh enough to un-suppress." Inspection showed why: 738 of the
  1485 watches share one *identical* `published_at` of 2022-11-22 — an
  unmistakable bulk-import artifact, not a launch date — and even the
  August 2026 dates split into two very different shapes: a genuine
  22-second-wide burst of coordinated sibling launches (Weekender New
  England, Weekender New England Chronograph, Expedition Sierra 40mm, MK1
  Chronograph, each appearing as matched pairs seconds apart) versus a
  separate, unrelated 4-hour scatter the same calendar day touching
  dozens of pre-existing, unrelated product lines — almost certainly a
  routine Shopify catalogue republish/maintenance sweep, not evidence of
  anything newsworthy.
- At the shipped **72-hour** window (matching the already-trusted
  `specialist_freshness_window_hours` bar used for SpecialistLead): only
  **7 of 1485** products qualified, and every one of them belonged to a
  tight, seconds-apart, matched-sibling-pair cluster — the real signature
  of a coordinated launch, zero scatter noise.

This is a deliberate precision/recall tradeoff, made explicitly rather
than by accident: the 72-hour window would **not** have caught
TW2Y86600/TW2Y86500's actual 10-day publish-to-baseline gap if literally
replayed against history (which this investigation does not do — no
historical Event was fabricated or backfilled). It fixes the general
failure *class* going forward — any future source baseline (onboarding a
new source, or a deliberate Sprint-9-style `force_baseline` re-onboarding)
that happens to include a product published within 72 hours of that run
will now correctly alert — while keeping the false-positive exposure on
every such baseline at effectively zero, validated against real data
rather than assumed.

## Fix

1. `app/services/freshness.py`: new `classify_baseline_product_freshness()`
   (reuses the existing `FreshnessResult` dataclass; does not modify
   `classify_lead_freshness`, which remains SpecialistLead-only and
   unchanged). The module docstring's now-disproven claim about official
   Events needing no freshness concept is corrected in place, with this
   incident cited as the reason.
2. `app/services/pipeline.py`: `_record_product_transition`'s baseline
   guard now consults this classification, but **only** for the
   `is_new_watch` branch — `NEW_REGION` and all price/availability
   transitions remain unconditionally silent during baseline exactly as
   before, since a publication timestamp says nothing about whether a
   price changed. Two small private helpers added
   (`_parse_extra_specs_published_at`, `_new_reference_baseline_freshness`).
   The resulting Event's `reasons` list records the override explicitly
   (`"baseline override: published <age> before baseline discovery, within
   the 72h window"`) for auditability.
3. `app/core/config.py`: new `product_baseline_freshness_window_hours`
   setting (default `72`), independently tunable from
   `specialist_freshness_window_hours` even though it currently shares the
   same value, and from the much wider `availability_recent_launch_window_days`
   (30 days) used elsewhere for an unrelated scoring bonus.
4. No new collector, no new source, no SKU-specific branch anywhere in
   application code. Every collector that doesn't capture a `published_at`
   (Citizen, Seiko, Casio UK, ...) is completely unaffected — verified by
   a dedicated regression (`test_baseline_new_reference_without_published_at_stays_silent`).

## WatchBench / regression coverage

Six new tests (`tests/test_core.py`, `239 -> 245` passing, Ruff clean):

- `test_baseline_new_reference_with_fresh_published_at_still_creates_event`
  — the mechanism, isolated.
- `test_baseline_new_reference_with_stale_published_at_stays_silent` —
  uses the real TW2Y86600 10-day gap deliberately, proving the fix does
  not simply un-suppress baseline wholesale.
- `test_baseline_new_reference_without_published_at_stays_silent` —
  every other collector, unaffected.
- `test_watchbench_timex_weekender_new_england_baseline_launch_now_caught`
  — **the permanent Hall-of-Shame specimen**, run through the real
  `run_product_observation_pipeline` entrypoint (not a hardcoded SKU
  branch) with a Weekender-New-England-shaped fixture, proving an Event
  is created **and** a configured Discord webhook actually receives the
  call.
- `test_watchbench_weekender_repeat_run_does_not_duplicate` — dedup on
  repeat poll.
- `test_watchbench_weekender_fresh_baseline_no_webhook_creates_event_no_crash`
  — Event persists, notifier no-ops cleanly, nothing crashes without a
  webhook.

## What this does not fix, and was not asked to

- The historical TW2Y86600/TW2Y86500 miss itself is not retroactively
  corrected — no historical Event was fabricated or backfilled, and none
  should be.
- The "new Timex automatic GMT watches" control story remains genuinely
  ambiguous (no single SKU nameable from the report); if a specific
  reference is later identified, the mechanism above already covers it if
  it shares the same baseline-sweep shape.
- Citizen's case needed no fix — it already worked.
- No new source, brand, or collector was added. No freshness protection
  was weakened for anything outside the single, narrow, evidenced
  `is_new_watch`-during-baseline path described above.
