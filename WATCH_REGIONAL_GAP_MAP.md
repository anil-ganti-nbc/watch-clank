# WATCH REGIONAL GAP MAP — Big Four incremental-information audit
Phase F, 2026-08-25. Principle: priority goes to *incremental information*,
never duplicate crawling. Live probes from this network where reachable.

## Casio / G-Shock

| Region/Surface | Status today | Incremental value | Priority |
|---|---|---|---|
| Japan sitemap | COVERED (casio_jp_sitemap, 6h cadence) | — | — |
| UK sitemap | COVERED (casio_uk_sitemap) | — | — |
| Europe sitemap | COVERED (casio_europe_sitemap) | — | — |
| US (casio.com/us) | **MISSING** — same SFCC/sitemap pattern as UK | US-specific colourways + earlier NA availability; G-Shock US exclusives | **P1** |
| gshock.casio.com regional portals | 403 from this vantage | Dedicated G-Shock launch pages often precede catalogue rows | P2 (needs Hetzner probe) |

## Seiko

| Region/Surface | Status today | Incremental value | Priority |
|---|---|---|---|
| Seiko US products.json | COVERED (seiko_products) | — | — |
| Seiko JP products.json | COVERED (seiko_jp_products) | JP-first references (L-WATCH-004 class) | — |
| Grand Seiko (separate brand site) | **MISSING entirely** | Distinct premium line; own release cadence; strong search traffic | **P1** (Wave-2; price band mostly >$2.5k → metadata-prioritised, not suppressed) |
| Seiko EU/UK storefronts | Unprobed this pass | Presbyope regional deltas vs US/JP pair | P3 |
| seiko_watch_co.jp newsroom | Not covered by news lane? (seiko_jp_news exists, 1 lead total — verify feed health) | JP announcement surface | P2 |

## Citizen

| Region/Surface | Status today | Incremental value | Priority |
|---|---|---|---|
| Citizen US products+news | COVERED | — | — |
| Citizen Germany | RETIRED deliberately 2026-08-17 (noise) | Do not re-add without noise fix | Blocked-by-decision |
| Citizen UK | robots.txt explicitly disallows ClaudeBot-class crawlers (UK_SIGNAL_PATH_RESEARCH) | Respectful non-crawl stands | REJECT for now |
| Citizen JP (citizen.jp) | **MISSING** | The actual home-market announcements; Series 8/Caliber 0200 stories hit JP first (L-WATCH-005's real origin) | **P1** |

## Timex

| Region/Surface | Status today | Incremental value | Priority |
|---|---|---|---|
| Timex US products+news | COVERED | — | — |
| timex.co.uk | **CONFIRMED Shopify products.json live this pass** (page-1: 247 watches incl. UK SKUs TW4B34400UK…) | UK-exclusive variants (`…UK` suffix); NEW_REGION evidence on existing US SKUs; the *regional-presence* collector pattern proven on a second Shopify store | **P1 — cheapest incremental win in the whole map** |
| Timex DE/other locales | timex.de serves HTML shell at products.json (redirect chain) | Low | P3 |

## Cross-cutting note

Every regional addition must enter through `_auto_baseline_for_first_run`
(silent first run) so a new region's historical backlog can never replay the
Timex-avalanche failure. Regional appearance is observation + at most
NEW_REGION — never novelty (WATCH_EVENT_SEMANTICS.md decision table).
