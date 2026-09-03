# Regional signal-path research — Citizen HK, Citizen JP, Seiko AU/APAC

**Date:** 2026-09-03
**Vantage:** Hetzner (`204.168.142.1`), the same cloud vantage used for the
2026-08-14 UK research. Windows is network-blocked for several of these.
**Outcome: NO COLLECTOR BUILT for any of the three lanes.** Every candidate
surface was probed and none carries per-reference product URLs.

This mirrors `UK_SIGNAL_PATH_RESEARCH.md`, which exists for the same reason:
documenting why a lane was *not* built is as operationally valuable as
building one, and prevents the same research being redone from scratch.

## Why these three were investigated

Operator authorised (2026-09-03) exactly these lanes from the Watch Clank L
archive's track C, each to be built delivery-silent in experimental soak:

| Lane | References the archive says were missed |
| --- | --- |
| Citizen HK | `JY8144-50E`, `AV0104-06W`, `NH8410-59E`, `NH8410-59X`, `NH8414-58B`, `NH8410-08B` |
| Citizen JP | `ES9503-01E`, `EG7048-56E`, `EG7049-53Y` |
| Seiko AU/APAC | `HBB003K1`, `HBB004K1` |

## Permission check (done first, before any content fetch)

| Domain | robots.txt | Verdict |
| --- | --- | --- |
| `citizen.com.hk` | 200 — `User-agent: *`, `Disallow: /stats/`, **`Crawl-delay: 10`** | Crawling permitted outside `/stats/`; a 10s crawl delay would be mandatory |
| `citizen.jp` | **403 on robots.txt itself** (returns an HTML error page) | **Refuses automated access. Not bypassed, not retried with a spoofed agent** — same discipline as the Akamai-blocked Casio JP product pages |
| `citizenwatch-global.com` | 200 — `User-Agent: *`, declares a sitemap index | Permitted |
| `www.seikowatches.com` | 200 — `User-agent: *`, no Disallow rules at all | Permitted |
| `seiko.com.au` | 200, empty | Permitted |

No ClaudeBot/GPTBot/CCBot/anthropic-specific rules were found on any of them
(the Citizen UK robots.txt ClaudeBot ban noted in `scripts/systemd/README.md`
does not appear on these domains).

## Probe results

### Citizen HK — `citizen.com.hk` — NOT VIABLE
- `/sitemap.xml` → 200, 8,343 bytes, **84 `<loc>` entries**.
- Every entry observed is `http://www.citizen.com.hk/` — the homepage,
  repeated with different `<lastmod>` values spanning 2016–2023. It is a
  legacy/degenerate sitemap, not a product index.
- `JY8144` does not appear anywhere in it.
- `/sitemap_index.xml` → 404.
- **Blocker:** there is no machine-readable product surface to collect. A
  lane here would require discovering and parsing JS-rendered listing pages,
  which is a different (and much heavier) collector class than the
  sitemap-delta family, under a mandatory 10s crawl delay.

### Citizen JP — `citizen.jp` — BLOCKED, and NOT VIABLE via the global site
- `citizen.jp/robots.txt` → **403**. A site that will not serve robots.txt
  to us is refusing automated access; no bypass was attempted.
- Fallback checked: `citizenwatch-global.com` sitemap index → 200. Its 16
  children are **Pages, FAQ, News, Notices, Manuals and watch-movement
  articles only** — there is no product/watch catalogue sitemap.
- `ES9503-01E` etc. therefore have no reachable official index.

### Seiko AU/APAC — NOT VIABLE (closest of the three)
- `seiko.com.au/sitemap.xml` → 404. `seikoboutique.com.au` is **not**
  Shopify (`/products.json` → 404, IIS error page; `/search` → 404), so the
  existing `shopify_family` pattern does not apply.
- `www.seikowatches.com/sitemap.xml` → **200, real XML sitemap index with
  107 locale children**, including `au-enSeikoSitemap1.xml`. Promising.
- That AU child → 200, 324 KB, **1,771 URLs**. But every `products` URL is a
  category or marketing page:
  `/au-en/products`, `/au-en/products/kingseiko`,
  `/au-en/products/kingseiko/special/timeless_style/history`, …
- **`HBB003K1` and `HBB004K1` do not appear in it.** There are no
  per-reference product pages in the AU sitemap.
- **Blocker:** the sitemap is a content sitemap, not a catalogue. A
  NEW_REFERENCE-capable lane needs per-reference URLs; these do not exist
  at this surface.

## Conclusion

All three authorised lanes fail the same test: **the official, permitted,
machine-readable surfaces do not contain the missed references.** Building a
collector anyway would mean either guessing at an undiscovered endpoint or
scraping JS-rendered pages — and for Citizen JP, bypassing an explicit 403.
Per the archive's own instruction, a single regional miss is not automatic
authorisation for a new collector, so none was built.

Nothing was changed: no collector added, no entry in
`health.KNOWN_COLLECTORS` or `collector_registry`, no systemd unit, and no
change to `delivery_gate.EXPERIMENTAL_MATURITY_COLLECTORS`.

## What would unblock each lane

- **Citizen HK** — identify the real product-listing endpoint (the search-hit
  listing pattern `citizen_products.py` already uses for citizenwatch.com may
  have an HK equivalent). Any lane must honour `Crawl-delay: 10`, which
  materially limits pagination breadth per run.
- **Citizen JP** — needs a surface that does not 403. Worth checking whether
  a regional storefront (rather than the corporate site) exists and is
  permitted. Do not attempt `citizen.jp` again without new evidence that
  access terms changed.
- **Seiko AU/APAC** — the most likely win. Seiko's global site is permitted
  and well structured; the question is whether a per-reference AU catalogue
  exists at another path (the JP market is served by
  `store.seikowatches.com`, which `seiko_jp_products` already collects, so an
  AU storefront equivalent is plausible). If found, `SitemapDeltaConfig`
  makes it close to configuration-only.

Note for whoever picks this up: `SitemapDeltaCollector` fetches ONE sitemap
URL and does not follow a sitemap *index*. Seiko's global index would need
either index-following added to the family, or the specific locale child URL
pinned in config.
