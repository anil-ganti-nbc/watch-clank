# Incident: GGW lead 119 null delivery state

**Status: OPEN.** Do not mark remediated from 018 soak alone.

Great G-Shock World lead 119 (`GWG-B1000-1A3JF` + `GWF-D1000BC-1JF`) persisted with `delivery_state=NULL` and `notified_at=NULL`. Migration 018 made that class diagnosable. Migration 019 (undeployed) is the stronger insert-time persistence guarantee. Those are two different claims.

## Revisions (resolved, not guessed)

| Role | Full SHA (`git rev-parse`) | Alembic | Production |
|---|---|---|---|
| Live soak | `1b80b8d532cd6f689df14d940240d4ac3bbfb6db` | `018_lead_delivery_terminal_state` | **yes** — image `watch-clank:1b80b8d532cd6f689df14d940240d4ac3bbfb6db` |
| Insert-time invariant | `6fd1042bc79c71337344f37192f5a1487ecd2ddc` | `019_lead_ingest_unresolved` | **no** — do not deploy during 018 soak |

An earlier report invented a full hash for `6fd1042`. Discard it. The only accepted 019 SHA is the `git rev-parse 6fd1042` value above.

## Verified deployment cutoff

SQLite-safe backup immediately before 018 migrate:

`/home/anilganti/watch-clank-backups/watch_clank.db.pre-018-1b80b8d-20260909T132823Z`

**Cutoff:** `2026-09-09T13:28:23Z`. Soak cohort = specialist leads with `created_at >=` that instant. Legacy gated/`sent` rows before the cutoff may lack `delivery_reason` / `delivery_receipt_id`; those rows are not 018 sign-off evidence.

Lead 119 and the other 110 `unresolved_historical` / `PRE_TERMINAL_STATE_CONTRACT` rows stay unresolved until individually evidenced. Do not backfill them as sent or gated.

## Review windows

| Window | Earliest review |
|---|---|
| 48-hour | **2026-09-11 13:28 UTC** |
| 72-hour | **2026-09-12 13:28 UTC** |

Do not poll sources solely to force Discord. Natural specialist timer traffic only.

## What a passing 018 soak proves

Observed post-018 behaviour on newly created leads: non-null terminal state after a completed source run, machine-readable gate reasons, receipts for attempted sends (including failures and correlation follow-ups), no duplicate *same-purpose* resend, no unexpected collector failures.

It does **not** prove crash-after-insert cannot leave NULL (that is 019). It does **not** close this incident.

## What 019 still owes after 018 soak

1. Deploy only the reviewed commit `6fd1042bc79c71337344f37192f5a1487ecd2ddc` (re-resolve with `git rev-parse` on the deploy host).
2. Apply **only** `019_lead_ingest_unresolved` after a SQLite-safe backup.
3. Confirm 018 historical rows remain `unresolved_historical` (019 must not rewrite them).
4. Open a **separate** observation window: every newly ingested lead is non-null **at INSERT**, including a crash/skip before notify (`unresolved` / `INGEST_UNFINALIZED` or a later finalized outcome). Zero new NULLs is necessary but not sufficient; ingest-unfinalized leftovers after a completed run must be `failed` / `RUN_COMPLETION_MISSING_OUTCOME`.

## 018 soak sign-off criteria (cohort-scoped)

All counts below are **created after the cutoff**, unless labelled historical.

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
WHERE created_at >= '2026-09-09 13:28:23'
  AND delivery_state IS NULL;
```

Must be 0. Pre-cutoff NULLs were backfilled to `unresolved_historical` and are out of this check.

### 3. Reasons on new gated/failed/unresolved rows

```sql
SELECT COUNT(*) FROM specialist_leads
WHERE created_at >= '2026-09-09 13:28:23'
  AND delivery_state IN ('gated','failed','unresolved','unresolved_historical')
  AND (delivery_reason IS NULL OR delivery_reason = '');
```

Must be 0. Do **not** run this against pre-cutoff `gated` rows.

### 4. Notification attempts and receipts (not only `provider_*`)

An attempt is a `delivery_receipts` row for `entity_type='SPECIALIST_LEAD'` with `first_attempt_at` or `created_at` ≥ cutoff. Purposes are separate: `lead_early_warning` and `lead_correlation`. Count **FAILED / ATTEMPTED / PROVIDER_ACCEPTED / PROVIDER_IDENTIFIED**. Checking only lead `delivery_state IN ('provider_accepted','provider_identified')` misses failed sends and correlation follow-ups.

```sql
SELECT purpose, lifecycle_state, COUNT(*)
FROM delivery_receipts
WHERE entity_type = 'SPECIALIST_LEAD'
  AND created_at >= '2026-09-09 13:28:23'
GROUP BY 1, 2;
```

Join check: every soak-cohort lead with `delivery_state='failed'` and reason `PROVIDER_ERROR` or `NOTIFIER_UNAVAILABLE` after a send was attempted must have a matching receipt for that purpose (or an explicit `NOTIFIER_UNAVAILABLE` with no webhook — no receipt is then expected). Every `provider_accepted` / `provider_identified` cohort lead must have `delivery_receipt_id` and a receipt row. Receipt **count** after cutoff must equal new attempt rows, not the pre-cutoff fleet total of 175.

Do not treat HTTP accepted as operator-visible.

### 5. Duplicates: purpose + newly introduced references

Dedup is per **notification purpose**. Early-warning and correlation must not suppress each other.

Set-overlap gating (`DUPLICATE_REFERENCE_ALREADY_ALERTED`) is **not** an automatic pass. For each soak-cohort lead with that reason:

1. Take its `reference_candidates`.
2. Subtract the union of candidates on *earlier* FRESH leads that have `notified_at` set (same early-warning purpose).
3. If the remainder is **non-empty**, a grouped story introduced a new reference that 018 suppressed. Record as a **fail** of the “no lost new reference” criterion, even if no second Discord send occurred.

A grouped GGW-class item that repeats one old SKU and adds a new SKU must not be signed off as healthy dedup.

Same-purpose resend of an already `notified_at` lead must still be zero extra sends.

### 6. Collector health (unchanged scope)

No unexpected specialist-run `FAILED` cluster. Goldsmiths UK and Citizen DE timers remain disabled/inactive. No source expansion. No live poll to force an alert.

### 7. Historical rows (negative control)

```sql
SELECT COUNT(*) FROM specialist_leads
WHERE delivery_state = 'unresolved_historical'
  AND delivery_reason = 'PRE_TERMINAL_STATE_CONTRACT';
```

Must remain 111 (or the pre-soak count if a human later evidenced individual rows — none should be auto-converted). Lead 119: still no `notified_at`, no fabricated receipt.

## Sign-off language

- **018 soak pass:** “Observed 018 behaviour on post-cutoff leads is acceptable.” Incident stays **OPEN**.
- **018 soak fail / inconclusive:** do not deploy 019; do not close the incident.
- **Incident close:** only after 019 is deployed from the resolved SHA, verified, and its own observation window is recorded separately from this 018 soak.
