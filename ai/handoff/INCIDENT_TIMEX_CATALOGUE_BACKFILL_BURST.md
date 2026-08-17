# Incident: mass NEW_REFERENCE burst on the local macOS dev database

**Reported:** 2026-08-17, ~07:39 UTC, a large simultaneous batch of Timex
US `NEW_REFERENCE` events (~40 pasted as examples) observed on the local
dashboard, confidence mostly 40-50. External research confirmed every
externally-checked reference (Deepwater Meridian, Timex x Fortnite,
National Parks Fast Wrap, Marlin Quartz GMT) is a genuinely pre-existing
product (2024/2025 model years), not an August 2026 launch.

**Status at investigation time:** local patch only, tested, not committed
or pushed, per explicit instruction. Hetzner untouched.

## Environment clarification (critical)

This did **not** happen on Hetzner. It happened on the local macOS dev
database (`.mac-dev/data/watch_clank.db`, `DATABASE_URL` set by `mac/run`
and the local dashboard). Hetzner's own Timex catalogue was independently,
deliberately, fully baselined during an earlier sprint
(`ai/handoff/HETZNER_DEPLOYMENT.md`) and was not re-examined as part of
diagnosing this incident beyond confirming it's a separate database (see
"Comparison with Hetzner" below).

## What actually happened (reconstructed from the local DB + logs)

**This is not Timex-specific.** In the same ~12-minute window
(07:35-07:42 UTC), Casio, Citizen, and Seiko all had comparably large
first-observation bursts on this same local database:

| Brand | Total local watches | Created in this burst | % |
|---|---|---|---|
| Casio | 312 | 290 | 93% |
| Citizen | 556 | 158 | 28% |
| Seiko | 519 | 297 | 57% |
| Timex | 617 | 300 | 49% |

The pasted ~40-reference list was a fragment of one collector's share of a
much larger, four-brand, ~1045-event burst.

**Root mechanism:** `operational_epochs` is completely empty on this local
database -- no epoch has ever been started here, so `is_baseline_active()`
(`app/services/epoch.py`) has returned `False` for every run since this
database's creation, and `force_baseline` was never passed for any of
these collectors' registered CLI args
(`app/services/collector_registry.py`: `timex_products` is
`("--experimental-product", "timex")`, no `--force-baseline`). Unlike
Hetzner, which received a genuine, deliberate, one-time full-catalogue
baseline for every source during its redeployment sprint, this local
database has only ever been exercised via sporadic, small "run all safe
collectors" dashboard clicks (`app/main.py`'s `/operations/run-all-safe`,
which shells out to the exact same `scripts/run_pipeline.py --live`
production entrypoint the dashboard's "RUN NOW" button and, on Hetzner,
systemd timers all use -- confirmed no web-layer logic divergence).

Each product-catalogue collector's `discover_all_pages()`/equivalent
**always fully paginates the real, live, current catalogue** every run
(confirmed by reading `app/collectors/timex_products.py`: "the full
catalogue (~1606 items) is always fully paginated above, but only
`max_items` of it gets fetched/parsed per run"). `known_product_urls`
delta-prioritization (from the earlier Hall-of-Shame sprint) then
correctly sorts genuinely-new-to-this-DB items first, capped at
`default_max_items` (300 for Timex/Casio/Citizen/Seiko products). Over
five small sessions (2026-08-13 to 2026-08-14), this local database had
only accumulated 317 of Timex's real ~1445-1606 watch catalogue. After a
~3-day gap with no runs at all, a "run all safe" click on 2026-08-17
advanced the same, correctly-working delta-discovery process by another
300-item chunk -- confirmed disjoint (**zero overlap**) with the
previously-known 317 references. The same dynamic, independently, applied
to Casio/Citizen/Seiko.

**Answering the investigation's own checklist directly:**

- Which Timex source/discovery path produced these? `timex_products`
  (Shopify `/products.json`), the same production code path used
  everywhere else. Unchanged.
- Were these references truly absent from all previous **local**
  observations? Yes, confirmed (zero overlap).
- Absent because Timex hid them, or because this collector hadn't found
  them yet? The latter -- this local DB's own incomplete catalogue
  coverage, not a source-side change. External verification (2024/2025
  model years) independently confirms these were not hidden by Timex.
- Did the real Timex catalogue size jump? No evidence of that -- only
  this local DB's *knowledge* of an already-large catalogue jumped.
- Did a sitemap/endpoint/pagination/API change? No evidence found; same
  collector code, same endpoint, unmodified since the earlier
  baseline-absorption fix (which touched `pipeline.py`/`freshness.py`,
  not `timex_products.py` itself).
- Did the collector itself recently change? No.
- Were identities remapped/rediscovered? No -- zero overlap confirms
  genuinely first-time-locally-seen references, not re-processing.
- Did all ~40 pasted references enter during one run? Yes -- all within
  `timex_products` run id 95 (2026-08-17 07:39:37-07:39:41 UTC).
- More old products in the same event not shown in the pasted excerpt?
  Yes, dramatically more: the real run discovered 300, and the same
  pattern hit Casio/Citizen/Seiko simultaneously.
- Evidence of a broader Timex site migration/reindex? No direct evidence.
  The far more parsimonious, evidence-backed explanation -- a
  never-fully-baselined local database working through a real backlog,
  identically across four unrelated brands at once -- fully explains the
  observation without needing to posit a Timex-side event, and a
  coincidental same-moment reindex across four independent manufacturers'
  websites is not a credible alternative.

**One open, secondary question, explicitly not chased further:** the very
first `timex_products` run on this local DB (id 16, 2026-08-13 17:58) shows
300 new watches but 0 events, with `force_baseline=False` and
`is_baseline_active()=False` confirmed both ways (run-level and
observation-level `is_baseline=0`) -- inconsistent with
`_record_product_transition`'s logic as read, which should have created
300 real Events. This predates and is unrelated to the reported 07:39
burst (which behaved exactly as the code predicts: `is_baseline=0`, 300
new, 300 events, 1:1). Flagged for awareness, not resolved here, since it
does not change this incident's diagnosis or fix.

## Comparison with Hetzner

Not independently re-verified in this investigation (out of scope per
explicit instruction: "do not modify or deploy to Hetzner"). Prior
sprints already established Hetzner's Timex catalogue was fully,
deliberately baselined in one pass (`ai/handoff/HETZNER_DEPLOYMENT.md`,
`ai/handoff/INCIDENT_TIMEX_BASELINE_ABSORPTION.md`), so the specific
"never-baselined local DB still catching up" mechanism diagnosed here is
not expected to recur there for Timex specifically. Whether an analogous
gap could exist for a *future* source onboarded to Hetzner without a
proper full-catalogue baseline is exactly the general risk this fix
addresses, independent of any specific host.

## Event-semantics audit (Phase 2)

`NEW_REFERENCE`'s existing, documented design intent
(`app/services/editorial.py` module docstring, Sprint 2 policy) already
states: *"A baseline observation (first time we see a reference) is
evidence, not automatically 'news' on its own -- NEW_REFERENCE still gets
a score, but the reasons are explicit about what is and is not known."*
In other words, the codebase's own semantics were never "newly launched
product" -- they were always, honestly, **"first time this source/region
observation has ever recorded this reference,"** with the score (40 for a
first-party observation with no recognisable-family bonus, 50 with one)
reflecting exactly that epistemic humility, not a false claim of
certainty. `NEW_REGION`, `PRICE_CHANGE`, `AVAILABILITY_CHANGE`,
`SOLD_OUT`, `RESTOCK` are the other distinct concepts this system already
separates; there is no separate "restored/reappearing reference" or
"regional expansion" event type beyond `NEW_REGION`, and no code path
conflates "newly observed" with "newly launched" anywhere in construction
-- the conflation, to the extent the dashboard invited it, was purely a
**presentation-context gap**, not a semantic error in the primitive
itself.

**Conclusion: the raw detections were correct.** `NEW_REFERENCE` did
exactly, honestly, what it has always claimed to do. The gap was
downstream: nothing communicated "300 of these arrived in one run" to a
reader, so 300 individually-honest, individually-modest-confidence
detections read as if they might be 300 independent stories.

## Scoring audit (Phase 6)

Not changed. `score_event()`'s NEW_REFERENCE scoring (30 base + 10
first-party + up to 20 for a recognisable family) landing at 40-50 for
these events is exactly its documented, intentional behaviour -- "recall
over precision... a plausible-but-uninteresting alert costs a journalist
30 seconds" (Sprint 2 policy, `editorial.py`). `editorial_eligibility()`
confirms `NEW_REFERENCE` is unconditionally editorially eligible
regardless of score ("non-availability event retains existing policy") --
this is also unchanged and, per the same reasoning, correct: an
individually-modest-confidence detection is still a real detection worth
a human's 30 seconds, exactly the design's own stated tradeoff.
**No score, no threshold, no eligibility rule was tuned.**

## Fix (context, not suppression)

Every `NEW_REFERENCE` Event created within `run_product_observation_pipeline`
is now annotated, after the run's fetch loop completes, with truthful,
deterministic, purely-computed-from-already-available-data burst context:

```python
{
    "same_run_new_reference_count": <int>,
    "same_run_discovered_count": <int>,
    "probable_catalogue_backfill": <bool>,
}
```

`probable_catalogue_backfill` is `True` only when **both**
`same_run_new_reference_count >= catalogue_backfill_burst_min_count`
(default 15) **and** the same-run new/discovered ratio
`>= catalogue_backfill_burst_min_ratio` (default 0.5) -- both new,
independently-tunable `Settings` fields
(`app/core/config.py`). Both conditions are required deliberately: count
alone would flag any large, healthy catalogue; ratio alone would flag an
ordinary small run that happens to have a tiny `discovered_count`. The
defaults are chosen from this incident's own real historical data, not
guessed: genuine steady-state Timex runs on this local DB found at most 7
new references per run; the anomalous run found 300 of 300 discovered.
15/50% sits comfortably above the former and well below the latter on
both axes -- documented as a conservative, disclosed heuristic, not a
scientific constant, in both the code comment and here.

**Nothing is suppressed.** Every Watch, SourceObservation, and Event row
is created exactly as before -- same count, same score, same
event_type, same editorial eligibility. Events are additionally annotated
only when flagged; the run's own `summary_metadata["backfill_context"]`
always records the count/ratio computation (even when `False`), so the
"we checked and it's not a burst" case is auditable too, not silently
absent.

**Deliberately not done, and why:** individual events are still
persisted/scored/notified one-by-one *inside* the same fetch loop that
computes the final burst count only *after* that loop ends -- the true
same-run count isn't knowable until every item in the run has already
been processed. Restructuring that into a defer-then-notify or batched/
grouped single-Discord-message model (the "one grouped incident" idea
floated as a design suggestion) would give the *notification* text the
same burst-aware context this fix gives the *dashboard/DB*, but requires
materially restructuring the live per-item notify path that ordinary,
non-burst runs also use correctly every day in production, both here and
on Hetzner. That is a real architectural change, not a diagnosis-first
patch, and is recommended as separate, deliberate follow-up work (see
Recommendation below) rather than folded into this fix uninvited.

## Verification

- Focused tests: `pytest -k "backfill or isolated_new_timex"` -- 4/4 pass.
- Complete canonical suite: 249 passed (was 245), Ruff clean.
- Replayed a representative real burst (20 of the real reported
  references, real SKUs/titles/collections) against an isolated in-memory-
  equivalent test database -- not Hetzner, not the real local `.mac-dev`
  DB.
- Every one of the 20 raw Watch/SourceObservation/Event rows confirmed
  retained (`test_timex_catalogue_backfill_burst_annotates_without_suppressing`).
- A genuinely isolated single new reference confirmed **not** flagged as
  backfill (`test_isolated_new_timex_reference_not_flagged_as_backfill`).
- Identical repeat run confirmed zero phantom duplicate Events
  (`test_timex_backfill_burst_repeat_run_no_phantom_duplicates`).
- A separately-processed Citizen reference confirmed to carry **no**
  burst-context keys at all -- proves no cross-run/cross-brand leakage
  (`test_backfill_burst_annotation_does_not_affect_other_brands`).
- No test configured a Discord webhook URL; no test contacted any real
  network endpoint (offline fixtures throughout, matching every other
  test in this file).

## Recommendation

1. **Deploy this patch** (dashboard/DB-level burst annotation) once
   reviewed -- low-risk, purely additive, fully tested, does not touch
   scoring/eligibility/suppression/other brands.
2. **Separately consider** (not part of this patch): should a
   `probable_catalogue_backfill` run also change *Discord* notification
   behaviour (a single grouped summary message instead of N individual
   alerts)? This needs a real design decision about the live per-item
   notify path's timing, not a quick addition -- flagged for your
   decision, not assumed.
3. **Operational note, not a code issue:** if this local `.mac-dev`
   database is meant to keep being used for realistic testing, consider
   giving Timex/Casio/Citizen/Seiko a genuine one-time full-catalogue
   `--force-baseline` pass on it (mirroring what Hetzner already has) --
   that would let this database finish "catching up" once, deliberately,
   silently, the same way Hetzner's onboarding did, rather than
   discovering large chunks of real backlog unprotected on whatever future
   session happens to advance past the current 300-per-brand ceiling.

## Addendum (2026-08-17, follow-up): run-16 root cause, epoch lifecycle audit

Investigated at the owner's request, read-only, no production state changed.

### Why the first local `timex_products` run (id 16) showed 300 new watches, 0 events

**Conclusively explained -- not epoch/baseline related at all.** Run 16
executed 2026-08-13 17:58:04 +0530 (local clock). Commit `c474e75`
("Hall of Shame sprint: fix silent product-catalogue discovery gap" --
the fix that changed `_record_product_transition`'s `is_new_watch` branch
from an unconditional no-op into a real `NEW_REFERENCE`-emitting path)
was authored **2026-08-14 23:21:52 +0530 -- 1 day 5h24m after run 16**.
At the moment run 16 executed, the local checkout's code simply did not
yet contain the capability to emit a `NEW_REFERENCE` Event from a new
Watch at all; `is_new_watch=True` was, at that point in this repository's
own history, a genuine no-op. Watch/SourceObservation persistence was
never gated on this (confirmed unconditional), so 300 real rows were
created with 0 events -- exactly, deterministically reproducing pre-fix
behavior, not a bug in currently-shipped code. Runs 39 (2 new, 3 events)
and 70 (0 new, 2 events), also pre-`c474e75`, are independently explained
the same way: their events were `SOLD_OUT`, from the
price/availability-transition path (`classify_price_availability_transition`),
which `c474e75` never touched and which worked identically before and
after. Run 95 (the reported burst) executed after this local checkout had
since advanced past `c474e75`, `c81ebed`, and this investigation's own
`f9a401a` -- fully current code, behaving exactly as current code should.

### How a new Watch Clank DB is supposed to acquire its first epoch/baseline -- and why `.mac-dev` never did

There is no automated path. `app/services/epoch.py`
(`start_epoch`/`start_baseline`/`complete_baseline`/`is_baseline_active`)
and its CLI, `scripts/epoch.py`, have not been modified since the commit
that introduced them (`07e189f`, Sprint 7, 2026-08-11) -- the mechanism
has never been revisited, wired into anything else, or automated.
`mac/bootstrap` runs `alembic upgrade head` (schema only) and explicitly
tells the operator to run `mac/test`, `mac/health`, `mac/dashboard`, or
`mac/run` next -- it never mentions `scripts.epoch`. `app/main.py` (the
web dashboard) has zero epoch-related routes, UI elements, or warnings of
any kind. Across every handoff doc and `HANDOFF.md`, `scripts/epoch.py`
is mentioned exactly once, in Sprint 7's own historical narrative
describing the one-time, by-hand action that created `epoch_1` on the
then-primary database on 2026-08-11 -- there is no "getting started"
document, README section, or bootstrap step that surfaces it. `.mac-dev`
never had an epoch for the same reason any fresh database never would:
nothing in the tooling a new operator is actually pointed at ever asks
for one. This is a lifecycle gap, not a `.mac-dev`-specific oversight.

### Whether Hetzner (or any other current DB) can be in the same state

**Checked directly, read-only: Hetzner's `operational_epochs` table is
also empty (0 rows).** `is_baseline_active()` returns `False`
unconditionally whenever `epoch is None` (`app/services/epoch.py:31-32`),
so this function has, in fact, never returned `True` on Hetzner, ever.
Hetzner's real Timex/Casio/Citizen/Seiko onboarding was protected
correctly during the 2026-08-14 redeployment sprint entirely by the
**separate, source-scoped** `force_baseline=True` CLI mechanism (Sprint
9) -- a deliberate, per-source, one-time flag passed by hand for each
source's first invocation, unrelated to the epoch table -- which is why
that onboarding was safe despite the epoch table being empty. That
protection is retrospective and per-source, not structural: **any future
source onboarded to Hetzner whose first run omits `--force-baseline`
would hit the exact same unprotected-burst mechanism diagnosed in this
incident, except with real, currently-configured Discord webhooks live on
the other end.** Windows was not checked (unreachable this session, per
standing project convention -- reported NOT VERIFIED, not assumed either
way). Sprint 7's own historical record (`HANDOFF.md` line ~912: "Started
epoch `epoch_1` at 2026-08-11T13:11:25Z") describes creating a real epoch
on what was, at that time, the primary/Windows database -- suggesting
Windows may be the one instance that *does* have a real epoch, but this
is an inference from historical documentation, not a direct read, and
should be verified before being relied on.

One additional, structural inconsistency worth recording: `pipeline.py`'s
two direct call sites (`app/services/pipeline.py:1032,1306`) rely on
`is_baseline_active()`'s own internal `epoch is None -> False` handling,
while every call site in `app/services/specialist_leads.py` pre-guards
with its own `active_epoch and is_baseline_active(...)` pattern, which
short-circuits to a falsy value without ever reaching
`is_baseline_active()`'s internals when no epoch exists. Both patterns
currently resolve to the same outcome given the current implementation,
but they are two independently-written guards, not one shared policy --
evidence that "what happens with zero epochs" was never a deliberately
designed, single answer, just an emergent default of how each call site
happened to be written.

### Proposed invariant (design options, not implemented)

The core problem: **"no epoch exists" currently means "fully live, every
protection off" by default**, on every database, including the one
currently sending real Discord alerts. That default is backwards for
safety, but flipping it blindly is dangerous precisely because Hetzner
*already* depends on it to keep working today -- a naive change would
silently break real, currently-correct production behavior the moment it
shipped, for a database this investigation has no ability to coordinate a
migration on unilaterally.

Three options, increasing in invasiveness:

1. **Loud, non-blocking signal only.** Add a startup/health-check warning
   (dashboard banner, `mac/health` output, systemd health endpoint) when
   `operational_epochs` is empty: *"No epoch has ever been created on this
   database -- baseline protection is fully inactive."* Zero behavior
   change, zero regression risk, immediately closes the "nobody would ever
   know" half of the problem. Weakest fix; does not prevent the burst,
   only makes it visible before the fact instead of after.
2. **Grandfather, then flip the default.** Add a one-time, explicit,
   reviewable migration step that stamps every *currently-operational*
   database (Hetzner confirmed, Windows pending verification) with a real
   epoch whose baseline is already marked complete -- an honest record of
   "this database's baseline period already happened, historically,
   before this invariant existed" -- and only *after* that grandfathering
   is confirmed applied everywhere it needs to be, change
   `is_baseline_active()`'s no-epoch case to mean "baseline is active"
   (protective) instead of "baseline is inactive" (permissive). This
   closes the gap structurally for every future database, including ones
   nobody remembers to think about, without silently breaking Hetzner --
   but it is two coordinated changes (data migration + behavior flip), not
   one, and the flip must not ship before the grandfathering is verified
   done.
3. **A narrower, targeted circuit breaker**, independent of
   `is_baseline_active()` entirely (no risk to Hetzner's current
   behavior at all): before a product-observation run's fetch loop
   starts, if no epoch has ever existed on this database **and** this is
   the very first run ever recorded for this specific collector_id
   (`known_product_urls` would be empty) **and** `force_baseline` was not
   explicitly passed, refuse to notify (or refuse to run at all) and
   print/log an explicit instruction to run `scripts.epoch` or pass
   `--force-baseline`. Scoped tightly enough to never touch any
   already-populated source's ongoing behavior (Hetzner's real sources all
   have non-empty `known_product_urls` today, so this would never fire for
   them), which makes it safe to ship without a grandfathering step -- but
   it only protects the *first-run* case, not a database that's already
   partway through an unprotected catch-up the way `.mac-dev` is right now.

No option was implemented. All three remain live choices pending your
decision on the tradeoff between protection strength and rollout risk.

### Reassessing `_annotate_new_reference_burst()`

**Still worth keeping, but it is not the fix for this incident's real
root cause, and should not be mistaken for one.** It is exactly what the
heading here calls it: useful presentation/audit metadata. Two concrete
limitations, now clear in light of the epoch findings:

- It runs **after** the fetch loop, once the true same-run count is
  known -- but individual events are scored, persisted, *and notified*
  **inside** that same loop, one at a time, as they're discovered. On a
  database with a real, configured Discord webhook (Hetzner, unlike
  `.mac-dev`), every individual alert in a burst would already be sent
  before this function ever runs. It cannot prevent a live notification
  flood; it can only make the aftermath legible on a dashboard.
- It is a same-run heuristic with no awareness of *why* a burst happened.
  It would equally (and correctly) flag a burst caused by the lifecycle
  gap described above, a genuine future Timex site migration, or a
  legitimately huge simultaneous multi-SKU collection launch -- which is
  appropriate for what it is (a truthful, cause-agnostic signal), but it
  is not a substitute for preventing the specific, now-identified,
  structural cause of *this* incident.

Recommendation: keep it (already tested, already reviewed, genuinely
useful regardless of which lifecycle option above is chosen), but treat
the lifecycle invariant as the higher-priority, not-yet-decided piece of
work -- it is the one that actually protects Hetzner's real Discord
channel, which this metadata function, by construction, cannot do.
