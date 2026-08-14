# Incident: Casio production path cannot emit Events or notify Discord

**Discovered:** 2026-08-14, during a Discord-authority audit following the
Hetzner redeployment sprint. **HEAD at discovery:** `12e8d3e`.

## Precise root cause

`scripts/run_pipeline.py::run_live_or_scheduled(max_items, scheduled: bool = False)`
is the single entrypoint for **both** `--live` (manual) and `--scheduled`
(systemd/Task Scheduler) Casio runs. `scheduled` is used for exactly one
thing: a `logger.info(..., scheduled=scheduled, ...)` log field. It has
**zero effect** on behavior. Both call sites converge on the identical line:

```python
run = pipeline.run_multi_source_pipeline(max_items=max_items)
```

**Correction to this incident's own initial framing**: this is *not* a
"manual works, scheduled doesn't" bug. There is no asymmetry — manual and
scheduled Casio runs have always been byte-identical in behavior, because
they are the same function call. The real defect is one level down.

`PipelineService.run_multi_source_pipeline` (`app/services/pipeline.py`)
has **no `emit_events` or `notify` parameter at all**. Internally it calls:

- `process_news_announcement(...)` — default `emit_events: bool = False`
- `process_fetch_result(...)` — default `emit_events: bool = False`

Both call sites inside `run_multi_source_pipeline` omit `emit_events`
entirely, so both silently take the `False` default. Tracing further:
`process_news_announcement`'s body reads `if emit_events: self._record_watch_event(...)`
— when `emit_events=False`, `_record_watch_event` is **never called at
all**. This is not "events are created but not sent" — **no `Event` row
is ever created** for anything Casio's production path discovers. There
is therefore nothing to notify about; Discord not being configured is a
second, independent, compounding problem, not the only one.

This has been true since the function was introduced (Sprint 1) and was a
**deliberate scope decision at the time** — Sprint 1's own priority was
"don't destabilize the one working source while everything else is being
built," and Sprint 2 explicitly recorded "the Casio production path never
calls notify=True" as an intentional safety property while Discord/event-
scoring infrastructure was still being proven out on the "experimental"
brands. That reasoning was correct in 2026-08-11. It stopped being correct
once Citizen/Seiko/Timex/the specialist RSS lanes matured into real,
scheduled, systemd-timer production sources with proven event/notify
wiring (this repo's own 2026-08-14 sprints) while Casio — the original and
most mature source — was never upgraded to match. The gap became a live
bug the moment Casio was left the only lane still silent by default.

## Which collector paths are affected

Only `run_multi_source_pipeline` (Casio: `casio_multi`, both official news
+ catalogue enrichment). Verified by reading every other scheduled
entrypoint's actual defaults, not assumed:

- `run_product_observation_pipeline` (citizen_products, citizen_de_products,
  seiko_products, seiko_jp_products, timex_products, casio_uk_sitemap):
  `emit_events: bool = True`, and internally calls `notify=emit_events` —
  **correct**.
- `run_brand_news_pipeline` (citizen_news, seiko_jp_news [brand], timex_news):
  `emit_events: bool = True`, internally `notify=emit_events` — **correct**.
- `run_publication_pipeline` (casioblog, gcentral, plus9time, monochrome,
  deployant, fratello, watchtime — `app/services/specialist_leads.py`):
  calls `service.notify_new_lead(...)`/`notify_correlation(...)`
  unconditionally per lead (no disabling default anywhere) — **correct**,
  and independently well-gated (see Phase 3 below).

So: **1 of 17 registered scheduled collectors** (`casio_multi`) is
affected. It is, however, the original production source and the one with
the longest real operational history on Hetzner (73+ real collector_runs
before this sprint), which is why the practical impact is real despite the
narrow blast radius.

## Hetzner webhook configuration state (as audited, unchanged by this doc)

- `DISCORD_EDITORIAL_WEBHOOK_URL`: not configured (no `secrets.env` exists
  on the host).
- `DISCORD_HEALTH_WEBHOOK_URL`: not configured, same reason.
- `EDITORIAL_NOTIFICATIONS_ENABLED`: not explicitly set anywhere on
  Hetzner; code default is `True`, moot with no webhook URL present
  (`DiscordNotifier.editorial_enabled` requires both).

Two independent, compounding reasons nothing has ever reached Discord from
Hetzner: (1) this code defect for `casio_multi` specifically, and (2) no
webhook configured for any of the 17 sources. Fixing only one leaves the
other in place.

## Phase 1 matrix — manual vs. scheduled, every registered collector

Manual and scheduled are the same CLI entrypoint (`scripts/run_pipeline.py`)
calling the same `PipelineService` method for every collector below — there
is no separate "scheduler" code path anywhere in this codebase to audit
independently. The matrix therefore reduces to: does the one shared
function default to emitting events/notifying, or not.

| Collector(s) | Function | Manual events | Scheduled events | Manual notify | Scheduled notify | BUG? |
|---|---|---|---|---|---|---|
| casio_multi | `run_multi_source_pipeline` | NO | NO | NO | NO | **YES — fixed this sprint** |
| citizen_news, seiko_jp_news (brand), timex_news | `run_brand_news_pipeline` | YES | YES | YES | YES | No |
| citizen_products, citizen_de_products, seiko_products, seiko_jp_products, timex_products, casio_uk_sitemap | `run_product_observation_pipeline` | YES | YES | YES | YES | No |
| casioblog_rss, gcentral_rss, plus9time_rss, monochrome_rss, deployant_rss, fratello_rss, watchtime_rss | `run_publication_pipeline` + `SpecialistLeadService.notify_new_lead`/`notify_correlation` | N/A (SpecialistLead, not Event) | N/A | YES, per-lead gated | YES, per-lead gated | No |
| (n/a — `run_casio_pipeline`) | `run_casio_pipeline` | — | — | — | — | Not a live entrypoint: called only from `tests/test_core.py`, never from `scripts/run_pipeline.py` or any scheduled/systemd path. Out of scope — not touched. |

**Conclusion: exactly one collector (`casio_multi`) was affected, for
exactly one reason (a missing parameter on one function), with no
manual-vs-scheduled asymmetry anywhere in the codebase.**

## Fix (Phase 2)

`run_multi_source_pipeline` gained `emit_events: bool = True`, threaded to
**both** of its internal call sites:

1. `process_news_announcement(..., emit_events=emit_events, notify=emit_events)`
   — the official news announcement path.
2. `process_fetch_result(..., emit_events=emit_events, notify=emit_events)`
   — the catalogue-enrichment path (Casio Japan, currently Akamai-blocked
   in production, but the code path is identical and now consistent).

This is the exact same contract `run_brand_news_pipeline` and
`run_product_observation_pipeline` already use — no new "scheduler
intelligence logic" was created, no enum/mode was invented; Casio's
production path was brought into line with the pattern every other
production lane already followed. `scripts/run_pipeline.py` was not
touched — `run_live_or_scheduled` still calls `run_multi_source_pipeline`
with no explicit `emit_events` argument, so both `--live` and `--scheduled`
now correctly inherit the new `True` default identically, preserving the
(correct) property that manual and scheduled behavior can never diverge
for this collector.

**A second, deeper defect found while testing the fix**: even with
`emit_events=True`, the notify threshold used by both `_record_watch_event`
and `_persist_product_event` was a hardcoded `100.0` for any non-
experimental (`experimental=False`, i.e. official/production) event —
and `score_event`'s real maximum for `NEW_REFERENCE`/`NEW_REGION` is 90
(30 base + 10 first-party + 20 recognisable-family + at most 10 each for
limited-edition/collaboration/unusual-material, all five simultaneously,
which essentially never happens). **100.0 was mathematically unreachable
for the official lane — Discord could never have fired for Casio even
after the `emit_events` fix, regardless of Discord configuration.** Fixed
by replacing both hardcoded `100.0` literals with a new
`Settings.discord_official_min_score` (default `50.0`, matching
`score_event`'s own HIGH-confidence cutoff — deliberately stricter than
the experimental lane's permissive `discord_experimental_min_score=0`,
preserving the intended official-vs-experimental distinction while making
it actually reachable). `DISCORD_OFFICIAL_MIN_SCORE` documented in
`.env.example` alongside the existing experimental variable.

**A third finding, caught by the regression test for the catalogue call
site specifically**: the first pass of this fix only updated the news-
announcement call site; the catalogue-enrichment `process_fetch_result`
call site was still missing `emit_events`/`notify`. Caught immediately by
`test_scheduled_casio_catalog_known_watch_new_region_creates_event_and_notifies`
(0 Events where 1 was expected), fixed before this change was ever
deployed anywhere.

## Discovery timestamp

2026-08-14T18:5x UTC (Discord-authority audit), confirmed and documented
2026-08-14/15 in this remediation sprint.
