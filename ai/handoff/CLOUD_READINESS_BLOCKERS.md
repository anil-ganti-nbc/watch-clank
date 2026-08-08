# Watch Clank — Cloud Readiness Blockers (Tier D: architecture prep only)

**Date:** 2026-08-08
**Branch:** `cloud/watch-prep`

This document lists the reasons Watch Clank stays in architecture-prep-only
status right now, and the concrete technical items that would need to change
before any real deploy, in addition to the reasons. It is a companion to
`CLOUD_READINESS_CHECKLIST.md`, which covers what's already portable.

## Why this stays prep-only (process blockers, not technical ones)

1. **Its own HANDOFF.md says not to.** The 2026-08-08 checkpoint entry ends
   with: *"Continue the soak per section 42; do not start Stage 2."* This
   instruction is about Watch Clank's own product roadmap (editorial scoring,
   multi-source ingestion, etc.), but it doubles as a hard constraint on this
   cloud-migration pass: there is an active, monitored soak test in progress
   and this work must not interfere with it in any way — not by touching the
   live DB, not by running the pipeline, not by starting or stopping the
   Windows scheduled task, not by building/running a container against the
   same `data/` directory.
2. **A live soak test is currently running.** Per HANDOFF.md: DB counts as of
   the last checkpoint were `collector_runs: 24`, `release_leads: 10`,
   `watches: 22`, with the scheduled task (`WatchClank-CasioJapan`, 90-minute
   interval) expected to keep firing and be checked for `SUCCESS`/`PARTIAL`
   (not `FAILED`) status. This branch's changes are additive-only (new files
   under `ai/handoff/`, two `.draft` files at repo root) specifically so nothing
   here can affect that soak in progress.
3. **Tier D classification.** Per the cloud-migration project's own tiering,
   Watch Clank is architecture-preparation-only: the deliverable for this
   pass is planning artifacts, not working infrastructure. No image is built,
   no container runs, no compose stack starts.

## Known product-level risk carried forward (not something this pass can fix)

- **Akamai bot protection on Casio Japan's catalogue pages.** Documented in
  Watch Clank's own README.md ("Site applies Akamai bot protection; some
  environments receive 403") and reconfirmed in the same 2026-08-08 HANDOFF.md
  checkpoint ("Casio Japan catalogue + G-Shock/Edifice/Oceanus/Pro Trek listing
  pages: HTTP 403, confirmed still Akamai-blocked, as expected"). This is a
  known, accepted limitation of the *current* Stage 1 scope (the pipeline
  correctly classifies these as `BLOCKED`, not `FAILED`, and continues), not a
  containerization problem — but it is directly relevant to any future cloud
  deploy decision: **moving the collector to run from a different network
  origin (a cloud egress IP instead of the current local/residential one) has
  a real chance of changing the bot-protection outcome, for better or worse,
  and has not been tested.** This should be a named risk in any future go/no-go
  for actually deploying Watch Clank's pipeline service, not something to
  discover after the fact.

## Concrete technical items for a real deploy (not started here)

These are listed for a future session that actually starts Stage 2 /
containerization work — none of them are done, and doing them is out of
scope for this pass.

1. **Migration-application strategy.** The soak-day outage in HANDOFF.md
   (DB pinned at `002_ops_statuses` while code was at `003_release_leads`,
   causing every scheduled run to fail with `no such table:
   source_component_states`) happened because `alembic upgrade head` is a
   manual step that's easy to skip. A container-based deploy needs a
   deliberate answer for how migrations get applied — see the note at the
   bottom of `Dockerfile.draft` for two sketched options (separate one-shot
   migrate invocation vs. a startup check that refuses to run on schema
   drift). Not decided here.
2. **`/health` lacks version/build identity.** Assessed in detail in
   `health_identity_adapter.draft.md`: the endpoint already does real
   dependency checks (a genuine strength), but has no git-sha/image-tag/
   build-time fields, no liveness-vs-readiness distinction, and no explicit
   Alembic-head-vs-code-head check (as opposed to the incidental
   missing-table check that happened to catch the one outage that occurred
   so far). Design sketched, not implemented.
3. **SQLite-on-shared-volume constraints for the two-service split.** If
   `pipeline` and `dashboard` ever run concurrently against the same SQLite
   file (WAL mode) from two separate containers, this needs the same
   care already present in `app/services/run_lock.py`'s single-process lock
   — a second container process is not automatically covered by that lock.
   Not evaluated in depth in this pass; flagged for whoever picks this up.
4. **Dashboard exposure.** `docker-compose.draft.yml` binds it to
   `127.0.0.1:8765:8765` and gates it behind a compose `profile` so it isn't
   started by default. Any actual multi-host/cloud deployment needs a real
   decision about how (or whether) to expose it at all — the draft
   deliberately does not solve this, only avoids the unsafe default (public
   port).
5. **Base image / dependency pinning not verified.** `Dockerfile.draft` was
   written by inspection of `pyproject.toml`, not by building it. Whether
   `selectolax` and other compiled dependencies need extra system packages on
   `python:3.12-slim` is unverified — flagged explicitly in the Dockerfile's
   own comments rather than guessed at.

## What this pass deliberately did not touch

- `app/collectors/`, `app/parsers/`, `app/normalization/`, `app/services/`
- `alembic/` (no new migrations, no changes to existing ones)
- Any existing test file
- The live soak test, its DB (`data/watch_clank.db`), its snapshot store
  (`data/snapshots/`), its Windows Scheduled Task (`WatchClank-CasioJapan`),
  or its logs
- `app/main.py`'s `/health` route (assessed only, not modified)
- `.env.example` (reviewed only — already clean, no changes needed)
