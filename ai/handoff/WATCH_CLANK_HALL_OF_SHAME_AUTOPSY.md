# Watch Clank — Hall of Shame forensic autopsy

**Sprint date:** 2026-08-14. **Starting HEAD:** `22da9bd` (clean, == `origin/main`).
**Author:** Claude, forensic/remediation sprint per owner brief.

## How to read this document

Every factual claim below is tagged:

- **VERIFIED** — confirmed against live source code, a live network probe (this
  session, 2026-08-14), or a real database (either the documented Windows
  production autopsies from 2026-08-12, or this session's own macOS dev
  database `.mac-dev/data/watch_clank.db`, built by running current-HEAD
  collectors against real live sources today — labelled **live-capability
  probe**, not production history).
- **INFERRED** — a reasonable conclusion from VERIFIED evidence, not itself
  independently confirmed.
- **NOT VERIFIED** — could not be confirmed this sprint (no reference number
  supplied, no host access, or genuinely ambiguous evidence). Stated as a gap,
  not guessed at.

**Important scope note on database evidence.** The actual Windows production
database (`C:\Users\anil\Desktop\Watch clank\watch-clank`) was not reachable
this session — the operator's Windows machine has been away since before this
sprint began (documented in HANDOFF.md Sprint 12: "returns 2026-08-18"). Two
other real databases were used instead:
1. The Hetzner cloud deployment's live SQLite volume (`watch_clank_staging_data`),
   inspected read-only via SSH — this is real but running a **stale image
   frozen at commit `fcb5e918`, 2026-08-10**, predating Sprints 5–12 entirely
   (see Phase 13-16 section).
2. This session's own macOS dev database, built fresh today by running every
   current-HEAD collector live against real production sources
   (`mac/bootstrap` + `run_product_observation_pipeline`/`run_brand_news_pipeline`
   with `--force-baseline`, then a repeat run). This is the best available
   evidence for "what would current HEAD actually discover today," even
   though its own operational history (created_at timestamps) is this
   session's, not the original Windows timeline.

Where a case already had a dedicated forensic autopsy from a prior session
(`ai/handoff/CITIZEN_REGIONAL_AUTOPSY.md`, `ai/handoff/SPECIALIST_SOURCE_EXPANSION_SPRINT14.md`),
that autopsy's findings are treated as VERIFIED production evidence and cited directly.

---

## Positive control — the shower incident (SEIKO HCC005 / HCC006)

**Not a failure.** Reconstructed successful path:

| Stage | Result |
|---|---|
| Source | `monochrome_rss` (SPECIALIST_PUBLICATION, tier 2) |
| Discovery | Monochrome RSS item published `2026-08-13T03:02:25Z`, discovered same run |
| Parse / identity | References `HCC005` and `HCC006` extracted into `reference_candidates` |
| Freshness | `editorial_freshness = FRESH` |
| Classification | `SpecialistLead` (Layer B early-warning), correctly never auto-promoted to an official `Event` |
| Discord eligibility | Layer B leads are early-warning, not official-Event alerts — by design, a human/journalist is the verification layer (Sprint 2's explicit recall-over-precision pivot) |

**VERIFIED** live in this session's dev DB: lead id 51, source `monochrome`,
title *"First Look – The New Seiko Presage Classic Series 'Craftsmanship'
Enamel and Urushi Lacquer HCC005 & HCC006"*, `editorial_freshness=FRESH`.
The operator's alert at 05:56 CEST / 09:26 IST reflects real, working Layer B
discovery arriving quickly after publication — a competing journalist simply
published faster on that occasion. Nothing in Watch Clank needs to change for
this case, and no code change was made in response to it.

---

## Case-by-case ledger

### Case 1 — Timex Quartz E Line 30mm (TW6A01000 / TW6A00900 / TW6A00800)

| Field | Value |
|---|---|
| Brand | Timex |
| WC source | `timex_products` (Shopify catalogue) |
| Earliest WC evidence | **VERIFIED** (live-capability probe): all three watches present, `reference_canonical` = `TW6A01000VQ`/`TW6A00900VQ`/`TW6A00800VQ`, created during the Timex onboarding force-baseline run |
| Baseline state | Baseline (Sprint 9's `force_baseline` mechanism, documented in HANDOFF.md) |
| Event/Discord | None fired — correct, by design |
| Root cause | **O — expected, not a failure** |
| Would current HEAD catch it | Already caught, correctly suppressed as onboarding baseline |
| Confidence | VERIFIED |

Pre-existing classification stands: **GRANDFATHERED / TIMEX ONBOARDING ERA.**
No change made or needed. This confirms the mission brief's own instruction
not to "fix" baseline suppression because of this case — baseline suppression
is working exactly as intended here.

### Case 2 — Citizen Tsuyosa Shore "Time Slip" (NJ0238-57E)

Full prior autopsy: `ai/handoff/CITIZEN_REGIONAL_AUTOPSY.md` (2026-08-12,
VERIFIED against the real Windows production DB at the time).

| Field | Value |
|---|---|
| Earliest first-party evidence | Citizen Watch Global, 2026-07-23 (GLOBAL announcement) |
| Earliest WC evidence | US product observation, 2026-08-11 13:23:28, USD 525.00, correctly absorbed into the Citizen source's initial baseline |
| UK evidence | Citizen UK lists GBP 349.00 — **not monitored**; direct HTTP returns Cloudflare 403 (re-confirmed live from Hetzner this session, HTTP 403) |
| Root cause | **H — regional commercialisation** (a known watch's first listing in a new market had no representable transition) + **A — source coverage** (UK blocked) |
| Remediation status | **REMEDIATED for the mechanism** — `_record_product_transition`'s `NEW_REGION` branch (commit `6b6e4ef`, "Detect regional commercialisation events") now fires when a known watch's product observation lands in a region not seen before, sourced from either prior product observations or prior announcement regions. Proven with a dedicated regression (`test_known_citizen_first_product_listing_in_new_region_is_regional_intelligence`) and 2 live production runs (0 churn). **NOT remediated: UK itself is still not collected** (Cloudflare-blocked, no bypass attempted, correctly so). |
| Would current HEAD catch it today | **PARTIAL** — if a UK/DE-style collector existed and reported the observation, yes, cleanly (this is exactly what the Citizen Germany collector added the same day now proves live). As it stands, US-only coverage means the *mechanism* is proven but this specific market is still dark. |

This is the strongest evidence that the regional-commercialisation gap is a
real, systemic pattern, not a one-off — see Case 8, 9, 11, and the Big
Question section below.

### Case 3 — Seiko Presage × Tradman's Bonsai (HCC009J1)

Full prior autopsy: `ai/handoff/SPECIALIST_SOURCE_EXPANSION_SPRINT14.md`.

| Field | Value |
|---|---|
| Notebookcheck-relevant publication | Monochrome `2026-08-12T03:00:27Z`, Fratello ≈`2026-08-12T05:00:14Z` |
| WC state at time of miss | No Watch, SourceObservation, ReleaseLead, SpecialistLead, or Event — genuine discovery failure (VERIFIED against the real production DB, 2026-08-12) |
| Root cause | **C — discovery** (no specialist RSS source covered Monochrome/Fratello at the time) |
| Remediation status | **REMEDIATED** — Monochrome, Deployant, Fratello, WatchTime RSS collectors added same day (commit `da722f7`) |
| Would current HEAD catch it today | **YES — VERIFIED live**, this session's dev DB has both the Monochrome (`03:00:27Z`) and Fratello (`05:00:14Z`) leads, both `editorial_freshness=FRESH` |

Classification: **KNOWN FAILURE → REMEDIATION DEPLOYED → now a regression specimen** (see `tests/test_core.py` specialist-source tests and the live evidence above).

### Case 4 — Peanuts × Timex Legacy Mini Heart Hug (TW6A07400 / TW6A07400VQ)

| Field | Value |
|---|---|
| Earliest public evidence | Shopify `published_at` 2026-08-07T00:00:12-04:00 |
| WC onboarding | ~5 days later (Timex Sprint 9, 2026-08-12/13) |
| WC evidence | **VERIFIED** (live-capability probe): watch present, created in the same Timex force-baseline batch as Case 1 |
| Root cause | **O — expected, not a failure** (source-scoped silent baseline worked as designed) |
| Would current HEAD catch it | Already caught, correctly suppressed |

Classification unchanged: **GRANDFATHERED PRE-COVERAGE MISS.** No baseline weakening made or warranted.

### Case 5 — Casio F-B100W international rollout

| Field | Value |
|---|---|
| WC Casio coverage | `casio_multi` = `casio_intl_news` (Casio Corporate global press releases) + `casio_japan` catalogue (Akamai-**blocked**, confirmed historically and unchanged) |
| Regional product/price collector | **None exists for any Casio region** — no UK, no US, no DE, no global product catalogue collector at all |
| WC evidence | **NOT FOUND** in this session's live-capability probe (queried for `F-B100W`/`FB100W`) |
| Root cause | **A — source coverage.** Casio is the *only* one of the four official brands with zero product/price/availability collector anywhere outside the permanently-blocked Japan catalogue. CASIOBLOG/G-Central are specialist rumor/announcement blogs, not price or availability trackers. |
| Would current HEAD catch it | **NO** |
| Confidence | INFERRED discovery-failure (no reference-level Windows DB access to confirm the miss originally happened), VERIFIED that no capability to catch it exists today |

Classification: **CONFIRMED GENUINE REGIONAL COVERAGE FAILURE** — the most
structurally under-covered of the four official brands.

### Case 6 — Seiko Presage Cocktail Time regional rollout

| Field | Value |
|---|---|
| WC Seiko coverage | `seiko_jp_news` (corporate news, JP+global topics, small item cap) + `seiko_products` (Seiko **USA** Shopify catalogue only, 222/276 items, fully covered — no cap issue) |
| Regional gap | No UK/EU/other-market Seiko product collector exists |
| WC evidence | Not independently verified against a specific reference (none supplied in the case brief) |
| Root cause | **A — source coverage**, same shape as Case 5/8/11: US is covered, nothing else is |
| Would current HEAD catch it | **PARTIAL** — if the rollout happened to include the US and matches the existing catalogue, `seiko_products` would see it as a normal observation (and, after this sprint's fix, correctly as `NEW_REFERENCE` if genuinely first-seen). If the rollout is UK/EU-only, **NO**. |

### Case 7 — Citizen Eco-Drive mother-of-pearl (Notebookcheck published 2026-08-14 — today)

| Field | Value |
|---|---|
| Reference | **Not supplied** in the case brief — no SKU to search the DB or catalogue for |
| Live check today | `citizenwatch.com` US search for "mother of pearl" returns **0 results** (VERIFIED, live fetch this session) |
| `citizen_news` leads today | No lead containing "pearl" found in this session's live-capability probe; two Eco-Drive-titled leads exist but neither matches (a Promaster yacht-racing Eco-Drive from 21 May, and a "50th Anniversary LIGHT in BLACK" from 14 May) |
| Root cause | **NOT VERIFIED — inconclusive without a reference.** Two live, real limitations are visible regardless of which one applies here: (1) `citizen_news` only processes its default item cap per run (mirrors the Case 10 pagination-window concern, see Phase 6), so a same-day announcement may simply not yet be in the processed window; (2) Citizen product-page search doesn't guarantee an exact-phrase catalogue match even for real inventory. |
| Would current HEAD catch it | **NOT VERIFIED.** Citizen is mature coverage (US products + GLOBAL news both monitored) — no onboarding exemption applies, so if this fails to appear within a reasonable window it would be a genuine miss, but this sprint could not confirm the specific SKU either way. |

This case is flagged honestly as **unresolved** rather than forced into a
classification the evidence doesn't support. Recommended follow-up: capture
the actual reference number and re-run this specific autopsy.

### Case 8 — Casio GD-350S-1 UK open sale (preorder → available, £100 inc. VAT)

| Field | Value |
|---|---|
| Watch known to WC | **NOT FOUND** in this session's live-capability probe |
| Casio UK accessibility | **VERIFIED blocked** — `curl` from the Hetzner host (the real vantage point any cloud collector would use) returns **HTTP 403** for `casio.com/uk/...` |
| Would the *event semantics* represent this if the data existed | **YES** — verified by code reading: `classify_price_availability_transition` already classifies a `PREORDER`→`AVAILABLE` availability-string transition as `AVAILABILITY_CHANGE` (not gated by this sprint's SOLD_OUT/RESTOCK-only `AVAILABILITY_EVENT_TYPES` tightening), so the semantic layer is *not* the blocker here. |
| Root cause | **A — source coverage** (no Casio UK collector exists at all) compounded by **M — technically blocked** (Cloudflare 403) |
| Would current HEAD catch it | **NO** |

This is the clearest "watch already known, commercial state changed" case in
the set, and the clearest confirmation that Casio's total absence of any
accessible product/availability collector (Case 5's finding) is the same root
cause recurring.

### Case 9 — Citizen Nighthawk US release (CA0890-54H / CA0897-04H)

| Field | Value |
|---|---|
| WC evidence | **VERIFIED live-capability probe**: both watches exist (`citizen_products`, US, USD 595.00 / 575.00), with **`is_baseline = 0`** on every observation — i.e. these were **not** absorbed by an onboarding baseline, they were discovered during ordinary catalogue runs |
| Events before this sprint's fix | **ZERO** — confirmed by direct query (`event_watches` join returns no rows for either watch) |
| Root cause | **J — event classification.** This is the clearest, most directly-reproduced instance of this sprint's central finding: `_record_product_transition`'s `is_new_watch` branch returned `{"event_type": None, "reason": "baseline_new_watch"}` **unconditionally**, even outside any active baseline. A genuinely new-to-Clank SKU discovered *only* through the product catalogue (no matching `citizen_news` announcement) had **no event path at all** — not `NEW_REFERENCE`, not anything. |
| Remediation status | **FIXED this sprint** — see Remediation section. Re-verified live: `test_new_watch_from_catalogue_creates_new_reference_event` uses this exact reference pair as its regression fixture. |
| Would current HEAD catch it | **YES**, after this sprint's fix |

### Case 10 — Q Timex Continental Chronograph

| Field | Value |
|---|---|
| WC evidence | **VERIFIED**: three Continental Chronograph SKUs (`TW2Y93200VQ`, `TW2Y92400VQ`, `TW2Y92300VQ`) plus a Continental Day/Date (`TW2Y35200VQ`) exist in the catalogue, all created during the 2026-08-13 Timex onboarding **force-baseline** run |
| Ambiguity | **NOT VERIFIED which SKU the Notebookcheck story refers to** — no reference supplied in the case brief. If it is these exact SKUs, this is **Case-1/4-style grandfathering** (expected). If it is an *additional* Continental Chronograph SKU added to Timex's store after the 2026-08-13 baseline, two compounding, VERIFIED, now-fixed gaps explain the miss. |
| Pagination audit (VERIFIED, live) | Timex's real catalogue is **1606 products / 1445 watches**. The `timex_products` collector's default `max_items=300` on any **non-baseline** run processed only the **first 300 items in Shopify's default (non-recency-guaranteed) sort order** — roughly **19%** of the real catalogue, every single routine run, forever, until this sprint. |
| Event-path audit | Even for the ~19% that *was* processed, a genuinely new SKU hit the same Case-9 `is_new_watch` silent gap. |
| Root cause | **B — polling latency / pagination cap**, compounding **J — event classification** |
| Remediation status | **BOTH FIXED this sprint** — see Remediation section |
| Would current HEAD catch it | **YES** for any genuinely new SKU discovered from now on, regardless of catalogue sort position |

This case is the strongest evidence for the Phase 6 "catalogue cap" hypothesis
the mission brief explicitly asked to be tested — confirmed true, and fixed
in the same change as Case 9's root cause because they compound each other.

### Case 11 — Citizen Dress Classic UK rollout (BJ6570-50E / BJ6574-59E / BJ6572-54X)

| Field | Value |
|---|---|
| WC evidence | **VERIFIED live-capability probe**: all three watches exist via `citizen_products` (US catalogue, collection "Classic Eco"), `is_baseline = 0` |
| UK accessibility | **VERIFIED blocked** — Cloudflare 403 from Hetzner, same as Case 2/8 |
| Zales (US) chronology clue | Consistent with these being real US-catalogue SKUs Watch Clank's US collector can and does see |
| Root cause | **H — regional commercialisation** + **A — source coverage** (UK blocked) — identical shape to Case 2, same brand, same blocked market |
| Would current HEAD catch it | **PARTIAL** — the underlying references are already known (US), so a hypothetical UK collector would correctly fire `NEW_REGION` (the Case 2 mechanism, already proven); the UK collector itself does not exist and the market remains genuinely blocked |

### Case 12 — Seiko Prospex Alpinist Mechanical GMT (HBC008J / HBC009J)

| Field | Value |
|---|---|
| WC evidence | **NOT FOUND** — no Seiko Japan store collector exists at all |
| **Geo-restriction claim — TESTED AND NOT CONFIRMED.** | The mission brief states Seiko's Japanese store is geo-restricted from some locations. **Live test from the real Hetzner vantage point** (the same network any cloud collector would use): `curl https://store.seikowatches.com/products/hbc008j` → **HTTP 200**, full page. `curl .../hbc008j.json` → **HTTP 200**, a genuine **Shopify-style product JSON endpoint** (`{"product":{"id":..., "title":"HBC008J", "body_html":...`), the exact same mechanism already proven safe for Timex/Seiko-USA/Citizen. |
| Root cause | **A — source coverage**, not M. This sprint corrects the mission brief's own working assumption with live evidence: this is a **missing capability**, not a technically inaccessible boundary case. |
| Would current HEAD catch it | **NO** (no collector exists), but **could be built** with the same low-risk, already-proven Shopify-JSON pattern |
| Caveat | This was tested from one vantage point (Hetzner, Helsinki/EU) via `curl`, not from every location, and not through a real browser session. It is possible the site blocks specific *other* regions, blocks checkout/payment rather than product viewing, or applies bot-detection differently to a real browser vs. `curl` — none of that was observed here. Recommended as a **documented follow-up build**, not implemented this sprint (a new collector + onboarding + baseline + reference-normalization is a separate, larger unit of work — seeing five failures fixed by one change was this sprint's priority, not building a fifth collector for one case). |

---

## Failure taxonomy (Phase 2)

| Case | Primary layer(s) | Secondary |
|---|---|---|
| 1 (Timex E Line) | O — expected | — |
| 2 (Citizen Tsuyosa) | H — regional commercialisation (remediated) | A — UK coverage gap (open) |
| 3 (Seiko Bonsai) | C — discovery (remediated) | — |
| 4 (Peanuts Timex) | O — expected | — |
| 5 (Casio F-B100W) | A — source coverage | — |
| 6 (Seiko Cocktail Time) | A — source coverage | H |
| 7 (Citizen mother-of-pearl) | NOT VERIFIED | B (possible) |
| 8 (Casio GD-350S-1) | A — source coverage | M — technically blocked |
| 9 (Citizen Nighthawk) | J — event classification (fixed) | — |
| 10 (Q Timex Continental) | B — polling/cap (fixed) | J — event classification (fixed) |
| 11 (Citizen Dress Classic) | H — regional commercialisation | A — UK coverage gap (open) |
| 12 (Seiko Alpinist GMT) | A — source coverage (corrected from assumed M) | — |
| Positive control (HCC005/6) | O — expected success | — |

### Root-cause matrix

| Root cause | Cases affected | # of Ls | Fix | Complexity | Risk | Recall gain | False-positive risk |
|---|---|---|---|---|---|---|---|
| **J — new-SKU-via-catalogue produces no event** | 9, 10 (+ generically protects every future Citizen/Seiko/Timex/Citizen-DE catalogue discovery) | 2 confirmed, systemic | `_record_product_transition`'s `is_new_watch` branch now emits `NEW_REFERENCE` outside baseline | Low (single function, reuses existing scoring/eligibility/Discord plumbing) | Low (baseline guards unchanged, safe by construction — see Remediation) | High — this was a total blind spot for an entire discovery channel | Low — same risk profile as the news pipeline's already-trusted `NEW_REFERENCE`, gated by the same baseline discipline |
| **B — catalogue pagination cap excludes most of a large catalogue** | 10 (Timex, ~81% of catalogue excluded), latently affects Citizen (~43% excluded once >300 SKUs) | 1 confirmed + systemic exposure | Delta-prioritize undiscovered URLs over already-known ones before applying `max_items`, reusing the proven `citizen_de_products` sitemap-delta pattern | Medium (2 collector files + registry wiring) | Low (falls back to identical positional-slice behavior when no known-URL history exists, e.g. fresh baseline) | High for Timex specifically | None — strictly increases discovery of genuinely new items, never fabricates |
| **A — Casio/Citizen/Seiko have zero accessible regional product collectors outside US(+DE for Citizen)** | 2 (UK), 5, 6, 8, 11 (UK), 12 | 6 of 12 cases touch this | Not fixed this sprint — see Remediation Plan for why | High (each new region is its own onboarding project: access-method research, baseline, reference normalization) | N/A (not attempted) | Would be the single largest remaining recall gain | Requires the same care as every prior source onboarding (force-baseline discipline, live validation) |
| **M — Cloudflare blocks Casio UK and Citizen UK from direct HTTP** | 8, 11, (2's UK leg) | 3 | None attempted — correctly so, per explicit instruction not to bypass access controls | — | — | — | — |
| **H — regional-commercialisation semantics** | 2, 6, 8, 11 | 4 | Already remediated pre-sprint (`NEW_REGION`, commit `6b6e4ef`) for the *mechanism*; blocked in practice by A/M above for UK | — | — | — | — |
| **C — specialist source gap** | 3 | 1 | Already remediated pre-sprint (Monochrome/Deployant/Fratello/WatchTime) | — | — | — | — |

---

## Phase 3 — the Big Question, tested

**Hypothesis:** Watch Clank is better at "does this watch exist?" than "what
just changed about where/when/how it can be bought?"

**Tested, not assumed. Result: TRUE, and more specific than the hypothesis
states.**

Of the 12 cases, excluding the 2 grandfathered/expected cases and the 2
already-remediated cases, **8 cases remain genuinely explained by evidence**:

- **6 of 8** (Cases 2, 5, 6, 8, 11, 12) trace to **regional/market coverage**
  — Watch Clank knows a watch *exists* (often in the US) but has no sensor
  for a different market's commercial state.
- **2 of 8** (Cases 9, 10) trace to a **deeper, more specific version of the
  same hypothesis**: even within a market Watch Clank *does* monitor, the
  product-catalogue discovery channel had **zero path to say "this is new"**
  at all — it could create a `Watch` row (existence) but never an `Event`
  (change worth telling a journalist about). This is not a regional problem;
  it is a categorical gap between "the database learned a fact" and "the
  editorial layer was told a fact."
- **1 case (7)** is inconclusive without a reference number.

**Conclusion: the hypothesis is correct, and its sharpest form is Cases 9/10's
root cause — a structural blind spot between discovery and notification for
one entire class of source (product catalogues), not merely "regional
coverage is thin."** That sharper, more specific, more mechanical version is
exactly what this sprint's implemented fix addresses, while the broader
regional-coverage gap (A/M above) is real, large, but correctly left as
documented follow-up rather than five bespoke scrapers built in one night.

---

## Phase 4 — Regional coverage matrix

See `ai/handoff/REGIONAL_COVERAGE_MATRIX.md` for the full brand × region grid.

**Summary:** US is the only region covered for all four brands' product
catalogues (Timex/Citizen/Seiko) or news (all four). Casio has **no**
accessible product/price collector anywhere (Japan catalogue permanently
Akamai-blocked). Citizen additionally covers Germany (added same sprint as
the `NEW_REGION` mechanism). UK is blocked (Cloudflare 403, verified live for
both Casio and Citizen) for every brand that was tested. Japan is blocked for
Citizen (403, per the 2026-08-12 autopsy) but **was found NOT blocked for
Seiko's own JP store** this sprint (see Case 12) — an important, evidence-based
correction, not yet acted on.

---

## Phase 5 — Event semantics audit

Currently representable event types (VERIFIED by reading `app/services/editorial.py`
and `app/services/pipeline.py`): `NEW_REFERENCE`, `NEW_REGION`, `PRICE_CHANGE`,
`AVAILABILITY_CHANGE`, `SOLD_OUT`, `RESTOCK`. There is no separate
`PREORDER_OPEN`/`ON_SALE`/`NEW_PRODUCT`/`OFFICIAL_ANNOUNCEMENT` type — a
`PREORDER`→`AVAILABLE` availability-string transition (Case 8's shape)
already classifies correctly as `AVAILABILITY_CHANGE` via
`classify_price_availability_transition`; no new type was needed or added, in
keeping with "prefer one capability over five bespoke ones."

**SOLD_OUT noise — already corrected pre-sprint.** Commit `5e1b500`
("Tighten availability editorial eligibility", 2026-08-12, `SCORING_RULE_VERSION`
0.2.0→0.3.0) added `editorial_eligibility()`: a `SOLD_OUT`/`RESTOCK` event is
still **persisted** (for historical analysis) but only surfaces in Recent
Intelligence / Discord if it has confirmed editorial character (limited
edition, collaboration, or a recognisable family) **and** clears a raised
score threshold. Verified this still holds: `test_ordinary_restock_is_preserved_but_editorially_hidden`
and `test_citizen_product_availability_transitions_sold_out_then_restock`
both pass unmodified in this sprint's full suite. **No SOLD_OUT spam was
reintroduced** — this sprint's new catalogue-discovery `NEW_REFERENCE` events
are not in `AVAILABILITY_EVENT_TYPES`, so they are unaffected by and don't
weaken this gate.

A known watch moving `ANNOUNCED → PREORDER`, `PREORDER → AVAILABLE`, or
`ABSENT IN REGION → PRESENT IN REGION` is representable today wherever a
collector exists to observe it. Cross-currency price pairs are still never
compared (`classify_price_availability_transition` requires identical
currency), confirmed unchanged.

---

## Phase 6 — Polling & catalogue-cap audit

| Collector | Cadence | Real catalogue size (VERIFIED live) | Pre-sprint `max_items` coverage per non-baseline run | Fixed this sprint? |
|---|---|---|---|---|
| `timex_products` | 360 min | 1606 items / 1445 watches | 300 (**~19%**, blind positional slice) | **YES** — delta-prioritized |
| `citizen_products` | 360 min | 465 items (VERIFIED live 2026-08-14; documented as ~530 in Sprint 4, catalogues drift) | 300 (**~65%**, blind positional slice) | **YES** — delta-prioritized |
| `citizen_de_products` | 720 min | sitemap-bounded | already delta-tracked (`known_urls_from_observations`, prior sprint) | already correct — used as this sprint's template |
| `seiko_products` | 360 min | 222–276 items | 300 (**100%**, no cap engaged) | not needed — verified full coverage already |

Ordering guarantee: **none verified** for Shopify's default (unauthenticated)
`/products.json` sort — not confirmed to be recency-based. This is exactly
why a positional slice was unsafe and delta-prioritization (by URL history,
not by trusting sort order) was chosen over, e.g., requesting a `sort_by`
query parameter that isn't confirmed to exist/behave consistently.

Sitemap-delta vs. paginated-JSON-delta: both now converge on the same
pattern — prioritize URLs not already in `SourceObservation` history,
fill remaining budget with known URLs for price/availability refresh.

---

## Phase 7 — Specialist sensor audit

Current registry (VERIFIED, `app/services/health.py::KNOWN_COLLECTORS`):
`casioblog_rss`, `gcentral_rss`, `plus9time_rss`, `monochrome_rss`,
`deployant_rss`, `fratello_rss`, `watchtime_rss` — 7 specialist sources, all
RSS, all bounded, all on the pre-existing 72-hour publication freshness
policy.

**Would each Hall-of-Shame case be caught by a CURRENT specialist source
today?** Only Case 3 (Bonsai) is a specialist-discovery case, and yes —
already demonstrated live above. No other case in this set is a specialist-
source-shaped miss; Cases 5/6/8/12 are official-source regional-coverage
gaps, not specialist-blog gaps. **Great G-Shock World** and **NEEL** remain
documented-but-deferred candidates from Sprint 5/7 research, unchanged this
sprint — no evidence this sprint justified promoting either over the
higher-leverage fixes actually implemented.

---

## Phase 8 — Reddit / community intelligence (research only, per explicit instruction)

No Reddit collector exists and none was built this sprint. Quick research
check (public accessibility only, no scraping attempted):

- Reddit's official API (`api.reddit.com`) requires OAuth app registration;
  a read-only public JSON feed (`reddit.com/r/<sub>/new.json`) exists but is
  rate-limited and its terms-of-service posture for unauthenticated
  automated polling is explicitly discouraged/unstable — not a "real RSS
  endpoint" in the same low-risk class as the specialist blogs already
  onboarded.
- None of this sprint's 12 cases were shown to be Reddit-shaped misses —
  every genuine miss traced to either official regional coverage (A/M) or
  the catalogue-discovery gap (J/B), not community/social signal latency.
- **Recommendation: still research-only.** If pursued later, scope it as a
  narrow `COMMUNITY_EARLY_WARNING` Layer-B-style lead source (mirroring the
  existing `--ingest-manual-lead` pattern already built for Instagram
  leakers), never an OFFICIAL Event source, and never a generic firehose
  across all five subreddits at once — start with one bounded subreddit's
  public JSON feed, at low cadence, evaluated for signal-to-noise before any
  second one is added.

No code was written for this phase, per the mission's explicit default.
