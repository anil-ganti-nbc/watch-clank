# WATCH CLANK — EPOCH 1

Generic, portable record of the Sprint 7 operational reset. Machine-specific
details (exact archive path, local DB file locations) are documented
separately in HANDOFF.md's Sprint 7 checkpoint, not here.

## Epoch 1

- **Start timestamp:** 2026-08-11T13:11:25Z
- **Baseline started:** 2026-08-11T13:22:08Z
- **Baseline completed:** 2026-08-11T13:24:31Z
- **Git commit at reset:** `425b0e5` (Sprint 6 HANDOFF checkpoint) — Sprint 7's
  own changes land on top of this, see HANDOFF.md for the exact ending commit.
- **Schema version:** `006_operational_epochs`

## Active sources at Epoch 1 baseline

**OFFICIAL**
- Casio (casio_intl_news + casio_japan, collector_id `casio_multi`)
- Citizen news (`citizen_news`)
- Citizen products (`citizen_products`)
- Seiko news (`seiko_jp_news`)
- Seiko products (`seiko_products`)

**SPECIALIST**
- CASIOBLOG (`casioblog_rss`) — tier 2
- G-Central (`gcentral_rss`) — tier 2, new this sprint
- Plus9Time (`plus9time_rss`) — tier 2, new this sprint

**MANUAL_INGESTION**
- Geesgshock (Instagram, `--ingest-manual-lead`)

**RETAILER_EARLY_LISTING**
- None automated yet. NEEL and Japan Select investigated (Japan Select
  confirmed real, cheap, Shopify-based, same class of source as the
  already-proven Citizen/Seiko product collectors) but deliberately deferred
  this sprint to stay in scope — see HANDOFF.md for the full research notes.

## Baseline counts (silent, zero editorial noise)

| Source | New watches / leads | New Events | Alerts sent |
|---|---|---|---|
| Casio (casio_multi) | 9 new watches | 0 | 0 |
| Citizen news | 9 new watches, 10 leads | 0 | 0 |
| Seiko news | 0 new watches, 1 lead | 0 | 0 |
| Citizen products | 291 new watches | 0 | 0 |
| Seiko products | 222 new watches | 0 | 0 |
| CASIOBLOG | 10 leads | 0 | 0 |
| G-Central | 20 leads | 0 | 0 |
| Plus9Time | 20 leads | 0 | 0 |
| **Total** | **555 watches, 50 specialist leads** | **0** | **0** |

Immediate repeat run against unchanged source state: **0 new watches, 0 new
events, 0 new specialist leads, 0 alerts** across all 8 sources — verified,
not assumed.

## What this means going forward

Every watch/observation/lead created during the baseline window is
permanently marked `is_baseline=true` with `epoch_id` pointing at this row
(see `app/models/epoch.py`, `operational_epochs` table). It is real
discovery data — correlation and lead-time math can still use it — but it
will never retroactively become an Event or a Discord alert. Anything
discovered *after* baseline_completed_at follows normal editorial rules.
