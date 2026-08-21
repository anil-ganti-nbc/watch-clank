# Watch Clank — Hostile Architecture Audit (2026-08-21)

Auditor: independent hostile review. Evidence sources: full git history (63
commits), working tree at `ef2800d`, `HANDOFF.md` (2,216 lines), all 28
`ai/handoff/*.md` documents, application code (`app/`), tests (350),
three SQLite databases inspected strictly read-only (`.mac-dev` archive,
reset `data/`, live field-test app DB), deployment scripts, and the
`clank-project-archive` / `diagnostic-clank` materials reachable locally.

Remediation shipped on branch `audit/hostile-watch-clank-2026-08-21`
(commit `ea2f5a8`): three surgical fixes + three regression tests,
353 passing, ruff clean. Nothing was deployed; no production DB touched.

---

## 0. Verdict

Watch Clank's core question — *"genuinely useful editorial intelligence, or
merely something observed?"* — is **not answered by the architecture**. It
is answered by **patch accretion**: at least 14 incident-specific gates
(baseline guard, 72h published_at override, reactivation tags, burst
annotation, accessory phrases, staleness parsers, queue tiers…) each
closing one historically observed hole. The default path still equates
`FIRST_SEEN_BY_CLANK == NEW_REFERENCE` for everything that lacks a
REACTIVATED tag, and the operator's precision crisis is the direct,
predicted consequence. The system is also **structurally incapable of
seeing most Casio/Citizen market activity** it claims to cover, which —
not randomness — explains the current brand skew.

## 1. Provenance findings (highest severity first)

### P0-1. ~~The "Phase 0 remediation" commit does not exist~~ — RETRACTED, see Correction (2026-08-21, second pass)
> **RETRACTED.** This finding was **false**, caused by an audit process
> failure: the auditor worked from a stale local clone (`ef2800d`) and
> mistook absence from un-fetched local refs for nonexistence upstream.
> `bf87c7dff679bcee7e2d71dd68e2314ea37fcd10` ("Phase 0 remediation")
> exists on `origin/main`, one commit ahead of the audited base, and was
> fully re-audited in the second pass — see the **CORRECTION** section at
> the end of this document. The original text is preserved below for the
> record; do not rely on it.

`bf87c7dff679bcee7e2d71dd68e2314ea37fcd10` is not in this repository:
`git cat-file` fails; no commit mentioning "phase" exists on any ref; no
`.github/` directory; no `phase0_mutation_authorizer`,
`UNVERIFIED_PRODUCTION`, or loopback-authorizer symbol anywhere in the
tree. Either that work was done outside this repo and lost, or it never
happened. **Deployment/security authority: UNKNOWN.**

What *does* exist is a different, smaller containment: a field-test
middleware (`app/main.py:186-219`) that in `WATCH_CLANK_FIELD_TEST=1`
mode permits POSTs only to `/operations/run/*`, `/operations/run-all-safe`,
`/api/qc/review/*`, `/api/qc/lead-review/*`; a launcher that strips all
`DISCORD*`/`*WEBHOOK*` env vars and binds `127.0.0.1`
(`native/macos/launcher.py:45-58`); `_require_loopback` on mutating
routes. Verified consequence: **the local GUI is NOT read-only** — run-one,
run-all-safe, event QC, lead QC, and corrections all work locally. The
feared P-incident regression is not present; the described Phase 0 artifact
is. Both facts must be reconciled with whoever owns that narrative.

*(All of the above paragraph's claims were true of `ef2800d` and are
superseded by the re-audit of `bf87c7d` in the CORRECTION section.)*

### P0-2. Field-test production DB has no operational epoch
`~/Library/Application Support/Watch Clank/data/watch_clank.db` contains
**zero `operational_epochs` rows** while holding 5,364 watches. Therefore
`is_baseline_active()` is permanently False there and the entire epoch
suppression layer is inert; safety rests solely on the per-collector
`_auto_baseline_for_first_run` heuristic. Any code path that creates a
Watch without that heuristic's two preconditions (no epoch AND collector
never ran) floods. This is exactly the F-incident surface, still armed.

### P0-3. `_auto_baseline_for_first_run` covers only the no-epoch case
`app/services/pipeline.py:168-192`: returns False whenever *any* epoch
exists. Adding a new collector/brand/region to an existing, running
deployment without remembering `--force-baseline` replays the 300-event
flood (incident F) for that source's whole catalogue. Safety of a normal
growth operation depends on operator memory, not architecture.

### P1-4. Casio is structurally blind, not unlucky
Live field-test DB: Casio has **2,207 watches and zero Events ever**;
every local `casio_multi` run is PARTIAL with `casio_japan: BLOCKED`
(Akamai). Product knowledge comes from `casio_intl_news` (10 items/run)
plus UK/EU sitemaps that carry **no price, no availability, no
timestamps**. Casio cannot emit RESTOCK/SOLD_OUT/PRICE_CHANGE from any
covered surface, and its "newness" signal is URL-delta only. The
operator's suspicion that Casio underperforms is **confirmed and
structural** (Citizen's failures are different: see P1-5).

### P1-5. Timex precision collapse is quantified and architectural
Field-test DB reviews: Timex events 2 USEFUL / 17 NOT_USEFUL (**10.5%
precision**); Citizen burst 0/47 useful (45 OUT_OF_STOCK). The 17 Timex
NEW_REFERENCE events (Aug 17–19) include Easy Reader Day Date,
Weekender slip-through, Expansion Band models — catalogue fossils whose
Shopify `published_at` was bulk-touched days before discovery. The
Aug-19 hotfix itself documents that `published_at` "is not launch
authority", yet `NEW_REFERENCE` is still emitted from first-seen alone
(`pipeline.py:1382-1383`) unless a REACTIVATED/backorder tag exists.
There is no evidence dimension for *earliest external existence*
(retailer history, prior-region stock, archive mentions), so the H-avalanche
cannot be fixed by any threshold — only by an evidence model (§5).

### P1-6. Silent dead source reads HEALTHY
`monochrome_rss`: 20 consecutive ZERO_ITEMS runs in the field-test DB —
zero items ever — while `health.py` counts ZERO_ITEMS as success ⇒
HEALTHY forever. **Fixed** on the audit branch (3+ consecutive empty
successes ⇒ WARNING).

### P1-7. Windows lock "liveness probe" could kill processes
`run_lock._pid_alive` used `os.kill(pid, 0)`; on Windows that call is
`TerminateProcess` for any signal outside CTRL_C_EVENT/CTRL_BREAK_EVENT —
the stale-lock check could kill the lock owner or a PID-reused process.
**Fixed** on the audit branch (non-POSIX skips PID liveness, relies on
timestamp staleness). Residual: POSIX PID-reuse false-alive (documented,
low likelihood, staleness backstop).

### P2-8. Enrichment cap and UNKNOWN conflation (G residuals)
`MAX_AVAILABILITY_ENRICHMENT_FETCHES = 60` (`citizen_products.py:68`):
item 61+ silently keeps the availability-blind search-hit record;
enrichment *failure* falls back to the same record, so
**fetch-failure-UNKNOWN is indistinguishable from source-says-UNKNOWN**.
Burst annotation (`_annotate_new_reference_burst`) runs after Discord
alerts were already sent — admitted, disclosed, still wrong order.

### P2-9. Stale-run recovery can manufacture concurrency (R)
`stale_run_threshold_minutes=45`; force-baseline sweeps legitimately run
longer (`max_items=None`). A second entrant's `recover_stale_runs()`
marks the live run FAILED and starts a concurrent writer against
single-writer SQLite. WAL + 60s busy_timeout makes this usually survivable,
not safe. The single-writer assumption is real but undocumented and
un-enforced.

### P2-10. Scheduler authority is fragmented (D/S)
Reconstructed mechanisms: invisible root cron on Hetzner (never found,
image `fcb5e91`, predates Discord code — probably harmless but
unverified); `deploy` crontab legacy entries (disabled `af3b84c`);
19 user-systemd Docker timers (current, `Persistent=true`, rendered via
`render_units.py`); Windows Task Scheduler (unreachable since ~Aug 11 —
status UNKNOWN); macOS field-test has **no scheduler at all** (manual RUN
NOW only). The "≈226 misfires" figure appears in **no accessible
artifact** — reported UNVERIFIED; the plausible generator is the
multi-mechanism overlap above plus machine sleeps. A silently-stopped
schedule surfaces only as heartbeat WARNING (3× cadence) — nothing pages
if the DB itself stops being written.

### P2-11. Repo/systemd drift
Static units in `scripts/systemd/` are stale `/opt/watch-clank` artifacts
missing gear-patrol/great-gshock-world/casio-europe; Hetzner actually uses
registry-rendered units. Anyone installing from the repo gets a different
fleet than production runs. Also `.deployed-id` (`fcb5e91`) remains a
known misleading artifact.

### P2-12. Adversarial input accepted as a reference
`_REFERENCE_PATTERNS["Timex"]` had no digit requirement (its own Casio
pattern does): "twentieth", "tweeting" extracted as Timex references and
`normalize_timex_reference` is an unvalidated passthrough. **Fixed** on
the audit branch with regression test.

### P3 — smaller confirmed items
- Official-news staleness: generalized, but an unparseable date on a
  non-Timex source still means "fresh" (documented trade-off).
- Accessory gate is title-phrase-only and official-path-only; specialist
  leads remain structurally unguarded (classify-before-extract not built).
- `EventReview` lacks DUPLICATE (leads have it) — asymmetric lifecycle.
- Historical plus9time/casioblog LEAKED_IMAGE rows (pre-classifier)
  persist as STALE artifacts in DBs — correctly excluded from queues.
- Timex identity: `TW4B20700` vs `TW4B207009J` coexist as separate
  Watches (JDM-suffix variants defeat the single-match prefix link) —
  duplicate-watch class is live, not hypothetical.

## 2. Incident ledger (A–AD, re-adjudicated)

| # | Incident | Status after this audit |
|---|---|---|
| A | No Event path from product collectors | Fixed `c474e75`; invariant is call-site opt-in (`emit_events=False` defaults), not structural — a new runner can still silently ingest |
| B | `casio_multi` invisible | Fixed `c81ebed` (+ threshold-100 bug); matrix audited; opt-in hazard remains (see A) |
| C | No Discord authority on Hetzner | Fixed `415fc4c`; secrets on Hetzner only; local delivery disabled by design; **Windows sender status UNKNOWN** |
| D | ≈226 scheduler misfires | Number UNVERIFIED (absent from all local artifacts); fragmentation confirmed (P2-10) |
| E | Baseline absorbed real Timex launches | Fix principled but Timex-only + 72h; hour-73 launches and all timestamp-less brands remain absorbable; human-gated catch-up tool exists (good) |
| F | First-run flood (~300 events) | Auto-baseline closes no-epoch case only; epoch-present growth path still floods (P0-3); field-test DB has no epoch (P0-2) |
| G | Citizen 47-item false burst | Upstream churn confirmed; enrichment capped at 60, item 61+ silent; failure≡UNKNOWN; sold-out still labeled NEW_REFERENCE (P1-5/P2-8) |
| H | Timex old-SKU avalanche | **OPEN, ongoing** (17 more events post-window; 10.5% precision). Needs evidence model, not thresholds |
| I | FIRST_SEEN≠NEW_REFERENCE | Taxonomy exists but triggers only on REACTIVATED/backorder tags; every other weak-evidence upgrade path unchanged |
| J | Stale official-news alerts | Both paths gated (`232e9cb`); residual: unparseable date ⇒ fresh (non-Timex) |
| K | Atelier strap FP | Phrase gate shipped; strap Event id 72 preserved in field-test DB as evidence; specialist path unguarded |
| L | NH35→Seiko misattribution | Title-first + incidental-window classifier shipped and tested; multi-brand ambiguity honestly → None |
| M | LEAKED_IMAGE dump | `classify_lead_type` + EDITORIAL_MENTION shipped; historical rows remain (harmless, stale) |
| N | Lead QC gap | SpecialistLeadReview shipped; dispositions asymmetric (no event DUPLICATE); QC is advisory-only by contract — correct for now |
| O | Corrected-review UX | `is_corrected` + toggle shipped (`fe7f689`); correction history auditable; no cross-event poisoning (review tied to event_id) |
| P | Phase 0 containment | **Described commit does not exist** (P0-1); actual containment present; local GUI mutation-capable — workflow intact |
| Q | Windows stale locks | `os.kill` hazard **fixed here**; PID-reuse residual; DB-adjacent lock path fixes Docker case (`ce8e821`) |
| R | Full-DB writer coordination | De-facto single-writer; recovery-vs-long-run race (P2-9); assumption undocumented |
| S | Deployment authority confusion | GitHub canonical; Hetzner production+notification (last verified `938cc62`+Gear Patrol timer); Windows UNKNOWN; local field-test delivery-off; **production state as of today: UNKNOWN (no access this session)** |
| T | `citizen_de` retirement mismatch | Confirmed: timer stayed enabled until the 2026-08-17 deploy disabled it seconds-to-hours before a loud failure; retirement procedure now includes timers, but checklist is prose, not enforced |
| U | Citizen UK blind spot | OPEN (Cloudflare + robots.txt ClaudeBot disallow; EUR-only alternative correctly rejected). UK-first Citizen releases remain invisible |
| V | Casio regional gaps | EU sitemap added (2,016 watches, GBA-950 verified); **JP product catalogue still BLOCKED**; sitemap≠coverage (no price/avail/time) |
| W | Great G-Shock World | Atom/RDF + re.ASCII + GCW prefix shipped with real-fixture tests; sibling JP sources evaluated and honestly rejected |
| X | Gear Patrol gap | Shipped + deployed (timer installed 2026-08-17/18 reconciliation); category filter + URL extraction; sitewide-feed noise handled by `required_category` |
| Y | Instagram omission | Manual ingestion CLI exists (`--ingest-manual-lead`); no SOCIAL_EARLY_WARNING lane; recall cost accepted, transport difficulty documented |
| Z | Hall of Shame corpus | Original 12: 6 genuine gaps, 2 grandfathered, 2 pre-fixed, 1 inconclusive, 1 corrected-assumption; HCC005/HCC006 = human-latency positive control. Post-repair corpus = **10 numbered specimens**; HANDOFF prose "Timex x6, Casio x4, Citizen x1" (=11) is wrong — actual tally Timex 5 / Casio 4 / Citizen 1 |
| AA | Luke Skywalker | Confirmed positive control: real Event + confirmed Discord delivery 2 days before competitor — system worked |
| AB | Seiko/Orient specialists | Researched, recommended (PR TIMES Seiko/Epson, Orient Place), **not implemented** — open gap |
| AC | Brand skew | Quantified §3 — Timex precision 10.5%, Citizen burst 0%, Casio structurally blind; skew explained, not random |
| AD | "Fixed" regressions | Re-tested from first principles where feasible; three new defects found & fixed (Timex regex, os.kill, ZERO_ITEMS health); 350-test suite green before, 353 after |

## 3. AC quantification (field-test DB, read-only)

Window available locally: 2026-08-13 → 2026-08-19 (Hetzner DB not
accessible from this machine; recall vs Notebookcheck ground truth is
NOT computable here and is reported as UNKNOWN rather than guessed).

| Brand | Watches | Events (all-time) | Reviewed | Useful | Precision |
|---|---|---|---|---|---|
| Timex | 1,460 | 17 NEW_REFERENCE + 10 avail. | 19 | 2 | **10.5%** |
| Citizen | 519 | 47 NEW_REFERENCE (one burst) | 47 | 0 | **0%** |
| Casio | 2,207 | **0** | — | — | no signal at all |
| Seiko | 1,178 | 34 avail. transitions | 0 | — | unreviewed |

Delivery: `alerted=0` on **all 108 events** locally (by design — Hetzner
holds notification authority). Editorial success on this machine is
therefore entirely dependent on a host the operator isn't looking at.

## 4. Collector matrix (condensed; no blank cells)

| Collector | Discovery | Δ semantics | Price/Avail | Timestamp | Events | Freshness gate | Notify | Health |
|---|---|---|---|---|---|---|---|---|
| casio_multi | news index + JP catalogue | URL/GUID | news: none; JP: yes (BLOCKED) | free-text date | NEW_REF/REGION via news; transitions JP-only | parse-or-fresh | inline ≥50 | KNOWN+cadence |
| casio_uk/europe_sitemap | sitemap `<url>` delta | URL set diff | **none** | lastmod (unused) | NEW_REF/REGION only | baseline/auto only | inline ≥50 | KNOWN+cadence |
| citizen_news / seiko_jp_news / timex_news | RSS/Atom | GUID | none | ISO (timex strict) | NEW_REF/REGION | timex strict; others parse-or-fresh | inline ≥50 | KNOWN+cadence |
| citizen_products | search API breadth | URL set diff | enrich ≤60/run, else UNKNOWN | none | NEW_REF/REGION/transitions* | baseline only | inline ≥0 (experimental) | KNOWN+cadence |
| seiko_products / seiko_jp_products | listing JSON | URL set diff | yes | none | transitions + NEW_REF/REGION | baseline only | inline ≥0 | KNOWN+cadence |
| timex_products | products.json | URL set diff | yes | **published_at** (ignored post-baseline) | transitions + NEW_REF/REGION | baseline 72h override | inline ≥0 | KNOWN+cadence |
| casioblog/gcentral/plus9time | RSS | GUID | none | pubDate | Leads only | classify_lead_freshness | ≥40 conf, FRESH only | KNOWN+cadence |
| monochrome/deployant/fratello/watchtime/great_gshock_world/gear_patrol | RSS/Atom/RDF | GUID | none | pubDate | Leads only | same | same | KNOWN+cadence; **ZERO_ITEMS trap (fixed)** |

\* transitions only for URLs actually re-fetched within the 300-item cap;
long-tail refresh is positional, so RESTOCK/SOLD_OUT detection for most
of a 1,400-item catalogue is arbitrary-order and multi-cycle.

## 5. Architecture questions

1. **Domain model.** Source→CollectorRun(→lock)→SnapshotFetch/Blob→parse→
   {Watch+Family→SourceObservation} | ReleaseLead | SpecialistLead;
   Events from transition classifiers; EventWatch link; QC tables beside;
   OperationalEpoch + denormalized `is_baseline` flags;
   notification state scattered (`Event.extra.alerted`,
   `SpecialistLead.notified_at`, webhook config). **"Newness" lives in at
   least five loosely-synchronized places**: Watch-row creation, URL-delta
   sets, epoch/baseline flags, lead URL-dedup, and freshness columns.
   That, not any single bug, is the root condition behind E/F/G/H/I.
2. **Identity.** `(manufacturer, brand, reference_canonical)`; conservative
   passthrough normalizers; Timex single-match prefix linking. Sound for
   colorways (suffixes preserved), unsound for regional/JDM variants
   (TW4B20700 vs TW4B207009J duplicates live). No alias table.
3. **Novelty representation.** A pile of booleans + reason strings. No
   evidence ledger: no `earliest_external_date`, `earliest_retailer_date`,
   `prior_OOS_history`, `region_first_seen`, or independence-weighted
   confidence. `FIRST_SEEN_BY_CLANK` exists but is reachable from exactly
   one trigger instead of being the *default* discovery truth that stronger
   claims must earn. Inverted from where it should be.
4. **Freshness/novelty coupling.** Partially separated (freshness module,
   queue tiers) but Events carry no freshness column at all — the
   freshness docstring's "Events don't need one" assumption has now been
   wrong twice (Sprint 10, Aug-19) without a schema change to show for it.
5. **QC as training data.** Right semantics chosen (event-scoped,
   correction-preserving, advisory-only). Not yet wired to anything —
   acceptable; do not add ML before the evidence ledger exists.
6. **Single writer.** Real but undocumented; enforce (advisory lock table
   or documented invariant + max-run-duration ≤ stale threshold) rather
   than pretend concurrency works.
7. **Retirement/deployment procedure.** Registry-driven rendering is the
   right mechanism; delete or regenerate the stale static units; remove
   `.deployed-id`; make the retirement checklist executable (registry,
   CLI choices, timers, dashboards, docs, tests — currently prose).

## 6. What was changed on the audit branch

`audit/hostile-watch-clank-2026-08-21` @ `ea2f5a8` (pushed):
1. Timex reference pattern digit requirement (+test).
2. Windows-safe lock liveness check (+test).
3. Persistent-ZERO_ITEMS health degradation (+test).
353 tests passing, ruff clean. No behavior change to collection semantics,
no DB migrations, nothing deployed.

## 7. Recommended next actions (priority order)

1. **Invert the novelty default**: make `FIRST_SEEN_BY_CLANK` the default
   event_type for catalogue first-sightings; upgrade to `NEW_REFERENCE`
   only with affirmative evidence (published_at cluster shape, first-party
   announcement, recognisable-family+availability, or human catch-up).
   This single change addresses H, G-semantics, and I together.
2. **Add an evidence ledger to Watch** (earliest_external_date,
   publication evidence, prior-OOS count, region set) and score from it.
3. **Close the epoch-present growth hole**: auto-baseline any collector's
   first-ever run regardless of epoch, or refuse to schedule a collector
   whose collector_id has no baseline-flagged run.
4. **Create an epoch row in the field-test DB** (or document why its
   absence is safe) — currently the epoch layer is dead code there.
5. **Casio**: replace blocked JP scraping reliance with JP sitemap/news
   structured surfaces; accept that UK/EU sitemaps need a periodic
   availability spot-check to ever emit transitions.
6. **Implement PR TIMES Seiko + Orient Place** (researched, ready).
7. **Reconcile the Phase 0 narrative** with the repository (P0-1) and
   record scheduler-authority facts (Windows state, root cron) in
   HANDOFF — currently UNKNOWN.
8. Enforce/document single-writer; align `max_run_duration_seconds` <
   `stale_run_threshold_minutes` or scope recovery by run heartbeat.

---

# CORRECTION (2026-08-21, second pass) — P0-1 retracted, Phase 0 re-audited

## What went wrong

The first audit concluded that commit `bf87c7dff679bcee7e2d71dd68e2314ea37fcd10`
("Phase 0 remediation") "did not exist". **That conclusion was false.** The
auditor's local clone was at `ef2800d`; `bf87c7d` had been pushed to
`origin/main` one commit ahead of that base, and the auditor ran `git
cat-file` and tree greps against local refs **without ever fetching**. A
claim of absence made without a fetch is not evidence — it is the exact
class of confident false conclusion this audit was commissioned to prevent,
and it happened anyway. Process rule adopted from now on: every review
begins with `git fetch origin --prune` and an explicit local-HEAD vs
remote-HEAD reconciliation.

## Repository reconciliation

- Audited base: `ef2800dc8f06918224295431db64cf42859c75e3`
- Remote HEAD: `bf87c7d` (squash of branch `phase0/containment`,
  7 commits `1f4785d..48ae520`, all children of `ef2800d`)
- First-audit branch: `audit/hostile-watch-clank-2026-08-21`
  (`ea2f5a8` + `bc8859b`), diverged from `ef2800d`: remote +1, audit +2
- Successor branch: `audit/watch-clank-remediation-2026-08-21`, created
  from `origin/main` (`bf87c7d`); audit fixes re-applied selectively
  (not merged blindly)

## Re-audited findings affected by bf87c7d

1. **P0-1 (retracted).** The commit exists; it adds Phase 0 CI
   (`.github/workflows/phase0-{ci,container}.yml`), Gitleaks config +
   contract test, dependency locking (`uv.lock`,
   `requirements.container.lock`), Dockerfile pinning, loopback-only
   `app_host` validation, a request-level containment middleware, the
   supported fail-closed launcher `app/serve.py`, and a Win32
   `OpenProcess` lock-liveness check.
2. **Dashboard mutation behaviour — REVERSED from the first audit.** On
   `ef2800d` the local GUI could mutate (QC, corrections, run-one,
   run-all-safe). On `bf87c7d` a new `phase0_dashboard_containment`
   middleware denies EVERY non-GET/HEAD/OPTIONS request unless
   `app.state.phase0_mutation_authorizer` is installed — and **nothing in
   production code ever installs one** (only tests monkeypatch it).
   Verified at runtime against the real server (`python -m app.serve`),
   not inferred: loopback GET `/` → 200; POST Event QC → 403; POST Lead
   QC → 403; POST run-one → 403; POST run-all-safe → 403; identical 403s
   under `WATCH_CLANK_FIELD_TEST=1`. The macOS packaged launcher sets no
   authorizer either. **The entire field-test workflow — QC triage,
   corrections, collection triggers — is currently dead on Phase 0
   HEAD in every launch profile.** The original audit's operational fear
   (P-incident) was directionally right even though its existence claim
   was wrong. Restoring a loopback-scoped local authorizer (installed by
   the supported launcher/field-test profile only) is required to revive
   the workflow and remains an operator-gated security decision per
   bf87c7d's own merge note; deliberately NOT done unilaterally here.
3. **Windows lock handling.** bf87c7d replaced `os.kill(pid, 0)` on nt
   with `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + access-denied
   tolerance — strictly better than both the original bug and the first
   audit's "return True" fallback, which is therefore dropped. The
   audit's contract test is retained and adapted: Windows must never call
   `os.kill` for liveness; handle ⇒ alive; last_error 5 ⇒ alive;
   otherwise dead.
4. **CI status.** First audit said "no `.github/` directory". With
   `bf87c7d` fetched: Phase 0 CI and container-smoke workflows exist.
   Local equivalence verified by running the full suite + ruff (364
   passed) on the successor branch.
5. **Deployment/security authority.** Still no production deployment
   change: bf87c7d's own message states Hetzner/NAS untouched and
   deployment convergence is operator-gated. Authority statement stands:
   GitHub canonical for source; Hetzner presumed production but its
   current runtime state remains UNVERIFIED from this machine.
6. **Unaffected findings.** Everything not touching Phase 0 surfaces
   (novelty semantics, baseline holes, Casio blindness, Timex precision,
   health semantics, scheduler fragmentation, identity ambiguity, QC
   asymmetry) was re-checked against the bf87c7d tree where relevant and
   stands as originally reported.

## Remediation shipped on the successor branch

(`audit/watch-clank-remediation-2026-08-21`, rebased onto `bf87c7d`)

- Re-applied: Timex prose-word reference regex fix (+test) — still absent
  on bf87c7d; ZERO_ITEMS health degradation, upgraded to explicit
  documented setting `zero_item_warning_streak` (+tests).
- Dropped: first audit's Windows `_pid_alive` fallback (superseded by
  bf87c7d's OpenProcess); replaced with a stronger contract test.
- Phase 6 novelty inversion: product-catalogue first sightings now default
  to FIRST_SEEN_BY_CLANK; NEW_REFERENCE requires affirmative publication
  evidence (same trusted bar as the baseline override); REACTIVATED/
  backorder tags always win. Official-news path keeps NEW_REFERENCE (a
  first-party announcement IS launch evidence). Burst detection counts
  both first-sighting types.
- Phase 7 evidence provenance: every novelty Event now carries a
  structured `novelty_evidence` block in `Event.extra` (collector, region,
  local first-seen, source published_at, existed-locally-before,
  reactivation signal, publication freshness state, baseline state,
  classification reason). Additive JSON; no schema change.
- Phase 8 baseline hole closed: `_auto_baseline_for_first_run` no longer
  exempts epoch-bearing databases — ANY collector with zero successful
  runs auto-baselines its first run regardless of epoch. Collectors with
  established history are grandfathered (documented; residual risk defused
  by Phase 6 labelling instead of silence).
- 14 existing tests updated to the corrected semantics (they encoded the
  old first-seen⇒NEW_REFERENCE assumption); 6 new adversarial tests added.
- Full suite: 364 passed, ruff clean.
