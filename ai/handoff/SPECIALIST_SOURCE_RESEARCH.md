# Specialist source research — Layer B (early-warning) candidates

Sprint 5, 2026-08-11. Research method: web search + direct WebFetch of real
pages/feeds, plus direct fetch of real Notebookcheck watch articles'
"Source(s)" attribution sections (not guessed — quoted from what each
article actually cites). No automated collection was built for anything
here except CASIOBLOG (see bottom of this doc for why).

## Explicitly requested sources

| Source | Type | Brands | URL | Access method | RSS? | Structured data? | Pub. frequency | Historical scoop evidence | Recommended tier |
|---|---|---|---|---|---|---|---|---|---|
| **CASIOBLOG** | SPECIALIST_BLOG | Casio (G-Shock, Edifice, Pro Trek, others) | casioblog.com | RSS (`/en/feed/`), real, confirmed live | **Yes** — `/en/feed/` and `/feed/`, standard WordPress RSS2.0 | Title/category/pubDate/excerpt in every item | ~hourly `updateFrequency`, several posts/week in practice | Directly cited by Anubhav Sharma on Notebookcheck for the MRG-B5000SA-2 leak ("first image... strong track record"); explicitly named in this sprint's brief as historically used for unannounced Casio watches | **2** |
| **NEEL** (neel.co.jp) | RETAILER_EARLY_LISTING | Grand Seiko, Seiko, Casio, Citizen (+ others) | neel.co.jp | Real Japanese authorized-retailer e-commerce site (Yokohama), has product pages/prices/stock | No | Real catalogue HTML with price/stock per product (same class of source as citizenwatch.com/seikousa.com, unconfirmed if server-rendered JSON exists — not investigated to that depth this sprint) | Continuous (live retail catalogue) | **Not found** as a directly-cited Notebookcheck source in this sprint's search sample — confirmed only that it is a real, legitimate authorized multi-brand retailer matching the profile of "Asian retailer database" leak sources already cited elsewhere (e.g. Great G-Shock World's database-entry leaks) | **3** (unproven for NBC specifically, but a real, safely-collectible retailer) |
| **Oracle Time** (oracleoftime.com) | SPECIALIST_PUBLICATION | Broad watch industry (Seiko appears; not Casio/G-Shock-focused) | oracleoftime.com | Real UK watch magazine, "Watches" archive is chronological with author bylines, categories (New Release, Hands-On, Industry News), 238 pages of archive | Not checked this sprint | Structured listing page, looks scrapable | Multiple posts/day (general watch industry) | No G-Shock/Casio leak evidence found; it's a broad luxury/general watch publication, closer to "official confirmation aggregator" than early-warning for this project's specific brands | **3**, low priority — brand mismatch (little Casio/G-Shock content observed) |
| **@geesgshock** (Instagram) | SOCIAL_LEAKER | Casio G-Shock specifically | instagram.com/geesgshock | Social-media only, **no public API/RSS** | No | No | Irregular, event-driven | **Directly confirmed**, repeatedly, as a live Notebookcheck citation (Kristen Spradlin, Nov 2025, "red-hot G-Shock" leak — sole cited source; multiple other Anubhav Sharma articles) — the single most-cited specialist source found in this research | **3** — high editorial value, cannot be safely automated (see below) |

## Additional sources found via Notebookcheck author source-mining (Phase 10)

Real citations found by fetching actual NBC articles' "Source(s)" sections
(not inferred from headlines):

| Source | Type | Cited by | Example | Notes |
|---|---|---|---|---|
| **Great G-Shock World** (gshockjp.blog.jp) | SPECIALIST_BLOG | Anubhav Sharma, Kristen Spradlin | GA-2100LXB/GM-2100LXB "spotted in Asian retailer database" (May 2026); GM-H5600 fitness G-Shock leak | Japanese blog, explicitly credited for database-entry-style leaks (model codes surfacing before official reveal, sometimes with **no images** — pure reference-number leaks) | **2** |
| **@morgan_gshock** (Instagram) | SOCIAL_LEAKER | Anubhav Sharma | GBX-H5600 leaked promotional graphic | Second G-Shock leaker account, same access constraints as geesgshock | **3**, same automation blocker as geesgshock |
| **Casioblog (Russian-language)** | SPECIALIST_BLOG | Anubhav Sharma | MRG-B5000SA-2 — "first image", described as having "a strong track record" | This is casioblog.com's Russian edition (casioblog.com without `/en/`) — same site, same RSS mechanism (`/feed/` vs `/en/feed/`), already covered by the implemented collector | already covered |

No additional sources were found specifically attributable to Abhinav
Fating for Casio/Seiko/Citizen coverage in this sprint's search sample —
his visible Notebookcheck byline work in this search was general consumer
tech (smartphones, laptops), not watches. This is a negative finding, not
an omission: stated honestly rather than fabricated.

## Ranked recommendation

1. **CASIOBLOG** — implemented this sprint (RSS, no anti-bot concerns, confirmed real historical NBC citations). Highest automation feasibility + highest confirmed editorial value.
2. **Great G-Shock World** — not implemented this sprint, but same profile as CASIOBLOG (specialist blog, confirmed NBC citations for database-style leaks). Recommended next.
3. **@geesgshock / @morgan_gshock (Instagram)** — highest single-source editorial value found (most-cited leaker in this research), but **cannot be safely automated** — see below. Manual ingestion endpoint built instead (`--ingest-manual-lead`).
4. **NEEL** — real, legitimate, safely-collectible retailer (has actual product pages, matches the profile of retailer-database leaks already valuable to NBC), but no direct historical NBC citation found this sprint. Worth a future investigation pass, not implemented this sprint (time-boxed).
5. **Oracle Time** — deprioritized. Broad general-watch publication, not brand-focused; no G-Shock/Casio leak evidence found. Low relevance to this project's specific brands.

## Why @geesgshock was not automated

Per this sprint's explicit instruction: do not bypass Instagram
authentication, evade rate limits, circumvent anti-bot protections, or use
private/stolen APIs. Instagram does not offer a public RSS/API mechanism
for monitoring a public account's posts without either the official Graph
API (requires the account owner's authorization — not available for a
third-party account we don't own) or scraping the authenticated web app
(exactly the anti-bot/auth boundary this project has consistently refused
to cross for every other source, e.g. Casio Japan's Akamai protection).

Instead: `scripts/run_pipeline.py --ingest-manual-lead` (see
`app/services/specialist_leads.py::SpecialistLeadService.ingest_candidate`)
lets a human paste a post URL, reference candidate(s), and a short claim
summary; it is stored identically to a collector-sourced lead
(`ingestion_method="manual"`), with the same tier/type/attribution
discipline, so it participates in correlation and lead-time tracking like
any other Layer B lead. This satisfies the sprint's explicit fallback
requirement ("the system should be able to ingest a Geesgshock post
manually even if automated discovery cannot safely be implemented yet")
without fabricating a collector that doesn't exist.

## Real example leads (live, 2026-08-11, from the implemented CASIOBLOG collector)

```
SOURCE: CASIOBLOG (tier 2, SPECIALIST_BLOG)
TITLE: [Rumors] G-SHOCK GWR-B3000 — is the GRAVITYMASTER line finally
       getting the update fans have been waiting for?
REFERENCE CANDIDATE: GWR-B3000
PUBLISHED: 2026-04-10
NOTEBOOKCHECK USED IT: not directly checked for this specific article, but
  CASIOBLOG as a source is independently confirmed used by NBC elsewhere
OFFICIAL CONFIRMATION: GWR-B3000-1A / -A2 / -B8 already exist in Watch
  Clank's own production DB (discovered via casio_intl_news, 2026-08-08) —
  but the lead's extracted reference ("GWR-B3000", family-level) does not
  exact-match the official references ("GWR-B3000-1A" etc.), so
  correlate_pending_leads() correctly does NOT auto-correlate them — this
  is the conservative "never fuzzy-match" behavior working as designed,
  not a bug. A human reviewing this pair would obviously see the
  connection; the system deliberately does not guess it for them.
WOULD WATCH CLANK HAVE CAUGHT IT?: Yes, as an UNCONFIRMED Layer B lead
  (confirmed live — this exact lead exists in the DB right now, run_id=80).
  Automatic correlation to the official reference is a known, honest
  limitation (family-prefix vs full-suffix), not implemented this sprint.
```

```
SOURCE: @geesgshock (Instagram, tier 3, SOCIAL_LEAKER)
TITLE (NBC article using it): "Casio's new red-hot G-Shock watches break
  cover in fresh leak"
PUBLICATION TIME: 2025-11-14 (Notebookcheck article date; the Instagram
  post itself may predate this — exact original post timestamp not
  independently verified this sprint)
WHAT IT REPORTED: Four red G-Shock variants (DW-5600RRB, DW-6900RRB,
  GA-110RRB, GA-2100RRB) expected in December, via leaked renders
NOTEBOOKCHECK USED IT: Yes — sole cited source for this article (Kristen
  Spradlin)
WOULD WATCH CLANK HAVE CAUGHT IT AUTOMATICALLY?: No — Instagram cannot be
  safely automated (see above). It COULD have been captured via the manual
  ingestion endpoint if a human had pasted the post at the time.
```

Lead-time precision note: exact original social-post timestamps were not
independently verified against Instagram in this research (would require
visiting the platform, which this sprint deliberately avoided per the
anti-automation instruction) — reported dates above are Notebookcheck's own
publication dates, not claimed as the leak's first-appearance time.

## Sprint 7 additions (2026-08-11, Epoch 1 reset sprint)

| Source | Type | Brands | URL | Access method | RSS? | Outcome |
|---|---|---|---|---|---|---|
| **G-Central** | SPECIALIST_BLOG | Casio (G-Shock focus) | g-central.com | Real, confirmed live (`/feed/`), WordPress RSS2.0, `updatePeriod=hourly` | Yes | **Implemented** — real regional-release, restock/availability, and collaboration coverage confirmed in live capture. Found and fixed a real false-positive during isolated live validation: the family-prefix reference regex matched "GAme" inside the plain word "game" (title: "G-Shock is now on Roblox with new obby game..."); fixed by requiring at least one digit in the matched suffix. |
| **Plus9Time** | SPECIALIST_PUBLICATION | Seiko, Grand Seiko, Citizen | plus9time.com | Real, confirmed live (`/blog?format=rss`), Squarespace RSS2.0 | Yes | **Implemented**, with an honest finding: the real live capture is predominantly historical/archival (vintage catalog scans, patent filings, trademark news) rather than early-warning leads for current unreleased references. Brand is reliably identifiable from category text; reference extraction legitimately returns empty for most items. Still implemented because it is real, cheap, safe, and Seiko/Citizen specialist coverage was otherwise thin — not because it produces the highest-value leads of the three specialist sources. |
| **Japan Select** | RETAILER_EARLY_LISTING (investigated) | Casio, Seiko, Citizen | japan-select.com | Real Shopify store, confirmed public `/collections/{brand}/products.json` (same mechanism already proven safe for Citizen/Seiko USA in Sprint 3), Casio collection alone showed 203 products with price/availability/reference per item | N/A (JSON) | **Deferred, not implemented.** Technically feasible and cheap by the same proven pattern as existing product collectors — this is a documented scope decision (stay within this sprint's bounded plan of two new automated sources, avoid "Commerce Clank" scope creep), not a technical blocker. Good candidate for a future sprint. |
| **Great G-Shock World** | SPECIALIST_BLOG | Casio (G-Shock) | gshockjp.blog.jp | Real, confirmed live in Sprint 5 research (real NBC citations) | Not re-verified this sprint | Deferred again — G-Central and Plus9Time were prioritized per this sprint's explicit brief ordering; Great G-Shock World remains the next recommended specialist addition. |
| **NEEL** | RETAILER_EARLY_LISTING | Casio, Seiko, Citizen, Grand Seiko | neel.co.jp | Real, confirmed live in Sprint 5 research | N/A | Deferred again — no new investigation depth added this sprint; still a real, legitimate, safely-collectible retailer with no confirmed NBC citation found so far. |

Both newly implemented sources were live-validated against an isolated
throwaway database (not the Epoch 1 operational DB) before being included
in the Epoch 1 baseline — real network fetches, real baseline-then-repeat
dedup confirmed (20 leads each on first run, 0 on immediate repeat).
