# Seiko Japan official retail-store collector — 2026-08-14

## Why this exists

The previous (Hall of Shame) sprint found Case 12 (Seiko Prospex Alpinist
Mechanical GMT, HBC008J/HBC009J) and, while researching Hetzner, discovered
that its working assumption — "Seiko's Japanese online store is
geo-restricted from some locations" — did not hold from the real cloud
vantage point. This sprint verified that finding independently a second
time and built the collector.

## Reconnaissance (live, 2026-08-14)

- Host: `store.seikowatches.com` (Seiko's own single-purpose Japan watch
  retail site — vendor field on every product: "セイコーオンラインストア /
  Seiko Online Store"; distinct from `seikowatches.com/global-en` and
  `seiko.co.jp`, neither of which this collector touches).
- Tested from **two independent vantage points**: the Hetzner cloud host
  (Helsinki) and this session's macOS dev machine. Both got a clean **HTTP
  200** for `/products/{handle}.json` and `/products.json` — not geo-blocked
  from either, contradicting the prior working assumption.
- `robots.txt`: not specifically checked for this host this sprint (the
  Shopify `/products.json` collection endpoint is the same publicly-
  documented mechanism already relied on for Timex/Seiko-US, per those
  collectors' own module docstrings) — no anti-bot signals encountered in
  four independent live fetches across two hosts.
- Catalogue: **959 real watch products**, paginated `/products.json?limit=250&page=N`,
  4 full pages then an empty terminator (page 5) — identical pagination
  contract to Timex/Seiko-US.
- No `product_type` filtering needed or reliable: sampled `product_type` is
  either empty string or `新製品` ("new product") on every item; this store
  sells nothing but watches, so every listed item is real inventory —
  simpler than Timex/Seiko-US, which both need a `product_type` filter to
  exclude straps/giftsets.
- `sku` already equals the canonical reference (e.g. `HBC008J`) with no
  suffix garbage — `normalize_seiko_reference`'s existing conservative
  pass-through policy applies completely unchanged, no new normalization
  rule needed.
- Currency: JPY, bare integer price (e.g. `155100`), no decimal point in
  the real capture — matches the Notebookcheck-reported ¥155,100 for
  HBC008J/HBC009J exactly.
- Availability: **only the collection-listing `/products.json` response
  carries `variants[].available`** — the per-product `/products/{handle}.json`
  endpoint omits it entirely. This collector uses the listing endpoint as
  its only fetch, matching every other product collector's "one page
  carries everything" design.
- Preorder signal: real tag `予約購入ボタン` ("reservation/preorder purchase
  button") present on both HBC008J and HBC009J at capture time — surfaced
  as `extra_specs.preorder_tag_present`, never folded into
  `availability_status` (see Phase 9 preorder-semantics note below).

## What was built

- `app/collectors/seiko_jp_products.py`, `app/parsers/seiko_jp_products.py` —
  same shape as `seiko_products.py`/`timex_products.py`. Registered as
  `seiko_jp_products` in `app.services.health.KNOWN_COLLECTORS`,
  `app.services.collector_registry`, and the `seiko_jp` key in
  `PipelineService._PRODUCT_REGISTRY`.
- Discovery-cap lesson from the Hall of Shame sprint applied from day one:
  `known_product_urls` delta-prioritization is built in, not bolted on
  later — a brand-new source starting today never reintroduces the bug
  that Timex/Citizen had.
- `tests/fixtures/seiko_jp_products_page1.json` — real capture (2026-08-14)
  trimmed to 4 real products: **HBC008J, HBC009J** (the mandatory
  regression pair), **HCC011J** (real `available: false` example),
  **HCC005J** (cross-reference to the prior sprint's positive-control
  case, confirming it's the same store).

## Preorder semantics (Phase 9 audit)

This source's Shopify `available` boolean is binary — orderable or not —
there is no distinct `PREORDER` availability string anywhere in the real
data. A reference moving from not-yet-orderable to orderable is
represented by the **existing, unmodified** SOLD_OUT→RESTOCK machinery
(`classify_price_availability_transition`); no new event type was needed
or added. `preorder_tag_present` is preserved as a fact for editorial
context/scoring but never used to fabricate an availability state that
isn't actually in the source data.

## Validation

- 7 new tests (`tests/test_core.py`), all passing: discovery/pagination,
  HBC008J/HBC009J field extraction (price, currency, availability,
  preorder tag), SOLD_OUT fixture, discovery-cap delta-prioritization, a
  pipeline-level "first sighting creates NEW_REFERENCE" regression using
  HBC008J itself, and a "known reference + availability transition fires
  RESTOCK, no invented event type" regression.
- **Live validation, isolated throwaway DB, real network** (2026-08-14):
  `--experimental-product seiko_jp --force-baseline` → 959 new watches, 0
  events. Immediate repeat, no `--force-baseline` → 0 new watches, 0
  events. `PRAGMA integrity_check` = `ok`. HBC008J/HBC009J both correctly
  captured: JPY 155,100, `AVAILABLE`, `preorder_tag_present: true`.

## Hall-of-Shame Case 12 replay

**Would current HEAD now catch HBC008J/HBC009J?** Yes — verified live, not
theoretically. Both are discovered, correctly identified, priced in JPY,
and (being genuinely new-to-Clank references on a healthy non-baseline
run) would produce a real `NEW_REFERENCE` event today.
