# New specialist source: Gear Patrol — Waterbury Heritage Chronograph source-gap specimen

## The specimen

**Article:** "Timex Wrenches Its Heritage Waterbury Watch into a Historic Racing Chronograph"
**Source:** Gear Patrol (gearpatrol.com)
**URL:** https://www.gearpatrol.com/watches/timex-waterbury-heritage-chronograph-tw2y93300/
**Published:** 2026-08-13T15:15:15+00:00 — verified directly from the
live feed's own `<pubDate>` field (RFC822, parsed via the existing,
unmodified `parse_rss_feed` date handling), not inferred from recollection.
**References:** TW2Y93300, TW2Y93400

Gear Patrol covered this watch roughly three days before the local
Timex US collector independently discovered the same references —
a real, demonstrated lead-time gap, not a hypothetical one.

## Discovery surface investigation

Checked, in the preferred order the brief specified:

1. Dedicated watch RSS/Atom feed (`/watches/feed/`) — Cloudflare-blocked
   (403/challenge page).
2. Dedicated watch sitemap (`/sitemap.xml`, `/watches/sitemap.xml`) —
   also Cloudflare-blocked.
3. `robots.txt` itself — also blocked, confirming this is broad
   Cloudflare bot-protection rather than a feed-specific restriction.
4. **Sitewide RSS 2.0 feed (`https://www.gearpatrol.com/feed/`)** —
   clean HTTP 200, standard WordPress RSS 2.0, `<channel><item>`
   nesting, `<pubDate>` RFC822, one `<category>` per item, `<dc:creator>`.
   No robots.txt disallow rule applies to it. This is the only viable
   first-party discovery surface found and is what was implemented.

No Google/search-engine dependency was used or considered.

## Historical evaluation (bounded)

A real live fetch of the sitewide feed (75 items, its full retained
window at fetch time — roughly the trailing few days given posting
volume) was inspected directly, not sampled from memory:

- **Category distribution:** Watches 16, Footwear 14, Outdoors 13,
  Audio 8, Motorcycles 7, Motoring 6, Style 4, Deals 3, Today in Gear 3,
  Food & Drink 1. **Watches is ~21% of sitewide volume** — confirms
  category filtering is load-bearing, not optional, for this source.
- **Brand-relevant proportion within Watches:** of the 16 Watches-tagged
  items, 8 matched a tracked brand (Casio/Seiko/Citizen/Timex) via the
  existing, unmodified brand-detection regex; the other 8 covered
  untracked brands (e.g. Rolex/Omega-class coverage) and were correctly
  excluded by the pre-existing brand-match-exactly-one-brand rule —
  no new filtering logic was needed for that layer.
- **Evergreen/buying-guide/deal/lifestyle proportion:** the real feed
  included a "Today's Best Deals" post that name-checked "Seiko Sports
  Watches" in its title/description but was tagged `Deals`, not
  `Watches` — a real, observed false-positive risk that category
  filtering (not keyword filtering) correctly excludes. Captured
  verbatim as a fixture regression (see Tests).
- **Overlap with existing sources:** none of Watch Clank's 7 existing
  specialist sources nor the official Timex/Casio/Seiko/Citizen
  collectors carried this specific Waterbury story as of this
  investigation.
- **Does Gear Patrol beat manufacturer detection:** yes, by ~3 days
  for this specimen — the triggering evidence for this task.
- **Noise level:** low once category-filtered — every admitted lead in
  the live run was genuine watch editorial (a launch story, a roundup,
  a fitness-tracker variant), none were buying guides or deals.
- **Author/category/tag structure improving precision:** category was
  sufficient and is what was used; author (`dc:creator`) was inspected
  but added no useful signal (Gear Patrol's watch beat is written by a
  small number of staff writers who also write other categories, so
  author alone would not discriminate).

**Verdict: recall improvement is real** (demonstrated 3-day lead-time
gap on the triggering specimen, confirmed additive against all 7
existing sources) **and the noise is controllable** (category filtering
reduces sitewide volume by ~79% before any brand matching runs).

## Filtering strategy implemented

`PublicationSource` gained a `required_category: str | None` field
(default `None`, zero behavior change for the 8 existing sources).
Gear Patrol is registered with `required_category="Watches"`. In
`parse_specialist_publication_feed`, items are skipped before brand/
reference extraction if their `<category>` set doesn't contain the
required category (case-insensitive). This uses the site's own
taxonomy rather than relying on keyword filtering, per the brief.

A second, independent fix was required for correct reference
extraction: Gear Patrol's real headlines never state the model number
in prose — it appears only in the URL slug (e.g.
`.../timex-waterbury-heritage-chronograph-tw2y93300/`). The existing
reference-extraction blob (title + description) was extended to also
scan the canonicalized URL. URL canonicalization
(`_canonicalize_url` — strips query string and fragment) was added
ahead of both reference extraction and the stored `source_url`, so
tracking parameters can't defeat the existing exact-string dedup key.

## Registration and scoring

Registered as `gear_patrol` in `source_registry.py` at **tier=3**
("credible specialist requiring verification"), not tier=2. Reasoning:
the existing tier-2 specialists (CasioBlog, G-Central, Plus9Time,
Monochrome, Great G-Shock World) are single-purpose horology
publications with a demonstrated multi-year focus; Gear Patrol is a
general lifestyle/gear publication where watches are one of ten
categories, and its watch desk has no equivalent demonstrated history
in this codebase's existing source research. Tier=3 matches the
existing Deployant precedent, which is the closest prior comparable
(a broader-scope publication with a real but non-exclusive watch
desk). Per the code's actual behavior (verified by grep), source tier
today only affects Discord alert text labeling
(`format_early_warning_alert`), not scoring/eligibility gating — so
this tier assignment is the correct, existing "source-scoring model"
mechanism to use, honestly described rather than invented.

Cadence set to 90 minutes in `health.py`'s `EXPECTED_CADENCE_MINUTES`,
matching Deployant's existing tier-3 cadence rather than the 45-minute
cadence used for the single-purpose tier-2 blogs.

`max_items` defaults to 60 for this source specifically (vs. 20 for
the other publication sources) because the feed is sitewide: 20 raw
items would only cover a few hours of real content given ~79% is
non-watch, silently truncating effective watch coverage. 60 covers
roughly the same real watches-relevant window the other sources get
from 20 fully-watches items.

## Verification performed

- **Focused tests:** 7/7 passed, isolated (stashed away the unrelated
  pending Timex-burst patch and re-ran to confirm no cross-contamination).
- **Full canonical suite:** 260 passed, Ruff clean — same isolated tree.
- **Real, live, end-to-end run** against the actual production
  entrypoint (`python -m scripts.run_pipeline --experimental-specialist
  gear_patrol --live --force-baseline`) into an isolated throwaway
  SQLite database: 8 real leads created from the live feed, including
  the exact Waterbury specimen with `TW2Y93300` correctly extracted
  from the URL (not present in title/description).
- **Real repeat run**, same database, same live feed, ~1 minute later:
  0 new leads — dedup confirmed against real network state.
- **Category-leakage check on live data:** all 8 admitted leads were
  correctly Casio/Seiko/Timex-branded, all under `/watches/`; a direct
  count against the raw live feed (16 Watches-category items total,
  8 matched a tracked brand) confirms the other 8 Watches items were
  correctly excluded by brand-matching, and none of the 59 non-Watches
  items (Footwear/Outdoors/Audio/Motorcycles/Motoring/Style/Deals/
  Today in Gear/Food & Drink) leaked through.
- No real Discord/notification endpoint was contacted at any point —
  no webhook is configured in this local checkout, so
  `DiscordNotifier.editorial_enabled` is `False` and delivery is a
  clean no-op by construction, not by test mocking.

## Known limitation found during live validation

One genuine, minor reference-extraction imprecision was found on the
live feed (not present in the curated fixture): the URL slug
`casio-g-shock-gm-2110d-4a-sale/` (title: "G-Shock Just Put Its Steel
'CasiOak' on Clearance") extracted as `GM-2110D-4A-SALE` — the real
reference is `GM-2110D-4A`, with `-sale` appended in the URL. The
existing per-brand reference regex's trailing character class
(`[A-Z0-9-]{3,24}`) is permissive enough to also consume a following
hyphenated English word, since letters alone satisfy that class. This
is a general risk introduced by scanning URLs for any source (not
Gear-Patrol-specific), and affects precision, not safety: the
extracted candidate would fail to correlate against a clean official
`GM-2110D-4A` Watch row (a missed correlation), not produce a false
one. Not fixed in this pass — a targeted fix (e.g. stopping the URL
scan at the first segment that contains no digit) is easy to imagine
but risks false negatives against real, longer, all-alphabetic-segment
Casio references (e.g. `GMW-BZ5000BD-1JF` already exercised elsewhere)
without a larger sample of real slugs to validate against. Documented
here rather than guessed at.

## Not deployed

This source was implemented and verified entirely in the local macOS
development checkout, per the task's explicit constraint. No Hetzner
SSH, image build, `--force-baseline` against the real Hetzner
database, or systemd unit installation was performed. `gear_patrol_rss`
exists in `collector_registry.py`/`health.py` (so status/health tooling
recognizes it if it is ever deployed) but has no running timer anywhere.
