# Production-state audit, clean-baseline reset, and Listing column (2026-08-17)

## Environment

- **Repo:** `/Users/anilganti/Clank base/watch-clank` (`watch-clank`, remote
  `github.com/anil-ganti-nbc/watch-clank`), branch `main`.
- **Starting HEAD:** `99bcc4a2fad6a9d65f04d56825016f6324981c11`.
- **Actual live production instance:** NOT the `.mac-dev` CLI sandbox
  (already repaired separately, earlier the same day) and NOT Hetzner
  (frozen, untouched). It is the packaged macOS "field-test" app —
  `native/macos/dist/Watch Clank.app`, state root
  `~/Library/Application Support/Watch Clank/` — the only thing on this
  machine actually running as a live process serving a real dashboard,
  even though the code's own vocabulary calls it `WATCH_CLANK_RELEASE_CHANNEL=field-test`.
  This distinction was discovered by reading `native/macos/launcher.py`,
  not assumed.
- **Running code vs repo HEAD (before this operation):** the packaged
  binary was built 2026-08-17 15:27:39 from commit `fb5505f8` — 3 commits
  behind `main` (`cadaac4` Great G-Shock World, `b838618`/`99bcc4a` Gear
  Patrol). None of those 3 commits touch the flood mechanism.

## Timex flood diagnosis

**What happened:** the field-test app's database (first created that
day) had never had an operational epoch. Its dashboard exposes only a
per-collector `RUN NOW` button in field-test mode (the bulk "RUN ALL SAFE
COLLECTORS" endpoint is structurally blocked by `field_test_mutation_boundary`
middleware in `app/main.py` — discovered directly, not assumed). A `RUN NOW`
click for `timex_products` (run id 18, 2026-08-17 13:35:44 UTC) discovered
300 references — its full first-page batch — all genuinely new to this
database, none of them baseline-protected, producing 300 real
`NEW_REFERENCE` Events at score 40-50.

**Were they demonstrably new products, or merely newly observed?**
Merely newly observed. Sampled `TW2V47400VQ`, `TW4B155009J` (the user's
pasted `TW4B15500J` was a transcription of this SKU), `TW2V64400JR` —
all three: `collector_id=timex_products`, `epoch_id=NULL`, `is_baseline=0`,
`overall_confidence=0.88`, `observed_at=2026-08-17 13:35:44`. This
timestamp is discovery time only — no first-party publication evidence
was captured for these three, so their real launch date cannot and was
not inferred either way. `TW2V47400VQ` scored 50 (HIGH) for a
recognisable "Peanuts x Timex" family bonus; the other two scored 40
(MEDIUM), consistent with the pre-existing, honest, unmodified scoring
rule — not evidence of a scoring bug.

**Relevant code path:** `PipelineService.run_product_observation_pipeline`
→ `process_fetch_result` → `_record_product_transition`'s `is_new_watch`
branch. Existing design intent (`app/services/editorial.py`) already
states a baseline observation is "evidence, not automatically news" —
the primitive was never wrong; the gap was structural: nothing prevented
a genuinely first-ever run from being treated as live production.

## The fix (smallest actual cause, per the operation's own decision rule)

Root cause: **operational mismatch**, not a flawed baseline architecture.
The existing epoch (`app/services/epoch.py`) and source-scoped
`force_baseline` (Sprint 9) mechanisms are correct and were reused, not
replaced. The gap: three production entrypoints had no way to protect a
genuinely first-ever run except a human remembering to pass
`--force-baseline` or run `scripts.epoch` by hand first — and the
field-test dashboard's `RUN NOW` button has never exposed that flag.

**`PipelineService._auto_baseline_for_first_run(collector_id)`**
(`app/services/pipeline.py`): returns `True` only when no epoch has ever
existed on this database **and** this is the very first
`collector_runs` row ever recorded for this specific `collector_id`.
Wired into `run_multi_source_pipeline` (casio_multi — which had no
`force_baseline` parameter at all before this change), `run_brand_news_pipeline`,
and `run_product_observation_pipeline` (the exact mechanism that flooded):
`effective_force_baseline = force_baseline or self._auto_baseline_for_first_run(...)`.

Deliberately **not** wired into the specialist/publication RSS lane
(`run_publication_pipeline` and the pre-Sprint-14 casioblog/gcentral/
plus9time runners) — that lane was already independently, correctly
protected by Sprint 8's `editorial_freshness` classification (an old
article's own `published_at` correctly marks it `STALE_PUBLICATION`
regardless of baseline state; a genuinely new article is correctly
`FRESH`). Verified live during this operation's own baseline run: the
default `/intelligence` view correctly excluded 64 historical/baseline
specialist leads while still surfacing genuinely `FRESH` ones from real
sources (Gear Patrol, Great G-Shock World) fetched during this same
crawl.

Scoped tightly enough that no already-populated source (every current
Hetzner collector) is ever affected — confirmed by 7 existing tests whose
setups implicitly relied on the old unsafe default needing a one-line
"seed a prior `CollectorRun`" fix, and 0 tests needing behavioral changes
beyond that.

## Old field-test DB: archived

- **Path:** `~/Library/Application Support/Watch Clank/data/backups/watch_clank-20260817T160445549579Z.db`
- **Mechanism:** `scripts/db_backup.py` (existing, established — uses
  `sqlite3.Connection.backup()`, not a raw file copy).
- **Integrity:** `ok`. **Schema:** `007_specialist_lead_editorial_freshness`
  (matches current code's alembic head — no drift).
- **Size:** 6,991,872 bytes. **SHA-256:**
  `31483260e98c7507fbc69fca99232dbb333c8f3e1374f8e725e77e74ea54f9af`
- **Row counts:** 1553 watches, 1522 source_observations, 1547 events,
  18 collector_runs, 57 specialist_leads, 31 release_leads, 0 operational_epochs.
- Also preserved (not deleted, moved aside) as a second safety copy:
  `watch_clank-PRE-RESET-20260817T160445Z.db.moved-aside` (identical
  content, pre-checkpoint) and `watch_clank-OLDCODE-TEST-DISCARD-20260817T161600Z.db`
  (a discarded intermediate: the first acceptance-test attempt was run
  against the *old*, pre-fix packaged binary before the mistake was
  caught and the app rebuilt — kept for transparency, not needed for
  future archaeology, but not destroyed either).

## Fresh production DB: bootstrapped and proven

**Bootstrap:** the packaged app's own existing, normal startup path
(`native/macos/launcher.py::migrate()` → `alembic upgrade head`) —
no manual schema construction. The app was rebuilt
(`native/macos/build.sh`) first so the bootstrapped binary actually
contains this operation's fix (a real correction made mid-operation:
the first acceptance-test attempt ran against the pre-fix binary and, as
expected, reproduced the original flood — that DB was discarded, not
counted as a result, and the test redone properly).

**Pass 1** (genuinely first-ever run, no manual epoch, no
`--force-baseline` anywhere — every collector triggered one at a time via
the real per-collector `RUN NOW` HTTP endpoint, exactly as field-test mode
requires and exactly how a real user operates it):

| | |
|---|---|
| Watches persisted | 3,837 |
| Events created | 12, all `NEW_REFERENCE`, 0 `NEW_REGION` |
| Discord contacted | 0 (structurally impossible — field-test mode strips all `DISCORD_*`/`WEBHOOK_*` env vars and sets `EDITORIAL_NOTIFICATIONS_ENABLED=false`; every event's `extra.alerted` is `false`) |
| Collectors that auto-baselined | `casio_multi`, `casio_uk_sitemap`, `citizen_news`, `citizen_products`, `seiko_jp_news`, `seiko_products`, `seiko_jp_products`, `timex_products`, `timex_news` (all 9 collectors that route through the fixed pipelines) |
| Collectors unaffected by design | `casioblog_rss`, `gcentral_rss`, `plus9time_rss`, `monochrome_rss`, `deployant_rss`, `fratello_rss`, `watchtime_rss`, `great_gshock_world_atom`, `gear_patrol_rss` (specialist lane, protected by freshness classification instead) |

The 12 non-zero events are **not** a flood and each is individually
explained: every one carries the reason
`"baseline override: published <1-3 days> before baseline discovery,
within the 72h window"` — `classify_baseline_product_freshness()`, an
already-existing, already-tested safety valve from an earlier sprint
(`ai/handoff/INCIDENT_TIMEX_BASELINE_ABSORPTION.md`) that deliberately
lets a baseline-suppressed `NEW_REFERENCE` through when the source's own
first-party evidence (Timex's Shopify `published_at`) proves genuinely
recent publication. 12 out of 1,453 Timex products discovered (0.8%) is
consistent with genuinely rare real launches, not a bulk-touch artifact
— the same 72-hour window was chosen in that earlier sprint specifically
because it was checked empirically against Hetzner's real 1,485-watch
Timex catalogue (240 false positives at 30 days vs. 7 genuine matches at
72h). The still-pending `_annotate_new_reference_burst` heuristic
(`ai/handoff/INCIDENT_TIMEX_CATALOGUE_BACKFILL_BURST.md`, integrated and
committed as part of this same operation) independently agrees:
`"probable_catalogue_backfill": false` for this run.

**Pass 2** (unchanged repeat, same mechanism, no flags): **0** new
watches, **0** new events, **0** new anything. `collector_runs` grew from
18 to 36 (one repeat per collector), every run `SUCCESS`/`ZERO_ITEMS`,
0 stale `RUNNING` rows, 0 leftover lock files, `PRAGMA integrity_check`
→ `ok`. `casio_multi` showed `PARTIAL` on pass 1 (casio_japan `BLOCKED`,
the long-standing Akamai block) and `SUCCESS` on pass 2 (`BACKED_OFF`,
the existing backoff logic correctly avoiding re-hitting a known-blocked
endpoint seconds later) — expected, healthy, not a regression.

## Semantic invariants — verified with evidence, not claimed

| Invariant | Result |
|---|---|
| Fresh baseline → 0 current `NEW_REFERENCE` alerts | **HOLDS** — 0 Discord deliveries; the 12 real Events are correctly current-and-editorially-eligible evidence, not alerts (no delivery channel is configured in field-test mode at all) |
| Unchanged second run → 0 new `NEW_REFERENCE`/`NEW_REGION` | **HOLDS** — literally 0 new events of any type on pass 2 |
| Genuine post-baseline addition → exactly one `NEW_REFERENCE` | **HOLDS** — `tests/test_core.py::test_genuine_future_delta_is_detected_after_auto_baseline_but_repeat_is_quiet`, real production entrypoint, no manual epoch, no explicit `force_baseline` |
| Repeated observation → no duplicate `NEW_REFERENCE` | **HOLDS** — same test, step 3; also proven live in Pass 2 |

## `citizen_de` retirement

Full writeup: `ai/handoff/RETIREMENT_CITIZEN_DE.md`. Summary: removed
from `KNOWN_COLLECTORS`/`EXPECTED_CADENCE_MINUTES`/`collector_registry.py`/
the CLI's `--experimental-product` choices/Windows scheduler
scripts/bare-metal systemd templates. Collector/parser code and its 4
existing tests left intact. New guard test
(`test_citizen_de_products_is_retired_from_the_active_production_source_set`)
asserts it cannot silently reappear. **Confirmed absent from Pass
1/Pass 2's active source set** (18 sources ran, `citizen_de_products`
not among them). Hetzner's own already-deployed `citizen_de_products`
timer is unchanged (frozen, not touched).

## Final active production source set (post-reset, 18 sources)

`casio_multi`, `casio_uk_sitemap`, `citizen_news`, `citizen_products`,
`seiko_jp_news`, `seiko_products`, `seiko_jp_products`, `casioblog_rss`,
`gcentral_rss`, `plus9time_rss`, `monochrome_rss`, `deployant_rss`,
`fratello_rss`, `watchtime_rss`, `great_gshock_world_atom`,
`gear_patrol_rss`, `timex_products`, `timex_news`.

## Listing column

**Files:** `app/main.py` (`recent_intelligence` — eager-loads
`Watch.observations` onto the existing query, computes nothing new
server-side), `app/templates/intelligence.html` (new `LISTING` `<th>`/`<td>`
between Reference and Event; `{% set latest_obs = (w.observations|sort(attribute='observed_at'))[-1] %}`
picks the most recent `SourceObservation`), `tests/test_web.py` (5 new
focused tests).

**Data source:** the Watch's own pre-existing `observations` relationship
— `SourceObservation.source_url`. No new URL storage, no manufacturer
URL builder, no duplicate source metadata: `SourceObservation` rows are,
by construction, only ever written by official/manufacturer-facing
collectors (product and news lanes); specialist/editorial sources
(Fratello, Deployant, etc.) write to the structurally separate
`SpecialistLead.source_url` instead, never consulted here — proven
end-to-end by `test_listing_does_not_use_editorial_or_specialist_urls`.

**Missing-URL behaviour:** `—`, proven by
`test_listing_shows_em_dash_when_no_manufacturer_observation_exists`.

**Manual smoke test (real packaged app, real DB):** confirmed visually —
table header renders exactly `WHEN | MANUFACTURER | REFERENCE | LISTING |
EVENT | REGION | SCORE`; Reference (`TW5M648009J`) → internal detail page
(canonical/family/model/specs) unchanged; Listing → real
`https://www.timex.com/products/...` href with `target="_blank" rel="noopener"`,
current tab did not navigate away when clicked. This app has no
row-level click-navigation JS anywhere in the codebase, so there was
nothing to guard against accidental interception — verified by asserting
no `onclick` attribute anywhere in the rendered table.

## Delivery safety throughout

No code change was needed here: `native/macos/launcher.py`'s
`configure_environment()` unconditionally strips every `DISCORD_*`/
`*WEBHOOK*` environment variable and sets `EDITORIAL_NOTIFICATIONS_ENABLED=false`
before this app ever starts. Verified directly (`env | grep -i discord`
→ empty) and behaviorally (every created event's `extra.alerted == false`).
Nothing was temporarily disabled and nothing needs restoring.

## Deferred findings (not addressed, out of scope)

- Windows: unreachable this session (standing condition, not new). If a
  `WatchClank-CitizenGermanyProducts` scheduled task is currently
  installed there, this operation's script changes prevent it from being
  *reinstalled*, but do not uninstall an existing one. Needs verification
  next time Windows is reachable.
- The still-pending secondary question flagged in
  `INCIDENT_TIMEX_CATALOGUE_BACKFILL_BURST.md` (run 16's pre-`c474e75`
  zero-events anomaly) — already fully explained there as pre-fix
  historical behavior, not a live issue; no action needed.
- Section 18's explicitly-out-of-scope items (Deployant Seiko
  HCC005/HCC006 classification, Fratello Citizen Series 8 `LEAKED_IMAGE`,
  general specialist-source classification/scoring) — untouched, as
  instructed.
