# New specialist source: Great G-Shock World — GCW-B5000 source-gap specimen

## The specimen

**Article:** "MR-G３０周年第２弾「MRG-B5000SA-2」と、フルカーボンオリジン「GCW-B5000」他！"
**Source:** Great G-Shock World (gshockjp.blog.jp)
**URL:** https://gshockjp.blog.jp/G-SHOCK-newmodel-Late2026-20260816
**Published:** 2026-08-16T10:39:01+09:00 (2026-08-16 01:39:01 UTC) — verified
directly from the source's own Atom `<issued>` field, not inferred.
**References:** GCW-B5000, MRG-B5000SA-2

Great G-Shock World is directly, repeatedly cited by name in Notebookcheck's
own coverage of this exact watch family — e.g. its 2026-03-02 MRG-B5000
registration story
(https://www.notebookcheck.net/New-upcoming-premium-Casio-MR-G-MRG-B5000-watch-reportedly-registered-in-global-database.1239026.0.html)
links directly to `gshockjp.blog.jp/MRG-B5000-newmodel-20260301` as its
source. This is a demonstrated, named upstream-of-competitor-journalism
relationship, not a speculative one.

## Existing coverage check

None of Watch Clank's 7 existing specialist RSS sources (CasioBlog,
G-Central, Plus9Time, Monochrome, Deployant, Fratello, WatchTime) nor its
official Casio collectors (casio_multi/casio_intl_news, casio_japan)
picked up this article or either reference, as of this investigation
(checked directly against the live Hetzner database). CasioBlog's own
tracked leads hadn't advanced past their initial 2026-08-14 baseline batch
at all — no genuine gap in polling cadence, simply no coverage of this
specific story by any currently-tracked source. **Confirmed additive, not
redundant.**

## Source evaluation

**Ingestion surface:** Atom feed, `https://gshockjp.blog.jp/atom.xml`
(livedoor Blog platform). Also available as RSS 1.0/RDF at `/index.rdf`
(rejected in favor of Atom — see below). No RSS 2.0 variant exists on this
platform; both available formats were fetched and inspected directly
(`curl`), not assumed.

- **Bounded:** yes, ~15 most-recent entries per fetch, same discipline as
  every other approved specialist source.
- **Chronological:** yes, newest-first, consistent with every checked
  fetch across this investigation.
- **Timestamps:** `<issued>` (Atom 0.3's true first-publish field, ISO
  8601 with explicit `+09:00` offset) — used in preference to `<modified>`
  (last-edited time), matching this codebase's existing discipline of
  never treating a different timestamp concept as publication time.
- **Dedup:** existing source-url dedup key in `SpecialistLeadService.ingest_candidate`
  applies unchanged — verified via a real repeat-run (0 new leads second
  time, live network fetch).
- **Reference extraction:** exact, deterministic, via the existing
  regex-based `_REFERENCE_PATTERNS["Casio"]` — extended (see Fix below).
- **Freshness/baseline safety:** existing `classify_lead_freshness`
  applies unchanged (BASELINE at onboarding, FRESH/STALE_PUBLICATION
  afterward) — verified by test and live run.
- **No anti-bot bypass, no article-page crawling, no `content:encoded`
  read** — same boundary as every other specialist source.

## Fix required before this source was usable (found, not assumed)

Two real, general bugs were found and fixed while onboarding this source
— not special-cased to GCW-B5000, both benefit any future non-English
source:

1. **`parse_rss_feed` cannot parse this platform's feeds at all.** RSS 1.0/
   RDF (the alternative format) places `<item>` elements as siblings of
   `<channel>`, not children — `channel.findall("item")` finds zero items,
   silently. Rather than extend the existing, proven RSS2-only parser (kept
   untouched to protect its existing tests), a new, parallel
   `parse_atom_feed()` was added to `app/parsers/rss_common.py` for the
   Atom format instead — chosen over fixing RDF support specifically
   because Atom is the more standard, more likely-to-recur shape for
   future non-English sources. `PublicationSource` gained a `feed_format`
   field (default `"rss2"`, zero behavior change for the four existing
   sources) so `parse_specialist_publication_feed` can select the right
   parser per source.
2. **Brand and reference-pattern regexes silently failed on real,
   verified Japanese-language input.** Python's default Unicode-aware
   `\b` treats CJK ideographs as "word" characters, so an ASCII term
   directly adjacent to Japanese text with no separating space/punctuation
   — e.g. this article's real title, `"...G-SHOCK秋冬予想..."` — never
   matches at all: `re.search(r'\bg-shock\b', title, re.IGNORECASE)`
   returns `None` on the genuine specimen text, verified empirically
   before any fix was written. `re.ASCII` added to every pattern in
   `app/parsers/specialist_publications.py` restores standard boundary
   behavior; a no-op for the four purely-English existing sources.
   Separately, `"GCW"` was added to the Casio reference-prefix
   alternation (alongside the existing `GA/GW/GM/GMW/...` family) — a
   real, general Casio prefix family that was simply missing, not a
   `GCW-B5000`-specific branch.

Both fixes are general-purpose and covered by dedicated tests, not
specimen-specific hacks.

## Registration

`source_registry.py` already contained a `great_gshock_world` entry
(tier 2, `SPECIALIST_BLOG`, `gshockjp.blog.jp`) from earlier, unautomated
research — this sprint wired it up rather than re-deciding its tier.
Registered as `great_gshock_world_atom` in `collector_registry.py` /
`health.py` (45-minute expected cadence, matching the other
frequently-updated specialist sources — this blog posted 5 times across
2026-08-14 through 2026-08-17, comparable to or faster than CasioBlog/
Fratello/Monochrome).

## Verification performed

- Full canonical test suite: 257 passed (was 254 before this source),
  Ruff clean.
- Real, live, end-to-end run against the actual production entrypoint
  (`python -m scripts.run_pipeline --experimental-specialist
  great_gshock_world --live --force-baseline`) into an isolated throwaway
  database — 7 real leads created from the live feed, including the exact
  GCW-B5000/MRG-B5000SA-2 specimen with both references correctly
  extracted.
- Real repeat run against the same isolated database, same live feed:
  0 new leads (dedup confirmed against actual network state, not just a
  fixture).
- Permanent regression tests using the real specimen (not a hardcoded
  branch): reference extraction from the real Japanese title, baseline-
  silence + dedup, and a WatchBench acceptance test proving a same-shaped
  article published "2 hours ago" (steady-state, non-baseline) produces a
  FRESH SpecialistLead and a real attempted Discord delivery.

## Additional candidate research (Phase 4) — rejected sources, and why

At least four further Japanese/Asian specialist-source candidates were
researched for Seiko, Citizen, and Orient/Orient Star coverage. None were
automated. Reported honestly rather than forced:

| Candidate | What it is | Why rejected |
|---|---|---|
| grail-watch.com | English-language "grail watch" enthusiast/wiki site, covers Orient among others | RSS feed (`/feed/`) returns HTTP 403. Per this task's own constraint against bypassing anti-bot protection, disqualified outright without further investigation. |
| webchronos.net / en.webchronos.net (Chronos Japan) | Legitimate professional publication, ~4-5 articles/day per its own description | Both its Japanese and English RSS feeds (`/feed/`) are valid, well-formed RSS 2.0 -- and completely empty (zero `<item>` elements) at the moment checked. A real publication that does not expose its content through the feed it advertises; not something to force via HTML crawling. |
| konta-watch.blog.jp | Very active Japanese blog.jp site ("A middle-aged man's blog about watches worn by celebrities"), genuinely covers Citizen/Orient Star/others, valid RDF feed, 3 posts in the 3 days checked | Technically excellent surface, but editorial content is "which existing watch did this actress wear in this drama" -- not new-product leaks or announcements. Fails the additive-signal test on content type, not technical feasibility. |
| bokunekotokei.blog.jp | Another active blog.jp site covering Seiko/Citizen/Orient | Sampled content is explicitly vintage/historical (1950s references) -- not current new-product intelligence. Same rejection class as above. |
| Retailer sites as a class (Gressive, Nanaple, GINZA RASIN, TiC TAC, Harada HQ, IPPO JAPAN WATCH, ROOK JAPAN) | Japanese authorized-retailer blogs/product pages for Seiko/Citizen/Orient Star | Commerce/promotional content, not independent editorial sources -- a structurally different source class (`RETAILER_EARLY_LISTING`, already represented once by the existing `neel_jp_retailer` tier-3 entry) that would need its own dedicated evaluation of commercial bias and price/availability claims, not a casual addition alongside a `SPECIALIST_BLOG` decision. |

**No second automatable source was found in this pass.** This is reported
as a genuine research outcome, not a shortfall — Great G-Shock World's
combination of single-brand focus, high posting frequency, a real bounded
feed, and demonstrated citation by international press turned out to be
comparatively rare among the candidates actually checked. Further
targeted research (particularly for Seiko and Orient Star specifically,
where no dedicated leak-focused blog was found at all) is a reasonable
follow-up, not attempted further here to avoid forcing a weak source in
to hit a quota.
