# Horology Sentinel — Runbook

**Shipped:** 2026-09-08/09 (branch `feature/horology-sentinel`)
**Status:** implemented + tested; NOT deployed, NOT scheduled anywhere. Every activation step below is an explicit operator action.

## What it is

A cheap, deterministic first-stage tripwire: every 15 minutes it polls first-party
discovery surfaces (Shopify `products.json`, product sitemaps — never full product
pages, never the enrichment pipeline), extracts watch identities, and sends an
immediate Discord **WATCH SIGHTING** for any identity the store has never seen.

A sighting claims exactly one thing: *a previously unseen watch identity or
relevant first-party URL has appeared in a monitored source.* It is NOT market
novelty, NOT article-worthiness, NOT an Event. `first_seen != market novelty`
(WATCH_EVENT_SEMANTICS.md) is preserved by construction: the Sentinel writes no
watches, observations, or events. Horology Clank's normal pipeline remains solely
responsible for enrichment, novelty checking, classification, and editorial
judgement; it can correlate sightings later via `sentinel_sightings.identity_key`
(the Sighting ID in each alert is the correlation handle).

## Architecture

```text
manufacturer/source (declarative registry: app/sentinel/config.py)
  -> adapter poll (app/sentinel/adapters.py: shopify_products | sitemap)
  -> identity (app/sentinel/identity.py: reference extraction + normalization
               + global cross-region dedup key)
  -> store (app/sentinel/store.py: sentinel_identities unique-constraint dedup,
            per-source arming latch in sentinel_source_state)
  -> unseen? -> sentinel_sightings row + Discord WATCH SIGHTING
               (app/sentinel/alerting.py: DiscordNotifier editorial lane +
                delivery_receipts, idempotent)
  -> Horology Clank enrichment continues unchanged, on its normal cadence
```

- One scheduler firing = one sweep = one `collector_runs` row
  (`collector_id="horology_sentinel"`), per-source stats in
  `summary_metadata.per_source` and `summary_metadata.totals`.
- Overlap protection: `RunLockService` kernel lock
  (`horology_sentinel.run.lock`, database-adjacent). An overlapping firing
  exits `SKIPPED_OVERLAP` (exit code 0).
- Per-source isolation: any adapter failure marks only that source FAILED;
  the sweep continues with the rest.
- Failure honesty: a failed/blocked source records its status truthfully in
  `sentinel_source_state.last_status` and `collector_runs`; a temporary HTTP
  failure never forgets identities (the store is only touched by successful
  polls) and never re-baselines an armed source.

## Sources enabled initially (all fast tier, 15-minute cadence unless noted)

| Source | Surface | Region | Adapter |
|---|---|---|---|
| `timex_us` | `https://www.timex.com/products.json` page 1 | US | shopify_products |
| `timex_uk` | `https://timex.co.uk/products.json` page 1 | UK | shopify_products |
| `seiko_us` | `https://seikousa.com/products.json` page 1 | US | shopify_products |
| `casio_uk_sitemap` | `casio.com/uk/sitemap.xml` | UK | sitemap (reuses casio_uk_sitemap collector's parser) |
| `casio_jp_sitemap` | `casio.com/jp/sitemap/watches.xml` (~17.8k URLs) | JP | sitemap, **60-minute cadence** |
| `citizen_eu_sitemap` | `citizenwatch.eu` product sitemap | EU | sitemap (generic) |

Deliberately absent: `citizenwatch.co.uk` (robots explicitly disallows
ClaudeBot + Cloudflare 403 — must never be added), Casio US sitemap (not
published yet — see WATCH_REGIONAL_GAP_MAP.md), Citizen US (HTML search API,
not a cheap machine-readable feed — declarative follow-up).

Ordering evidence (2026-09-08, Hetzner vantage): the plain unscoped
`/products.json` endpoint returns newest-first on all three stores; the
collection-scoped variant is NOT reliably ordered and must not be swapped in.
Page 1 only (250 products) is the documented tripwire limitation: an item
pushed past position 250 by a mass catalogue import would be caught by the
normal Horology Clank pipeline instead.

## Identity / deduplication logic (exact)

1. Reference present (Shopify variant SKU, or sitemap URL reference):
   - trim whitespace;
   - **regional-suffix collapse**: `TW5M74100UK -> TW5M74100`. Only for
     unhyphenated alphanumeric SKUs, only for the evidence-backed allowlist
     `REGIONAL_SUFFIX_ALLOWLIST = {"UK"}`, only when the character before the
     suffix is a digit. Colour suffixes (`TW6A01000VQ`) and hyphenated systems
     (`AW1911-53A`) are never touched. This is the Casio JDM-allowlist
     principle: suffix-stripping needs explicit evidence.
   - normalize through the **shared** `app/normalization/references.py`
     normalizer for the manufacturer (Casio JDM allowlist; Citizen/Seiko/Timex
     conservative pass-throughs). The Sentinel invents no brand rules.
   - identity key: `<manufacturer>:<reference_canonical>`, lowercased.
2. No reference: canonicalize the first-party URL (lowercase scheme/host,
   drop query/fragment, strip trailing slash), SHA-256, take 24 hex chars.
   Key: `<manufacturer>:url:<digest>`. Conservative by design: only the exact
   same product page dedups against itself.
3. Dedup is **global across regions**: the same reference via Timex US, Timex
   UK, or any other lane is ONE identity. First sighting wins; `first_source`,
   `first_region`, and the exact `reference_raw` first spelling are preserved.

## Baseline behaviour

- A source that has never completed a successful poll is **disarmed**. Its
  first successful poll is a silent per-source baseline: every identity is
  admitted (`sentinel_identities.admitted_via='BASELINE'`) with zero Discord
  pings, and the source becomes armed (`sentinel_source_state.armed_at` set).
- `python -m scripts.run_pipeline --sentinel-baseline` forces the same
  silence for one sweep (`collector_runs.is_baseline=1`) — used after a
  deliberate reset or to pre-arm a newly added source quietly.
- Result: on first deployment, the existing catalogue never floods Discord.
  The first sweep baselines; from the second sweep onward, unseen identities
  alert.
- Baseline vs runtime state is mechanically distinguishable and tested:
  `collector_runs.is_baseline`, `admitted_via`, `per_source[].baseline_mode`,
  and the arming latch are all asserted in `tests/test_sentinel.py`.

## Alert contract

```text
**WATCH SIGHTING**
Brand: Timex
Model: TW5M74100
Title: TIMEX IRONMAN Adrenaline 46.5mm Resin Strap Watch
Source: Timex US (US)
Detected: 2026-09-08T15:04:05+00:00
URL: https://www.timex.com/products/...
Sighting ID: 42

Status: `UNENRICHED / FIRST-SEEN ONLY`
```

- One message per sighting, sent immediately (never batched behind
  enrichment). `Title` is omitted (never invented) for sitemap sources.
- Delivery rides the fleet's existing editorial webhook + authority boundary
  (`DiscordNotifier`, `editorial_notifications_enabled`, `watch_clank_instance`).
  It is deliberately NOT governed by the soak-contract experimental silence:
  that gate applies to pipeline Events, and immediate sighting alerts are the
  Sentinel's entire purpose. Sightings are their own delivery intent
  (`delivery_receipts`: `entity_type='SENTINEL_SIGHTING'`,
  `purpose='sentinel_sighting'`) with an idempotency key — a retried sweep can
  never double-ping an accepted sighting, and a failed ping is durably
  recorded (`alert_state='FAILED'` + receipt), never silently lost.
- `alert_state` vocabulary: `SENT` / `FAILED` / `DISABLED` (editorial lane
  unconfigured — sighting still persisted) / `CAPPED`.
- Flood breaker: beyond `SENTINEL_MAX_ALERTS_PER_SWEEP` (default 20) alerts in
  one sweep, further sightings are recorded as `CAPPED` and a single health
  alert raises the anomaly. With per-source arming this should be unreachable;
  if it ever fires, treat it as a catalogue-replay incident.

## Noise controls (what can never alert)

Already-known references; duplicate regional listings (global dedup +
`UK`-suffix collapse); repeated feed entries; pagination reshuffles; price
changes; inventory/restock changes; product-page edits; image changes;
availability changes; known watches appearing in new collection pages. Price/
inventory/content fields are not part of the identity and are not compared.
Only a previously unseen identity produces a WATCH SIGHTING.

## Scheduling (operator activation, when authorized)

The Sentinel is registered in `health.py KNOWN_COLLECTORS`,
`EXPECTED_CADENCE_MINUTES` (15), and `collector_registry` (single source of
truth), so unit generation is automatic:

```bash
python -m scripts.systemd.docker.render_units --out-dir /tmp/units
# copy watch-clank-horology-sentinel.{service,timer} to ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now watch-clank-horology-sentinel.timer
```

The rendered invocation is
`python -m scripts.run_pipeline --sentinel --qualification-provenance SCHEDULED`
(override in `render_units.py`, enforced by
`test_production_wiring.py::test_every_non_casio_multi_collector_timer_carries_scheduled_provenance`).
Standard deployment discipline applies before any of this: reviewed SHA on
Hetzner, DB backup + integrity check, `python -m scripts.migrate` (migration
017 is purely additive), verify first sweep is a silent baseline, verify
source arming + zero-event behaviour on sweep 2, then leave it alone.

Manual/operational commands:

```bash
python -m scripts.run_pipeline --sentinel                          # one sweep
python -m scripts.run_pipeline --sentinel --sentinel-baseline      # silent re-baseline
python -m scripts.run_pipeline --sentinel --sentinel-source timex_uk   # one source, cadence bypassed
```

## Configuration

- Sources: `app/sentinel/config.py` (`SENTINEL_SOURCES`). Adding a brand is one
  frozen dataclass entry (adapter choice + endpoint + cadence + region). No
  core code changes.
- Settings (`app/core/config.py`): `SENTINEL_ENABLED` (kill switch, default
  true), `SENTINEL_MAX_ALERTS_PER_SWEEP` (20), `SENTINEL_LAST_SEEN_REFRESH_HOURS`
  (24 — a known identity's `last_seen_at` is refreshed at most this often, so
  the 15-minute poll never becomes write churn).

## Persistence

Migration `017_sentinel_identities` (purely additive, follows 016):

- `sentinel_identities` — the global known-identity store (unique
  `identity_key`; `admitted_via`; first/last seen provenance).
- `sentinel_sightings` — one row per live first-seen detection; row id is the
  alert's Sighting ID; carries `alert_state` + optional run correlation.
- `sentinel_source_state` — per-source arming latch + last poll status.

## Tests

`tests/test_sentinel.py` (18 tests, fully offline, mapping to the operator's
13 required cases + format/cadence extras): unseen→one sighting; repeat→no
second sighting; cross-region dedup (`TW5M74100UK`); two distinct refs→two
alerts; price/title change→no alert; silent baseline; first post-baseline
alert; per-source failure isolation; representative Timex/Casio/Seiko/Citizen
identity parsing (incl. the shared Casio JDM allowlist); URL fallback;
overlap lock + racing-admission safety; delivery receipt idempotency +
DISABLED/FAILED durability; restart survival. Full suite: 578 passed,
2 skipped, Ruff clean on all touched files.

## Limitations / follow-ups

- Shopify lanes read page 1 (250 products) of the newest-first feed only; a
  mass import pushing items past position 250 is the normal pipeline's job.
- The newest-first ordering is live evidence, not a contract — if a store
  changes feed order, the tripwire degrades silently (the enrichment pipeline
  still covers it). A future per-source "zero new identities in N days" health
  signal could surface that.
- Citizen US (HTML search API) and press/blog RSS are not wired; both are
  declarative adapter additions when wanted.
- The Sentinel does not write into the pipeline tables; correlating a sighting
  with later enrichment is by `identity_key` / Sighting ID (a future
  enrichment-side join, deliberately out of scope here).
