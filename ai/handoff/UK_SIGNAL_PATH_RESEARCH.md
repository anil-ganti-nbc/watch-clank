# Casio / Citizen UK legitimate signal-path research — 2026-08-14

## Mandate

The prior sprint confirmed Casio UK and Citizen UK both return Cloudflare
403 for their product pages. This sprint's brief: find a **legitimate**
alternative signal path — RSS, sitemaps, structured data, regional APIs,
authorized retailers, specialist coverage — without bypassing Cloudflare,
geo-restrictions, or any access control. If none exists, document BLOCKED
honestly rather than force a fragile workaround.

All checks below were run live, 2026-08-14, from the real Hetzner cloud
vantage point (the actual network any deployed collector would use) unless
stated otherwise.

## Casio UK — result: PARTIAL signal path found, built

| Candidate | Result |
|---|---|
| Product pages (`www.casio.com/uk/watches/.../product.<REF>/`) | HTTP 403, resistant to browser-like `User-Agent`/`Accept-Language` headers — a real Cloudflare bot-management block, not a naive UA check. **Not attempted to bypass.** |
| **`www.casio.com/uk/sitemap.xml`** | **HTTP 200.** Explicitly published in `www.casio.com/robots.txt` (`Sitemap: https://www.casio.com/uk/sitemap.xml`, alongside sitemaps for every other Casio region). 1822 real product URLs, each with a real `<lastmod>` timestamp. |
| `robots.txt` policy check | No ClaudeBot/GPTBot/AI-crawler-specific disallow anywhere in Casio's (very long) robots.txt — it disallows dozens of named SEO/scraping tools individually but explicitly allows Googlebot/AdsBot/Oncrawl/Screaming Frog/SiteAuditBot, and closes with a blanket `User-Agent: * / Allow: /`. Fetching the sitemap is both technically accessible and not against stated site policy. |
| Casio UK/global RSS | None found — `casio_intl_news` already covers Casio's corporate global press releases; no UK-specific RSS discovered. |
| Retailer/authorized-dealer surfaces | Not investigated this sprint (the sitemap finding was sufficient and higher-confidence — first-party beats third-party). |

**Both Hall-of-Shame Casio UK cases confirmed present in the sitemap with
real, dated evidence:**
- `GD-350S-1`: `<lastmod>2026-08-12T15:46:46.821Z</lastmod>` — matches the
  preorder-to-open-sale editorial development almost to the day.
- `F-B100W-1A` / `F-B100W-3A`: `<lastmod>2026-08-03T13:...</lastmod>` —
  matches the international-rollout story.

**Built:** `app/collectors/casio_uk_sitemap.py` + `app/parsers/casio_uk_sitemap.py`
(`casio_uk_sitemap` collector, region `UK`). See that module's docstring
for the full, honest limitation this design is built around, summarized
here:

**The sitemap carries only a canonical URL, a reference (parsed from the
URL path), and a `<lastmod>` timestamp — never price, currency, or
availability.** Wired through the existing, completely unmodified pipeline,
this can only ever produce two outcomes:
1. `NEW_REFERENCE` — a reference never seen by Clank from *any* Casio
   source before.
2. `NEW_REGION` — a reference already known from another Casio source
   (e.g. `casio_multi`'s international news or Japan catalogue), now first
   observed in the UK.

It **cannot** produce `PRICE_CHANGE`/`AVAILABILITY_CHANGE`/`SOLD_OUT`/
`RESTOCK` — `classify_price_availability_transition` requires real
price/availability evidence on both sides of a comparison and safely
no-ops when both are absent, by design, not as a bug. A bare `<lastmod>`
bump on an already-UK-observed reference produces **no event** today,
because this collector genuinely has no way to know what changed. A human
still has to look — this source narrows *when* to look, it does not
replace verification.

## Citizen UK — result: BLOCKED, nothing built

| Candidate | Result |
|---|---|
| Product pages (`www.citizenwatch.co.uk`) | HTTP 403 (Cloudflare), confirmed live, unchanged from the prior sprint. |
| `robots.txt` | HTTP 200 (readable) — and it explicitly matters: Citizen's robots.txt uses the modern "Content-Signal" policy format and includes **`User-agent: ClaudeBot` / `Disallow: /`** by name, alongside Amazonbot, Applebot-Extended, Bytespider, CCBot, GPTBot, Google-Extended, and meta-externalagent. **This is a decisive, independent reason beyond the Cloudflare block**: even if the technical block were lifted, this site has explicitly told Claude-identified agents not to crawl it. |
| `citizenwatch.eu` (the same regional platform `de.citizenwatch.eu` uses for Germany) | **Real and accessible** (`citizenwatch.eu/en/`, HTTP 200; product sitemap `citizenwatch.eu/media/sitemap/sitemap-products-citizenwatch.eu.xml`, HTTP 200, 399 real product URLs). **But it is EUR-priced, not GBP** — confirmed by fetching a real product page with both `Accept-Language: en-GB` and `de-DE` headers and a `?currency=GBP` query-string attempt: all three returned `"priceCurrency": "EUR"` unchanged. This is a genuine, real, first-party EU-wide catalogue (a legitimate future "broader EU coverage" candidate), but it does **not** solve the UK-specific problem — it would misrepresent Eurozone pricing as UK commercialisation evidence, which this project's own currency-integrity rule (never compare/fabricate across currencies) forbids. **Not used for anything UK-labeled.** |
| Neither of the mandatory UK regression references (`NJ0238-57E`, `BJ6570-50E`/`BJ6574-59E`/`BJ6572-54X`) appear in the `.eu` sitemap | Confirmed by direct search — further evidence this platform doesn't carry UK-specific listings. |
| Specialist/community coverage | No existing specialist source (Monochrome/Deployant/Fratello/WatchTime/CASIOBLOG/G-Central/Plus9Time) is UK-commercialisation-focused; none investigated further this sprint per the explicit instruction not to add specialist sources unless they directly solve this problem and clear the acceptance bar — none found that do. |

**Conclusion: Citizen UK remains a genuine, honestly-documented blind
spot.** No collector was built. This is the correct outcome per the
sprint's explicit instruction: "A known blind spot is better than an
unstable anti-bot workaround" — and, independently, per the site's own
stated policy toward this exact class of agent.

## Hall-of-Shame replay (evidence-based, not theoretical)

| Case | Would current HEAD catch it? |
|---|---|
| Case 5 — Casio F-B100W international rollout | **PARTIAL.** The UK leg specifically: yes, as a `NEW_REFERENCE`/`NEW_REGION` signal (no price/availability) if F-B100W wasn't already known elsewhere; live-confirmed present in the sitemap. Other regions still uncovered. |
| Case 8 — Casio GD-350S-1 UK preorder→open sale | **PARTIAL, and this is the honest, important nuance.** The sitemap collector would flag "GD-350S-1's UK listing changed on 2026-08-12" (real, matching the actual editorial date) as a `NEW_REGION`-shaped signal if GD-350S-1 was already known from another Casio source — enough to prompt a human to go look. It would **not** automatically confirm "now on open sale, £100" — that specific commercial fact remains inaccessible without reading the blocked product page. Live-reproduced in `tests/test_core.py::test_casio_uk_sitemap_known_gd350s1_from_japan_emits_new_region`. |
| Case 2 (UK leg) / Case 11 — Citizen UK | **NO.** Genuinely blocked, both technically and by explicit site policy. Unchanged from the prior sprint. |

## What was not attempted, and why

No Cloudflare bypass, no browser-automation session, no VPN/geo-spoofing,
no anti-bot-evasion technique of any kind — for either brand. The Casio
sitemap finding is not a workaround; it is a real, separately-published,
intentionally-public resource with no stated restriction against this
project's collection method. The Citizen `.eu` EUR-priced catalogue was
found and explicitly **not** repurposed as fake UK evidence.
