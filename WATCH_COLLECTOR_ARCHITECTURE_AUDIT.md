# WATCH COLLECTOR ARCHITECTURE AUDIT
Watch Clank — collector-family archaeology, 2026-08-25
Canonical repo at audit time: `anil-ganti-nbc/watch-clank` @ ec5753d

## Purpose

Phase A of the expansion programme. Every existing collector is mapped to a
generic acquisition pattern so that the new reusable families absorb the
bespoke implementations' *proven* behaviour instead of reinventing it.
Nothing here is a redesign proposal; working collectors stay untouched.

## Collector inventory and pattern classification

| Collector | Brand/Source | Mechanism | Generic family | Structured data | Availability | Price | Notes |
|---|---|---|---|---|---|---|---|
| casio_japan | Casio JP | HTML catalogue crawl (Akamai-blocked in field) | Catalogue Crawler | none | none | none | BLOCKED→BACKED_OFF; exit-0 by design; superseded by sitemap lanes |
| casio_intl_news | Casio intl | News listing + detail pages | Newsroom Collector | partial (HTML) | n/a | n/a | ReleaseLead path; freshness gate `_ISO_TIMESTAMP_NEWS_SOURCES` |
| casio_uk_sitemap | Casio UK | Sitemap delta (`product.<REF>/`) | **Sitemap Delta** ✅ | URL+lastmod only | None (honest) | None | The proven family template; NEW_REGION-capable |
| casio_jp_sitemap | Casio JP | Same | **Sitemap Delta** ✅ | same | same | same | 360-min cadence (earliest official surface) |
| casio_europe_sitemap | Casio EU | Same | **Sitemap Delta** ✅ | same | same | same | 720-min cadence |
| citizen_products | Citizen US | Search API pages → detail JSON-LD-ish | Structured Product (partial) | yes per-item | yes | yes | known-URL new-first slicing; fetch_availability_for_new |
| citizen_news | Citizen US | News listing | Newsroom Collector | partial | n/a | n/a | Layer A news path |
| seiko_products | Seiko US | Shopify products.json | Shopify Catalogue ✅ | full product JSON | variant flag | yes | slice-starvation repaired 2026-08-24; now known-URL aware |
| seiko_jp_products | Seiko JP | Shopify products.json | Shopify Catalogue ✅ | full product JSON | variant flag | yes | known_urls wired |
| seiko_jp_news | Seiko JP | RSS/news | Newsroom Collector | partial | n/a | n/a | 1 lead total in current DB |
| timex_products | Timex US | Shopify products.json | Shopify Catalogue ✅ | full product JSON | variant flag | yes | published_at capture → freshness/bulk-touch evidence |
| timex_news | Timex US | Shopify blog Atom feed | Newsroom (Atom) ✅ | entry metadata | n/a | n/a | strict ISO-timestamp policy |
| gcentral_rss / casioblog_rss / plus9time_rss | Specialist | RSS/Atom | Specialist RSS | entry metadata | n/a | n/a | Layer B → SpecialistLead, never Events |
| monochrome_rss / fratello_rss / watchtime_rss / deployant_rss / gear_patrol_rss / great_gshock_world_atom | Specialist | RSS/Atom | Specialist RSS | entry metadata | n/a | n/a | ZERO_ITEMS streaks correctly WARNING post-repair |

✅ = already matches a reusable family cleanly.

## The four generic families that already exist implicitly

1. **Shopify Catalogue** (`products.json` pagination): seiko_products,
   seiko_jp_products, timex_products. Full structured records; identity =
   variant SKU. Three bespoke copies of the same loop.
2. **Sitemap Delta**: casio_uk/jp/europe. Reference-from-URL + lastmod;
   honest no-price/availability; NEW_REGION capable. Four bespoke copies
   (three Casio locales) of one shape.
3. **Newsroom/RSS**: timex_news Atom, specialist RSS set, news listing
   crawlers. Feed into ReleaseLead/SpecialistLead, never directly Events.
4. **Structured Product Page** (JSON-LD/hydration): citizen_products is the
   closest (search discovery + per-detail extraction). No generic form yet.

## What the expansion adds (Phase C conclusion)

- **SitemapDeltaCollector family** — extracted 2026-08-25
  (`app/collectors/sitemap_family.py`), first consumer `tissot_sitemap.py`.
  Configuration-only onboarding for SFCC/multi-locale SKU-in-URL stores.
- **ShopifyCatalogue family** — NOT yet extracted; three live implementations
  remain authoritative. Extraction deferred: zero live pressure, and Law
  "don't redesign working code" applies. Backlog item.
- **Newsroom family** — deferred; existing news paths satisfy current needs.
- **StructuredProduct (JSON-LD) family** — designed in the source matrix;
  implementation queued behind Tissot soak evidence (Tissot product pages
  carry schema.org Product with sku/mpn/price/availability — verified live).

## Constraints confirmed intact

- Collectors never write DB (all return CollectorRunResult).
- FIRST_SEEN ≠ NEW_REFERENCE inversion untouched.
- Baseline auto-application on first run unchanged (`_auto_baseline_for_first_run`).
- Health semantics uniform via http_util component_status helpers.

## Friction recorded (Unified Architecture dogfood)

- Registry population is lazy inside `run_product_observation_pipeline`
  (700-line method); adding a brand requires editing an inline import block.
  Works, but "registry-driven onboarding" would be better served by a module-level
  declarative table. Recorded as friction, not changed mid-programme.
