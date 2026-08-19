# 2026-08-19 emergency hot-fix and regression audit

Triggered by a batch of real, user-reported misses/false-positives
(Cavatina Luxe, TW6A E-Line, Snoopy Umbrella, CasioBlog EQB-1300D, Atelier
strap, TW2Y38700 Pan Am RESTOCK, TW4B20700 REACTIVATED). Investigated with
live probes against the real Timex feed/catalogue and a read-only copy of
the real Hetzner production DB — not guesses. Full incident-by-incident
detail is in the session transcript; this file records what must survive
as permanent policy.

## Permanent rule: `published_at` is not launch authority

**Shopify's `published_at` field (captured into `Watch.extra_specs` by
`timex_products`/`timex_news`) is evidence of catalogue publication/
activity — when the platform touched this listing — NOT the manufacturer's
original launch date.** It has already been observed, on the real Hetzner
catalogue, to be:

- Genuinely reliable at small scale, seconds-apart, single-collection
  clusters (Cavatina Luxe: 5 SKUs within 6 seconds; TW6A E-Line: 3 SKUs
  within 3 seconds) — this is the shape `classify_baseline_product_freshness`
  was tuned against.
- Unreliable at larger scale: a live check found a 23-product cluster
  sharing one identical timestamp across totally unrelated collections
  (Waterbury Classic, Easy Reader, Weekender, Q Timex Marbella, ...) — a
  routine catalogue-sync/reindex batch, not a launch. A wider 14-day
  proximity check found 14-product clusters with the same shape.
- Touched by non-launch catalogue operations: `TW4B20700` carries a
  `REACTIVATED`/`Backorder Eligible` tag and a `published_at` of
  literally today, despite belonging to an older Expedition Field
  generation.

**Consequence for every future feature**: never treat a fresh
`published_at` alone as proof of a new launch. Use it only:
1. As one signal among several (family-size clustering, an independent
   first-party blog/news article, independent third-party coverage).
2. Behind an explicit, human-reviewed gate (see
   `PipelineService.find_baseline_catchup_candidates`/
   `create_baseline_catchup_events` — read-only reporting + explicit
   opt-in `watch_ids`, never automatic).

## What shipped this session

1. **Accessory false positive** (`app/services/pipeline.py`,
   `_looks_like_accessory_only`): the news/blog pipeline
   (`process_news_announcement`) had no product-type gate at all, unlike
   the catalogue collector. A real post — "Timex Atelier NBR Synthetic
   Rubber Strap ... Now Available Separately." — had already created a
   live, unreviewed, Discord-alerted `NEW_REFERENCE` Event (id 124) for a
   strap on Hetzner. Fixed with a narrow phrase match ("available
   separately", "sold separately", etc.), not a `"strap" in title` ban —
   legitimate titles like "... Leather Strap Watch" must keep working.

2. **Generalized official-news staleness gate** (`_stale_official_announcement`):
   was scoped to Timex only (`_ISO_TIMESTAMP_NEWS_SOURCES`). Generalized to
   any source with a confidently-parseable date (ISO or the confirmed
   Casio/Citizen/Seiko free-text month-name shapes) — a date that fails to
   parse changes nothing (zero regression risk to existing recall).

3. **`notify_correlation` freshness gap** (`app/services/specialist_leads.py`):
   the actual mechanism behind the CasioBlog EQB-1300D-5A/-2A incident.
   `notify_new_lead` already refused anything not `editorial_freshness ==
   "FRESH"`; `notify_correlation` (the follow-up alert sent once a lead
   correlates with a real official Watch) had no such check at all. Live
   DB proof: lead id 10 (published 2026-03-28) correctly never sent as an
   early warning, but correlated 2026-08-17 with `lead_time_days=142.12` —
   exactly matching the reported incident date — and would have sent a
   "FAMILY_MATCH — NOT EXACT" alert with no age check. Fixed.

4. **Bounded, human-gated baseline catch-up**
   (`find_baseline_catchup_candidates` / `create_baseline_catchup_events`):
   see the permanent rule above. Two functions, deliberately: the first is
   read-only and reports `nearby_published_at_count` (90s proximity
   clustering) as a diagnostic only, never an auto-filter; the second only
   ever acts on an explicit `watch_ids` list a human supplies. Default
   window tightened from an initially-considered 30 days to 14 days after
   live data showed 30 days pulls in bulk-sync noise.

5. **RESTOCK/SOLD_OUT queue priority** (`app/services/qc.py`,
   `_QUEUE_PRIORITY_TIER`): real incident — TW2Y38700 (Pan Am Waterbury
   Ace, 11 months old) restocked and sat in the default QC queue at equal
   priority to genuine `NEW_REFERENCE` discoveries (recency-only
   ordering). RESTOCK/SOLD_OUT are real, legitimately time-sensitive
   inventory news — not suppressed, still fully reachable via the
   event-type filter — but now sort below NEW_REFERENCE/NEW_REGION in the
   default queue regardless of recency.

6. **`REACTIVATED` catalogue-tag signal** (`_reactivation_signal`):
   `TW4B20700` is a real, correct `NEW_REFERENCE` by this system's own
   "first seen by Clank" semantics (event_type is honestly about
   discovery, not launch date — see the `/intelligence` banner). But a
   `REACTIVATED`/`Backorder Eligible` catalogue tag is now surfaced as an
   explicit reason string, so a reviewer doesn't have to click through to
   the listing to learn "this might not be a new design."

## Explicitly NOT done (by design)

- No automatic catch-up of any baseline-absorbed watch. Only the 9
  specifically investigated (Cavatina Luxe ×5, TW6A E-Line ×3, Snoopy
  Umbrella ×1) have a human-reviewed recommendation; the other 60+
  candidates in the 14-day window are preserved for later inspection, not
  acted on.
- `TW2Y38700`'s RESTOCK is not suppressed — restocks are real inventory
  news independent of original launch date. Only its queue priority
  changed.
