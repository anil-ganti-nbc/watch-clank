# Incident: GGW lead 119 null delivery state

**Status: OPEN.** Do not mark remediated from 018 soak alone.

Great G-Shock World lead 119 (`GWG-B1000-1A3JF` + `GWF-D1000BC-1JF`) persisted with `delivery_state=NULL` and `notified_at=NULL`. Migration 018 made that class diagnosable. Migration 019 (undeployed) is the stronger insert-time persistence guarantee. Those are two different claims.

## Revisions (resolved, not guessed)

| Role | Full SHA (`git rev-parse`) | Alembic | Production |
|---|---|---|---|
| Live soak | `1b80b8d532cd6f689df14d940240d4ac3bbfb6db` | `018_lead_delivery_terminal_state` | **yes** — image `watch-clank:1b80b8d532cd6f689df14d940240d4ac3bbfb6db` |
| Insert-time invariant | `6fd1042bc79c71337344f37192f5a1487ecd2ddc` | `019_lead_ingest_unresolved` | **no** — do not deploy during 018 soak |

An earlier report invented a full hash for `6fd1042`. Discard it. The only accepted 019 SHA is the `git rev-parse 6fd1042` value above.

## Deployment clocks (do not collapse)

The original soak “cutoff” was a **backup timestamp**, not migrate-complete and not first 018 process start. Those three instants are different. Sign-off must count leads in each interval separately.

| Instant | UTC | Evidence |
|---|---|---|
| **T0 backup** | `2026-09-09T13:28:23Z` (filename); file closed `13:28:27Z` | `~/watch-clank-backups/watch_clank.db.pre-018-1b80b8d-20260909T132823Z` |
| **T_schema** | after T0, **no later than** `13:29:34Z` | Alembic `018` applied in a one-shot container. No migrate log line with a clock was retained; the next durable host write is docker.env. |
| **T_armed** | `2026-09-09T13:29:34Z` | `~/.config/watch-clank/docker.env` mtime; user systemd **Started** every enabled Watch Clank timer. Image OCI revision `1b80b8d532cd6f689df14d940240d4ac3bbfb6db`. |
| **T_first_018** | `2026-09-09T13:32:47Z` unit start; `13:32:50.213765Z` `pipeline_start` | `watch-clank-casio-multi.service` → `collector_runs.id=7796`. First specialist 018 run among the same burst: `gear_patrol_rss` `7799` at `13:32:50.619500Z`. First Great G-Shock World 018 run: `7811` at `14:00:04.886873Z`. |

Last pre-018 writer: Horology Sentinel run `7795` completed `13:23:53Z`. Collectors were stopped during backup/migrate.

Observed at 2026-09-09 evening recon (still account again at 48h/72h review):

| Interval | Lead `created_at` | Count then |
|---|---|---|
| T0 ≤ t < T_armed | backup-to-armed | **0** |
| T_armed ≤ t < T_first_018 | armed, no 018 process yet | **0** |
| t ≥ T_first_018 | 018 soak cohort | **4** (will grow) |

**018 soak cohort for sign-off** = `created_at >= 2026-09-09 13:32:50`.

**Interstitial buckets** (T0–T_armed and T_armed–T_first_018) must be re-counted at review. If either is ever non-zero, those rows are **not** 018-process evidence: schema may already be 018, but no 018 collector had started. Report them separately; do not fold into the soak pass/fail numerator.

Lead 119 remains **historical evidence** of the null-path class and of the any-overlap grouped-ref mechanism. Its **actual disposition is unresolved** (`unresolved_historical` / `PRE_TERMINAL_STATE_CONTRACT`, `notified_at` NULL). Do not backfill it, or the other 110 historical rows, as sent or gated.

## Review windows

Reviews at **13:28 UTC** are **preliminary**. They are measured from T0 backup and **precede** 48 / 72 hours of actual 018 execution.

| Kind | 48-hour end | 72-hour end | Label |
|---|---|---|---|
| T0 backup (13:28:23Z) | **2026-09-11 13:28 UTC** | **2026-09-12 13:28 UTC** | **Preliminary** — not a full process-based window |
| Process-based (T_first_018 `pipeline_start`) | **2026-09-11 13:32:50.213765 UTC** | **2026-09-12 13:32:50.213765 UTC** | Full 018 execution window |
| GGW-specific (first GGW 018 run 7811) | **2026-09-11 14:00:04 UTC** | **2026-09-12 14:00:04 UTC** | Full GGW 018 execution window |

Do not poll sources solely to force Discord. Natural specialist timer traffic only. A preliminary review may record observed outcomes so far; it cannot certify a full 48h / 72h of 018 process time.

## What a passing 018 soak proves

Observed 018 outcomes on post-T_first_018 leads after a completed source run: non-null terminal state, machine-readable gate reasons, receipts for *recorded* sends that reached `_deliver_lead_alert` (including failures and correlation follow-ups), no duplicate *same-purpose* resend, no unexpected collector failures.

A soak pass **explicitly retains** the receipt blind spot and the known any-overlap grouped-ref defect. **Neither** zero overlap instances **nor** zero new null states establishes overall delivery correctness. Zero new nulls is a necessary cohort check only. Zero `DUPLICATE_REFERENCE_ALREADY_ALERTED` rows does not clear the overlap defect.

It does **not** prove crash-after-insert cannot leave NULL (that is 019). It does **not** close this incident. It does **not** resolve lead 119’s actual disposition.

## What 019 still owes after 018 soak

1. Deploy only the reviewed commit `6fd1042bc79c71337344f37192f5a1487ecd2ddc` (re-resolve with `git rev-parse` on the deploy host).
2. Apply **only** `019_lead_ingest_unresolved` after a SQLite-safe backup.
3. Confirm 018 historical rows remain `unresolved_historical` (019 must not rewrite them).
4. Open a **separate** observation window: every newly ingested lead is non-null **at INSERT**, including a crash/skip before notify (`unresolved` / `INGEST_UNFINALIZED` or a later finalized outcome). Zero new NULLs is necessary but not sufficient; ingest-unfinalized leftovers after a completed run must be `failed` / `RUN_COMPLETION_MISSING_OUTCOME`.

## 018 soak sign-off criteria (cohort-scoped)

All counts below are **created after T_first_018 (`2026-09-09 13:32:50`)**, unless labelled historical or interstitial.

### 1. Sample is real delivery-path traffic

`cohort_n = 0` → **INCONCLUSIVE**, not pass. “No new leads” is not evidence that gating, sending, or dedup worked.

Record, at review time:

| Path | Count required to *claim* that path |
|---|---|
| Gated (any 018 reason) | ≥1 to claim gating observed |
| Send attempted (receipt exists for that lead+purpose) | ≥1 to claim send path observed |
| Dedup gated `DUPLICATE_REFERENCE_ALREADY_ALERTED` | ≥1 to claim dedup observed; else **unobserved**, not proven |

A pass may still be recorded for *observed* paths if others are honestly marked unobserved. Do not infer unobserved paths from silence.

### 2. Null state (new leads only)

```sql
SELECT COUNT(*) FROM specialist_leads
WHERE created_at >= '2026-09-09 13:32:50'
  AND delivery_state IS NULL;
```

Must be 0. Also count the two interstitial windows; if they contain NULLs, report them as non-018-process anomalies, not as soak-cohort failures.

Zero new nulls does **not** establish overall delivery correctness. Pre-T0 NULLs were backfilled to `unresolved_historical` and are out of this check.

### 3. Reasons on new gated/failed/unresolved rows

```sql
SELECT COUNT(*) FROM specialist_leads
WHERE created_at >= '2026-09-09 13:32:50'
  AND delivery_state IN ('gated','failed','unresolved','unresolved_historical')
  AND (delivery_reason IS NULL OR delivery_reason = '');
```

Must be 0. Do **not** run this against pre-cutoff `gated` rows.

### 4. Notification attempts vs receipts (receipts are incomplete)

Receipts **record** attempts that reached `DeliveryReceiptService.record`. They **cannot prove** every intended send produced a receipt.

**018 dispatch path (code, `1b80b8d`):** `_deliver_lead_alert` calls `send_editorial_alert` then always `receipts.record` if that function returns. Real `DiscordNotifier._post_detailed` swallows HTTP exceptions and returns a `DeliveryAttempt` (never raises). Tests: successful send and `send=False` / 500 both persist a receipt (`tests/test_specialist_lead_terminal_state.py`).

**Known receipt-less branches (expected, not soak failures):**

- Policy gates (baseline, stale, editorial disabled, confidence floor, duplicate-ref) never call `_deliver_lead_alert`.
- `NOTIFIER_UNAVAILABLE` because `editorial_enabled` is false (no webhook) returns before HTTP.

**Blind spot (label explicitly at sign-off):**

- `notify_new_lead` / `notify_correlation` wrap the inner path in `except Exception` and write `failed` / `NOTIFIER_UNAVAILABLE` **without** a receipt if anything raises *before* `receipts.record` (e.g. `get_source_profile`, `format_*_alert`, or a mock/`MagicMock` notifier that raises). There is **no independent attempt table**. Host structlog (`lead_early_warning_failed`, `discord_post_failed`, `discord_post_exception`) is the only other dispatch evidence and is not a durable ledger.
- Therefore: receipts ⊆ recorded attempts. Intended-but-unrecorded attempts are **unobservable from SQLite alone**. Soak pass language is “every *recorded* send/fail that reached `_deliver_lead_alert` has a receipt,” not “every attempt has a receipt.”

Reconcile at review:

```sql
-- recorded attempts (both purposes, all lifecycle states)
SELECT purpose, lifecycle_state, COUNT(*)
FROM delivery_receipts
WHERE entity_type = 'SPECIALIST_LEAD'
  AND created_at >= '2026-09-09 13:32:50'
GROUP BY 1, 2;

-- failed cohort leads with no receipt (blind-spot candidates)
SELECT l.id, l.delivery_reason
FROM specialist_leads l
WHERE l.created_at >= '2026-09-09 13:32:50'
  AND l.delivery_state = 'failed'
  AND l.delivery_receipt_id IS NULL;
```

Every cohort `provider_accepted` / `provider_identified` lead must have `delivery_receipt_id`. `PROVIDER_ERROR` after `_deliver_lead_alert` must have a FAILED receipt. `NOTIFIER_UNAVAILABLE` with no receipt is either the expected no-webhook gate or the exception blind spot — distinguish via reason + logs, do not treat as a silent pass.

Do not use the pre-T0 fleet receipt total (175) as the soak numerator. Do not treat HTTP accepted as operator-visible.

### 5. Duplicates: known 018 defect (any-overlap suppresses the whole lead)

**Status: known defect in live 018 (`1b80b8d`).** Unchanged: `notify_new_lead` gates the entire lead when `reference_candidates` intersects **any** other lead with `notified_at` set (`any(... intersection ...)`, then `DUPLICATE_REFERENCE_ALREADY_ALERTED`). It does not notify the remainder set. Undeployed 019 does not fix this.

This is a sign-off defect **even if the natural soak never exercises it.** Do not treat “no DUPLICATE_REFERENCE_ALREADY_ALERTED rows” as proof the grouped-ref path is healthy. Zero overlap instances do **not** establish overall delivery correctness.

**Lead 119 / Mudmaster:** lead `113` (`2026-08-29`, `delivery_state=sent`, `notified_at` set) already carried `GWF-D1000BC-1JF`. Lead `119` (`2026-08-31`) is the grouped GGW story `GWF-D1000BC-1JF` + **`GWG-B1000-1A3JF`**. 119 is **historical evidence** of this mechanism; its **actual disposition remains unresolved** (`unresolved_historical`; notify never left a terminal state — the original null-path bug). We **cannot** prove 018 duplicate-gated that row. We **can** prove that if notify had run under 018’s overlap rule, Frogman overlap with 113 would have suppressed the **whole** lead, including the new Mudmaster SKU. That is the lost-Mudmaster mechanism: set-overlap, not missing parser extraction.

Dedup remains per **notification purpose** (early-warning vs correlation must not suppress each other).

At review, still run the remainder-set check on any soak-cohort `DUPLICATE_REFERENCE_ALREADY_ALERTED` rows. A non-empty remainder is an **instance** of this known defect, not a surprise. Same-purpose resend of an already `notified_at` lead must still be zero extra sends.

## Next independent repair (not this soak; not 019)

**Partial-reference deduplication.** An already-alerted Frogman must not suppress a newly introduced Mudmaster in the same lead. Preserve the **full source record**. Evaluate notification eligibility **per reference and per purpose**. Do not drop the remainder set, and do not rewrite historical lead 119 into a fabricated sent/gated outcome.

This repair is independent of 019 (insert-time non-null). Do not start it during 018 soak. Do not fold it into an 019 deploy. Production stays on 018 until this soak’s process-based windows are reviewed.

### 6. Collector health (unchanged scope)

No unexpected specialist-run `FAILED` cluster. Goldsmiths UK and Citizen DE timers remain disabled/inactive. No source expansion. No live poll to force an alert.

### 7. Historical rows (negative control)

```sql
SELECT COUNT(*) FROM specialist_leads
WHERE delivery_state = 'unresolved_historical'
  AND delivery_reason = 'PRE_TERMINAL_STATE_CONTRACT';
```

Must remain 111 (or the pre-soak count if a human later evidenced individual rows — none should be auto-converted). Lead 119: still no `notified_at`, no fabricated receipt; historical evidence only; actual disposition unresolved.

## Sign-off language

- **Preliminary review (11/12 Sep 13:28 UTC):** snapshot of observed 018 outcomes so far. Not a full 48h / 72h of 018 process time. Cannot certify the process-based or GGW-specific windows.
- **018 soak pass (after process-based ends 11/12 Sep 13:32:50.213765 UTC; GGW-specific 11/12 Sep 14:00:04 UTC):** “Observed 018 outcomes on post-T_first_018 leads are acceptable **while retaining** the receipt blind spot and the known any-overlap grouped-ref defect.” Neither zero overlap instances nor zero new nulls establishes overall delivery correctness. Incident stays **OPEN**. Lead 119 stays historical with unresolved disposition. Interstitial buckets must be zero or explained.
- **018 soak fail / inconclusive:** do not deploy 019; do not close the incident.
- **Incident close:** only after 019 is deployed from the resolved SHA, verified, its own observation window is recorded separately, **and** the partial-reference dedup repair is either shipped or explicitly accepted as remaining design debt. Lead 119 is not closed by soak silence.
