# Timex miss autopsy + recall improvement (Sprint 11, 2026-08-12)

## Why this sprint exists

Real Timex launches were published by the user's Notebookcheck watch-writer
colleagues while Watch Clank surfaced nothing. This is a real production
false-negative, not a hypothetical -- the brief forbids blindly adding
sources; the miss had to be root-caused first.

## Miss matrix (verified against real NBC articles + live production DB)

Three misses were fully verified end-to-end (NBC article fetched, exact
SKUs/timestamps extracted, cross-referenced against the live DB's stored
evidence). Three additional NBC search hits (Expedition Capstone, Todd
Snyder Dylan, Huckberry Black Max 1979) were found but have much lower
Notebookcheck article IDs (~1.1-1.19M vs ~1.35-1.37M for the three verified
misses) -- strong evidence they are older archived content surfaced by
generic search terms, not genuine *recent* misses in this sprint's window.
They are listed as unresolved/lower-confidence, not claimed as verified
misses.

| # | NBC article | NBC pub date | Watch Clank's own evidence | Root cause |
|---|---|---|---|---|
| 1 | [MK1 Chronograph](https://www.notebookcheck.net/Timex-launches-two-MK1-Chronograph-watches-with-sunray-dials.1361265.0.html) (Antony Muchiri) | 2026-08-06T14:07:00+02:00, cites `timex.com/products/...-tw2y71300`/`...-tw2y71200` directly | Own blog post 2026-08-04T09:00:04-04:00 (2 days *before* NBC); catalogue `published_at` also 2026-08-04 | CATALOGUE_ALREADY_BASELINED + PARSER_FAILURE (confirmed, see below) |
| 2 | [Todd Snyder x Timex Marlin Mesh](https://www.notebookcheck.net/Timex-s-affordable-34mm-vintage-inspired-Marlin-Mesh-just-got-even-more-stylish.1353751.0.html) (Kristen Spradlin) | 2026-07-29 | Own blog post 2026-07-28T07:00:07-04:00 (1 day *before* NBC) | Same as #1 |
| 3 | [E Line Automatic w/ Miyota](https://www.notebookcheck.net/Timex-launches-3-new-Automatic-E-Line-watches-with-Miyota-movements-and-exhibition-casebacks.1365542.0.html) | ~2026-08-11/12 (article ID clusters with #1/#2) | Own blog post "New For Fall: The E Line Returns" 2026-08-11T09:00:04-04:00 | Same pattern (baseline + parser) |
| 4-6 | Expedition Capstone 39mm / Todd Snyder Dylan / Huckberry Black Max 1979 | unresolved -- likely older content, not genuine recent misses | partial match only (Capstone title differs from stored lead) / no match found | UNKNOWN -- flagged, not claimed |

**Did Watch Clank have the evidence before NBC published, for misses #1-3?
YES, in all three cases.** Watch Clank's own official Timex blog feed
(`timex_news`) captured each of these stories 1-2 days *before* Notebookcheck
published, every time. This was never a discovery-latency or source-coverage
problem. It was entirely a downstream intelligence problem, in two layers.

## Two confirmed root causes (not guesses -- read from real captured content)

### 1. CATALOGUE_ALREADY_BASELINED (correct-by-design, not a bug)

All three products' catalogue SKUs (e.g. `TW2Y71200VQ`, `TW2Y71300VQ`)
were absorbed into `timex_products`' very first-ever crawl (`CollectorRun`
id=81, `is_baseline=True`), because their Shopify `published_at` (2026-08-04,
2026-07-28, 2026-08-11 respectively) pre-dated that first crawl. This is
`force_baseline` working exactly as designed -- a brand's entire existing
catalogue must not alert as "1445 new watches!" on day one. Nothing to fix
here; noted as a real, expected, one-time absorption window, not a
recurring gap.

### 2. PARSER_FAILURE -- confirmed via real captured blog content

Fetched the real stored payloads (`SnapshotBlob` for `ReleaseLead` id=25 and
29) directly from disk. Both had `model_references: []`. The real SKUs
(`TW2Y71200`, `TW2Y71300` for #1; `TW2Y84000`/`TW2Y83900`/`TW2Y83800` for #2)
**are** present in the raw content -- embedded in Shopify CDN image
filenames, e.g.:

```
https://cdn.shopify.com/.../14065_TX_TC_26_PFB_TW2Y71200_3_600x600.jpg
```

`MODEL_RE = re.compile(r"\b(TW[0-9A-Z]{6,10})\b")` requires a `\b` word
boundary before `TW`, but the SKU is preceded by `_`, which is a `\w`
character in regex -- so no boundary exists and the match never fires. The
actual shoppable-product widget itself is stripped to a bare
`[SHOPPABLE_PRODUCT_BLOCK]` placeholder by Shopify's own Atom feed
generation, so there is no href to recover either -- the image filename is
genuinely the only structured signal left in the feed. Confirmed
reproducible on both real posts (2/2), not a one-off.

## The fix (app/parsers/timex_news.py, app/services/pipeline.py)

1. Added `IMAGE_SKU_RE = re.compile(r"[_/](TW[0-9A-Z]{6,10})[_.]")`, scanning
   `content` (which includes `<img src="...">` markup) in addition to the
   existing prose regex. Deliberately anchored on the confirmed real
   Shopify naming convention (`_SKU_` or `/SKU.`), not a loosened `\b`, so
   it can't start matching inside unrelated prose.
2. **A second, only-discovered-by-live-validation bug**: the image-filename
   SKU never carries the catalogue's trailing variant suffix (`TW2Y71200`
   vs the catalogue's real stored `TW2Y71200VQ`). `normalize_timex_reference`
   is a deliberate conservative passthrough (no suffix-stripping, unlike
   Casio's JDM allowlist, proven by its own dedicated test) -- an exact
   `reference_canonical` match therefore always misses, and
   `_resolve_or_create_watch` would create a phantom duplicate Watch
   instead of linking the real catalogue one. Fixed with a Timex-scoped
   prefix-match fallback in `_resolve_or_create_watch`: only auto-links
   when the prefix resolves to **exactly one** existing watch; ambiguous
   or zero matches fall through to normal creation. Zero behavior change
   for Casio/Citizen/Seiko.

Both bugs were confirmed via **live validation against an isolated copy of
the production DB** before either fix, then again after -- the first
attempt (regex fix alone) fired 7 unwanted `NEW_REFERENCE` events and
created duplicate watches; the second attempt (regex + prefix-fallback)
produced 0 events, 0 duplicates, correct `watch_ids` linkage. Then applied
for real to the live production DB with identical clean results, followed
by a repeat run producing 0 new leads/watches/events (stability proven).

## Regression tests (tests/test_core.py)

- `test_timex_news_parser_extracts_sku_from_image_filename_real_capture` --
  MK1 Chronograph, real fixture data.
- `test_timex_news_parser_extracts_sku_from_image_filename_marlin_mesh_real_capture`
  -- Marlin Mesh, real fixture data, proves it's not a one-off.
- `test_timex_news_parser_does_not_match_sku_substrings_mid_word` -- false
  positive guard.
- `test_timex_news_image_filename_sku_resolves_to_existing_catalogue_watch_not_duplicate`
  -- the exact live-validation-caught dedup bug, as a permanent regression.
- Pre-existing `test_real_historical_timex_article_does_not_become_current_news`
  and `test_fresh_timex_article_still_creates_event` still pass unchanged --
  proves the freshness gate built in Sprint 10 is still correctly applied
  on top of the newly-extracted references (old news stays silent, fresh
  news still fires).

## What this closes, honestly

Going forward, a Timex blog post following this exact real-world shape
(SKU only in image filename, no href in the feed) will now: extract the
real SKU -> link to the correct existing catalogue watch (if the catalogue
already has it) or create real new-to-Clank evidence (if it doesn't) ->
correctly fire `NEW_REFERENCE` on the day it's published, subject to the
existing 72h freshness gate. This is a same-day catch, not a multi-day lag
-- see the E Line Automatic case (own blog 2026-08-11T09:00:04, i.e. the
day it's published).

**What this does NOT close:** if Timex ever publishes a launch through a
channel outside `the-timex-blog.atom` entirely (see the unresolved
Huckberry-exclusive case above), this fix does nothing -- that would be a
genuine SOURCE_COVERAGE_GAP, and no evidence was found in this sprint
proving that's a recurring pattern (one ambiguous, unverified data point,
not the "repeatedly early" bar the brief sets for adding a new source).
