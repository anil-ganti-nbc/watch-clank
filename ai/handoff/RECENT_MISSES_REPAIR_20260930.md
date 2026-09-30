# September 30 Watch Clank miss repairs — COPS-000086

Implemented locally on current GitHub main cca7123, preserving the NAS
authority label patch af39e05 as e1954c3. Production remains pinned to
af39e059b4f26273297cb8e89cd602ec4887ae0a on the NAS. No production DB,
timer, webhook, service, image, or qualification decision was changed.

## Implemented changes

- Add Timex India to the 15-minute Sentinel using the existing Shopify
  adapter, exact SKU identity, IN region and independent silent baseline.
- Add Citizen US men's/women's new-arrivals pages to the same fast sweep.
  One request per source currently; bounded four-page traversal rejects
  incomplete results rather than arming from a partial baseline.
- Add Casio's US collaboration archive as a one-request fast source,
  keyed by distinct first-party campaign URLs. A known DW5600 base family
  cannot silently suppress a newly linked collaboration page.
- Preserve Citizen website-first-received dates and badges separately
  from publication evidence. Neither becomes `published_at` or proves stock.
- Refresh known Casio UK/JP/EU URLs oldest-observed first after unseen
  URLs. Selection advances only when real observations commit, with no
  new schema or cursor. Budget stays 300. Parse up to 25,000 candidates
  before selection rather than permanently hiding everything after 3,000.
- Recognize F-B100W / F91W in specialist feeds. Keep G-Central/CASIOBLOG's
  family-root extraction contract; multi-brand feeds retain full variants
  and also gain DW/AW/AE coverage. No historical leads are reprocessed.
- Set G-Central's canonical health/renderer cadence to 15 minutes and
  update static timer/installer definitions. No installer was executed.
- Store new-lead publication-to-discovery and discovery-to-provider-
  acceptance latency separately in collector run metadata. Unknown dates
  and reversed timestamps do not become fabricated zero-latency samples.

## Source evidence (read-only September 30 probes)

Timex India is viable for the fast sighting role. Its public robots.txt
permits `/products.json`, and page 1 returned 250 Timex/Watch records,
monotonically decreasing publication timestamps. Both exact misses are
present, including from the NAS's network:

| SKU | Source publication time (IST) | Price field (INR) |
|---|---|---|
| TWG027900JQ | September 22, 13:56:50 | 11,995 |
| TW2Y70600UJ | September 22, 13:56:35 | 21,995 |

Sources: [robots](https://shop.timexindia.com/robots.txt),
[catalogue feed](https://shop.timexindia.com/products.json?limit=250&page=1),
[Main Street](https://shop.timexindia.com/products/timex-main-street-round-36mm-black-dial-analog-men-watch-twg027900jq),
[Waterbury](https://shop.timexindia.com/products/timex-waterbury-round-39mm-blue-dial-analog-men-watch-tw2y70600uj).
The Sentinel records a listing sighting, not price/stock transitions.
Small metadata-only fixture captures retain the exact two references.
India suffixes are not stripped into US/UK identities without evidence.

Citizen US robots allows these collection paths. Men's new arrivals
returned 30 structured hits and women's 18; both returned HTTP 200 from
the NAS. AT8296-53A appears on the men's page, with source website-first-
received date September 30 and model introduction date October 1.
Sources: [robots](https://citizenwatch.com/robots.txt),
[men's listing](https://citizenwatch.com/us/en/collection/mens-new-watches?offset=0&limit=48),
[women's listing](https://citizenwatch.com/us/en/collection/womens-new-watches?offset=0&limit=48).
The fixture contains only AT8296 product metadata, not the full HTML body.

Casio's [US collaboration archive](https://gshock.casio.com/us/collaborations/)
returned 48 distinct campaign URLs in one request, including Jae Tips.
The archive also returned HTTP 200 from the NAS (660,040 bytes).
Its [robots.txt](https://gshock.casio.com/robots.txt) allows WatchClank,
ClaudeBot, GPTBot and ChatGPT-User on this path. The source explicitly
disallows CCBot, which this collector does not use. Candidate selection
reads the complete main content, deduplicates image/button links, and
keeps only same-host limited/40th campaign pages. No product pages are
fetched and no publication date or availability is inferred.

## DW5600 timing and remaining source gap

NAS specialist lead 238 / receipt 483 prove G-Central publication at
04:49:44 UTC, discovery at 05:27:08 UTC, provider identification at
05:27:09.526717 UTC: 2,244 seconds discovery delay, about 1.53 seconds
transport delay. The configured NAS timer is still 2,700 seconds; its
read-only service outcome was success at 12:27:13 IST, which alone does
not prove acquisition health. Raw urllib probes to G-Central's feed and
robots from the NAS returned 403; existing scheduled DB evidence showed
successful acquisition earlier. No header change or bypass was attempted.
Validate acquisition through the real collector after any approved cadence change.

[G-Central's story](https://www.g-central.com/new-u-s-collabs-jae-tips-x-g-shock-dw6900-for-complexcon-and-bait-x-g-shock-dw5600/)
was a day later than other coverage. The 15-minute definition addresses
polling delay only; it cannot make this source publish earlier. BAIT's
official storefront links to `https://info.baitme.com/`, which timed out
in the source audit. Its store robots disallows `/rss*`. Hodinkee's
article feed probe returned 403. No unverified source was integrated.
An earlier BAIT/social collaboration path remains unresolved.
BAIT Yamagata was absent from the Casio archive on the probe date; adding
the archive improves future official collaboration coverage without a
claim that it would have caught this exact miss.

## Explicit limits

- CA4722-06L, AQ5025-04N and AT8297-51L were absent from accessible
  configured official sources. Citizen JP denies requests; Citizen UK
  is robots-disallowed and blocked. These coverage gaps remain.
- F-B100W refresh/reference recognition improves catalogue continuity and
  specialist correlation. Casio sitemaps contain no stock fields, so
  UK sellout/Japan restock detection is not solved by sitemap rotation.
- Timex India page 2+ and mass republishing are bounded-sighting limitations.
  Its September 22 entries will be silently baselined, not replayed as launches.
- Goldsmiths remains experimental and disabled on the NAS. Preserve that
  disabled state; do not blanket-enable every rendered unit.

## Deployment handoff

The standing workspace instruction forbids deployment from this checkout.
An independently authorized NAS deployment must preserve the existing DB
and sole sender, take an integrity-checked SQLite backup, use an immutable
image revision, and compare the running revision independently. Preserve
the NAS label fix, both retired/disabled sources, existing service mounts,
secret-file references and qualification state. No schema migration is needed.

Update the existing NAS G-Central timer's `OnUnitActiveSec` to `900s`
only after approval; current NAS units are source-relative and differ from
the historical Docker renderer templates. The Sentinel service already
runs every 15 minutes, so the four new sources require no new timers.
Confirm its first successful polls for all four new sources show
`baseline_mode=true`, zero sightings and zero receipts; later unseen
identities must produce one sighting and one provider receipt each.
Check G-Central real acquisition and both latency dimensions, and confirm
Casio refresh slices advance across consecutive scheduled runs.

## Validation

Exact-reference fixtures, silent baseline/arming, one-receipt admission,
duplicate suppression, bounded Citizen pagination/failure, Casio refresh
progress on identical payloads, references past the old discovery ceiling,
family parsing compatibility, separate timing evidence and generated timer
cadence are covered offline. Final full suite on Windows / Python 3.14:
635 passed, 2 skipped. Required correctness lint (`E9,F63,F7,F82`), bytecode
compilation and Git whitespace checks pass. Full style lint of the new
adapters, collectors, parsers and regression files passes; broader style
findings already present in the pipeline are unchanged.
