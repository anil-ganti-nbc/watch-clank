# Watch Clank — post-repair Hall of Shame autopsy (2026-08-17)

## Method note

All ground truth in this document comes from three real databases, inspected
read-only: the live local macOS field-test DB, its pre-repair archive, the
`.mac-dev` CLI sandbox, and — critically — **Hetzner's real, live production
database**, copied into an ephemeral, read-only Docker container for
inspection (never mutated). Where a fact could not be independently
verified, it is reported as such rather than inferred. No timestamp in this
document was invented.

## The corpus, exact references (determined from evidence, not assumed)

| # | Specimen | Reference(s) |
|---|---|---|
| 1 | Timex Weekender New England | TW2Y86600VQ / TW2Y86500VQ |
| 2 | Timex Weekender New England Chronograph | TW2Y85000VQ / TW2Y85200VQ |
| 3 | Timex Deepwater Meridian 300 Titanium | TW2Y48300VQ / TW2W82100VQ |
| 4 | Peanuts x Timex Expedition Acadia | TW2Y84700JT / TW2Y84600JT |
| 5 | Citizen Luke Skywalker | AW1910-48W |
| 6 | Peanuts x Timex Legacy Mini Heart Hug | TW6A07400 / TW6A07400VQ |
| 7 | Casio full-carbon G-Shock GCW-B5000 | GCW-B5000, GCW-B5000UN-1, GCW-B5000UN-6 |
| 8 | Casio GBA-950 EU release | GBA-950-1A/-2A/-7A/-7A2/-9A3 |
| 9-10 | Casio ABL-100WE | ABL-100WE-1A/-2A/-7A, ABL-100WEG-9A, ABL-100WEPC-1B |

## Hall-of-Shame matrix

| Specimen | Earliest evidence | Expected source | WC first seen | Watch/Obs? | Event/Lead? | Alert? | Primary root cause | Secondary | Fix | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1. Weekender New England | Shopify `published_at` 2026-08-04 (10.6d gap) | `timex_products` | 2026-08-14 18:33:57 (Hetzner redeploy baseline) | YES, `is_baseline=1` | None, ever | None | **BASELINE_STORM_CASUALTY** | — | Already fixed for the *class* (72h freshness override, Aug 17); this specific SKU's real gap (10.6d) exceeds the window — correctly not retroactively saved | Grandfathered, no action |
| 2. Weekender New England Chronograph | Shopify `published_at` 2026-08-04 (10.6d gap) | `timex_products` | 2026-08-14 18:33:57 (same sweep) | YES, `is_baseline=1` | None | None | **BASELINE_STORM_CASUALTY** | — | Same as #1 | Grandfathered |
| 3. Deepwater Meridian 300 Ti | Shopify `published_at` 2026-07-23 (22d gap) | `timex_products` | 2026-08-14 18:33:58 (same sweep) | YES, `is_baseline=1` | None | None | **BASELINE_STORM_CASUALTY** | — | Same as #1 | Grandfathered |
| 4. Peanuts x Acadia | Shopify `published_at` 2026-08-07 (7.6d gap) | `timex_products` | 2026-08-14 18:33:57 (same sweep) | YES, `is_baseline=1` | None | None | **BASELINE_STORM_CASUALTY** | — | Same as #1 | Grandfathered |
| 5. Citizen Luke Skywalker | not independently verified | `citizen_products` | 2026-08-15 18:40:09 (ordinary, non-baseline) | YES, `is_baseline=0` | **YES — id 38, NEW_REFERENCE** | **YES — real HTTP success, confirmed delivery** | **NOT A FAILURE** | — | None needed | **Working correctly** (real alert 2 days before competitor) |
| 6. Peanuts x Legacy Mini Heart Hug | Shopify `published_at` 2026-08-07 (7.6d gap) | `timex_products` | 2026-08-14 18:33:57 (same sweep) | YES, `is_baseline=1` | None | None | **BASELINE_STORM_CASUALTY** | — | Same as #1 | Grandfathered |
| 7. GCW-B5000 | Great G-Shock World, published 2026-08-16T10:39:01+09:00 | `great_gshock_world_atom` | 2026-08-17 11:19:08 (source's own onboarding sweep) | N/A (SpecialistLead) | **YES — real SpecialistLead** | None (BASELINE, by design) | **SOURCE_GAP → FIXED, then BASELINE_STORM_CASUALTY on the exact triggering specimen** | — | Source added (prior sprint); this exact article was the onboarding baseline itself | Source fixed going forward; this specimen permanently grandfathered |
| 8. GBA-950 EU | G-Central, published 2026-07-02T08:59:28Z | `g_central` (found) + **no EU-mainland product collector** (gap) | 2026-08-14 18:36:25 (G-Central onboarding sweep) | Casio's UK sitemap: absent (confirmed live). No Watch/Observation existed anywhere pre-fix | **YES — SpecialistLead, correctly STALE_PUBLICATION** | None (correctly stale) | **REGION_GAP** (product-collector layer) + BASELINE_STORM_CASUALTY (specialist layer, on this exact article) | — | **NEW: `casio_europe_sitemap` collector added** (this session) | **FIXED going forward** |
| 9-10. ABL-100WE-7A/-2A | not independently verified | `casio_uk_sitemap` | 2026-08-14 18:34:17/18 (Hetzner redeploy baseline) | YES, `is_baseline=1` | None | None | **BASELINE_STORM_CASUALTY** | — | `lastmod` evidence checked — all values weeks/months stale, would not qualify under any reasonable freshness window even if extended | Grandfathered, no action warranted |

## 1. Root-cause clusters

**Cluster A — the 2026-08-14 Hetzner redeploy baseline sweep (7 of 10 specimens: #1, 2, 3, 4, 6, 9, 10).**
Every Timex and Casio-UK-catalogue specimen was created in the *same*
onboarding force-baseline run (`timex_products` 18:33:56-58, `casio_uk_sitemap`
18:34:15-18 — 20 seconds apart, one deployment session). This is the
identical mechanism the prior `INCIDENT_TIMEX_BASELINE_ABSORPTION.md`
investigation already diagnosed and partially fixed (72-hour first-party
freshness override). Checked against real `published_at` evidence for
every Timex specimen with a Shopify timestamp: every real gap (7.6-22
days) exceeds 72 hours, so **none of these would be saved even by the
existing fix if literally replayed** — this is not a residual bug, it is
the deliberate, evidence-tuned precision/recall boundary already chosen
in that sprint. For the two Casio specimens, `casio_uk_sitemap`'s only
timestamp signal is sitemap `<lastmod>` (an unreliable proxy for launch
date, by the collector's own documented design) — checked directly:
every value is weeks-to-months stale, so extending the freshness
override to `lastmod` would not have helped and risks false positives
on ordinary catalogue maintenance. **Conclusion: this cluster is the
accepted, correctly-documented cost of full-catalogue onboarding, not a
new defect.**

**Cluster B — specialist-lane onboarding lag (#7, #8).**
Both GCW-B5000 and GBA-950 were genuinely discovered by a specialist
source (Great G-Shock World, G-Central respectively) — but each
source's *own* onboarding happened weeks-to-a-day after the specific
article was published, so the article was correctly classified
`BASELINE`/`STALE_PUBLICATION` and excluded from the default Current
view by the same, already-correct `editorial_freshness` mechanism. This
is structurally the same "grandfathering" cost as Cluster A, just on the
Layer B (SpecialistLead) side instead of Layer A (Event) — no new
mechanism needed, already working as designed.

**Cluster C — genuine regional coverage gap (#8, secondary).**
Independent of the specialist-lane timing above, GBA-950 exposed a real,
technically-confirmed gap: Casio's EU-mainland product catalogue
(`casio.com/europe`, `casio.com/de`) has no dedicated collector — only
UK is covered. Live-verified: GBA-950 (5 real colourways) exists on the
EU/DE sitemap right now and is **absent** from the UK sitemap entirely.
This is the one specimen in the corpus that traces to an actual,
fixable, currently-open gap rather than historical grandfathering — see
Fixes below.

**Cluster D — not a failure (#5).**
Citizen Luke Skywalker (AW1910-48W) is a real, ordinary, non-baseline
discovery that correctly fired `NEW_REFERENCE` and was **successfully
delivered to Discord** two days before the competitor's coverage
(confirmed via Hetzner's real `events`/notification evidence). If this
was still experienced as an editorial loss, it is a loss of *human
attention to a real alert already sent*, not a system failure. Confirmed
unrelated to the later `citizen_de` retirement (different collector,
different timeline).

## 2. Source-gap findings

Only **one** genuine, currently-open source gap was found in this
corpus: **Casio EU-mainland product/catalogue coverage** (UK is
covered, EU-mainland was not). No Timex, Citizen, or Casio-Japan gap was
found — every other specimen's source already existed and worked
correctly; the misses trace to onboarding timing, not absence of
capability.

## 3. Great G-Shock World verdict

**Already correctly resolved, no action needed this session.** Confirmed
live on Hetzner: deployed (`watch-clank:cadaac4`, currently running),
systemd timer firing every 45 minutes (`watch-clank-great-gshock-world-atom.timer`),
real GCW-B5000/MRG-B5000SA-2 SpecialistLead present in the production
database. The specific triggering article is permanently baseline-
grandfathered (it was the source's own onboarding sweep, one day after
publication) — expected and correct, not a defect. **The source gap
itself is fixed; the next equivalent story will be caught live.**

## 4. Additional specialist-source candidates researched (Seiko/Orient follow-up)

The prior sprint's Great G-Shock World research explicitly flagged
Seiko/Orient as an open follow-up ("no dedicated leak-focused blog was
found at all"). Fresh research this session, targeting that gap
specifically:

| Source | Brand | Feed | Verdict |
|---|---|---|---|
| PR TIMES — Seiko Watch Corp | Seiko | `prtimes.jp/companyrdf.php?company_id=10826` — verified HTTP 200, valid RDF, second-precision timestamps | **Recommended** — official primary source, real launches, needs a light non-product filter |
| PR TIMES — Epson Sales Corp (Orient's current corporate home since Feb 2026) | Orient/Orient Star | `prtimes.jp/companyrdf.php?company_id=33845` — verified HTTP 200, valid RDF | **Recommended with mandatory keyword filter** — ~55-60% of items are unrelated Epson consumer electronics noise |
| Orient Place (orientplace.blogspot.com) | Orient/Orient Star | `/feeds/posts/default` — verified HTTP 200, valid Atom, 274 entries | **Recommended, realistic expectations** — genuine release detail when it covers launches (~4-6 genuine posts/year), rest is historical/analytical |
| SJX Watches (watchesbysjx.com) | General (covers Seiko/Orient Star among others) | `/feed` — HTTP 200 valid RSS via plain `curl`; blocked (403) via the research tool's own fetch proxy specifically | **Conditional** — must re-verify against Watch Clank's real HTTP client before committing engineering time |
| Yeoman's Watch Review, WatchUSeek Seiko forum, The Grand Seiko Guy, SeriousWatches.com, Watch Media Online, Grand Seiko official news | various | dead / bot-paywalled (TollBit) / defunct / retailer / no feed | **Rejected**, reasons as listed |

**None implemented this session.** No specimen in this corpus is
Seiko/Orient-related, so implementing a new collector here would not
explain or fix any of the 10 misses under investigation — doing so would
be exactly the "add sources simply to increase count" the brief warned
against. Flagged as a strong, ready-to-implement candidate for a
dedicated future sprint (PR TIMES Seiko in particular: zero technical
blockers, official source, real timestamps).

## 5. Code changes

- **`app/collectors/casio_europe_sitemap.py`** / **`app/parsers/casio_europe_sitemap.py`**
  (new) — mirrors `casio_uk_sitemap` exactly (same proven pattern:
  Cloudflare-blocked product pages, unblocked sitemap, `<lastmod>`-only
  evidence, `NEW_REFERENCE`/`NEW_REGION`-only event capability). Wired
  into `app/services/pipeline.py`'s `_PRODUCT_REGISTRY`,
  `app/services/collector_registry.py`, `app/services/health.py`
  (`KNOWN_COLLECTORS` + `EXPECTED_CADENCE_MINUTES`, 720min matching UK),
  and `scripts/run_pipeline.py`'s `--experimental-product` choices.
  No other code changed — the pre-existing `_auto_baseline_for_first_run`
  invariant (from the immediately-prior production-reset sprint)
  automatically protects this brand-new collector's first-ever run with
  zero additional wiring.

## 6. Tests

6 new tests in `tests/test_core.py`, real fixture
`tests/fixtures/casio_europe_sitemap.xml` (captured live 2026-08-17,
real GBA-950 URLs/lastmods, not fabricated):
`test_casio_europe_sitemap_discovery_extracts_gba950_references_and_lastmod`,
`test_casio_europe_sitemap_run_prioritizes_unknown_urls_under_cap`,
`test_casio_europe_sitemap_parser_never_fabricates_price_or_availability`,
`test_casio_europe_sitemap_new_reference_gba950_is_editorially_current`
(the actual Hall-of-Shame recall proof), `test_casio_europe_sitemap_known_reference_from_intl_news_emits_new_region`,
`test_casio_europe_sitemap_first_run_auto_baselines_silently_then_repeat_is_quiet`.
278 passed total (was 272), Ruff clean.

## 7. Live validation

Real live run against `https://www.casio.com/europe/sitemap.xml` into an
isolated throwaway database (not production): first pass discovered
2,016 real watches including all 5 real GBA-950 colourways, `auto_baseline_applied=true`,
0 events (correctly silent). Repeat pass: 0 new watches, 0 new events.
`PRAGMA integrity_check`: `ok`. No stale locks. No Discord contact (no
webhook configured in this throwaway environment).

## 8. GitHub state

Committed and pushed to `origin/main` (see final report for exact SHA).

## 9. Hetzner state

`casio_europe_sitemap` deployed alongside the current already-validated
`cadaac4`-era codebase, force-baselined on first run (matching the
established onboarding convention used for every other Hetzner source),
repeat-verified 0/0, one new systemd timer installed
(`watch-clank-casio-europe-sitemap.timer`, 720min cadence matching UK).
No other Hetzner collector, timer, secret, or configuration touched.
`citizen_de` remains retired (not re-enabled). See final report for
exact before/after image tag and verification detail.

## 10. Remaining limitations

- The `.deployed-id` file at `/home/deploy/staging/watch-clank/.deployed-id`
  on Hetzner is a stale artifact from the pre-remediation legacy cron
  path (already disabled) and still reads `fcb5e91` — misleading to a
  future investigator who doesn't know the real deployment mechanism is
  `~/.config/watch-clank/docker.env`'s `WATCH_CLANK_IMAGE` +
  `systemctl --user` timers. Not touched this session (out of scope —
  cosmetic/documentation risk, not a functional defect), flagged for a
  future cleanup pass.
- SJX Watches' fetch-tool-specific 403 needs re-verification with Watch
  Clank's actual HTTP client before any future decision to automate it.
- Watch Media Online has genuinely good, dated, brand-attributed content
  but no feed at all — would require a scraping-based approach outside
  this project's current RSS/Atom-only architecture.
- Windows was not reachable this session (standing condition) — cannot
  confirm whether a stale `citizen_de` scheduled task still exists there.
