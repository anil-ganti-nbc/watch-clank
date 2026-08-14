# Watch Clank — Development Handoff
**Last updated:** 2026-08-15
**Current phase:** EPOCH 1 (started 2026-08-11T13:11:25Z), FOUR official brands (Casio/Citizen/Seiko/Timex, Sprint 9), with editorial-freshness semantics hardened at the Layer B specialist layer (Sprint 8), the Layer A official-news layer (Sprint 10, Timex-scoped), and Timex's own SKU-extraction/reference-resolution path (Sprint 11, see below). Operating from a fresh operational database after a controlled reset (Sprint 7) — the impromptu Sprint 1-6 production soak is preserved as an archive, not deleted. Between Sprint 11 and Sprint 12 (undocumented at the time, reconstructed 2026-08-14 — see that day's checkpoint) regional-commercialisation `NEW_REGION` detection, a Citizen Germany sitemap-delta collector, four new specialist RSS sources (Monochrome/Deployant/Fratello/WatchTime), Discord notification-authority honoring, and availability-editorial-eligibility tightening (SOLD_OUT/RESTOCK noise gating) all shipped. TEN Windows-scheduled tasks total as of Sprint 9-11 (Casio + Citizen news/products + Seiko news/products + Timex news/products + CASIOBLOG + G-Central + Plus9Time, all Ready/Enabled as of last verification) — Windows itself has not been reachable by any session since before Sprint 12 (away until 2026-08-18). Every specialist_leads row carries an `editorial_freshness` classification (FRESH/STALE_PUBLICATION/BASELINE/UNKNOWN_TIMESTAMP/MANUAL_UNDATED); official Timex news additionally has a dedicated publication-age gate at the Event-creation layer (`_ISO_TIMESTAMP_NEWS_SOURCES`, Sprint 10) since `ReleaseLead` itself carries no freshness column — see `ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md`, `ai/handoff/TIMEX_FRESHNESS_AUDIT.md`, and `ai/handoff/TIMEX_MISS_AUTOPSY.md` (Sprint 11). Layer B early-warning system with family-aware correlation + Discord editorial/health alerts wired (still inert — no webhook URLs configured anywhere this session could see). Cloud deployment infra exists; Sprint 12 discovered (read-only) that a `watch-clank` instance is live-soaking on Hetzner on a stale pre-Sprint-5 commit (`fcb5e918`) — **confirmed still true and still running on that same stale commit as of 2026-08-14** (see this day's checkpoint; not redeployed this sprint, deliberately — see `ai/handoff/REMEDIATION_PLAN.md`). **2026-08-14 (Hall of Shame forensic sprint):** reconstructed 12 real Notebookcheck competitive-miss cases, found and fixed the single highest-leverage architectural gap — product-catalogue collectors (Citizen/Seiko/Timex) could create a `Watch` row for a genuinely new SKU but had **no path to ever emit an Event for it**, plus a silent pagination cap that excluded ~81% of Timex's and ~35% of Citizen's real catalogue from every non-baseline run. Both fixed; full writeup in `ai/handoff/WATCH_CLANK_HALL_OF_SHAME_AUTOPSY.md`, `ai/handoff/REGIONAL_COVERAGE_MATRIX.md`, `ai/handoff/REMEDIATION_PLAN.md`. **2026-08-14/15 (post-Hall-of-Shame implementation sprint):** all three of that sprint's top follow-ups executed. (1) **Hetzner redeployed** to current GitHub HEAD (`d0ee4e9`) — real `git clone` + `docker build` on the host, migrated `003_release_leads`→`007_specialist_lead_editorial_freshness` in place (22 pre-existing watches preserved, verified backup taken first), every new-to-this-DB source force-baselined then repeat-verified 0/0, and a **real, verified, unattended `systemctl --user` deployment** installed (17 timers, generated from the collector registry, not hand-written) — no root was available or used; see `ai/handoff/HETZNER_DEPLOYMENT.md`. (2) **Seiko Japan retail-store collector built** (`seiko_jp_products`) — live-confirmed NOT geo-blocked, resolving Hall of Shame Case 12; see `ai/handoff/SEIKO_JP_COLLECTOR.md`. (3) **Casio UK sitemap-delta collector built** (`casio_uk_sitemap`) — the product pages remain Cloudflare-blocked, but the sitemap isn't and carries real dated evidence for both Case 5 and Case 8; **Citizen UK remains genuinely blocked** (Cloudflare *and* an explicit robots.txt disallow naming ClaudeBot) and was left undone, honestly documented rather than forced; see `ai/handoff/UK_SIGNAL_PATH_RESEARCH.md`. Now 17 registered collectors total (was 15). 232 tests (was 221), Ruff clean. **2026-08-15 (emergency notification-path remediation):** a Discord-authority audit found `casio_multi` — the original, most mature official source — could **never** create an `Event`, on either its manual or scheduled path (they're the same function call; there is no scheduler-specific code anywhere in this repo), regardless of Discord configuration. Root cause: `run_multi_source_pipeline` never passed `emit_events`/`notify` to its two internal call sites, so both silently took their own `False` default — unlike `run_brand_news_pipeline`/`run_product_observation_pipeline`/`run_publication_pipeline`, all three of which were already correct (audited explicitly, not assumed). Fixed by giving Casio's production path the same `emit_events: bool = True` contract the other three already use. A second, deeper defect found while testing the first fix: the non-experimental notify threshold was a hardcoded `100.0`, mathematically unreachable (`score_event`'s real ceiling is 90) — Discord could never have fired for the official lane even after the first fix. Replaced with a new, real `DISCORD_OFFICIAL_MIN_SCORE` setting (default 50). A third gap (the catalogue-enrichment call site missed in the first pass) was caught by its own regression test before ever reaching Hetzner. Full incident report, Phase-1 audit matrix of all 17 collectors, and the read-only silent-period audit (**nothing was actually lost** — 0 Events and 0 `FRESH`-eligible SpecialistLeads existed on Hetzner during the entire silent period): `ai/handoff/INCIDENT_SILENT_SCHEDULED_NOTIFICATIONS.md`, `ai/handoff/HETZNER_SILENT_PERIOD_AUDIT.md`. Deployed to Hetzner from GitHub (`c81ebed`), real Discord webhooks configured (`~/.config/watch-clank/secrets.env`, machine-local, never committed), one clearly-labeled test message delivered successfully to each of the editorial and health channels, verified via the real systemd execution path. **Hetzner is now the actual, live, configured Discord authority** for both editorial and health alerts. 238 tests (was 232), Ruff clean. **2026-08-14/15 (legacy launcher remediation):** a multi-day soak audit caught a natural, unintended execution of obsolete image `watch-clank:fcb5e91` (container `watch-clank-watch-clank-run-e9e53b5dcf88`, 19:45:01 UTC, exit code 3) — a pre-migration cron entry in `deploy`'s root-owned crontab (`/home/deploy/staging/watch-clank/deploy_run.sh`, invoked from two lines, one matching the 19:45 firing exactly) had survived the Hetzner redeployment sprint alongside the new 17-timer architecture, silently reading a stale `.deployed-id` file frozen at the Aug 10 checkout on every firing. Root cause fully proven via journal correlation (`ep=watch-clank-watch-clank-run-e9e53b5dcf88` at 19:45:01, byte-for-byte match) and the wrapper script itself. Both `deploy`-crontab lines commented out in place (not deleted), verified by `diff` to be the only two lines changed against a full pre-change backup — every other Clank's cron line, the wrapper, the compose file, `.deployed-id`, and the obsolete image were all left untouched for rollback/forensic comparison. Post-fix verification: old launch window (21:10 UTC) passed with zero trace, natural current-architecture runs continued firing cleanly (`casio_multi`, full RSS batch), no duplicate/overlapping executions found anywhere, three-way provenance match confirmed (git SHA = OCI revision = runtime identity, all `c81ebed...`), DB integrity `ok`. Full writeup: `ai/handoff/INCIDENT_LEGACY_FCB5E91_LAUNCHER.md`.
**Sprint priority note (record, don't erase history):** Sprint 1 deliberately held Stage 2 during soak. Sprint 2 was an explicit owner-directed priority change — recall over precision for the experimental lane, journalist is the verification layer — executed 2026-08-11, 3 days into the original soak hold. That original soak-hold reasoning was correct at the time; this is a documented pivot, not a retraction.
**Next developer:** Claude
**Primary environment:** Windows 10/11, local-first (also now macOS-portable — see `mac/` and `mac_dev_environment_setup` conventions; core application code is unchanged by that, only launcher scripts were added)
**Repository path (Windows):** `C:\Users\anil\Desktop\Watch clank\watch-clank`
**Repository path (macOS, this session):** `/Users/anilganti/Clank base/watch-clank`
**Dashboard:** `http://127.0.0.1:8765` (Windows convention) / `http://127.0.0.1:8918` (this session's `mac/dashboard` default — see `mac/dashboard` for the actual port)

(See prior handoff sections 0–64 as provided at session start for full architecture,
mission, and philosophy notes — omitted here for brevity, unchanged.)

---

# Checkpoint log

## 2026-08-14/15 — Legacy launcher remediation: obsolete fcb5e91 cron entry disabled

**Trigger:** a multi-day Hetzner soak audit observed a natural, unintended
Docker execution: container `watch-clank-watch-clank-run-e9e53b5dcf88`,
image `watch-clank:fcb5e91` (obsolete — accepted image is `c81ebed`),
created 2026-08-14 19:45:01 UTC, exit code 3 about one second later, on
the shared `watch-clank_default` network/volume. Not manually triggered;
the current 17-timer `anilganti` architecture did not explain it.

**Forensic identification (Phase A), no changes made until proven:**
worked outward from every scheduler location `anilganti` could read
directly (system cron, system systemd, `anilganti`'s own crontab and
user-systemd — all clean) to root-owned locations that required the
Hetzner Cloud web console (a separate, out-of-band, root-authenticated
access path independent of SSH; SSH root login is disabled and the
`anilganti` sudo password was unknown this session). Found `deploy`'s
personal crontab still contained two pre-migration `watch-clank` lines,
one (`45 1,4,7,10,13,16,19,22 * * *`) an exact schedule match for the
19:45 firing. Traced the wrapper it invoked
(`/home/deploy/staging/watch-clank/deploy_run.sh`, an abandoned staging
checkout frozen since 2026-08-10 07:38) and confirmed via `journalctl`
(`ep=watch-clank-watch-clank-run-e9e53b5dcf88` at 19:45:01, byte-for-byte
match to the reported container) and the wrapper's own source that it
reads a stale `.deployed-id` file (literally `fcb5e91`, unchanged since
the Aug 10 checkout) into `docker compose run`'s `IMAGE_TAG`. Full chain,
evidence, and access-path notes: `ai/handoff/INCIDENT_LEGACY_FCB5E91_LAUNCHER.md`.

**Remediation (Phase B):** commented out (not deleted) both `deploy`-
crontab `watch-clank` lines, in the same style already used in that same
crontab for smartphone-clank's 2026-08-10 migration. To avoid hand-typing
symbol-heavy content through the console (which has a confirmed keyboard
bug dropping Shift on typed/pasted symbols — `_`/`:`/`@`/`>`/`|`/`*` all
land as their unshifted base character; new memory note added to the
operator's own quirks file), the corrected crontab file was written over
a normal, reliable SSH session instead, and the console operator ran a
single symbol-free `crontab -u deploy <path>` to install it. Verified by
`diff` against a full pre-change backup that these were the **only** two
lines touched — every other Clank's cron line (free-game-tracker,
oem-radar, semiconductor-intelligence, chinese-tech-wire, feature-phone-
clank, smartwatch-clank, the already-disabled smartphone-clank line) is
byte-for-byte unchanged. The wrapper, compose file, `.deployed-id`, and
the `watch-clank:fcb5e91` image were all deliberately left in place for
rollback/forensic comparison; none of the 17 current timers were touched;
no persistent state (volume, DB) was altered.

**Verification (Phase C):** full remaining scheduler inventory confirmed
clean (root's own crontab empty, no system cron/systemd, `deploy` cannot
run `systemd --user` at all — no linger, no session). Old launch window
(21:10 UTC) passed with zero trace (`docker ps -a` empty, no matching
`collector_runs` row). Natural current-architecture runs continued firing
correctly both before and after the fix (`casio_multi` id 139, RSS batch
ids 140-152, all `SUCCESS`). No duplicate/overlapping executions found
anywhere in the post-cutover window. Three-way provenance verified
identical: git SHA = OCI `image.revision` label = runtime
`get_identity()["source_revision"]`, all `c81ebedbae27b85381cb9b6372220dfd84ab04e2`.
Non-destructive DB integrity check (throwaway copy inside a disposable
container, named volume mounted read-only, never written back):
`PRAGMA integrity_check` = `ok`, schema at `007_specialist_lead_editorial_freshness`
(current), no stale/non-terminal rows beyond the pre-existing,
already-documented `casio_multi` PARTIAL / `citizen_de_products`
ZERO_ITEMS behaviors (untouched, out of scope).

**Not done, deliberately:** no P2 work (17-way same-minute fan-out,
snapshot-volume growth, env-file permission classification), no Casio
Japan backoff / Citizen DE ZERO_ITEMS / cadence / notification / schema
changes, no image deletion, no rebaseline, no touching any other Clank's
scheduler.

**Files:** `ai/handoff/INCIDENT_LEGACY_FCB5E91_LAUNCHER.md` (new). No
application code changed — pure operational remediation plus
documentation, per this task's own scope.

## 2026-08-15 — Emergency notification-path remediation: casio_multi fix + Hetzner Discord authority live

**Starting point verified:** HEAD `12e8d3e`, 232 tests passing, Ruff clean
— the prior sprint's own end state, plus a same-day Discord-authority
audit (chat-only, no code changes) that first surfaced this incident.

**Root cause, precisely** (full trace: `ai/handoff/INCIDENT_SILENT_SCHEDULED_NOTIFICATIONS.md`):
`scripts/run_pipeline.py::run_live_or_scheduled` is the single entrypoint
for both `--live` and `--scheduled` Casio runs — `scheduled` is a log
field only, both branches call the identical
`pipeline.run_multi_source_pipeline(max_items=max_items)`. That function
had no `emit_events`/`notify` parameter at all; its two internal calls to
`process_news_announcement`/`process_fetch_result` omitted the argument
entirely, silently taking each function's own `emit_events=False` default.
Result: **zero `Event` rows were ever possible from Casio's production
path**, on either invocation mode, since the function existed (Sprint 1).
This was a deliberate scope decision at the time (don't destabilize the
one working source while Discord/scoring infrastructure was unproven on
the "experimental" brands) that quietly became a real bug once every other
brand matured into a real, scheduled, systemd-timer production source
with correct event/notify wiring — Casio was simply never brought along.

**Audited every other scheduled entrypoint before touching anything**
(matrix in the incident doc): `run_brand_news_pipeline`
(citizen_news/seiko_jp_news/timex_news), `run_product_observation_pipeline`
(all 6 product lanes), and `run_publication_pipeline` (all 7 specialist
RSS lanes) were all **already correct** — `emit_events=True`/`notify=
emit_events` by default, or (for specialist leads)
`SpecialistLeadService.notify_new_lead`/`notify_correlation` called
unconditionally per lead with proper internal gating (baseline, freshness,
confidence, dedup, webhook). **Exactly one of 17 registered collectors was
affected.**

**Fix:** `run_multi_source_pipeline` gained `emit_events: bool = True`,
threaded to both its internal call sites — the same contract every other
production pipeline already used. No new "scheduler intelligence logic,"
no enum/mode invented, `scripts/run_pipeline.py` untouched (manual and
scheduled inherit the new default identically, preserving the property
that they can never diverge for this collector).

**Second defect, found while testing the first fix:** the notify
threshold for non-experimental (official) events was a hardcoded `100.0`
in both `_record_watch_event` and `_persist_product_event` — but
`score_event`'s real maximum for `NEW_REFERENCE`/`NEW_REGION` is 90 (all
five bonus conditions simultaneously, essentially never). **Discord could
never have fired for the official lane even with `emit_events` fixed.**
Replaced both literals with a new `Settings.discord_official_min_score`
(default `50.0`, matching `score_event`'s own HIGH-confidence cutoff —
deliberately stricter than the experimental lane's permissive
`discord_experimental_min_score=0`). Documented in `.env.example`.

**Third gap, caught by its own regression test before deployment:** the
first pass of the fix only updated the news-announcement call site; the
catalogue-enrichment (`process_fetch_result`) call site was still missing
`emit_events`/`notify`. `test_scheduled_casio_catalog_known_watch_new_
region_creates_event_and_notifies` failed (0 Events where 1 was expected)
immediately, fixed on the spot.

**Tests:** 232 → **238 passed** (6 new: Phase 4-A/B/D/E/F/G from the
remediation brief — scheduled new product creates Event + notifies;
scheduled catalogue new-region creates Event + notifies; active epoch
baseline stays silent; Casio's lack of an ISO-timestamp freshness gate
stated honestly rather than "fixed," since freshness semantics were
explicitly out of scope this sprint; notifier failure never fails the
run; no webhook never crashes). Phase 4-C (specialist lead) was already
covered by pre-existing tests — not duplicated. Ruff clean throughout.

**Silent-period audit** (`ai/handoff/HETZNER_SILENT_PERIOD_AUDIT.md`,
read-only, no historical row touched): **0 `Event` rows ever existed on
Hetzner to lose** (nothing was ever eligible, since nothing was ever
created), and **0 `SpecialistLead` rows were ever `FRESH`-eligible**
during the entire period — every lead present was either the deliberate
force-baseline set (9) or genuinely old backlog correctly excluded by the
pre-existing Sprint 8 freshness gate (50, sampled 2026-07-10 to
2026-08-08, all discovered for the first time the same day their sources
were onboarded). **Nothing useful actually clanked into the void** — the
cost of both defects was structural/prospective, not a backlog of real
missed stories.

**Deployed to Hetzner from GitHub, no manual file copying:** `git pull`
on the existing clone (`c474e75`→`c81ebed`), rebuilt the image
(`watch-clank:c81ebed`, label-verified), updated
`~/.config/watch-clank/docker.env`'s image tag (no systemd unit changes
needed — units already reference the tag indirectly). Triggered one real
`casio_multi` run via the actual installed systemd service before and
after configuring secrets — clean both times, 0 new leads/watches (nothing
genuinely new existed in the news feed at the time; per this sprint's own
acceptance criterion, zero notifications when nothing is new **is**
correct — the test is that the path is *capable* of notifying, which the
regression suite proves directly).

**Discord secrets configured** (owner-supplied, two real webhook URLs,
correctly disambiguated via an explicit clarifying question before writing
anything): `~/.config/watch-clank/secrets.env` on Hetzner, mode `600`,
machine-local, never printed, logged, committed, or included in any
tracked systemd unit. `EDITORIAL_NOTIFICATIONS_ENABLED=true` set
alongside. Verified via the real `-e VARNAME` pass-through mechanism the
installed systemd units actually use (not a shortcut `--env-file`):
`editorial_webhook_configured=True`, `health_webhook_configured=True`,
`editorial_enabled=True`, `health_enabled=True`,
`discord_official_min_score=50.0`. **One clearly-labeled configuration-
test message delivered successfully to each channel** (editorial + health,
both explicitly say "CONFIGURATION TEST... not a real watch alert/ops
failure") — both returned delivery success. No fake DB `Event` was
created for this; the messages were sent directly via `DiscordNotifier`,
bypassing persistence entirely, per the explicit instruction not to
manufacture production events.

**No duplicate-sender risk:** the still-unlocated old invisible
`casio_multi` invocation mechanism (root's crontab, most likely — still
never found, no root access attempted) runs image `watch-clank:fcb5e91`,
built **2026-08-10**, which **predates Sprint 2** (2026-08-11, when the
Discord notification system was first written) entirely — that image's
code cannot send a Discord message under any configuration, because the
code to do so doesn't exist in that build. Hetzner is now unambiguously
the sole environment capable of sending, and is now actually configured
to do so.

**Windows:** unreachable this session, same as every session since before
the Hall of Shame sprint (away until 2026-08-18) — reported honestly as
NOT VERIFIED, not assumed. Once reachable, its own editorial webhook (if
any was ever configured there — never confirmed either way) must be
disabled/unconfigured so Hetzner remains the sole authoritative sender.

**Final health (Hetzner):** all 17 sources HEALTHY, schema at head, DB
integrity `ok`, 0 stale `RUNNING` rows, 0 active locks, `total events: 0`
(no flood from any of this sprint's verification runs), deployed image
label matches GitHub HEAD (`c81ebedbae27b85381cb9b6372220dfd84ab04e2`)
exactly.

**Not done, deliberately, per explicit scope constraints:** no new
sources, no new brands, no freshness-semantics changes (Casio's own lack
of an ISO-timestamp gate was documented, not fixed — matching Citizen/
Seiko's identical, already-accepted exposure), no Citizen UK work, no
Reddit, no UI redesign, no SSH/fail2ban changes, no SQLite sync, no replay
of historical leads/events into Discord.

## 2026-08-14/15 — Post-Hall-of-Shame implementation sprint: Hetzner redeploy, Seiko JP collector, UK research

**Starting point verified:** HEAD `c474e75` (== `origin/main`, clean tree),
221 tests passing, Ruff clean — the prior sprint's own end state. This
sprint implements its three named follow-ups.

### 1. Seiko Japan retail-store collector (`seiko_jp_products`)

`store.seikowatches.com` confirmed live, from two independent vantage
points (Hetzner Helsinki, this session's macOS host) — HTTP 200, **not**
geo-blocked, contradicting the working assumption carried forward from the
prior sprint. Real Shopify JSON catalogue, 959 watches (later 956-959 on
re-checks — real-world drift), no `product_type` filtering needed (this
store sells nothing else). Built mirroring `seiko_products.py`/
`timex_products.py` exactly, with this sprint's discovery-cap
delta-prioritization fix (`known_product_urls`) built in from day one
rather than retrofitted later. HBC008J/HBC009J (Hall of Shame Case 12)
both live-verified: JPY 155,100 each, matching Notebookcheck exactly,
`予約購入ボタン` (preorder) tag present. 7 new tests. Full detail:
`ai/handoff/SEIKO_JP_COLLECTOR.md`.

### 2. Casio UK sitemap-delta collector (`casio_uk_sitemap`)

Casio UK product pages remain Cloudflare-403 (resistant to browser-like
headers, not attempted to bypass). **`www.casio.com/uk/sitemap.xml` is
not blocked** — HTTP 200, explicitly published in Casio's own robots.txt,
no ClaudeBot-specific restriction anywhere in that (very long) file. Real
capture: both `GD-350S-1` (Case 8) and `F-B100W-1A`/`-3A` (Case 5) present
with real `<lastmod>` timestamps matching their editorial dates almost
exactly. Built a sitemap-only collector — **honest, load-bearing
limitation**: no price/currency/availability exists in a sitemap, so this
source can only ever produce `NEW_REFERENCE`/`NEW_REGION`, never
`PRICE_CHANGE`/`SOLD_OUT`/`RESTOCK` (the existing transition classifier
safely no-ops on all-`None` price/availability pairs — no new event type
was invented). 4 new tests including the mandatory GD-350S-1 regression
(`test_casio_uk_sitemap_known_gd350s1_from_japan_emits_new_region`).

### 3. Citizen UK — researched, correctly left undone

Product pages Cloudflare-403 (unchanged). **Citizen's own robots.txt
explicitly disallows `ClaudeBot` by name** (modern Content-Signal format,
alongside GPTBot/Amazonbot/CCBot/etc.) — a second, independent, decisive
reason beyond the technical block. `citizenwatch.eu` (the platform
`de.citizenwatch.eu` already uses) was investigated as a possible
alternative: real, accessible, 399 products — but confirmed **EUR-priced**
regardless of `Accept-Language`/`?currency=GBP` (tested both), so it was
explicitly *not* repurposed as fake UK/GBP evidence. No collector built.
Full research trail, including the Casio findings above:
`ai/handoff/UK_SIGNAL_PATH_RESEARCH.md`.

### 4. Hetzner redeployed to current HEAD

Full detail: `ai/handoff/HETZNER_DEPLOYMENT.md`. Summary: verified backup
taken and integrity-checked before touching anything; cloned from GitHub
directly on the host (`~/watch-clank`, no manual file copying); built
`watch-clank:d0ee4e9` on the host; migrated the existing volume in place
(`003_release_leads` → `007_specialist_lead_editorial_freshness`, 22
pre-existing watches preserved exactly); force-baselined every one of the
16 sources new to this DB (`casio_multi` was **not** baselined — it
already had real, non-baseline history here and kept it); repeat-verified
0 new watches/events across all 16 before enabling anything; discovered
`anilganti` is already in the `docker` group (no sudo needed) and that
`loginctl enable-linger` works for one's own account without root even
though `sudo -n` does not; generated and installed **real, working
`systemctl --user` timers** for all 17 collectors via a new registry-driven
generator (`scripts/systemd/docker/render_units.py` — reads the same
`collector_registry.py`/`health.py` the web dashboard uses, so nothing
here can silently drift from what's actually registered); verified one
unit manually end-to-end via `journalctl` before enabling the rest; final
state confirmed via `scripts.status`: schema OK at head, DB integrity OK,
4177 total watches, 0 stale RUNNING rows, 0 active locks, **all 17 sources
HEALTHY**. Every Hall-of-Shame reference across all 12 cases (including
GD-350S-1, F-B100W, HBC008J/HBC009J, NJ0238-57E, the three Dress Classic
UK refs, and both Nighthawk refs) confirmed present in the live deployed
database by direct query.

**Known, disclosed limitation:** the original invisible `casio_multi`
invocation mechanism (most likely root's crontab, never confirmed — no
root access available or attempted) was not found or disabled. It is
likely still firing independently on its own ~90-minute cadence against
the same (now-migrated) volume. This is safe — the migrations were purely
additive, and `RunLockService`'s per-`collector_id` lock means any overlap
resolves as `SKIPPED_OVERLAP`, never corruption — but redundant. Flagged
as the top remaining follow-up for whoever has root on that box.

**Discord authority: unchanged, unambiguous.** No webhook configured on
Hetzner (none existed to carry forward); every generated unit references
the webhook env vars via pass-through only, so with no `secrets.env` they
resolve empty and `DiscordNotifier.editorial_enabled` stays `False` by
construction. Not flipped to Hetzner-authoritative this sprint — the
explicit precondition "Windows can be disabled as editorial sender
without losing collection" could not be verified (Windows unreachable
this session, same as the prior sprint, away until 2026-08-18).

**Tests:** 221 → **232 passed** (11 new: 7 Seiko JP + 4 Casio UK). Ruff
clean throughout (one import-order auto-fix each round). No existing test
was weakened; all Hall-of-Shame regression specimens from the prior sprint
still pass unmodified.

**GitHub:** two commits this sprint, both pushed and remote-verified —
`c474e75..d0ee4e9`. Ending local/remote HEAD: `d0ee4e9`. (The Hetzner
deployment itself does not change GitHub state; it deploys what was
already pushed.)

**Not done, deliberately:** no Citizen UK collector (see above); no
Reddit/community source; no Orient Star; no UI redesign (existing generic
event rendering already handles every new source's output — verified, not
assumed); root's crontab was not located, touched, or disabled.

## 2026-08-14 — Hall of Shame forensic + remediation sprint

**Starting point verified:** HEAD `22da9bd` (== `origin/main`, clean tree),
215 tests passing, Ruff clean, Epoch 1 LIVE. `HANDOFF.md` had no checkpoint
entry at all for 7 real commits between Sprint 11 and the mac-launchers
commit (`6b6e4ef`..`5e1b500`) — reconstructed from `git log`, diffs, and
`ai/handoff/CITIZEN_REGIONAL_AUTOPSY.md`/`SPECIALIST_SOURCE_EXPANSION_SPRINT14.md`,
which turned out to already document most of it. That reconstruction is now
folded into the header summary above and into the autopsy doc below —
treat this paragraph as the pointer, not a duplicate of the full history.

**Why this sprint exists:** the operator supplied 12 real competitive
outcomes against Notebookcheck's coverage ("the Hall of Shame") and asked
for forensic reconstruction — genuine failure vs. expected behavior vs.
already-fixed — followed by the smallest safe fix that eliminates the most
failures, before traveling with the system unattended. Full methodology,
per-case ledger, taxonomy, root-cause matrix, and the "does this watch
exist vs. what just changed" hypothesis test are in
**`ai/handoff/WATCH_CLANK_HALL_OF_SHAME_AUTOPSY.md`** (read that document
for the real detail; this checkpoint only summarizes outcomes).

**Verdict on the 12 cases:** 2 grandfathered/expected (Timex E Line 30mm,
Peanuts x Timex — onboarding baseline working as designed), 2 already
remediated pre-sprint and reverified live (Citizen Tsuyosa `NEW_REGION`
mechanism; Seiko Bonsai HCC009J1 via the four specialist RSS sources added
between Sprint 11/12), 1 inconclusive without a reference number (Citizen
mother-of-pearl), 1 corrected from the brief's own assumption with live
evidence (Seiko JP retail store is **not** geo-blocked from Hetzner — a real
missing-capability finding, not a technically-inaccessible one), and **6
genuine coverage/architecture gaps** — of which 2 (Citizen Nighthawk US,
Q Timex Continental) were root-caused to a single mechanical bug and fixed
this sprint; the remaining 4 trace to Casio/Citizen having no accessible UK
collector (Cloudflare 403, verified live, not attempted to bypass) or no
Casio product collector anywhere at all.

**Root cause found and fixed (`app/services/pipeline.py`):**
`PipelineService._record_product_transition`'s `is_new_watch` branch
returned `{"event_type": None, "reason": "baseline_new_watch"}`
**unconditionally** — even outside any active baseline. A brand-new SKU
discovered *only* through a product catalogue (no matching news
announcement) got a `Watch` row and a `SourceObservation` but **no Event,
ever** — not `NEW_REFERENCE`, nothing. Live-reproduced against this
session's own dev database: Citizen Nighthawk (`CA0890-54H`/`CA0897-04H`)
existed as real, non-baseline watches with zero linked events before the
fix. Fixed by emitting a real `NEW_REFERENCE` event from that branch
(reusing the existing `_persist_product_event`/scoring/eligibility/Discord
plumbing already proven for `NEW_REGION`), still fully guarded by the
pre-existing `force_baseline`/epoch-baseline checks that run first — inert
for every watch already in any production database, since `is_new_watch`
is only ever `True` at first-ever creation.

**Second, compounding root cause found and fixed:** `timex_products`'
real catalogue is 1606 items; the collector's `max_items=300` default
applied a **blind positional slice** on every non-baseline run —
Shopify's default `/products.json` sort order is not confirmed
recency-based, so genuinely new SKUs sorting past position 300 could be
silently skipped forever (**~81% of the real catalogue never processed**
on any routine run). Citizen's real catalogue (465 items live) exceeds its
own 300 cap too (~35% excluded). Fixed by having both collectors accept an
optional `known_product_urls` set and prioritize not-previously-seen URLs
ahead of already-known ones before applying the cap — the exact
sitemap-delta pattern `citizen_de_products` already used, now reused
rather than reinvented. Seiko's real catalogue (222–276 items) is already
fully under its cap; left unchanged.

**Tests:** 215 → **221 passed** (6 net new: catalogue-discovery event
regression x3, collector-level delta-prioritization x3; 7 pre-existing
tests updated in place to assert the corrected — not weakened — behavior,
since a first-ever product sighting now legitimately produces one more
real event than before). `ruff check .`: all checks passed.

**Live validation (isolated throwaway DB, never any production/dev DB):**
`--experimental-product timex --force-baseline` → 1445 new watches, 0
events (baseline still silent). Immediate repeat, no `--force-baseline` →
0 new watches, 0 events (real steady-state stability — no flood, no
churn). Same pattern for Citizen: 465 new watches on baseline, 0/0 on
repeat. `PRAGMA integrity_check` = `ok` both times. Confirmed via template
inspection that Recent Intelligence renders the new catalogue-sourced
`NEW_REFERENCE` events correctly despite their different `extra` shape
(no `announcement_url`/`lead_id`) — no UI change needed.

**Hetzner (Phase 13-16, read-only via the `anilganti` SSH user, which
turned out to already be in the `docker` group — no sudo/password
needed):** the `watch-clank:fcb5e91` image found by Sprint 12 is **still
the one actually running**, confirmed live — `casio_multi` SUCCESS every
~90 minutes through today (2026-08-14 16:45 UTC, 10 discovered), schema
still pinned at `003_release_leads` (no `specialist_leads`, no
`operational_epochs`), 22 watches, 0 events ever. No systemd unit or timer
for watch-clank exists (confirmed via `systemctl list-units`/`list-timers`
— only `smartphone-clank` has real units); the invocation mechanism is
still not visible without root (most likely `docker run --rm` in root's
crontab, per Sprint 12's own hypothesis — still unconfirmed, root access
was not attempted). **Deliberately not redeployed** — bringing it to
current HEAD needs 4 migrations plus the documented multi-source
force-baseline sequence for every collector added since Sprint 5, which is
its own project, not a same-night patch. See
`ai/handoff/REMEDIATION_PLAN.md` for the full reasoning and the explicit
top-priority follow-up this leaves.

**Discord authority:** unchanged, and correctly so — Hetzner does not meet
any of the four preconditions (verified running current collectors,
correctly baselined, fresh-event generation verified, dedup verified) for
becoming authoritative. Windows' status could not be checked this session
(unreachable, away until 2026-08-18). No webhook secrets were read, echoed,
or committed at any point.

**Travel-safe state:** full test suite green, Ruff clean, DB integrity
`ok` on every database touched, no code left half-migrated. One stale
`RUNNING` row + orphaned lock file was found on this session's own macOS
dev database (`citizen_de_products`, started earlier today, no process
actually running) — recovered as part of this sprint's own hygiene, see
below; this was local dev state, not any production database, and does not
affect Windows or Hetzner.

**Not done, deliberately (see `ai/handoff/REMEDIATION_PLAN.md` for full
ranking):** no new regional collector built (Casio UK/global, Citizen UK,
Seiko JP store — all real, ranked candidates, none attempted); no Hetzner
redeploy; no Reddit/community collector (research-only, as instructed); no
Orient Star; no UI redesign (existing generic event rendering already
handles the new event shape).

**Starting point verified:** HEAD `01ecb9b` (macOS mac/ launcher scripts added same day, no application code changed by that commit), 199/199 tests passing, Ruff clean. Working from a fresh macOS clone (`/Users/anilganti/Clank base/watch-clank/`), Windows machine unavailable this session (returns 2026-08-18).

**Why this sprint exists:** the browser dashboard had not been touched since roughly Sprint 2-3 era (stale copy: "Accessible Casio news collectors will populate this.") while `local_windows/control_centre/` (Tkinter, six tabs) had absorbed most of the operational UI investment through Sprint 6-11. The web app queried `ReleaseLead`/`SourceComponentState` directly and had never been wired to `Event`, `SpecialistLead`, `OperationalEpoch`, or `app.services.health` at all — meaning it could not show official events, early-warning leads, or agree with the Windows GUI about what "healthy" means.

**Important limitation, disclosed honestly:** `local_windows/control_centre/` is genuinely never available to a session working from this repo — it's `.gitignore`'d **by policy** (see Sprint 6: "local-only, NOT pushed"), not merely unsynced. Everything in this sprint was built from this document's own sprint-by-sprint description of that GUI's behavior plus direct inspection of the shared backend it calls into (`app.services.health`, `app.services.discord_notify`, the model layer) — never from reading the Windows GUI's actual source, which was not an option regardless of Windows machine availability.

**Real, previously-undocumented finding on Hetzner (Phase 15):** contrary to every prior sprint's honest "no Hetzner deployment could be verified" disclosure, a `watch-clank` deployment **is live-soaking on Hetzner right now**, unattended, via a cron mechanism not visible without root access (no systemd unit, no crontab entry visible to the `anilganti`/`deploy` users, no docker-compose file found — most likely `docker run --rm` in root's crontab). Verified read-only:
- Image `watch-clank:fcb5e91` = commit `fcb5e918`, **2026-08-10**, "Merge pull request #3 from docs/flock-lock-finding" — predates Sprint 5 (specialist leads), Sprint 7 (Epoch/baseline), Sprint 8 (freshness fix), and Sprints 9-11 (Timex) entirely.
- The volume's DB confirms this: `specialist_leads` and `operational_epochs` tables **do not exist** (pre-migration-004 schema).
- `casio_multi` has run SUCCESS roughly every 90 minutes continuously through today (last observed: 2026-08-13 13:45 UTC, host time 14:22 UTC at check time). 22 watches, 10 release leads, 54 collector runs, `PRAGMA integrity_check` = ok, 0 events fired so far.
- **Left running untouched per explicit owner instruction** — this document is the record of the finding; redeploying/upgrading it is a separate, deliberate decision for a future session, not performed here. No production DB write, no container restart, no redeploy.

**Web application changes — new/rebuilt pages, all on top of existing shared backend, no collector/parser/scoring logic touched:**

- **Instance label (Phase 1):** new `Settings.watch_clank_instance` (env `WATCH_CLANK_INSTANCE`, default empty → renders honestly as `UNLABELED`, never inferred from hostname). Shown in the header on every page. `DiscordNotifier.notification_authority()` derives `WINDOWS`/`HETZNER`/`UNLABELED`/`NONE` from the label + existing `editorial_notifications_enabled`/webhook-configured state — `UNLABELED` is a deliberately distinct, loud state from `NONE` (an unlabeled host that would actually send alerts is a real misconfiguration risk, never silently folded into "nothing to see here").
- **Overview (`/`, Phase 3):** rebuilt on `app.services.health.get_health_snapshot()` — the same function `scripts/status.py` and the Windows GUI's RUN HEALTH CHECK use, so the web page can no longer disagree with either about source health (the old page queried `SourceComponentState` directly, a second, divergent source of truth). Manufacturer breakdown is a live `GROUP BY Watch.manufacturer` query, not a hardcoded brand list — Orient Star will not appear until it's a real collector with real rows. "Fresh intelligence" count = FRESH specialist leads + official events (Events don't carry `editorial_freshness` by design — see `freshness.py`'s own docstring on why they don't need it).
- **Recent Intelligence (`/intelligence`, Phase 4 — the highest-priority page):** Official Events (filtered through the existing `event_row_is_editorially_eligible` read-side check, reused rather than reimplemented) and Specialist Leads (`editorial_freshness == FRESH` by default) shown as two visually distinct sections. `?show=historical` reveals STALE_PUBLICATION/BASELINE/UNKNOWN_TIMESTAMP/MANUAL_UNDATED leads explicitly — never mixed into the default view. This is the exact protection Sprint 8 built; see `tests/test_web.py` for regression coverage proving the Sprint 8 incident class cannot recur through this page.
- **Operations (`/operations`, Phase 5) + RUN NOW / RUN ALL SAFE COLLECTORS (Phase 6):** new `app/services/collector_registry.py` maps all 15 `health.py` `KNOWN_COLLECTORS` to their exact `scripts/run_pipeline.py` CLI invocation (verified against each collector module's real `COLLECTOR_ID` constant and `run_pipeline.py`'s actual argument dispatch, not guessed from naming convention) — with an import-time consistency check against `KNOWN_COLLECTORS` that fails loudly if the two ever drift, the exact class of bug that made the Windows GUI need manual buttons added after real source drift. RUN NOW shells out to `python -m scripts.run_pipeline` (never duplicates collector/pipeline logic in the web layer), so `RunLockService` overlap protection, baseline/freshness semantics, and Discord authority are all inherited automatically, not reimplemented. Live-verified end-to-end: triggered a real CASIOBLOG RUN NOW, confirmed the resulting leads appeared correctly in Recent Intelligence.
- **Health / Diagnostics (`/diagnostics`, Phase 7 + Discord status, Phase 10):** per-source state/heartbeat/last-success/last-failure/last-item-count, all human-readable and timezone-labeled (new `Settings.display_timezone`, default UTC), never a bare ISO string. Discord section shows only safe booleans (`editorial_enabled`, `editorial_configured`, `health_configured`, `notification_authority`) — the actual webhook URL is never read into a template context anywhere in this sprint's code, and `tests/test_web.py::test_discord_webhook_secret_never_rendered` proves it with a real fake secret string asserted absent from the response body.
- **Run History (`/runs`, Phase 8):** added source/status/official-vs-specialist filters (driven by the same `collector_registry.py`), human timestamps, duration in seconds instead of raw milliseconds.
- **Scheduler (`/scheduler`, Phase 9):** DERIVED view (last run + `EXPECTED_CADENCE_MINUTES` → next expected run), always shown, always honestly labeled `DERIVED`, on every host. A live Windows Task Scheduler section (`schtasks /query`) is included but gated on `platform.system() == "Windows"` and falls back to nothing (not an error) on any other host — **this branch is unverified**, written from the 10 known task names in this document, never run against a real Windows Task Scheduler in this session. Needs a real check once Windows returns.
- **Web security (Phase 11):** this app has **no authentication whatsoever** — unchanged this sprint, not something invented mid-sprint per the brief's own instruction. `mac/dashboard` already binds `127.0.0.1` only; RUN NOW and RUN ALL SAFE COLLECTORS additionally add `_require_loopback()` as defense-in-depth inside the route handlers themselves, so a future `APP_HOST` change can't silently turn collector execution into an unauthenticated remote endpoint. Proven with a real test (`test_run_now_rejects_non_loopback_client`) — TestClient's default non-loopback host gets a real 403. **This app must not be exposed beyond localhost/private access until real auth exists — that is out of scope for this sprint and was not built.**

**New model relationship (no migration):** `EventWatch.watch` (viewonly, uses the pre-existing `watch_id` FK) — needed so Recent Intelligence can render manufacturer/reference per event without an extra query per row. `Watch` gets no reverse relationship; nothing else currently needs one.

**Tests:** 199 → **215 passed** (16 new, `tests/test_web.py`), Ruff clean. Covers: stale/baseline specialist evidence excluded from default Recent Intelligence, fresh evidence appears, official events appear, historical evidence stays reachable via `?show=historical`, publication time never replaced by discovery time, Operations lists every registered collector, RUN NOW rejects an unknown collector_id and rejects non-loopback callers, Discord secrets never render, `humantime` never renders a bare ISO string, empty-DB and populated-DB rendering, instance label never guessed, run-lock state reflected on Operations.

**Real-scale validation (Phase 14):** triggered RUN ALL SAFE COLLECTORS itself (not a synthetic script) against a fresh isolated local dev DB — all 15 registered collectors, real live network sources, sequential. See this sprint's final report for the resulting counts; this both validated UI rendering at real scale and end-to-end proved the RUN ALL SAFE COLLECTORS deliverable itself, rather than treating them as separate concerns.

**Deliberately not done this sprint (per explicit brief instructions):** no Orient Star/Reddit/new-publication collectors, no freshness-semantics changes, no database sync between Windows/Hetzner, no deletion of historical evidence, no fabricated test intelligence in any real database, no auth system invented, no Hetzner redeploy/restart, no SSH hardening changes, no Logs page (not in the brief's required top-level area list — see final report's gap list).

## 2026-08-12 (Sprint 11) — Timex miss autopsy, real recall fix, dual-runtime scaffolding

**Starting point verified:** clean tree, 175/175 tests passing (before this
sprint's additions), Ruff clean, Epoch 1 LIVE, 10 Windows tasks
Ready/Enabled, 0 stale locks. 1463 Timex watches, 0 Timex-linked Events.

**Why this sprint exists:** real Timex launches were published by the
user's Notebookcheck colleagues while Watch Clank surfaced nothing — a real
production false-negative, not a hypothetical. Full autopsy in
`ai/handoff/TIMEX_MISS_AUTOPSY.md`.

**Root cause (confirmed, not guessed):** three real NBC articles (MK1
Chronograph, Todd Snyder x Timex Marlin Mesh, E Line Automatic) were
verified end-to-end. In all three, Watch Clank's own `timex_news` blog feed
had the story 1-2 days *before* Notebookcheck published — this was never a
discovery-latency or source-coverage gap. Two confirmed, evidence-based
bugs, found by directly reading the real stored blob content off disk:

1. **PARSER_FAILURE**: the real SKUs are present in every post, but only
   inside Shopify CDN image filenames (e.g.
   `..._TX_TC_26_PFB_TW2Y71200_3_600x600.jpg`), never in prose — the
   existing `MODEL_RE`'s `\b` word boundary can't match past the leading
   `_`. The actual shoppable-product widget is stripped to a bare
   `[SHOPPABLE_PRODUCT_BLOCK]` placeholder by Shopify's Atom feed
   generation, so there's no href to recover either.
2. **A second bug, found only by live validation against a copy of the
   production DB** (not by tests): the image-filename SKU never carries
   the catalogue's trailing variant suffix (`TW2Y71200` vs the catalogue's
   real `TW2Y71200VQ`). Fixing bug 1 alone, live, fired 7 unwanted
   `NEW_REFERENCE` events and created 24 duplicate watches on the first
   validation pass — caught before touching production.

**Fix:** `IMAGE_SKU_RE` added to `app/parsers/timex_news.py` (anchored on
the real, confirmed Shopify filename convention, not a loosened `\b` —
zero risk of false positives in prose). Timex-scoped prefix-match fallback
added to `_resolve_or_create_watch` in `app/services/pipeline.py` — only
auto-links when the prefix resolves to exactly one existing watch; zero
behavior change for Casio/Citizen/Seiko or for ambiguous/zero matches.

**Live proof, twice:** ran the real fix against an isolated copy of the
production DB first (0 events, 0 duplicates, correct `watch_ids` linkage
on the second attempt), then applied it to the real production DB with
identical clean results (`data/watch_clank.db`, run ids 105-106), then
repeated once more for stability (0 new leads/watches/events on the
repeat). MK1 Chronograph and Marlin Mesh leads now correctly link
`watch_ids` to their real catalogue watches (652/653 and 658-660) instead
of staying orphaned. Total watches 2020 → 2027 (6 genuinely new-to-Clank
SKUs surfaced from older June leads, all correctly gated as historical —
0 events).

**No new source added.** NBC's own cited sources for the verified misses
were Timex's own product pages, not a third-party specialist — the brief's
bar for adding a source ("repeatedly early") was not met by anything found
this sprint. Three additional search hits (Capstone/Dylan/Huckberry) had
Notebookcheck article IDs ~200K lower than the verified three, strong
evidence they're older archived content, not genuine recent misses — flagged
as unresolved rather than treated as confirmed gaps.

**Regression:** 5 new tests (real MK1/Marlin Mesh image-filename extraction
proof from the live fixture, false-positive substring guard, the exact
dedup bug as a permanent regression). 175 → 176. Ruff clean (one import-order
autofix). Pre-existing Sprint 10 freshness-gate tests still pass unchanged.

**Dual-runtime (Phase 10-16):** Windows confirmed unaffected — all 10 tasks
still Ready, `data_access.py` still never imports `app.services.pipeline`
(no EXE rebuild needed, same fact as Sprint 10). Added the systemd unit
templates that were missing for Hetzner (`watch-clank-casioblog`,
`-gcentral`, `-plus9time`, `-timex-news`, `-timex-products` — mirroring
each Windows task's exact cadence and CLI args) plus a documented
`--force-baseline` pre-flight sequence for a brand-new cloud DB in
`scripts/systemd/README.md`. Documented (not built — needs no code, it's
already `.env`-driven) the Discord notification-authority policy: Hetzner
as future sole authority, Windows kept webhook-unconfigured. **No actual
Hetzner deployment was performed or could be verified — no SSH/host access
in this session, same honest disclosure as every sprint since Sprint 5.**
This section documents exactly what must happen on that host, not a claim
that it has happened.

**Final state:** 176/176 tests passing, Ruff clean, schema at head
(`007_specialist_lead_editorial_freshness`), DB integrity OK, all 10
sources HEALTHY, 0 stale locks.

## 2026-08-12 (Sprint 10) — Timex historical-freshness hardening

**Starting point verified:** HEAD `0ccf8b5`, clean tree, 166/166 tests
passing, Ruff clean, Epoch 1 LIVE, 10 Windows tasks Ready/Enabled, no
locks/stale RUNNING rows.

**Real gap found (audited, not assumed):** Sprint 8's freshness fix
(`SpecialistLead.editorial_freshness`) only covers Layer B specialist
sources. `ReleaseLead` (Layer A official news, including `timex_news`)
has no freshness column at all, and `_record_watch_event` — the method
that turns a `ReleaseLead` into a `NEW_REFERENCE`/`NEW_REGION` `Event` —
had zero publication-age gate. Only `is_baseline_active()`/`force_baseline`
suppressed it, which is epoch-scoped, not article-age-scoped. A genuinely
old official article, first discovered after baseline, would have fired a
real Event purely from discovery novelty. Full trace and live feed audit
in `ai/handoff/TIMEX_FRESHNESS_AUDIT.md`.

**Audit findings (live, 2026-08-12):** `timex_news`'s Atom feed currently
exposes 30 entries (2026-06-01 through 2026-08-11), confirmed to have no
real pagination (`?page=N` returns identical results regardless of N) —
so an already-rolled-off article cannot currently be rediscovered through
this endpoint; Phase 5's regression test simulates the scenario anyway,
per the brief's explicit instruction, since it's the class of bug being
hardened against, not a claim about today's specific feed shape. 0 NULL
`published_at` observed across all 30 live entries.

**Fix:** new `_ISO_TIMESTAMP_NEWS_SOURCES = frozenset({"timex_news"})` and
`_stale_official_announcement()` in `app/services/pipeline.py`, wired into
`_record_watch_event` only. Deliberately source-scoped: Casio ("July 15,
2026"), Citizen ("23 July 2026", sometimes "2 July2026" with no space),
and Seiko ("January 07, 2026") all store free-text `announcement_date`
strings, confirmed live to safely and predictably fail a strict
`datetime.fromisoformat()` parse — so they're structurally unaffected by
this hardening, not merely "expected to be." `_record_product_transition`
(RESTOCK/SOLD_OUT/PRICE_CHANGE/AVAILABILITY_CHANGE) and the catalogue
NEW_REFERENCE path were not touched at all — product/catalogue freshness
semantics remain exactly as before.

**Live proof, not just tests:** re-ran `timex_news` with `max_items=30`
(Sprint 9's baseline used the default 15) against the real production DB.
13 more real articles, genuinely new to Clank, surfaced (published
2026-06-01 through 2026-07-03) — **this is the exact bug-class scenario,
caught live, not simulated.** Result: 13 new `ReleaseLead` rows stored as
real historical evidence, 0 new Events. `scripts/status.py`'s `Latest
official event` timestamp did not move.

**Regression:** 6 new tests (real-historical-entry proof using the actual
"Todd Snyder x Timex Marlin Mesh" fixture entry, fresh-article proof,
NULL-timestamp proof, future-rediscovery simulation, Casio/Citizen
unaffected proof, product-transition unaffected proof). Test count 166 →
172. Ruff clean. Full existing suite (Casio/Citizen/Seiko/CASIOBLOG/
G-Central/Plus9Time/product-transitions) unaffected by construction —
`_record_product_transition` and every non-Timex `_record_watch_event`
call site are untouched code paths.

**GUI/EXE: no rebuild.** Verified `local_windows/control_centre/
data_access.py` imports only `app.core.config`, `app.db.session`,
`app.models`, `app.services.health` — never `app.services.pipeline`
(where this sprint's only code change lives). `runner.py`'s RUN NOW
buttons shell out to the live venv Python as a subprocess (always current
source, never bundled/frozen). The existing Sprint 9 EXE was launched
unmodified and confirmed correct against the current live DB (2020
watches, unmoved `Latest official event`, all 10 sources healthy) —
proof the "no rebuild needed" claim is verified, not assumed.

**No schema/migration change** this sprint — the fix lives entirely in
application logic, no new columns needed since `ReleaseLead`/`Event`
already carry everything used (`announcement_date`, and the decision is
made at creation time, not stored as a new state).

## 2026-08-12 (Sprint 9) — Timex as fourth official brand

**Starting point verified:** HEAD `23678d1`, clean tree, 165/165 tests
passing, Ruff clean, Epoch 1 LIVE, 8 Windows tasks Ready/Enabled, no
locks/stale RUNNING rows, DB integrity ok.

**Reconnaissance:** timex.com runs on Shopify (confirmed via its own
sitemap index shape -- `sitemap_products_N.xml`/`sitemap_collections_N.xml`
/`sitemap_blogs_1.xml`), same class of infrastructure already proven safe
for Seiko USA (Sprint 3). `/products.json?limit=250&page=N` is real and
public: 1606 total products across 7 pages, 1445 of them `product_type ==
"Watch"` (rest are straps/giftsets/protection plans), each with a real
per-variant SKU (e.g. "TW6A01000VQ" -- Timex's genuine canonical
reference), price, availability, and `published_at`. Currency confirmed
"USD" via the store's own `Shopify.currency` JS state. Separately, Timex's
official Shopify blog Atom feed (`/blogs/the-timex-blog.atom`) turned out
to be a genuine product-announcement feed, not generic lifestyle content --
real recent entries: "New For Fall: The E Line Returns In 30mm And 38mm",
"Q Timex Marbella: A Bolder Sense Of Time", a real collaboration ("Todd
Snyder x Timex Marlin Mesh"). Both lanes implemented: `timex_products`
(Shopify JSON catalogue) and `timex_news` (Atom feed, no per-item detail
fetch needed since the feed already carries full content).

**Implementation:** reused the existing generalized architecture exactly
as instructed -- `normalize_timex_reference` registered in `_NORMALIZERS`,
`timex` registered in both `_PRODUCT_REGISTRY` and `_BRAND_REGISTRY`. No
parallel Timex-specific pipeline. New parsers/collectors mirror
citizen_products.py/seiko_products.py/seiko_news.py's proven shape.

**Source-scoped silent baseline (the hard part):** Timex joined an epoch
whose baseline window had *already completed* (Casio/Citizen/Seiko went
live in Sprint 7). Reopening the epoch's global `baseline_started_at`/
`baseline_completed_at` window was rejected as unsafe -- it would have
silently suppressed events for any *other* source's concurrent scheduled
run too, not just Timex's. Instead added a new `force_baseline: bool`
parameter threaded through `_epoch_fields`/`process_fetch_result`/
`process_news_announcement`/`_record_product_transition`/
`_record_watch_event`/`run_product_observation_pipeline`/
`run_brand_news_pipeline`, plus a `--force-baseline` CLI flag -- a
per-call override, completely independent of the epoch's own lifecycle.
Proved this is genuinely source-scoped with a dedicated test
(`test_force_baseline_is_source_scoped_not_global`): Timex populated with
`force_baseline=True` in the same session as a normal (no force_baseline)
Citizen news announcement in the same live epoch -- Citizen's event fired
normally, proving Timex's baseline flag never leaked.

**Live results (real production DB, tasks paused during the operation,
re-enabled after):** Timex products baseline: 1445 new watches, 0 events.
Timex news baseline: 15 new leads, 5 new watches, 0 events. Immediate
repeat run (both with and without `--force-baseline`): 0 new watches, 0
new leads, 0 events, across both lanes, both times. Total DB watches
557 → 2010 (1453 of them Timex). Casio/Citizen/Seiko/specialist sources
confirmed unaffected throughout (`Latest official event` timestamp never
moved during any Timex operation).

**Bonus fix (same area, low-risk):** found `app/services/health.py`'s
`KNOWN_COLLECTORS`/`EXPECTED_CADENCE_MINUTES` had never been updated for
Sprint 7's G-Central/Plus9Time additions -- they were real, healthy,
scheduled sources that simply never appeared in `scripts/status.py` or the
GUI's health table. Fixed alongside adding Timex's own two entries.

**Scheduling:** `WatchClank-TimexNews` (90 min, matches
citizen_news/seiko_jp_news cadence) and `WatchClank-TimexProducts` (360
min, matches citizen_products/seiko_products cadence), both registered and
live-triggered successfully. All 10 Watch Clank tasks Ready/Enabled.

**GUI/EXE:** `app/services/health.py` is a packaged runtime dependency of
the GUI (imported via `data_access.py`) -- per the explicit "when in
doubt, rebuild" policy, the EXE was rebuilt (not skipped) even though no
GUI-owned file changed, and the rebuilt binary was launched and
screenshotted showing all 10 sources HEALTHY and 2010 watches, proving the
fix is in the package, not just the source tree.

**Test result:** 166 passed (up from 165 at Sprint 8's end -- wait, 151 at
Sprint 8 end, 165 after adding 14 Timex-specific tests, 166 after the
force_baseline source-scoping proof). Ruff clean throughout.

**Deliberately not done this sprint (per explicit instruction):** no NEEL/
Japan Select/Great G-Shock World/Oracle of Time, no fifth manufacturer, no
discount/retailer tracking, no cloud deployment, no GUI redesign, no Epoch
2 (Epoch 1 remains valid and untouched).

## 2026-08-11 (Sprint 8) — Fix: Epoch 1 stale material shown as fresh newsroom intelligence

**Starting point verified:** HEAD `07e189f`, clean tree, 142/142 tests passing,
Ruff clean, schema `006_operational_epochs`, DB integrity ok, 8 Windows
tasks Ready/Enabled, no locks/stale RUNNING rows.

**Incident (full writeup: `ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md`):** after
Sprint 7's Epoch 1 baseline, the GUI's Recent Intelligence tab showed
specialist-lead articles from March through August as if they were
breaking news. Traced one real item end to end (a G-Central article about
a UFC fighter's G-Shock, published 2026-07-26, discovered during the
2026-08-11 baseline run): every layer up through persistence worked
correctly — real parsed timestamp, correctly `is_baseline=True`, zero
`Event` rows created (baseline suppression from Sprint 7 worked exactly as
designed). The defect was two compounding gaps: (1) no concept of
"editorial freshness" existed anywhere downstream of `is_baseline`, and
(2) `local_windows/control_centre/data_access.py::get_recent_leads()`
ordered purely by `discovered_at` with **no filter at all**, and displayed
`discovered_at` in the "Time" column as if it were the article's own
timestamp. All 50 real specialist leads in the DB were confirmed
`is_baseline=True` — this was not an epoch/baseline defect, Sprint 7's
mechanism worked correctly; the gap was entirely downstream of it.

**Fix — new "editorial freshness" concept, deliberately separate from
"discovery novelty":** `SpecialistLead.editorial_freshness` (migration
007) with five states — `FRESH`, `STALE_PUBLICATION`, `BASELINE`,
`UNKNOWN_TIMESTAMP`, `MANUAL_UNDATED` — plus `freshness_reason` (human-
readable) and `freshness_evaluated_at`. New `app/services/freshness.py::
classify_lead_freshness()`: baseline leads always classify `BASELINE`
regardless of publication age; specialist/blog sources use
`published_at` against a new configurable
`specialist_freshness_window_hours` (default 72h); `RETAILER_EARLY_LISTING`
sources (none active yet) fall back to `discovered_at` since they have no
publication-time concept; manual ingestion with no date gets the honest
`MANUAL_UNDATED` label rather than being conflated with a parser failure.
Official-catalogue/product-transition Events were deliberately **not**
touched — they already had correct semantics (only created after a
healthy baseline, via existing `is_baseline_active()` guards from Sprint 7)
and don't carry an independent publication timestamp the way a blog
article does. `notify_new_lead` now refuses to alert anything that isn't
`FRESH`. Existing 50 leads deterministically backfilled by the migration
itself (self-contained classification logic, not importing app code, so
the migration's behavior can't silently drift if `freshness.py` changes
later).

**Live validation on the real production DB:** before fix, all 50 leads
sat in the GUI's default view unfiltered. Applied migration 007 (DB was
quiescent, no locks/RUNNING rows — no need to disable the 8 scheduled
tasks for this fast, ~50-row backfill). After: **50/50 correctly
reclassified `BASELINE`, 0 `FRESH`.** GUI Recent Intelligence
screenshotted live showing "Historical evidence suppressed: 50" with an
empty current-intelligence table — the historical evidence itself was
never deleted, just correctly excluded from the "current news" surface.
Triggered a real GCentral pass afterward: 0 new leads (correct dedup), 0
new events, DB integrity ok, all 8 tasks still Ready/Enabled.

**GUI/EXE:** `data_access.py` (query fix + `get_historical_leads_count()`)
and `main.py` (published_at display instead of discovered_at, suppressed-
count label) both changed — **EXE was rebuilt** per policy (source
consumed by the packaged binary changed) and the rebuilt binary was
launched and screenshotted showing the same "Historical evidence
suppressed: 50" result, proving the fix is actually in the package, not
just the source tree.

**Test result:** 151 passed (up from 142 — 9 new tests: the 7 brief-mandated
regression scenarios plus a GUI-query-parity test using the real live
G-Central fixture, which confirmed all 15 of its real March-August items
correctly classify `STALE_PUBLICATION`, none `FRESH`). Ruff clean.

**Deliberately out of scope this sprint (per explicit instruction):** no
new sources, no Epoch 2, no cloud work, no GUI redesign.

## 2026-08-11 (Sprint 7) — Epoch 1 reset: archive, fresh DB, baseline, G-Central + Plus9Time

**Starting point verified:** HEAD `425b0e5`, clean tree, 120/120 tests passing,
Ruff clean, schema at `005_specialist_lead_correlation_type`, 6 Windows tasks
Ready/Enabled, DB had 558 watches / 2100 observations / 12 events / 10
specialist leads / 99 collector_runs accumulated from Sprints 1-6's impromptu
production soak.

**Why:** the owner explicitly noted that initial discovery correctly treated
everything unknown to Clank as "new," which was valuable for proving the
pipeline but is not a useful newsroom starting state — most of that state
represents watches/articles that existed in the world long before Clank
started watching. This sprint distinguishes "first observed by Clank" from
"new after operational baseline" going forward.

**Phase 0-1 (freeze + archive):** disabled all 6 Windows tasks (confirmed
none RUNNING first), confirmed DB quiescent (no locks, no stale RUNNING
rows). Archived the entire pre-Epoch-1 state to
`C:\Users\anil\Desktop\Watch clank\archives\watch-clank-pre-epoch1-20260811T125715Z\`
(outside git, outside the repo's runtime paths): a safe sqlite3-backup-API
copy of the live DB (independently `PRAGMA integrity_check` verified: `ok`),
the actual retired live DB file, full status/schema/git-commit/scheduler/
DB-counts/source-registry summaries, copied logs, and a README documenting
what the archive is and how to restore it if ever needed. Never deleted.

**Phase 2 (epoch model):** new `operational_epochs` table (migration
`006_operational_epochs`) plus `epoch_id`/`is_baseline` columns on
`collector_runs`, `source_observations`, and `specialist_leads` (deliberately
NOT added to `events` — see below). New `app/services/epoch.py`
(`start_epoch`/`start_baseline`/`complete_baseline`/`is_baseline_active`) and
`scripts/epoch.py` CLI (`start`/`baseline-start`/`baseline-complete`/
`status`) — no manual SQL needed anywhere in this process.

**Phase 3 (fresh DB):** old `data/watch_clank.db` moved (not deleted) into
the archive as a second, independent provenance copy. Ran `python -m
scripts.migrate` against the now-empty path — normal migration path, no
special-casing — producing a fresh, empty, schema-current DB at the exact
same `DATABASE_URL` path every collector/the GUI/the EXE already resolve
automatically. Started epoch `epoch_1` at 2026-08-11T13:11:25Z.

**Phase 4 (baseline-suppression code):** `_record_product_transition` and
`_record_watch_event` (app/services/pipeline.py) now check
`is_baseline_active()` first and return `reason=epoch_baseline_active`
without creating an `Event` row — this is why `Event` never needed an
`epoch_id` column: baseline never creates one at all, by construction, not
by a flag. `SpecialistLeadService.ingest_candidate` still creates
`SpecialistLead` rows during baseline (stamped `is_baseline=True` — real
discovery data, needed later for correlation/lead-time), but
`notify_new_lead`/`notify_correlation` refuse to send while `is_baseline`
is true. All of `PipelineService`'s ~5 real `CollectorRun` creation sites
now stamp `epoch_id`/`is_baseline` via a new `_epoch_fields()` helper.
11 new tests cover this directly, including a real product-observation-path
stamping test and a real news-announcement-path suppression test.

**Phase 4 (specialist source expansion, before the baseline):** researched
and implemented G-Central (g-central.com, real WordPress RSS, Casio/G-Shock
regional-release/restock/collaboration coverage) and Plus9Time
(plus9time.com, real Squarespace RSS, Seiko/Grand Seiko/Citizen coverage —
honest finding: mostly historical/archival content, few extractable current
references, still implemented because it's real/cheap/safe and fills a
coverage gap). Both built with a shared `app/parsers/rss_common.py` core
(extracted, not touching the already-shipped `casioblog.py` parser). Found
and fixed a real false-positive during isolated live validation against the
actual live feed: the reference regex matched "GAme" inside the English word
"game" in a real G-Central headline about G-Shock on Roblox — fixed by
requiring a digit in the matched suffix, with a regression test using the
exact real title. Investigated Japan Select (real Shopify store, confirmed
public `/collections/casio/products.json`, 203 real Casio products with
price/availability/reference each — same proven-safe mechanism as Sprint 3's
Citizen/Seiko collectors) but deliberately deferred implementation to stay
within this sprint's bounded scope (two new sources, not three). Both new
collectors and their parsers passed the full acceptance bar (fixtures from
real live feed captures, canonical-URL/timestamp/attribution/reference tests,
duplicate suppression, silent-baseline, failure isolation) and were live-
validated end-to-end against an isolated throwaway database — not the Epoch 1
operational DB — before being included in the baseline.

**Phase 6 (silent baseline):** ran all 8 finalized sources sequentially
against the fresh operational DB with baseline active. Real results: Casio
9 new watches (casio_japan still Akamai-BLOCKED as expected, not a
regression), Citizen news 9 watches/10 leads, Seiko news 0 watches/1 lead,
Citizen products 291 watches, Seiko products 222 watches, CASIOBLOG 10
leads, G-Central 20 leads, Plus9Time 20 leads. **Zero Event rows created.
Zero Discord alerts sent** (verified directly against the DB, not assumed).
555 total watches, 50 total specialist leads, all stamped `is_baseline=true`.

**Phase 7 (repeat-run validation — the critical test):** immediately ran all
8 sources again against the now-LIVE (baseline-completed) epoch. Real
result: **0 new watches, 0 new specialist leads, 0 new events, 0 alerts**
across every source — verified directly against the DB before and after.
No unexplained churn to investigate or document.

**Phase 10-11 (GUI/EXE/scheduling):** re-enabled all 6 original Windows
tasks, then installed 2 new ones (`WatchClank-GCentral` 45min,
`WatchClank-Plus9Time` 360min — cadence justified by each source's real
observed posting frequency, see `install_windows_gcentral_plus9time_tasks.ps1`).
All 8 tasks Ready/Enabled, live-triggered GCentral and confirmed a real new
`collector_runs` row with zero side effects on watch/event/lead counts
(expected repeat). Both the GUI (`local_windows/control_centre/main.py`,
launched from source) and the already-built EXE
(`WatchClankControlCentre.exe`, launched **without rebuilding** — it didn't
need to change, since `app/services/health.py`'s query shape didn't change)
were live-screenshotted showing the new DB correctly: 555 watches, "Latest
official event: none", all 6 legacy sources HEALTHY.

**Test result:** 142 passed (up from 120 at sprint start — 22 new: epoch
lifecycle x5, baseline-suppression x6, health/DB-backup unaffected, G-Central
x5, Plus9Time x5, source-attribution x1). Ruff clean throughout.

**Next priorities:** get real Discord webhook URLs from the owner; Japan
Select implementation (technically ready, deliberately deferred); Great
G-Shock World and NEEL (deferred again, unchanged from Sprint 5/6); actual
cloud deployment execution (infra exists, still no host access from any
session so far).

## 2026-08-11 (Sprint 6) — Control Centre GUI/EXE, Discord wiring, family-aware correlation, cloud handoff prep

**Starting point verified:** HEAD was `08cf8c7` as expected, clean tree, 108/108 tests
passing (post-migration bump from 105), Ruff clean. Two Sprint 5 commits
(`61ce5ac`, `08cf8c7`) were unpushed from the prior sprint's deliberately-scoped
push authorization; this sprint's owner brief explicitly authorized pushing the
validated portable/core changes from those plus this sprint's own work, while
keeping the new Windows GUI/EXE strictly local. Pushed `08cf8c7` (containing
those two commits) first, verified remote HEAD, then did all Sprint 6 work on
top and pushed again separately as `0377718`.

**Family-aware correlation (migration `005_specialist_lead_correlation_type`):**
`SpecialistLeadService.correlate_pending_leads` now distinguishes
`EXACT_REFERENCE_MATCH` from `FAMILY_MATCH` (candidate matches a watch's
hyphen-stripped family root, e.g. lead "GWR-B3000" vs official
"GWR-B3000-1A") — deterministic, no fuzzy string similarity. Live-verified
against the real production DB: 3 real CASIOBLOG leads (including the
GWR-B3000 example documented in Sprint 5's research doc) now correctly
correlate as FAMILY_MATCH with real lead times of 66.8/87.6/119.8 days. A
new `format_correlation_followup_alert` labels FAMILY_MATCH explicitly as
"NOT EXACT", never as a confirmation.

**Discord wiring:** `SpecialistLeadService.notify_new_lead` /
`notify_correlation` send the Layer B early-warning / follow-up alerts,
gated by a new `discord_specialist_min_confidence` threshold and a new
`editorial_notifications_enabled` config flag (for the future cloud-handoff
policy: Windows can be told to stop sending once cloud becomes
authoritative, without any distributed-DB sync). Dedup via a new
`specialist_leads.notified_at` column — tested to fire once, never twice,
across repeat pipeline runs. Health-webhook alerts wired for the two real
outage classes this project has already hit: stale-run recovery
(`RunLockService.recover_stale_runs`) and schema mismatch
(`scripts/run_pipeline.py`'s existing gate) — both live-tested, and tested
to stay silent when there's nothing actionable (no health spam). **Webhook
URLs are still not configured anywhere in this environment** — nothing was
invented; see the final report for exactly what the owner needs to supply.

**CASIOBLOG scheduling:** new `WatchClank-Casioblog` Windows task, 45-minute
cadence (justified in `install_windows_casioblog_task.ps1`'s header —
cheap RSS, real ~hourly updateFrequency, well within the brief's 30-60 min
guidance). Registered and live-verified: triggered, fired, created a real
new `collector_runs` row (SUCCESS, 0 new leads — correctly deduped against
already-known leads).

**Ops improvements (kept small per the brief):** `app/services/health.py`
— a single reusable health-snapshot function (schema state, DB integrity
via `PRAGMA quick_check`, per-source HEALTHY/WARNING/FAILED/NEVER_RUN with
a heartbeat-overdue check against each collector's expected cadence, active
locks, stale RUNNING count) — used by both `scripts/status.py` (one-command
CLI, works on cloud too) and the new GUI's Health tab, so the two can never
disagree about what "healthy" means. `scripts/db_backup.py` uses sqlite3's
actual backup API (not a raw file copy, which can capture a torn snapshot
under WAL) with retention pruning — live-tested against the real production
DB (4.4MB backup produced). PowerShell wrapper logs (`scheduled-*.log`,
previously unbounded) now get a simple one-backup rotation via new
`lib_log_rotate.ps1`. Python's own log rotation (`RotatingFileHandler` in
`app/core/logging.py`) already existed from an earlier sprint — verified,
not rebuilt.

**Windows Control Centre GUI + EXE (local-only, NOT pushed):**
`local_windows/control_centre/` — Tkinter (stdlib), six tabs (Overview,
Recent Intelligence, Operations, Scheduler, Logs, Health/Diagnostics). Reads
the real DB via `app.services.health` and plain model queries; RUN NOW
buttons shell out to the real `python -m scripts.run_pipeline` (same code
path Task Scheduler uses, same lock protection — the GUI cannot bypass
`RunLockService`); Scheduler tab is restricted to a fixed list of known
Watch Clank tasks only (never arbitrary Task Scheduler administration), and
disabling the Casio production task requires an explicit confirmation
dialog. Packaged with PyInstaller as `WatchClankControlCentre.exe`
(`local_windows/dist/`) — a launcher, not a bundled second copy of the
collector stack: at startup it walks upward from its own on-disk location
looking for `alembic.ini` to find the real repo, and shells out to the
repo's own `.venv\Scripts\python.exe` for every actual run. Both the
source GUI and the built EXE were live-launched (not just code-reviewed)
against the real production DB and screenshotted mid-session: Overview
("WATCH CLANK IS ALIVE", 558 real watches, all 6 sources HEALTHY), Recent
Intelligence (real official events + real early-warning leads including
the FAMILY_MATCH correlation above), and Scheduler (real Task Scheduler
state for all 6 tasks) all confirmed rendering correctly. `.gitignore`
updated (`local_windows/`, `*.spec`, `WatchClank*.exe`) — confirmed via
`git status` that none of it is tracked.

**Test result:** 120 passed (up from 108 at sprint start — 12 new tests:
family-match correlation x3, Discord dedup x3, health snapshot x3, DB
backup x2, correlation-followup alert format x1). Ruff clean.

**Next priorities (per this sprint's own final report):** get real Discord
webhook URLs from the owner; Great G-Shock World collector (Sprint 5
research, deferred); NEEL investigation depth (Sprint 5 research,
deferred); actual cloud deployment execution (infra exists, no host
access from any session so far).

## 2026-08-11 (Sprint 5) — Shipped official hunter + started Layer B early-warning

**Starting point verified:** HEAD was `c9b39b8` as expected, clean tree,
85/85 tests passing, Ruff clean, Casio soak healthy.

**Phase 1 — push, but with a real merge first.** `git push` was rejected —
origin/main had diverged: a separate branch/session
(`feature/cloud-migration-hetzner`) had already been merged via PR #2/#3,
adding real, validated Hetzner deployment infrastructure (schema-mismatch
refusal at startup, a `resolved_lock_path` bug fix found via a genuine
Docker overlap test, Docker build, staging runbook). Investigated before
touching anything — no destructive action taken. Merged cleanly (`git merge
origin/main`, no conflicts; the one overlapping file, `scripts/run_pipeline.py`,
touched different functions on each side). Fixed 3 pre-existing Ruff import-
order issues in the merged branch, verified `--scheduled` still works with
the new schema-check gate, then pushed. **Remote HEAD independently
verified via `git ls-remote`** to match local exactly.

**Phase 2 — Windows scheduling activated for real.** New
`scripts/install_windows_experimental_tasks.ps1` (+ `run_scheduled_
experimental.ps1` from Sprint 4) registered `WatchClank-{CitizenNews,
SeikoNews,CitizenProducts,SeikoProducts}` alongside the existing
`WatchClank-CasioJapan`. All five confirmed `Ready`/`Enabled`; a real
triggered run of citizen-news produced a genuine new `collector_runs` row.
**Confirmed still running unattended later in this same session** — runs
78/79 (citizen_products, seiko_products) fired automatically via Task
Scheduler while other work was in progress, with zero manual intervention.

**Phase 3 — cloud: honest limitation, not built.** The merged Hetzner work
means deployment infrastructure (Dockerfile, docker-compose.staging.yml,
migrate-then-run procedure, the PID-namespace lock finding requiring an
external `flock` wrapper) already exists and was validated in a separate
session. This session has **no SSH/host access** to the actual Hetzner
instance — `deploy_run.sh` lives on the host, not in this repo. Cloud
deployment could not be literally performed here. Not fabricated as done.

**Phase 4-10 — specialist source research.** See new
`ai/handoff/SPECIALIST_SOURCE_RESEARCH.md` for the full table and evidence
trail. Highlights: CASIOBLOG has a real, working RSS feed
(`casioblog.com/en/feed/`) and is independently confirmed as a real
historical Notebookcheck citation (Anubhav Sharma, MRG-B5000SA-2 leak).
@geesgshock (Instagram) is the single most-cited specialist source found
in this research (multiple real NBC articles, sole-cited source in at
least one) but cannot be safely automated — no public API/RSS, and
automating it would mean crossing exactly the authentication/anti-bot
boundary this project has consistently refused (same reasoning as never
attacking Casio's Akamai protection). NEEL (neel.co.jp) confirmed real —
a legitimate Japanese authorized multi-brand retailer (Casio/Seiko/Citizen/
Grand Seiko) — but no direct historical NBC citation was found for it in
this sprint's search sample; documented, not implemented. Oracle Time
(oracleoftime.com) confirmed real but is a broad general-watch magazine
with no G-Shock/Casio leak evidence found — deprioritized. Great G-Shock
World and @morgan_gshock were found as *additional* real sources via
direct inspection of NBC articles' own "Source(s)" sections (not guessed) —
Great G-Shock World is a strong candidate for the next specialist collector
(same profile as CASIOBLOG: real blog, confirmed NBC citations, no
automation blocker).

**Phase 11-13 — early-warning data model.** New `specialist_leads` table
(migration `004_specialist_leads`), deliberately **separate** from
`release_leads` (Layer A/official) — see `app/models/specialist_lead.py`'s
docstring for why reusing the official table would risk exactly what this
sprint forbids (a leak becoming indistinguishable from a press release).
Fields cover source type/tier/URL, publication vs. discovery timestamps,
reference candidates, claim text, confidence, and correlation fields
(`correlated_watch_id`, `official_first_observed_at`, `lead_time_days`) —
enough to support future source-performance analytics (Phase 13) without
building any now. `app/services/source_registry.py` is a plain Python dict
mapping source_id -> (type, tier) — deliberately not a DB table yet, and
raises loudly on an unregistered source_id rather than defaulting a tier.

**Correlation is real and conservative, proven both ways:**
`SpecialistLeadService.correlate_pending_leads()` only matches an exact
(case-insensitive) reference string against `Watch.reference_raw`/
`reference_canonical` — no fuzzy matching. Tested and **live-verified
against the real production DB**: CASIOBLOG's real "GWR-B3000" rumor lead
did NOT correlate with the real official `GWR-B3000-1A`/`-A2`/`-B8` watches
already in the DB, because "GWR-B3000" (family-level, what the blog title
says) is not byte-equal to "GWR-B3000-1A" (full official reference). This
is the conservative design working exactly as intended, not a bug — and
it's an honest, documented limitation (family-prefix vs. full-suffix
matching would need real evidence before loosening, same bar every other
normalization decision in this project has had to clear).

**Implementation:** `app/collectors/casioblog.py` + `app/parsers/casioblog.py`
(RSS, real `<10s` fetch, no anti-bot concerns) + `app/services/
specialist_leads.py` (ingestion, correlation, `run_casioblog_pipeline`
with the same isolated-lock pattern as every Sprint 3/4 experimental lane).
Manual ingestion: `scripts/run_pipeline.py --ingest-manual-lead --lead-*`
for sources that cannot be automated (built specifically for
@geesgshock-style leads per the sprint's explicit fallback requirement).
New `format_early_warning_alert()` in `app/services/editorial.py`,
structurally distinct from the official `format_alert()` — always leads
with "EARLY WARNING — UNCONFIRMED", always shows tier/source type, never
uses "Editorial score" language.

**A real, live parser bug was found and fixed via testing before any live
run**: the real CASIOBLOG feed has a stray leading `\r\n` before its XML
declaration (confirmed live, not a fixture artifact) that Python's
`ElementTree` rejects per spec strictness even though it's harmless and
every real feed reader tolerates it. Fixed by stripping leading whitespace
before parsing.

**Tests:** 85 → **105 passed** (+1 schema-check test updated for the new
migration head, not a regression — see below). `ruff check .`: all checks
passed. `alembic current`: now `004_specialist_leads (head)`.

**Live validation (real network):**
```
CASIOBLOG: Run 1 -> 10 new leads (real, current rumors/announcements).
           Run 2 (repeat) -> 0 new leads (dedup confirmed with live data).
           Correlation pass against the real production DB -> 0 matches
           (honest — see the GWR-B3000 example above).
```
Also ran a real triggered run of `WatchClank-CitizenNews` via the new
Windows installer, and independently observed Windows firing
`citizen_products`/`seiko_products` on its own schedule later in the same
session — genuine unattended-scheduling evidence, not just installer
success.

**Discord:** still no webhook found in this environment (`.env` does not
exist, `discord_editorial_webhook_url`/`discord_health_webhook_url` both
unset). Not invented. `format_early_warning_alert()` is ready the moment
one is configured — no further code change needed to wire it in.

**Next action / top 5 priorities:** (1) implement Great G-Shock World as
the second specialist collector — same low-risk profile as CASIOBLOG,
already confirmed as a real NBC-cited source; (2) investigate whether
family-prefix-to-full-reference correlation can be done safely (e.g. only
when the family prefix uniquely identifies one Watch row) — would fix the
GWR-B3000-style non-correlation seen live this sprint; (3) get actual SSH/
host access to the Hetzner instance (or have the owner run the existing,
already-validated `STAGING_RELEASE_RUNBOOK.md` procedure directly) to
complete Phase 3; (4) investigate NEEL's own site structure the way Sprint
3/4 did for citizenwatch.com/seikousa.com, to determine if it's safely
collectible as RETAILER_EARLY_LISTING; (5) once a couple of specialist
sources have run for real days, review whether any produced a lead that
later actually correlated with an official watch, to start validating the
lead-time metric with real (not just synthetic-test) data.

## 2026-08-11 (Sprint 4) — Turned it on: scheduling + broad catalogue coverage

**Starting point verified:** HEAD was `114d7be` as expected, clean tree,
77/77 tests passing, Ruff clean, Alembic at head, Casio soak healthy (run
66 `PARTIAL`, run 65's Sprint-2 stale-recovery still in effect, no leftover
locks).

**Owner explicitly approved scheduling the four experimental lanes** based
on Sprint 3's passing acceptance criteria — not re-litigated this sprint.

**Phase 1 — scheduling infrastructure:**
- `scripts/run_pipeline.py`: new `--experimental-product {citizen,seiko}`
  flag (mirrors Sprint 3's `--experimental-brand`), default `max_items=300`
  to cover the full discovered catalogue rather than a small sample.
- `scripts/run_scheduled_experimental.ps1` (new): one generic Windows
  wrapper parameterized by `-Lane`, same exit-code contract and log-writing
  pattern as `run_scheduled.ps1`, own log files per lane
  (`scheduled-experimental-<lane>-{wrapper,python}.log`) so nothing
  interleaves with Casio's logs. Added to `validate_powershell.ps1`'s
  syntax-check list.
- `scripts/systemd/`: replaced the single ambiguous Sprint-2
  `watch-clank-experimental.*` pair (which was actually Citizen-news-only)
  with four clearly-named unit pairs: `watch-clank-{citizen-news,seiko-news,
  citizen-products,seiko-products}.{service,timer}`. Cadence: news lanes
  90 min (matches Casio's own interval — announcements are infrequent);
  product lanes 6h (same-day price/availability transitions are still
  caught well within a news cycle; keeps the paginated catalogue crawl's
  request volume low). Rationale documented in each `.timer` file and in
  `scripts/systemd/README.md`.
- **At least one real run of all four lanes was performed against the
  actual production `data/watch_clank.db`** (not a throwaway DB — the
  owner explicitly approved this): run ids 68-73. Casio's own run 67 fired
  naturally via Task Scheduler *during* this session, interleaved with the
  experimental runs, with zero interference in either direction — real
  proof of isolation, not just a design claim.

**Phase 2 — Citizen catalogue expansion (the sprint's highest-value task):**
Sprint 3's discovery only scraped product links off 3 small collection
pages (~8 references). Investigated citizenwatch.com broadly: no
sitemap.xml exists, but the collection pages themselves are backed by a
server-rendered search/listing API (`"data":{"limit":...,
"effectiveSearchMode"` JSON, same hydration pattern as the individual
product page) supporting `?offset=&limit=` pagination and reporting an
authoritative `total`. Confirmed live: `mens` collection total=348,
`womens` total=182. Each search "hit" already carries a `representedProduct`
object with almost the full spec set (case material, movement, water
resistance, dial/band/crystal, intro date, collection) — **no second HTTP
request per product needed**, unlike Sprint 3's per-page-fetch approach.
New `CitizenProductsCollector.discover_via_search()` paginates both
categories (bounded by `MAX_CANDIDATES_PER_COLLECTION=600` as
catalogue-collapse protection against an anomalous `total`), new
`parse_citizen_search_hit()` parser (shares field-mapping with the existing
`parse_citizen_product_html` via a new `_watch_from_product_data` helper).
Tradeoff, stated plainly: this broader path has no availability signal
(Citizen's search API doesn't expose inventory/orderable state, only the
individual product page does) — `availability_status` is `None`/UNKNOWN
from this path, never guessed. The old per-product-page path
(`discover_from_collection_html` + `parse_citizen_product_html`, which
*does* have availability) is kept, unused by default `run()`, available for
a future smaller/deeper pass.

**A pagination bug was found and fixed via testing before this shipped:**
the original loop terminated on "page returned fewer than `limit` items,"
which is correct for the real site (pages are always full except the last)
but broke on small test fixtures. Fixed to rely solely on the
source-reported `total` (authoritative) with the short-page heuristic only
as a fallback when `total` is unavailable. Caught by
`test_citizen_search_pagination_follows_total_across_pages` before any live
run, not after.

**Phase 3 — Seiko catalogue expansion:** `seikousa.com`'s
`/collections/all/products.json` supports Shopify's standard `?page=N`
pagination. Confirmed live: page 1 = 250 products, page 2 = 26 more, page 3
= empty (natural terminator) — **276 total products, 225 of them
`product_type == "Wrist Watches"`** (the rest: straps, clocks, gifts,
filtered out deterministically by that field, not by reference-format
guessing). This is the full catalogue, not a sample. New
`SeikoProductsCollector.discover_all_pages()`, bounded by `MAX_PAGES=20` as
catalogue-collapse protection. Seiko per-product-page enrichment (JSON-LD
`Product` schema exists and does carry a rich description — confirmed via a
time-boxed check) was investigated and found reliably obtainable, but
**deliberately not built**: it would require a second HTTP fetch per
product (225 extra requests) for enrichment beyond what the existing
Shopify listing already provides (title, sku, price, availability),
contradicting the "no second fetch per product" design just adopted for
Citizen. Documented as a real, available, not-yet-taken option — not a
blocker.

**Real, pre-existing bug found and fixed via this sprint's live
validation** (not something Sprint 4 introduced — present since Sprint 1):
`caliber_or_module`/`movement_type` are fields on both `ParsedWatch` and
`Watch`, correctly extracted by every brand's parser (confirmed: Citizen's
parser test already asserted `w.caliber_or_module == "H800"` at the
`ParsedWatch` level), but `_resolve_or_create_watch`'s `Watch(...)`
constructor in `app/services/pipeline.py` never read them from the `extra`
dict — and the `extra` dict itself never included them either. Silently
dropped for every brand, forever, until this sprint's field-completeness
check on the real production DB showed 0/311 Citizen watches had a
recorded movement despite the parser extracting one correctly. Fixed both
gaps; regression test added
(`test_citizen_product_baseline_observation_creates_no_event` now also
asserts `watches[0].caliber_or_module == "H800"`). Only affects newly
created watches going forward — the 311 Citizen/225 Seiko rows already in
the production DB from this session's live runs keep `caliber_or_module =
NULL` until a future observation backfills them (existing-watch backfill
only covers `model_name`/`collection` today, a pre-existing and unchanged
design choice, not expanded this sprint).

**Tests:** 77 → **85 passed**. New: Citizen search-hit pagination
(page-following, cross-collection dedup, safety-cap enforcement, the
baseline/no-duplicate-event pipeline path through the new parser), Seiko
multi-page pagination (follows pages until empty, stops on first empty
page, dedup across repeated pages), and the `caliber_or_module` regression.
`ruff check .`: all checks passed. `alembic current`: unchanged.

**Real production-DB state after this session's live runs:**
```
watches:                Casio 22, Citizen 311, Seiko 225
Citizen field completeness (of 311): case_material 297, collection 310,
  water_resistance_m 247, caliber_or_module 0 at capture time (bug found
  live, fixed for future observations — see above)
Citizen SourceObservations: 600 (price 600/600, availability 0/600 — all
  from the broad search-hit path this session; the depth path with real
  availability exists but wasn't the one scheduled by default)
Seiko SourceObservations: 450 (price 450/450, availability 450/450)
Events: 11 NEW_REFERENCE (all from citizen_news; product lanes correctly
  produced 0 events on both their baseline and repeat runs — real evidence,
  not fixture-only)
```
Repeat-run proof, real data, not synthetic: citizen_products run 68 → 300
new watches; run 69 (immediately after, live data unchanged) → 0 new
watches, 0 events. seiko_products run 70 → 225 new watches; run 71 → 0 new
watches, 0 events.

**Casio soak:** unaffected throughout. Run 67 fired via Task Scheduler
mid-session (`SUCCESS`), interleaved with six experimental runs, with zero
interaction — real evidence for the isolation claim, not just a design
argument.

**Discord:** still inert — no webhook found in this environment, none
invented, per instruction. `notify=True` is wired for both product lanes
identically to the news lanes (Sprint 2 pattern), so it activates the
moment a webhook is configured; no further code change needed.

**Next action / top 5 priorities:** (1) decide on Citizen's availability
gap — either accept UNKNOWN availability from the broad search path
long-term, or add a periodic smaller deep pass using the existing
per-product-page path for a curated subset (e.g. only watches with a
recent price/reference change) to recover availability without 500+ extra
requests every cycle; (2) if real Seiko availability enrichment
(movement/case material) is wanted later, the JSON-LD path is confirmed
viable — budget the extra per-product request cost explicitly; (3)
consider a backfill pass for the 311/225 watches already missing
`caliber_or_module` from before this session's fix; (4) let the four
scheduled lanes run unattended for a few real days and review event/alert
quality at real volume; (5) seikowatches.com's `/v3/api/` remains
unresolved — still not the priority while seikousa.com continues to work.

## 2026-08-11 (Sprint 3) — Real Citizen + Seiko product observations flowing

**Starting point verified:** HEAD was `82d1e44` as expected, clean tree,
62/62 tests passing, Ruff clean, Alembic at head. Casio soak checked
healthy: run 66 `PARTIAL` (normal — `casio_intl_news SUCCESS`, `casio_japan
BLOCKED`), and the Sprint 2 run-lock fix confirmed still in effect (run 65
correctly shows `FAILED` with `stale_recovery: true`, no leftover lock
files).

**Priority 1 — Citizen product data: found and built.**
`citizenwatch.com` (Citizen Watch America's real D2C store, Salesforce
Commerce Cloud/Mobify PWA Kit) is server-side rendered — the full product
record (name, brand, price, promotional price, inventory/availability,
case material, movement, water resistance, dial color, band material,
crystal, intro date) is embedded as JSON directly in the static HTML of
`/us/en/product/<reference>` pages (React Query hydration state). No API
call, no JS execution, no anti-bot circumvention needed — this is the same
plain `httpx` GET every other collector in this project already does.
Discovery: a small fixed set of collection pages
(`/us/en/collection/{attesa,tsuyosa,collabs}`) link directly to product
URLs with the reference embedded in the path. `app/collectors/
citizen_products.py` + `app/parsers/citizen_products.py`.

**Priority 2 — wired into transition events.** `process_fetch_result`
(previously 100% Casio-hardcoded — `parse_casio_product_html`, hardcoded
`region="JP"`) generalized with `parse_fn`/`default_region`/`emit_events`/
`notify`/`experimental` kwargs, all defaulting to the exact prior Casio
values, so the production Casio catalog-enrichment path is provably
unaffected (regression tests + the full pre-existing suite pass unchanged).
New `PipelineService._record_product_transition`: compares a new
`SourceObservation` against the most recent prior one for the same
watch+region and calls `classify_price_availability_transition` (built
dormant in Sprint 2, now live). Safety is structural, not a flag that could
be set wrong: `process_fetch_result` only ever reaches observation
creation after a successful fetch + successful parse, so any two
`SourceObservation` rows it compares were, by construction, both healthy —
a failed fetch never creates one, so it can never be compared against or
mistaken for a transition.

**Priority 3 — cross-source correlation: real, not just tested.** Sprint
1's Citizen news fixture references `CC4107-80H` (the Attesa titanium
limited edition). Live validation on 2026-08-11 found this exact reference
still listed on citizenwatch.com's live Attesa collection page, at
$2,195.00, `AVAILABLE`. Processing both the Sprint-1 news announcement and
the live product page resolves to the same `Watch` row (`reference_
canonical == "CC4107-80H"`, `new_watch=False` on the second), proven by
`test_citizen_news_and_product_references_correlate_to_same_watch` and
directly observed with real data in this session — not a coincidence
engineered for the test, an actual discovered fact about the live catalog.

**Priority 4 — Seiko: found, time-boxed correctly.** Additional
investigation of `seikowatches.com`'s `/v3/api/` confirmed it's real
(Azure-backed, `200 application/json`) but the exact route/payload still
wasn't recovered from a further round of reasonable guesses — time-boxed
and abandoned per instruction, not force-fit. Pivoted to alternative
first-party paths per Priority 4's explicit permission: found
`seikousa.com`. **Verified first-party before use** — its own Terms of
Service states "This website is operated by Seiko Watch of America LLC"
(Seiko's official US importer, the same corporate relationship Citizen
Watch America has to citizenwatch.com), not a third-party retailer despite
marketing copy ("Shop authentic Seiko watches") that read ambiguously at
first glance. It runs Shopify, which by default publicly exposes
`/collections/all/products.json` — a standard, publicly-documented Shopify
storefront feature returning full product records including
title/handle/vendor/product_type/tags/variants (sku/price/available) in
one request; `product_type == "Wrist Watches"` cleanly filters out straps
and other non-watch products. Currency (`USD`) confirmed via the store's
own `Shopify.currency = {"active":"USD"}` page state, not assumed.
`app/collectors/seiko_products.py` + `app/parsers/seiko_products.py`.

**Tests:** 62 → **77 passed**. New: Citizen product discovery/parsing
(real captured fixtures + synthetic price-drop/sold-out variants for
offline transition testing), the 12 Sprint-3 Citizen acceptance criteria
(baseline-no-event, repeat-no-duplicate-event, PRICE_CHANGE, SOLD_OUT→
RESTOCK, failed-fetch-cannot-fabricate-SOLD_OUT, news/product identity
correlation), Seiko product discovery/parsing/watch-type-filtering, and a
Seiko baseline→PRICE_CHANGE pipeline test. `ruff check .`: all checks
passed. `alembic current`: unchanged — no new migration needed (reuses
`SourceObservation`'s existing price/currency/availability_status/region
columns).

**Live validation (real network, throwaway SQLite DBs — never touched
`data/watch_clank.db`):**
```
Citizen: Run 1 (baseline) -> 8 new watches, 0 events (correct: all baseline)
         Run 2 (repeat, same live data) -> 0 new watches, 0 events (correct: no transition)
Seiko:   Run 1 (baseline) -> 15 new watches, 0 events
         Run 2 (repeat) -> 0 new watches, 0 events
```
Both runs used isolated lock files (`citizen_products.run.lock`,
`seiko_products.run.lock`) that were created and cleanly released — no
leftover locks in the real `data/` directory, confirmed after each run.

**Promotion status:** Citizen and Seiko product observation both pass the
sprint's stated bar for the experimental scheduled lane (fixtures, tests,
isolated live validation, repeated-run dedup, failure-transition
isolation) — per this sprint's explicit instruction not to require further
days of manual testing before experimental scheduling, they are ready to
add to `scripts/systemd/watch-clank-experimental.*` / an equivalent Windows
task once the owner wants real observations flowing on a schedule. Not
added automatically this session — scheduling installation is a
system-affecting action left for explicit confirmation.

**Next action / top 5 priorities:** (1) decide whether to actually schedule
the experimental product lanes now that they're proven; (2) if Seiko's
`/v3/api/` is ever cracked, it would add non-US-market Seiko coverage
seikousa.com can't (that store is US-only, English-only); (3) widen
Citizen's discovery collection-page seed list once initial alert quality
from the current three is reviewed; (4) consider a small correlation pass
that proactively cross-checks existing `ReleaseLead.model_references`
against product observations for brands/regions beyond the one live
example found this sprint; (5) Casio's own product/catalog path
(`app/parsers/casio_japan.py`) already has price/availability support —
once Akamai unblocks even occasionally, wire `emit_events=True` there too
(currently default False, unchanged) after a short review, since the
infrastructure is now identical across all three brands.

## 2026-08-11 (Sprint 2) — Recall tuning, price/availability logic, Discord, overlap-lock bug fix, portability

**Starting point verified:** HEAD was `b76da6b` as expected, clean tree
(only untracked `data/`), 48/48 tests passing, Ruff clean, Alembic at head.

**Real defect found and fixed during Phase 0 verification (not something
this sprint was looking for — found while checking soak health):**
`collector_runs` id=65 (`casio_multi`) was stuck `RUNNING` since 07:12 UTC
with a confirmed-dead process (pid checked via `Get-Process`, not present).
Root cause: `RunLockService` (`app/services/run_lock.py`) hardcoded
`collector_id == "casio_japan"` in its DB queries (`find_active_run`,
`recover_stale_runs`) regardless of what it was protecting.
`run_multi_source_pipeline` creates rows under `collector_id="casio_multi"`
but reused this same class — so a crashed `casio_multi` run's RUNNING row
could never be recovered, and would never even be detected as "active" by a
concurrent-run check either. Fixed by adding a `collector_id` constructor
parameter (default `"casio_japan"`, so every pre-existing caller is
byte-identical) and passing `collector_id="casio_multi"` explicitly from
`run_multi_source_pipeline`. Regression test added
(`test_run_lock_is_scoped_to_collector_id`). **Manually recovered run 65
against the real DB using the fixed code** (not hand SQL) — now `FAILED`
with `stale_recovery: true`, stale lock file removed, and a fresh scheduled
run (id=66) completed `PARTIAL` cleanly afterward. This also means the
experimental brand lane (see below) is now genuinely safe to schedule — its
lock was built with this fix already in place, isolated per-brand.

**Phase 1 — seikowatches.com:** time-boxed further investigation. Confirmed
`/v3/api/` is a live endpoint namespace served through Azure Front Door
(ARRAffinity cookie, `x-azure-ref` header — real backend, not a static 404
page) and returns `200 application/json` for guessed paths, but every
guessed route/payload (`/v3/api/news`, `/News`, `/news/list`,
`/News/GetList`, query-string and POST-body variants using the
`Country`/`Language` config values seen in `news.js`) returned an empty
body. The exact route/payload was not recovered in the time budgeted.
**Not built.** Still the single highest-value next task for Seiko.

**Phase 2 — product/catalogue observation:** not built this sprint. Casio's
catalog collector/parser already supports price+availability
(`app/parsers/casio_japan.py`), but it's Akamai-blocked. Citizen and Seiko
have no product-page collectors yet — only news-announcement collectors,
which rarely carry a numeric price (confirmed live: Citizen's Attesa
announcement said "Price ... subject to change", no figure). Building
real Citizen/Seiko catalog collectors from scratch was judged lower
priority than fixing the scheduling bug above and shipping the
recall-tuned scoring + safe Discord/portability work in the time available.
**Top priority for a future session.**

**Phase 3 — price/availability events:** the deterministic classifier is
built and tested (`app/services/editorial.py::classify_price_availability_
transition`), producing `PRICE_CHANGE` / `AVAILABILITY_CHANGE` / `SOLD_OUT`
/ `RESTOCK` from a healthy before/after observation pair. Guards: never
infers SOLD_OUT from a failed/unhealthy fetch on either side, never compares
different regions, never compares different currencies (no conversion
layer, explicitly out of scope). **Not wired to any live data source yet**
— it has nothing to compare until Phase 2 product collectors exist. Ready
to call the moment they do.

**Phase 4 — cross-region:** `NEW_REGION` (from Sprint 1) unchanged.
Confirmed via the existing test plus manual review that the merge-key
dedup caveat documented in Sprint 1 still applies — realistic `NEW_REGION`
needs more than one source per brand per region to be common.

**Phase 5 — recall tuning (`app/services/editorial.py`,
`SCORING_RULE_VERSION` 0.1.0 → 0.2.0):**
- Confidence thresholds lowered (HIGH ≥55→≥50, MEDIUM ≥30→≥25) —
  more genuine evidence now reaches MEDIUM/HIGH instead of LOW.
- New scoring dimensions, all evidence-gated (never guessed): limited
  edition (+10, +quantity if stated), named collaboration (+10), unusual
  material (+10). Extracted conservatively from the announcement **title
  only** via `PipelineService._extract_product_character` — e.g. "Limited-
  Edition Recrystallised Titanium" title text, not inferred from anything
  unstated.
- PRICE_CHANGE now scales with magnitude (`price_delta_pct`), capped at +35.
- `score_event` now rejects unknown `event_type` values (`ValueError`) —
  a real safety net against a typo silently producing an unscored/garbage
  event.
- **Live proof this changed real output:** the same live Citizen ATTESA
  announcement scored 60/100 in Sprint 1 and **80/100 in Sprint 2** (added
  "+10 limited edition" and "+10 unusual material (recrystallised
  titanium)") — same underlying evidence, better-tuned scoring.

**Phase 6 — Discord (`app/services/discord_notify.py`, new):**
`DiscordNotifier` with separate `send_editorial_alert`/`send_health_alert`,
each reading its own webhook URL from `Settings` (env/.env only, nothing
committed — `discord_editorial_webhook_url`, `discord_health_webhook_url`,
both `None` by default). Every public method catches all exceptions and
returns `False` on any failure — verified with a test that mocks
`httpx.post` to raise `ConnectionError` and confirms no exception escapes.
Wired into `_record_watch_event` via new `notify`/`experimental` params on
`process_news_announcement`, used only by `run_brand_news_pipeline`
(`notify=True, experimental=True`) — **the Casio production path never
calls `notify=True`**, so it cannot send anything even if a webhook were
configured. Threshold for the experimental lane is
`DISCORD_EXPERIMENTAL_MIN_SCORE` (default 0 — alert on everything during
tuning, per Sprint 2's explicit instruction). No real webhook is configured
anywhere in this repo/environment, so this is built and tested but
currently inert.

**Phase 7/8 — portability:** added `scripts/run_scheduled.sh` (same
exit-code contract, same log files as `run_scheduled.ps1`),
`scripts/setup-linux.sh`, and `scripts/systemd/` (service/timer templates
for both the Casio production lane and the experimental brand lane, plus a
`README.md`). **Decision recorded per Phase 8's explicit requirement:**
Windows and Linux/cloud use **independent SQLite databases** if run
simultaneously — no synchronization is built or planned; see
`scripts/systemd/README.md` for the full reasoning. The canonical runtime
command stays `python -m scripts.run_pipeline` on both platforms
(`--scheduled` for Casio, `--experimental-brand {citizen,seiko}` for the
new lane) — no entrypoint was renamed.

**Tests:** 48 → **62 passed**. New: run-lock collector_id scoping,
price/availability transition classifier (6 tests covering the
false-positive guards explicitly), recall-tuning scoring dimensions,
unknown-event-type rejection, Discord notifier safety (no-op, never-raises,
channel separation), product-character extraction. `ruff check .`: all
checks passed. `alembic current`: unchanged, `003_release_leads (head)` —
no new migration was needed (Discord config is env-only; price/availability
reuses existing `SourceObservation` columns).

**Promotion status:** still nothing new is scheduled. Citizen/Seiko are
schedulable now (isolated lock fixed) but deliberately not yet added to any
real scheduler — recommend a few days of manual `--experimental-brand` runs
first to observe real event/alert volume before enabling a systemd timer or
Windows task for them.

**Next action / top 5 priorities**, in order: (1) recover the
seikowatches.com `/v3/api/` route — likely the single highest-leverage
remaining task; (2) build a real Citizen product-detail-page collector for
price/availability (Citizen's news pages already link to product pages);
(3) same for Seiko once/if the API is cracked; (4) once Phase 2 exists, wire
`classify_price_availability_transition` into `process_fetch_result`; (5)
after a few days of experimental-lane data, decide Citizen/Seiko systemd/
Task Scheduler promotion using real observed alert quality, not just test
passing.

## 2026-08-11 — Multi-brand sprint: Citizen + Seiko experimental discovery, deterministic event/scoring layer

**Context:** owner-directed sprint to extend coverage to Citizen and Seiko and
add change-intelligence/editorial scoring, explicitly overriding the "hold
during soak" default (soak was 3 days in at the time). Executed conservatively
per the sprint brief's own non-negotiable rule: the Casio production path
must not be destabilized. Everything new is additive and isolated.

**Casio production path: unaffected.** All 35 pre-existing tests still pass
unmodified; two new regression tests
(`test_casio_production_path_emits_no_events_by_default`) explicitly guard
that the generalized `process_news_announcement`/`_resolve_or_create_watch`
produce byte-identical behavior for Casio's default call path. `scripts/run_
pipeline.py --scheduled` (the scheduled task's entrypoint) was not touched
and does not call any new code.

**Source investigation (live probes, 2026-08-11):**
- `citizenwatch-global.com/news/` — HTTP 200, static HTML, and (unlike Casio
  or Seiko) is *already* a pure watch-product feed with no corporate noise.
  Chosen as the Citizen source.
- `seiko.co.jp/en/news/` — HTTP 200, static HTML, but Seiko Group
  Corporation's *corporate* feed (watches + clocks + financial results +
  cultural sponsorships). Requires topic filtering; used as the Seiko source
  with a conservative `is_watch_announcement` filter (mirrors Casio's).
- `seikowatches.com/global-en/news` — HTTP 200 but is a JS-rendered Vue SPA
  backed by a `/v3/api/` REST endpoint. The endpoint path was not recovered
  from the minified bundle in the time available. **Documented gap, not
  built**: this is probably Seiko's best watch-only source and should be the
  first thing a future session investigates for Seiko (see priorities below).
- Grand Seiko's news page is the same SPA pattern — same gap.

**Code added:**
- `app/collectors/citizen_news.py`, `app/parsers/citizen_news.py` —
  discovery + parsing for the Citizen global news feed. Reference format
  `[A-Z]{2}[0-9]{4}-[0-9A-Z]{2,4}` confirmed against live announcement text
  (e.g. `CC4107-80H`).
- `app/collectors/seiko_news.py`, `app/parsers/seiko_news.py` — discovery
  (with topic filter) + parsing for seiko.co.jp. Reference format
  `S[A-Z]{2}[0-9]{3}[A-Z0-9]{0,3}` (e.g. `SPB255`).
- `app/normalization/references.py` — `normalize_citizen_reference`,
  `normalize_seiko_reference`: conservative pass-through (canonical == raw),
  per the brief's explicit instruction not to blindly apply Casio's JDM
  suffix rules to other brands. Documented in the module docstring as
  policy: a brand gets suffix-stripping rules only once evidence justifies
  it, same bar Casio's JDM allowlist had to clear.
- `app/services/editorial.py` (new) — deterministic, explainable event
  scoring. `EventEvidence` → `score_event()` → `ScoredEvent{score, confidence,
  reasons[]}`. Every point added has a reason string; unscored dimensions
  say `UNKNOWN` rather than being silently omitted. `format_alert()` renders
  the Phase 6 human-readable block. No LLM, no black-box classifier.
- `app/services/pipeline.py`:
  - `process_news_announcement` generalized with optional `manufacturer`,
    `brand`, `parse_fn`, `merge_key_prefix`, `default_region`, `emit_events`
    kwargs, all defaulting to the exact prior Casio-only values/behavior.
  - `_resolve_or_create_watch` now dispatches reference normalization by
    manufacturer via a small registry, defaulting to
    `normalize_casio_reference` (unchanged call for Casio).
  - New `_prior_regions_for_watch` / `_record_watch_event`: deterministic
    `NEW_REFERENCE` / `NEW_REGION` classification, writing to the existing
    (previously unused) `Event`/`EventWatch` tables from `001_initial` — no
    new migration needed. Only fires when `emit_events=True`, which the
    Casio production path never passes.
  - New `run_brand_news_pipeline(brand, ...)` — experimental orchestrator
    for Citizen/Seiko. Does **not** share `RunLockService`/overlap
    protection with the Casio `casio_japan` lock; writes its own
    `collector_runs` rows under brand-specific `collector_id`s
    (`citizen_news`, `seiko_jp_news`) and `source_component_states` rows,
    fully isolated from Casio's. Not called from any scheduled path.

**Known false-positive-protection finding (important, not a bug):** the
pre-existing `merge_key` duplicate-lead detection (unchanged, Casio-proven)
catches a second announcement of the *same* reference before event detection
ever runs — so `NEW_REGION` cannot fire from literally re-processing the
same reference text under a new URL; it only fires when a genuinely separate
lead (different merge_key, e.g. from a different source) references a watch
already seen in another region. This is correct/desired — it's the same
duplicate-suppression Casio already relies on — but it means realistic
`NEW_REGION` detection will depend on having more than one source per brand
per region eventually. Documented in
`test_brand_news_pipeline_new_region_detected_not_new_reference`'s docstring.

**Tests added (13):** discovery parsing (Citizen, Seiko), Seiko topic-filter
inclusion/exclusion, reference extraction + collection guessing for both,
conservative-normalization tests for both, an end-to-end experimental
pipeline test (lead + watch + event created from a fixture), NEW_REGION vs.
NEW_REFERENCE classification, a same-region-repeat produces-no-event
false-positive guard, the Casio-path-emits-no-events-by-default regression
guard, and two `editorial.py` unit tests (score bounds/explainability, alert
formatting only echoes supplied evidence). **48 passed** (was 35).
`ruff check .`: all checks passed.

**Live validation (real network, throwaway SQLite DB — never touched
`data/watch_clank.db`):**
```
citizen: run status=SUCCESS, 8 leads, 19 references, 19 watches, 19 NEW_REFERENCE events
  scores: HIGH(60) for recognisable families (Tsuyosa/Attesa/Promaster),
  MEDIUM(40) for unrecognised ones (Rainell, Eco-Drive, etc.)
seiko:   run status=SUCCESS, 1 lead discovered (Credor announcement) out of
  the corporate feed, 0 watches (Credor's reference format isn't covered by
  the current MODEL_RE — correctly produced 0 fabricated watches/events
  rather than guessing)
```
This is real evidence the discovery+identity+scoring pipeline works
end-to-end for a live first-party Citizen source today, and that the Seiko
corporate-feed filter is conservative (under- rather than over-discovers) —
consistent with "prefer missing a story to fabricating one."

**Discord/alerting:** not implemented. `format_alert()` produces the text
block; no webhook, no network call, no credentials. Deferred per the brief's
own Phase 7 sequencing ("first make the intelligence good") and because
there is no existing Discord infrastructure to reuse.

**Promotion status — nothing new is scheduled/production:**
| Source | Status | Notes |
|---|---|---|
| casio_intl_news | PRODUCTION, soaking | unaffected |
| casio_japan (catalog) | PRODUCTION, soaking, BLOCKED (Akamai) | unaffected |
| citizen_news | EXPERIMENTAL | live-validated once, needs multi-day soak before scheduling |
| seiko_jp_news | EXPERIMENTAL | live-validated once; corporate-feed filter needs more real samples before trust |
| seikowatches.com (Seiko brand SPA) | NOT BUILT | API endpoint not reverse engineered |

**Next action:** do not add these experimental brands to the Windows
scheduled task yet. Run `run_brand_news_pipeline` manually a few more times
over the coming days against the real `data/watch_clank.db`, inspect leads
for false positives (especially Seiko's topic filter), then decide whether
to graduate. Reverse-engineer `seikowatches.com`'s `/v3/api/` news endpoint
as the highest-value next step for Seiko coverage.

## 2026-08-09 — Soak-day timezone comparison outage found and fixed

**Root cause:** `SourceComponentState.backoff_until` (and `last_success_at`,
`last_blocked_at`) are declared `DateTime(timezone=True)`, but SQLite does not
actually preserve timezone offsets on that column type — any value reloaded
from the database (i.e. in every process after the one that wrote it) comes
back as a naive `datetime`. `PipelineService._should_skip_backed_off`
(`app/services/pipeline.py`) compared that naive, persisted `backoff_until`
directly against a fresh timezone-aware `datetime.now(UTC)`:

```python
return state.backoff_until > datetime.now(UTC)
```

Once `_update_component_state` had written a real `backoff_until` (which
first happened as a side effect of the previous checkpoint's manual pipeline
run, run 24, which hit the Casio Japan catalogue and got BLOCKED), every
subsequent scheduled invocation raised
`TypeError: can't compare offset-naive and offset-aware datetimes` inside the
`try` block of `run_multi_source_pipeline`, was caught by the existing
`except Exception` handler, and correctly written to a terminal `FAILED` row
— so the scheduler and the fail-safe terminal-status logic were never at
fault. The news-discovery half of the pipeline (the part that actually
matters editorially) never got a chance to run.

**Affected runs:** `collector_runs` 25–34 (2026-08-08 10:12 through
2026-08-09 08:42), all `FAILED` with
`"fatal_error": "can't compare offset-naive and offset-aware datetimes"`.
Each has a populated `completed_at` — none were left stuck in `RUNNING`.
These rows were left untouched as historical evidence, per instruction.

**Datetime policy established:** all operational timestamps are persisted
and compared in UTC. New shared utility `app/core/time.py`:
- `utc_now()` — `datetime.now(UTC)`.
- `ensure_utc(value)` — normalizes a possibly-naive persisted datetime to
  aware UTC (assumes naive means "already UTC", which is the only thing this
  codebase ever writes). Returns `None` for `None`.

**Code changed:**
- `app/core/time.py` (new) — the shared policy described above.
- `app/services/pipeline.py::_should_skip_backed_off` — normalizes
  `state.backoff_until` through `ensure_utc` before comparing against
  `datetime.now(UTC)`. This was the exact line that raised.
- `app/services/run_lock.py` — `find_active_run`, `recover_stale_runs`, and
  `_lock_is_stale` already had ad hoc `if x.tzinfo is None: x =
  x.replace(tzinfo=UTC)` normalization (which is why the stale-run-recovery
  path was never affected by this bug); replaced with calls to the shared
  `ensure_utc` for consistency, no behavior change.
- `app/main.py::dashboard` — same ad hoc normalization for
  `latest_run.started_at` replaced with `ensure_utc`, no behavior change.
- `tests/test_core.py` — 11 new regression tests (see below) plus two
  pre-existing unrelated fixes carried over from the previous checkpoint's
  Ruff/pytest pass.

No new migration was needed — the fix is entirely application-level
normalization at the read side, exactly as the columns already being
`DateTime(timezone=True)` intends; no destructive schema change was made or
required.

**Regression tests added** (`tests/test_core.py`):
1. `test_ensure_utc_normalizes_naive_and_aware`
2. `test_should_skip_backed_off_naive_stored_value` — reproduces the exact
   crash scenario (state written, then forced to reload naive from SQLite via
   `session.expire_all()`, matching what a second process/run actually sees)
3. `test_should_skip_backed_off_aware_stored_value`
4. `test_should_skip_backed_off_expired_backoff_allows_run`
5. `test_should_skip_backed_off_no_state_allows_run`
6. `test_repeated_403_increases_backoff`
7. `test_success_resets_backoff`
8. `test_multi_source_active_backoff_skips_catalog_cleanly` — asserts the
   catalog collector is never even invoked while backed off, and the run
   never reaches `FAILED` or stays `RUNNING`
9. `test_multi_source_expired_backoff_allows_catalog_run`
10. `test_news_success_catalog_backed_off_is_not_failure`
11. `test_news_deduplication_repeated_announcement_no_duplicate_lead`

Combined with the pre-existing `test_multi_source_news_success_catalog_blocked`,
this covers both BLOCKED and BACKED_OFF non-failure paths. Full suite: 35
passed (was 24 before this checkpoint's additions). `ruff check .`: all
checks passed.

**Manual verification against the real, in-use database (not a fresh one):**
- `alembic current` → `003_release_leads (head)`, unchanged.
- Confirmed the live DB actually held the naive-datetime landmine before the
  fix: `source_component_states` row for `casio_japan` had
  `backoff_until = '2026-08-08 12:51:05.885066'` (no offset) with
  `last_status = BLOCKED`.
- `python -m scripts.run_pipeline --scheduled` run manually against this real
  DB produced run_id=35, completing `PARTIAL` with no exception. The stored
  backoff window had actually expired by the time of this run, so it
  correctly re-attempted the catalogue live, got a clean fresh 403, and
  re-armed a new backoff window — `casio_intl_news SUCCESS` (10 discovered),
  `casio_japan BLOCKED`, overall `PARTIAL`.

**Scheduler-wrapper verification:**
`powershell -File .\scripts\run_scheduled.ps1` run directly, producing
`collector_runs` id=36: START/END logged in
`data/logs/scheduled-wrapper.log`, `exit_code=0`, `casio_intl_news SUCCESS`
(10 discovered), `casio_japan BACKED_OFF` (the fresh backoff window set by
run 35 was still active seconds later), overall `SUCCESS` — no timezone
exception, no reinstall of the Task Scheduler task needed.

**Current soak status:** the soak clock effectively restarts from this
checkpoint. Runs 25–34 (the outage window) are excluded from any future
"healthy soak" analysis; run 35 onward (2026-08-09 08:49 and later) is the
real signal. Casio Japan catalogue remains Akamai-blocked as expected — no
change to that known limitation. Next scheduled firing should confirm the
fix holds unattended; no further code changes are planned.

## 2026-08-08 — Soak-day migration outage found and fixed

**Delta:** DB was pinned at `002_ops_statuses` while code/head was `003_release_leads`.
Every scheduled run since at least 2026-08-07 20:12 (checked back through the full
wrapper log) failed with `sqlite3.OperationalError: no such table:
source_component_states`, exit_code=2. The scheduler itself was healthy (firing every
~90 min exactly as configured) — this was a pure "forgot to run `alembic upgrade head`
after pulling the 003 migration" gap. Per section 25's lesson, task registration
(and even task *execution*) is not proof the pipeline runs meaningfully; only
`collector_runs` rows with real content prove it.

**Migrations added:** none — applied the existing `003_release_leads` migration that
had not yet been run on this machine (`002_ops_statuses` → `003_release_leads`).

**Files substantially changed:**
- `tests/test_core.py` — `test_parser` was calling `.read_text()` without an encoding
  on UTF-8 fixtures containing Japanese text; on this machine Python defaults to
  cp1252, causing `UnicodeDecodeError`. Fixed to `.read_text(encoding="utf-8")`,
  matching the pattern already used elsewhere in the same file (news-list fixture read).
- `alembic/versions/003_release_leads.py` — Ruff auto-fix for unsorted import block
  (`I001`), no logic change.

**Test result:** 24 passed (was 23 passed, 1 failed before fix).

**Ruff result:** All checks passed (was 1 error before fix).

**Live probes performed:**
- Manual run of `python -m scripts.run_pipeline --scheduled` after the migration fix
  (run_id=24): `casio_intl_news` SUCCESS (10 discovered, 10 fetched), `casio_japan`
  BLOCKED (five URLs, all HTTP 403, correctly classified as BLOCKED not ZERO_ITEMS),
  overall status PARTIAL, 10 new release leads, 9 new watches. This matches the
  documented "healthy soak" pattern exactly (section 42).
- Casio International News: HTTP 200, confirmed live during this run.
- Casio Japan catalogue + G-Shock/Edifice/Oceanus/Pro Trek listing pages: HTTP 403,
  confirmed still Akamai-blocked, as expected.

**DB counts after fix:**
```
collector_runs: 24
release_leads: 10
watches: 22
source_observations: 0
snapshot_fetches: 10
snapshot_blobs: 10
pipeline_ledger: 44
source_component_states: 2
```

**Scheduler status:** Task exists, Ready, Enabled, last run 2026-08-08 14:12:25
(pre-fix, still failing), next run 2026-08-08 15:42:24 — should now succeed since it
shares the same DB path that was just migrated.

**Known limitations:** unchanged from prior handoff (Casio Japan catalogue still
Akamai-blocked; Citizen/Discord/editorial scoring still not implemented).

**Next action:** No further intervention needed. Let the scheduled task run at
15:42 and subsequent 90-minute intervals confirm success (status SUCCESS/PARTIAL,
not FAILED) now that the schema is current. Continue the soak per section 42;
do not start Stage 2. If a future session pulls new migrations, always run
`alembic upgrade head` immediately — this outage happened because that step was
skipped after `003_release_leads` was added to the codebase.
