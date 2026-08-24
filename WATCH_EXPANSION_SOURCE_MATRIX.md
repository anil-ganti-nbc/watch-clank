# WATCH EXPANSION SOURCE MATRIX — Wave 1
Live reconnaissance 2026-08-25 from the Windows field-test network vantage.
Every status below was probed with real HTTP requests; nothing assumed.

## Wave 1 verdicts

| Brand | Mechanism | Identity quality | Baseline risk | Bot friction | Editorial yield outlook | Verdict |
|---|---|---|---|---|---|---|
| **Tissot** | SFCC multi-locale sitemap (SKU-in-URL) + JSON-LD on pages | Excellent — T-number in URL, sku==mpn | Contained: ~648 SKUs, auto-baseline + FIRST_SEEN inversion | None on sitemaps | High: PRX/PRC/Le Locle are exactly NBC traffic territory | **GO (implemented)** |
| Hamilton | Custom commerce platform; edge intermittently resets connections from this vantage (inconsistent 200/timeout) | Unknown until stable access proven | Unknown | Edge-level flakiness observed | High (same Swatch-group buyer band) | **EXPERIMENT** — needs Hetzner-vantage probe before build |
| Longines | Site unreachable from this network (connection reset) | Unknown | Unknown | Hard network block observed | High | **DEFER** — re-probe from Hetzner; do not scrape blindly |
| Bulova | SFCC (`on/demandware.store`); robots.txt 200 OK; sitemap returns HTML error page → sitemap list differs; needs SFCC search-API recon | TBD | TBD | Low so far | Medium-high | **EXPERIMENT** — SFCC service-url discovery next |
| Swatch | Edge "Access Denied" on several paths from this vantage | Unknown | Unknown | Akamai-style denial observed | Medium (fashion-quartz; lower NBC watch fit) | **DEFER** |
| Orient / Orient Star | JP site WordPress landing, products.json 404; US retailer (orientwatchusa.com) Cloudflare-challenged | Unknown | Unknown | Cloudflare JS challenge confirmed | Medium (movement story = good hook) | **EXPERIMENT** — Seiko-group EPSON official newsroom is the better first surface |

## Evidence for the Tissot GO decision (live, 2026-08-25)

- `https://www.tissotwatches.com/sitemap.xml` → per-locale index
  (en-us/en-en/en-au/es-mx/fr-ch/...), fresh lastmod (2026-08-24).
- `en-us/sitemap_0.xml` → 2,467 URLs, **648 distinct SKUs**, URL shape
  `/en-us/T41118316.html` (reference = URL slug).
- Product page JSON-LD verified: `"sku": "T41118316"`, `mpn` equal,
  `priceCurrency: USD`, `price`, schema.org availability. (Pages themselves
  NOT fetched by the collector — same honest sitemap-only limitation as
  casio_uk_sitemap; JSON-LD lane is a separate future family.)
- Cross-locale probe (en-us vs fr-ca): identical SKU sets today → regional
  deltas will be meaningful when they appear, not noise.
- Live collector run: component_status SUCCESS, candidate_count 648.

## Fallback paths per brand

| Brand | Primary | Fallback |
|---|---|---|
| Tissot | en-us product sitemap | second locale sitemap as NEW_REGION evidence; JSON-LD StructuredProduct lane later |
| Hamilton | sitemap (once vantage works) | SFCC search suggestions endpoint used by site UI |
| Longines | — from here | Hetzner probe; official newsroom RSS if published |
| Bulova | SFCC sitemap convention (`/sitemap_index.xml` per-locale) | demandware search API `Product-HitTile` endpoints |
| Swatch | — from here | Hetzner probe; press/newsroom feed |
| Orient | EPSON/Orient official newsroom pages | authorized-retailer feeds (tier-3, SpecialistLead only) |

## Wave-2 quick scan (Phase G preview)

Christopher Ward (Shopify confirmed publicly) and Victorinox are likely
GO-grade cheap wins via the existing Shopify Catalogue family once extracted;
Grand Seiko/TAG need vantage checks; Nomos has clean Shopify storefront
(history: shop.nomos-glashuette.com). Rado/Certina/Mido share Swatch Group
edge infrastructure — likely same block pattern as Swatch from this network.

## Anti-goal guard

No ultra-luxury catalogue crawling is proposed anywhere above. Price bands
enter later as triage metadata (Phase J), never as ingestion filters.
