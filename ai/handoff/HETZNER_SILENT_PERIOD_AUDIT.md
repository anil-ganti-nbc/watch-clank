# Hetzner silent-period audit — 2026-08-15

Read-only inspection of the live Hetzner database (via a throwaway copy
inside a disposable container, never written back — no historical row was
altered). Answers Phase 5's question directly: **did anything editorially
meaningful clank into the void while notifications were unavailable?**

## Events (the `casio_multi` defect)

```sql
select count(*) from events;
-- 0
```

**Zero.** This is a clean, simple answer, not a hedge: `casio_multi` has
never created a single `Event` row on this database, ever (confirmed
across 70+ historical `collector_runs` before this sprint and every run
since the Hetzner redeploy). There is therefore nothing to retroactively
audit for missed editorial alerts from this defect specifically — there
was never anything eligible to notify about in the first place. The
defect's cost was structural (a capability that didn't exist), not a
backlog of missed real stories.

## SpecialistLeads (the missing-webhook problem)

```sql
select editorial_freshness, count(*) from specialist_leads group by editorial_freshness;
-- BASELINE: 9
-- STALE_PUBLICATION: 50
-- FRESH: (none)
```

**Zero `FRESH` leads exist**, and `notify_new_lead`'s actual gate
(`editorial_freshness == "FRESH" AND confidence >= discord_specialist_min_confidence [40]`)
matches zero rows. Every lead in the database today is either:

- **9 `BASELINE`** — correctly, deliberately silent (the mandatory
  force-baseline sequence performed during the Hetzner redeploy sprint).
- **50 `STALE_PUBLICATION`** — genuinely old articles (sampled range:
  2026-07-10 to 2026-08-08, all discovered for the first time on
  2026-08-14 when G-Central/Plus9Time/etc. were onboarded to this
  database) correctly excluded from "current news" by the pre-existing
  Sprint 8 freshness architecture (`ai/handoff/INCIDENT_EPOCH1_FRESHNESS.md`)
  — the exact "moldy bread" protection this project has repeatedly
  hardened. Sample: G-Central items about a UFC fighter's G-Shock, August
  new-release roundups, a Larry June collab, a Roblox tie-in — all real,
  all correctly judged not-current by the system's own design, independent
  of whether Discord was configured.

**Would any of these have notified under corrected production semantics?**
No — not because of the webhook or the `casio_multi` bug, but because none
of them clear the pre-existing, unrelated freshness/baseline gates that
were never broken. This is a genuine, mechanical answer, not an assumption.

## Conclusion

**Did anything useful clank into the void? No — zero eligible Events, zero
eligible SpecialistLeads existed during the silent period on this
database.** The practical cost of both defects (silent `casio_multi`, no
webhook) was entirely prospective — capability that didn't exist yet, not
a backlog of real stories that were generated and then lost. No historical
row requires replay, backfill, or correction. This audit did not alter,
delete, or replay anything.
