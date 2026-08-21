# Watch Clank — Remediation Phase 2: Operational Recovery, Semantic Validation, Casio/Citizen Recall (2026-08-21)

Branch: `audit/watch-clank-remediation-2026-08-21` (pushed; NOT merged).
Base: `bf87c7d` (Phase 0 remediation, origin/main). Prior phase: `7fef9d0`.

---

## 1. Executive verdict

**Semantically improved and locally operable; recall materially improved for
Casio; still NOT deployable until the operator re-baselines Hetzner and the
Phase 0 local-authority model is accepted.**

Specifically:
- The local field-test workflow is restored under an explicit, allowlisted,
  loopback-proven mutation authority (was: fully dead on Phase 0 HEAD).
- Novelty is now evidence-gated with provenance, strength grading, bulk-touch
  detection, and honest FIRST_SEEN defaults; the catch-up promotion path was
  found broken by the new semantics and fixed.
- Casio Japan — previously 100% blind — now has a live, robots-published,
  daily-fresh first-party surface (`casio_jp_sitemap`), validated end-to-end
  against the real sitemap including GBA-950/GCW-B5000 ground truth.
- Citizen UNKNOWN availability is now provenance-tagged (cap vs fetch-failure
  vs source-has-no-field).
- Event generation can no longer silently default off.

## 2. Repository provenance

- Starting local HEAD: `7fef9d0` on `audit/watch-clank-remediation-2026-08-21`
- Starting remote main: `bf87c7d` (verified after `git fetch origin --prune`;
  no drift during this phase)
- Graph verified: `ef2800d → bf87c7d → 7fef9d0`; prior-audit commits
  `ea2f5a8`/`bc8859b` remain on the superseded branch
- Divergence at start: branch +1 vs origin/main; tree clean
- Final HEAD: see §16; pushed to `origin/audit/watch-clank-remediation-2026-08-21`
- Dirty files at end: none

## 3. Phase 0 local workflow (runtime-verified, real servers, temp DBs)

| Action | direct uvicorn / bare app | `app.serve` (default) | `app.serve --profile local-operator` | macOS field-test launcher |
|---|---|---|---|---|
| GET `/` | 200 | 200 | 200 | 200 |
| Event QC POST | 403 | 403 | **reaches route** (404 on missing id) | **reaches route** |
| Lead QC POST | 403 | 403 | reaches route | reaches route |
| Correction (2nd POST) | 403 | 403 | allowed (same route) | allowed |
| run-one collector | 403 | 403 | allowed (202 in field-test mode) | allowed |
| run-all-safe | 403 | 403 | allowed (202) | allowed |
| unapproved POST | 403 | 403 | 403 | 403 |
| LAN client / Host spoof / X-Forwarded-For | 403 | 403 | 403 | 403 |

Before this phase, ALL mutation cells were 403 in every profile (verified
last phase). `GET /api/runtime` now reports `mutation_authority` honestly
(fixes the stale `read_only: False` lie).

## 4. Security model

- **Who installs authority:** only supported launchers — the macOS packaged
  field-test launcher (`native/macos/launcher.py`, which also strips all
  DISCORD*/WEBHOOK* env vars and sets EDITORIAL_NOTIFICATIONS_ENABLED=false)
  and `python -m app.serve --profile local-operator`.
- **What is authorized:** exactly four POST route families, anchored regexes
  in `app/local_operator.py`: `/api/qc/review/{id}`,
  `/api/qc/lead-review/{id}`, `/operations/run/{collector_id}`,
  `/operations/run-all-safe`. A future POST route must be added there
  deliberately; nothing is inherited.
- **How loopback is proven:** per request, client IP AND Host header must
  resolve loopback (`ipaddress…is_loopback` or `localhost`). Forwarded
  headers are never consulted (no proxy architecture exists).
- **What stays denied:** every non-allowlisted POST, all PUT/DELETE, any
  non-loopback client or Host, wildcard binds (config validator rejects
  non-loopback APP_HOST outright), and direct `uvicorn app.main:app` (no
  authorizer installed → fail-closed by construction).
- **Discord locally:** impossible in field-test profile — secrets stripped
  pre-startup + EDITORIAL_NOTIFICATIONS_ENABLED=false; regression-tested.

## 5. Novelty decision model (current rules)

| Type | Required evidence |
|---|---|
| `FIRST_SEEN_BY_CLANK` | DEFAULT for catalogue first sighting. Also forced by REACTIVATED/backorder tags, or fresh-but-bulk-touched timestamps, or no publication evidence. Score 5–15, QC tier 3, reviewable, Discord-suppressed unless `discord_first_seen_enabled=true`. |
| `NEW_REFERENCE` (catalogue) | published_at within 72h of observation AND cluster shape launch-like (MEDIUM strength): small sibling count or single-collection cluster. |
| `NEW_REFERENCE` (news) | First-party announcement article naming an unseen reference (STRONG; unchanged path). |
| `NEW_REGION` | Known reference, first observation in a new region (product observations ∪ announcement regions). |
| `RESTOCK` / `SOLD_OUT` | Healthy before/after pair AVAILABLE↔SOLD_OUT/UNAVAILABLE, same region. Editorial bar 70 + character gate unchanged. |
| `PRICE_CHANGE` / `AVAILABILITY_CHANGE` | Healthy same-region pair; currency must match. |
| Specialist leads | `classify_lead_type`: limited → collab → restock-language → leak-language → accessory-gate → reference → deal → EDITORIAL_MENTION. Never fabricated references; UNKNOWN allowed. |

## 6. Evidence provenance

Every novelty Event carries `extra["novelty_evidence"]`:

```json
{
  "collector_id": "timex_products",
  "region": "US",
  "local_first_seen_at": "2026-08-21T12:00:00+00:00",
  "source_published_at": "2026-08-21T10:00:00+00:00",
  "existed_locally_before": false,
  "source_reactivation_signal": false,
  "publication_freshness_state": "FRESH",
  "baseline_state": "INACTIVE",
  "official_article_corroboration": null,
  "evidence_strength": "MEDIUM",
  "cluster_shape": {"siblings": 2, "collections": 1},
  "classification_reason": "affirmative source publication evidence (...)"
}
```

Strength enum: STRONG (reserved: first-party announcements) / MEDIUM (fresh
timestamp, launch-like cluster shape) / WEAK (local absence, reactivation,
bulk-touch batch). WEAK never claims NEW_REFERENCE.

## 7. Baseline invariants

- Fresh DB: first run auto-baselines (epoch-independent since `7fef9d0`).
- New collector in existing DB (epoch present or not): auto-baselines —
  operator memory no longer load-bearing.
- Fresh launch during first baseline: survives via
  `classify_baseline_product_freshness` (72h) + MEDIUM cluster shape →
  NEW_REFERENCE even mid-baseline (tested with 50 historical + 3 launches).
- Partial/blocked/ZERO_ITEMS first run: only SUCCESS/PARTIAL/ZERO_ITEMS
  counts as initialization; BLOCKED/FAILED runs do not initialize, so a
  collector that never actually saw the catalogue re-baselines next run.
- Collector re-enabled after retirement: grandfathered from flood-suppression
  (has history) but its accumulated delta surfaces as labelled FIRST_SEEN,
  not confident claims; catch-up tooling promotes genuine launches.
- Interrupted epoch baseline: epoch guard suppresses independently.

## 8. Timex regression results

- Fossil class (Easy Reader Day Date etc., no timestamps): FIRST_SEEN_BY_CLANK,
  WEAK, silent-by-default — corpus specimens 2/3.
- Bulk-touched timestamp (9 unrelated collections sharing one stamp):
  FIRST_SEEN, reason names the sync batch — specimen 3.
- Genuine fresh launch (Cavatina-shaped single-collection cluster): 
  NEW_REFERENCE, alerts — specimens 4 + discord test.
- 73-hour launch: honest FIRST_SEEN locally AND recoverable via
  `find_baseline_catchup_candidates` → `create_baseline_catchup_events`
  (fixed this phase: FIRST_SEEN events no longer lock watches out of
  promotion) — specimen 5.

## 9. Casio autopsy

Ground-truth corpus (historical + current):

| Reference | Earliest official evidence | WC before | Root cause | WC after | Catching source |
|---|---|---|---|---|---|
| GBA-950 ×5 | EU sitemap 2025; G-Central 2026-07-02 | miss (REGION_GAP) | no EU collector → fixed 08-17; JP blind | NEW_REGION proof (EU) + JP sitemap contains GBA-950-2A/-7A live | casio_europe_sitemap + **casio_jp_sitemap (new)** |
| GCW-B5000/MRG-B5000SA-2 | Great G-Shock World 2026-08-16 | late (source gap, then onboarded) | source absent → fixed | lead path proven; JP sitemap contains GCW-B5000UN-6 live | great_gshock_world_atom + casio_jp_sitemap |
| EQB-1300D-5A/-2A | CasioBlog March | stale-correlation alert bug | notify_correlation had no freshness gate → fixed 08-19 | gated (regression suite) | casioblog_rss |
| GD-350S-1 UK | UK sitemap lastmod 08-12 | hit post-fix | — | covered | casio_uk_sitemap |
| F-B100W | UK sitemap 08-03 | hit post-fix | — | covered | casio_uk_sitemap |
| Future JP-first releases | JP watches sitemap (daily lastmod) | **total blindness** (Akamai-blocked pages) | product pages blocked; no JP surface | **closed**: 17.8k URLs, refs in paths, GBA-950/GCW-B5000 verified live | casio_jp_sitemap |

Recall before: 0/6 JP-relevant specimens detectable in Japan; transitions
impossible brand-wide (no price/availability anywhere). After: JP home
market covered by a daily-fresh surface; UK/EU retained; specialist lanes
intact. Remaining Casio limitation (honest): sitemaps carry no price/
availability → RESTOCK/SOLD_OUT still impossible for Casio; requires either
an accessible product-page transport (none legitimate today) or retailer
correlation (future work).

## 10. Citizen autopsy

- 47-item burst: upstream visibility churn; enrichment added 08-18; cap 60.
  This phase: items beyond the cap now carry `NOT_ENRICHED_CAP`, failed
  detail fetches carry `ENRICHMENT_FETCH_FAILED` (parser warnings + run
  metadata `availability_enrichment_capped_out`) — three different UNKNOWNs
  are now distinguishable. Cap stays 60 (load bound); honesty replaces
  capacity.
- Luke Skywalker AW1910-48W: positive control, untouched, still passes
  (non-baseline discovery → NEW_REFERENCE → delivered).
- UK: remains a documented blind spot (Cloudflare 403 + robots.txt disallows
  ClaudeBot by name; EUR-only alternative correctly rejected). No safe route
  found this pass; unchanged.
- DE: retired (standing decision).

## 11. Collector matrix (20 registered)

| ID | Brand | Region | Sched cadence | Freshness data | Price/Avail | Events | Leads | Health | Baseline | Key limitation |
|---|---|---|---|---|---|---|---|---|---|---|
| casio_multi | Casio | INTL+JP | 90m | free-text date | news:none; JP:blocked | yes | n/a | PARTIAL (JP BLOCKED) | auto | JP pages blocked; sitemap collector supersedes for JP |
| casio_uk_sitemap | Casio | UK | 720m | lastmod (stale) | none | NEW_REF/REGION | n/a | ok | auto | no price/avail |
| casio_europe_sitemap | Casio | EU | 720m | lastmod (stale) | none | NEW_REF/REGION | n/a | ok | auto | no price/avail |
| **casio_jp_sitemap** | Casio | JP | 360m | **lastmod (daily-fresh)** | none | NEW_REF/REGION | n/a | new | auto | no price/avail |
| citizen_news | Citizen | GLOBAL | 90m | pub date | none | yes | n/a | ok | auto | — |
| citizen_products | Citizen | US | 360m | none | enrich≤60, provenance-tagged | yes | n/a | ok | auto | UK blind; cap |
| seiko_jp_news | Seiko | JP | 90m | pub date | none | yes | n/a | ok | auto | — |
| seiko_products | Seiko | US | 360m | none | yes | yes | n/a | ok | auto | — |
| seiko_jp_products | Seiko | JP | 360m | none | yes | yes | n/a | ok | auto | — |
| timex_news | Timex | US | 90m | ISO strict | none | yes | n/a | ok | auto | image-filename SKUs only |
| timex_products | Timex | US | 360m | published_at (untrusted alone) | yes | yes | n/a | ok | auto | bulk-touch noise (now detected) |
| casioblog/gcentral/plus9time | Casio/Seiko/Citizen | — | 45–360m | pubDate | none | leads | yes | ok | freshness-based | legacy runners take no force_baseline |
| monochrome/deployant/fratello/watchtime | multi | — | 45–90m | pubDate | none | leads | yes | ZERO_ITEMS→WARNING (new) | freshness-based | monochrome historically empty |
| great_gshock_world_atom | Casio | JP-blog | 45m | pubDate | none | leads | yes | ok | freshness-based | RDF/Atom only |
| gear_patrol_rss | multi | US | 90m | pubDate | none | leads | yes | ok | freshness-based | sitewide feed, category-filtered |

(UNKNOWN where not verifiable from this machine: Hetzner runtime health.)

## 12. Source coverage matrix (brand × region)

- **Casio**: JP = sitemap (NEW, daily-fresh) + blocked pages; UK/EU =
  sitemaps; INTL = news; specialists = Great G-Shock World (high value),
  CasioBlog, G-Central. Blind: price/availability everywhere; social
  (@geesgshock-class Instagram) = manual CLI only.
- **Citizen**: US = products+news; UK = BLIND (documented); DE = retired;
  EU platform rejected as fake-GBP. Specialists: generic publications only.
- **Timex**: US = products+news (strongest metadata). Others: n/a.
- **Seiko**: JP retail + JP news + US products. Orient: NONE (PR TIMES
  Epson/Orient Place researched, recommended, not built — open).

## 13. New defects found this phase

1. **Catch-up lockout** (HIGH): FIRST_SEEN events excluded watches from the
   baseline-catch-up promotion path forever. Fixed: only novelty CLAIMS
   block. Tests: specimen 5.
2. **Specialist accessory hole** (MED): strap sale + SKU →
   POSSIBLE_NEW_REFERENCE lead. Fixed: accessory gate precedes reference
   matching; shared phrase list. Test: specimen 15.
3. **FIRST_SEEN Discord flood** (HIGH, live-found): initial post-baseline
   crawl of a large catalogue = hundreds of alerts/run at threshold 0.
   Fixed: FIRST_SEEN not audible by default (`discord_first_seen_enabled`),
   fully visible in queue/dashboard. Live-validated on casio_jp_sitemap.
4. **Stale provenance lie** (LOW): `/api/runtime read_only:False` after
   containment. Fixed: derived from installed authority.
5. **Bulk-touch masquerade** (HIGH): fresh-but-maintenance timestamps earned
   NEW_REFERENCE. Fixed: cluster-shape evidence strength. Specimen 3.

## 14. Remaining open defects (by editorial harm)

1. **Casio/Citizen price+availability transitions impossible** — no
   legitimate product-page transport; restocks invisible brand-wide for
   Casio, partially for Citizen (US enriched only).
2. **Citizen UK blind spot** — no safe surface found.
3. **Hetzner runtime state UNVERIFIED from this machine** — deployment of
   this branch (incl. new collector timer + migration 011) is operator-
   gated; old collectors there lack baseline-flagged runs (grandfathered).
4. **Orient/PR TIMES specialists unbuilt** (researched, ready).
5. **Social early-warning lane** — manual CLI exists; no SOCIAL_EARLY_WARNING
   semantics; operator-effort decision pending.
6. Windows runtime state UNKNOWN (unreachable since ~Aug 11).

## 15. Tests

Commands and results (final state):
- `.venv/bin/python -m pytest tests -q` → **399 passed, 1 skipped**
  (started this phase at 397+1skip context; +18 new tests across security,
  novelty corpus, casio_jp_sitemap, citizen provenance, DUPLICATE)
- `.venv/bin/python -m ruff check .` → clean
- Compile: full-suite import graph exercises every module; migrations run
  (`scripts.migrate` → head `011_event_review_duplicate`)
- Live server acceptance: see §3 (curl against `app.serve` both profiles)
- Live-source probes (read-only, rate-limited): Casio robots.txt, JP
  sitemap index + watches.xml (17,811 URLs), Europe sitemap (200),
  Great G-Shock World atom (200), JP pages (403, confirming block)
- Windows lock contract: `test_windows_lock_liveness_never_calls_os_kill`

## 16. Git

| SHA | Subject |
|---|---|
| `f0df6f5` | Restore authorised local field-test mutations (launcher-scoped) |
| `7a70ebd` | Harden novelty evidence, semantic corpus, and catch-up promotion path |
| `b152425` | Add Casio Japan sitemap-delta collector; gate FIRST_SEEN alerts |
| `8f0adcd` | Citizen availability provenance + editorial-by-default event generation |
| `ce086a2` | Fix ScalarResult count usage in Phase 8 test |
| `ba36cae` | EventReview gains DUPLICATE disposition (migration 011) |

Pushed: `origin/audit/watch-clank-remediation-2026-08-21` up to date.
Not merged to main.

## 17. Deployment

- Hetzner: untouched. NAS: untouched. Production DBs: untouched.
- Discord: no messages sent (all notifier tests use fakes/local hooks).
- Windows runtime: untouched/unreachable.
- macOS field-test DB (`~/Library/Application Support/Watch Clank`):
  inspected READ-ONLY only.
- Local repo dev DB (`data/watch_clank.db`, empty reset DB): migrated to
  head 011 as the migration smoke test — it is the execution workspace's
  own empty database, not production, and held zero rows beforehand.
- All acceptance tests ran against disposable temp SQLite DBs.

## 18. Operator decisions required

1. Merge `audit/watch-clank-remediation-2026-08-21`? (Recommended after
   field-test acceptance.)
2. Deploy to Hetzner? Requires: rebuild image, migrate (011 additive),
   install `watch-clank-casio-jp-sitemap.timer` via render_units.py, and
   decide whether existing collectors get one silent re-initialization run
   or stay grandfathered (current code: grandfathered).
3. Should FIRST_SEEN events ring Discord? Currently suppressed
   (`discord_first_seen_enabled=false`). Recommendation: keep suppressed;
   triage the queue daily instead.
4. Manual Instagram/social lead ingestion: worth operational effort? The
   CLI exists (`--ingest-manual-lead`); a SOCIAL_EARLY_WARNING lane needs
   only policy, not code, to start.
5. Accept PR TIMES Seiko/Epson + Orient Place as the next source sprint?
   (Research complete; both verified live feeds.)

---

## Final operating doctrine scorecard

- *Every useful discovery arrives with enough evidence to understand why it
  matters*: YES — novelty_evidence on every novelty Event; alert text states
  its basis.
- *Every uncertain discovery admits uncertainty*: YES — FIRST_SEEN default,
  WEAK/MEDIUM/STRONG strength, UNCONFIRMED alert language.
- *Every collector proves that it is alive*: PARTIAL — persistent-empty
  warning shipped; scheduler-did-not-run vs ran-and-found-nothing is
  distinguishable in CollectorRun rows but the dashboard does not yet say
  "expected next run by X" per source beyond heartbeat.
- *Every known blind spot closed or explicitly visible*: YES — Casio JP
  closed; Citizen UK / price-availability / Orient / social documented here.
