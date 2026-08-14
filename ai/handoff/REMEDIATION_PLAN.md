# Remediation plan — 2026-08-14 Hall of Shame sprint

Companion to `ai/handoff/WATCH_CLANK_HALL_OF_SHAME_AUTOPSY.md`. Ranks
candidate fixes, states which were implemented and why, and which were
deliberately deferred.

## Ranking (before implementation)

| Candidate fix | Known Ls prevented | Expected future recall | Complexity | Operational risk | False-positive risk | Verdict |
|---|---|---|---|---|---|---|
| **A. Emit `NEW_REFERENCE` on genuine first-ever catalogue sighting** (`_record_product_transition`'s `is_new_watch` branch) | Cases 9, 10 directly | High — protects every future Citizen/Seiko/Timex/Citizen-DE catalogue discovery going forward, not just these two | Low — one function, reuses existing scoring/eligibility/Discord plumbing already proven for `NEW_REGION` | Low — identical safety construction to the existing `NEW_REGION`/`force_baseline`/epoch-baseline guards, which already sit *above* this branch in the same function | Low — same profile as the already-trusted news-pipeline `NEW_REFERENCE`, and provably inert for all ~2000+ existing production watches (only fires for genuinely new `Watch` rows going forward) | **Implement** |
| **B. Delta-prioritize catalogue pagination past the item cap** (`timex_products`/`citizen_products`) | Case 10 directly; latently protects Citizen as its catalogue grows past 300 | High for Timex (81% of catalogue was unreachable on any routine run) | Medium — two collector files + registry wiring, but reuses the exact pattern already proven safe in `citizen_de_products` | Low — falls back to identical positional-slice behavior with no `known_product_urls`, i.e. zero behavior change for a fresh baseline run | None — strictly increases discovery of genuinely new items; never invents one | **Implement** |
| C. Build a Casio UK/regional product+price collector | Cases 5, 8 | High, but blocked (Cloudflare 403 — see M in the taxonomy) | N/A — blocked regardless of collector complexity | — | — | **Do not attempt** (would require bypassing Cloudflare, explicitly prohibited) |
| D. Build a Citizen UK product collector | Cases 2 (UK leg), 11 | High | N/A — blocked (Cloudflare 403) | — | — | **Do not attempt**, same reason as C |
| E. Build a Seiko JP-store (`store.seikowatches.com`) product/preorder collector | Case 12 | Medium-high (one brand, one region, but a real, evidence-corrected miss) | Medium — new collector + reference-normalization decision + onboarding baseline + live validation, i.e. a full new-source project, not a small patch | Low technically (same Shopify-JSON pattern as three existing collectors), but a full onboarding is its own multi-hour unit of work | Low, if built following the existing onboarding discipline | **Deferred — document only.** Fixes exactly one case; A+B fix two cases with less code and lower risk, and finishing a brand-new source onboarding safely (baseline run, live validation, systemd/Task Scheduler registration) inside the same sprint as everything else risked exactly the "half-built migration" the brief explicitly forbids. |
| F. Reddit/community early-warning layer | None of this sprint's 12 cases were shown to be Reddit-shaped | Unknown, unproven for this project's specific brands | High (new source type, new lead category, ToS/rate-limit considerations) | — | Real risk of noise if not scoped narrowly | **Research only**, per explicit sprint instruction — no code written |
| G. Redeploy Hetzner to current HEAD | Would bring the cloud instance from 0 real capabilities (pre-Sprint-5, `003_release_leads` schema) up to everything through this sprint | High in principle | High — 4 new migrations, mandatory per-source force-baseline sequence for every collector added since Sprint 5 (documented multi-step runbook in `SPECIALIST_SOURCE_EXPANSION_SPRINT14.md`/`scripts/systemd/README.md`), systemd unit installation, careful live validation before enabling Discord | High if rushed the night before the operator travels | High if any baseline step is skipped (exactly the "moldy bread"/flood risk class) | **Do not attempt tonight — document as the top follow-up.** This is a full deployment project on its own, not a patch; "do not leave Watch Clank halfway through an architectural migration" applies directly. |

## What was implemented

### Fix A — catalogue-discovery event emission

`app/services/pipeline.py::PipelineService._record_product_transition`: the
`is_new_watch` branch no longer unconditionally returns
`{"event_type": None, "reason": "baseline_new_watch"}`. It still does exactly
that when `force_baseline=True` or an epoch baseline is active (both guards
are unchanged and still run *first*) — but outside of baseline, a genuinely
first-ever product-catalogue sighting of a reference now scores and persists
a real `NEW_REFERENCE` `Event`, reusing `_persist_product_event` (the same
helper the `NEW_REGION` branch already used), so Discord eligibility,
editorial-eligibility gating, and Recent Intelligence rendering are all
inherited for free rather than reimplemented.

**Why this is safe for the existing production data:** `is_new_watch` is
only `True` at the moment a `Watch` row is first created. Every watch
already in a production database — Windows, Hetzner, or otherwise — will
never re-enter this branch; the fix is purely forward-looking.

### Fix B — catalogue pagination delta-prioritization

`app/collectors/timex_products.py` and `app/collectors/citizen_products.py`
`run()` now accept an optional `known_product_urls: set[str] | None`. When
supplied, discovered items are partitioned into "not previously observed"
and "previously observed," with not-previously-observed items placed first
before the existing `max_items` slice is applied — so a genuinely new SKU is
never starved by whatever arbitrary position it happens to sort into.
`app/services/pipeline.py::run_product_observation_pipeline`'s
`_PRODUCT_REGISTRY` now sets `"known_urls_from_observations": True` for both
`citizen` and `timex` (Seiko was left unchanged — its real catalogue,
222–276 items, is already fully under the 300-item cap, so the fix would be
inert there; not touching it keeps the change minimal). The
`known_urls_from_observations` → `known_product_urls` wiring in
`run_product_observation_pipeline` already existed generically (built for
`citizen_de_products`); this reuses it rather than duplicating it.

### Validation performed

- Full test suite: **221 passed** (was 215 before this sprint; 6 net new
  tests, 7 pre-existing tests updated to reflect the corrected behavior —
  see the autopsy's test list). `ruff check .`: all checks passed.
- Unit-level: new collector-level tests prove delta-prioritization surfaces
  a would-be-excluded item under a tight cap, and that omitting
  `known_product_urls` reproduces the exact old positional-slice behavior
  (zero behavior change for a fresh baseline run).
- **Live network validation** against an isolated throwaway database (never
  the Mac dev DB, never any production DB): `--experimental-product timex
  --force-baseline` → 1445 new watches, **0 events** (baseline still
  silent). Immediate repeat run, no `--force-baseline` → **0 new watches, 0
  events** (real steady-state stability, no flood, no churn). Repeated for
  Citizen: 465 new watches on baseline, 0/0 on repeat. `PRAGMA
  integrity_check` = `ok` both times.
- Confirmed via template inspection (`app/templates/intelligence.html`) that
  Recent Intelligence renders any `Event` generically (`(e.extra or
  {}).get(...)`, never a bare key lookup), so product-sourced `NEW_REFERENCE`
  events — which carry a different `extra` shape than news-sourced ones (no
  `announcement_url`/`lead_id`) — render without any template change needed.

## What was deliberately not implemented

- **No new regional collector** (Casio UK/global, Citizen UK, Seiko JP
  store) — see ranking table above. The Seiko JP store finding in
  particular is flagged as the **highest-value remaining gap** precisely
  because it's now evidence-backed as buildable, not because it was
  attempted and abandoned.
- **No Hetzner redeploy.** The cloud instance is stable and healthy on its
  current stale image (`fcb5e918`, `003_release_leads` schema, `casio_multi`
  running SUCCESS every ~90 minutes) — see the autopsy's Hetzner section.
  Bringing it to current HEAD requires four migrations and a careful,
  documented, multi-source force-baseline sequence that is its own project.
  Left untouched, per Phase 15's explicit sequencing (GitHub correctness >
  existing Windows functionality > existing Hetzner functionality >
  documented follow-up) and Phase 18's "a known missing source is
  preferable to a broken unattended system during travel."
- **No Reddit/community layer** — research only, as instructed.
- **No Discord authority change** — Hetzner does not meet any of the four
  explicit preconditions (verified running current collectors, correctly
  baselined, fresh-event generation verified, dedup verified) for becoming
  authoritative; Windows' status could not be verified this session (away
  until 2026-08-18, per HANDOFF.md Sprint 12).

## Top three follow-ups, ranked

1. **Redeploy Hetzner to current HEAD** (migrations 004→007, force-baseline
   every collector added since Sprint 5 per the existing documented runbook,
   install the existing-but-unused systemd timer templates, validate two
   clean repeat runs before considering Discord authority). This alone would
   retroactively give Hetzner every other fix in this document.
2. **Build the Seiko JP retail-store collector** (`store.seikowatches.com`),
   same Shopify-JSON pattern as three existing collectors, now evidence-
   backed as not geo-blocked from the real cloud vantage point.
3. **Investigate a Citizen/Casio UK access path that doesn't bypass
   Cloudflare** (e.g. a public retailer mirror, or accepting that this
   market stays journalist-sourced) — genuinely uncertain whether one exists;
   worth a short research pass before assuming it's permanently unreachable.
