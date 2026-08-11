# Incident: Epoch 1 stale material surfaced as "new" (2026-08-11, Sprint 8)

## Symptom

After Epoch 1 went live, the Windows Control Centre GUI's Recent
Intelligence tab showed specialist-lead articles published in
March-August as if they were current — e.g. a G-Central article about a
UFC fighter wearing a G-Shock (published 2026-08-08, three days before
Epoch 1's baseline) sat at the top of the list next to a Plus9Time catalog
scan from a 1973 Citizen brochure (published 2026-07-10) and a CASIOBLOG
rumor post from 2026-03-28 — all displayed with no visual distinction, all
sorted as if equally "recent."

## Investigation (real data, not hypothesis)

Queried every `specialist_leads` row directly:

```
total leads: 50
```

**Every single one has `is_baseline = True` and `epoch_id = 1`.** Their
`published_at` values range from 2026-03-13 to 2026-08-08 (a 5-month
spread); their `discovered_at` values are all within the ~2-minute window
of the Epoch 1 baseline run (2026-08-11 13:23:58 - 13:24:02 UTC).

### One item traced end to end

**Lead id=13**, source `g_central`:
- title: "Rapper Larry June's Midnight Organic releases limited G-Shock DW6900MO26-4"
- `published_at`: 2026-07-26 08:44:21 UTC (16 days before evaluation)
- `discovered_at`: 2026-08-11 13:23:59 UTC (during Epoch 1 baseline)
- `is_baseline`: True, `epoch_id`: 1
- correlation: UNCONFIRMED, no `correlated_watch_id`
- collector run: `gcentral_rss` run_id=1, the Epoch 1 baseline run

**Trace:**
1. **SOURCE** — g-central.com's real, live RSS feed (confirmed, this
   article genuinely exists at that URL and that publish date).
2. **DISCOVERY** — `run_gcentral_pipeline` (Epoch 1 baseline run, executed
   correctly, exactly once, no re-fetch).
3. **PARSER** — `parse_gcentral_feed` correctly extracted the real
   `pubDate` and converted it to `2026-07-26T08:44:21+00:00`. No parsing
   bug: the timestamp stored is accurate.
4. **PERSISTENCE** — `SpecialistLeadService.ingest_candidate` correctly
   stored `published_at` and, per Sprint 7's baseline-suppression work,
   correctly stamped `is_baseline=True`, `epoch_id=1`.
5. **LEAD/EVENT CREATION** — no `Event` was created (confirmed: `events`
   table has 0 rows). Sprint 7's baseline guard worked exactly as
   designed here.
6. **FRESHNESS/CLASSIFICATION** — **this step did not exist.** There was
   no code anywhere that compared `published_at` against "now" or against
   any freshness window. `is_baseline` existed as a flag but nothing
   downstream ever read it for display purposes.
7. **GUI QUERY** — `local_windows/control_centre/data_access.py::get_recent_leads()`:
   ```python
   rows = session.query(SpecialistLead).order_by(SpecialistLead.discovered_at.desc()).limit(limit).all()
   ```
   No `WHERE` clause at all beyond the implicit "all rows." Ordered
   purely by `discovered_at`.
8. **DISPLAY** — the GUI's "Time" column in the Recent Intelligence table
   renders `l.discovered_at` (see `main.py::IntelligenceTab`), i.e. it
   shows "2026-08-11" as the displayed timestamp for a July 26 article —
   actively misleading, not just unfiltered.

## Proven root cause

**Two compounding defects, both real, both required for the symptom:**

1. **Missing concept**: nothing in the persistence layer ever computed
   "is this evidence temporally fresh," only "has Clank seen it before"
   (`is_baseline`/dedup-by-URL). Discovery novelty and editorial freshness
   were conflated by omission — there was no freshness field to conflate
   *with*, freshness simply didn't exist as a concept.
2. **GUI display bug**: even with `is_baseline` available, the query never
   filtered on it, and the displayed "Time" column showed discovery time
   dressed up as if it were publication/event time.

This is **not** an epoch/baseline defect. Sprint 7's baseline mechanism
worked exactly as specified — it correctly created real evidence rows,
correctly tagged them `is_baseline=True`, and correctly prevented any
`Event` or Discord alert from being created. The defect is entirely in
(a) the absence of a freshness concept downstream of `is_baseline`, and
(b) the GUI's query/display never using `is_baseline` (or any other
freshness signal) to decide what counts as "recent."

## Per-source stale-item audit (before fix)

| Source | Total leads | Oldest published_at | Newest published_at | All is_baseline? |
|---|---|---|---|---|
| CASIOBLOG | 10 | 2026-03-28 | 2026-06-02 | Yes |
| G-Central | 20 | 2026-06-18 | 2026-08-08 | Yes |
| Plus9Time | 20 | 2026-03-13 | 2026-07-10 | Yes |

All 50 are baseline-era leads (correctly so — they were discovered during
the Epoch 1 baseline run and correctly marked as such). None of them
should ever have appeared as "new" in the GUI; the fix (below) reclassifies
them as `BASELINE` freshness and the GUI stops showing them by default,
without deleting any of them.
