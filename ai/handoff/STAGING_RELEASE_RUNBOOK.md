# Staging release runbook — Watch Clank on Hetzner

Under construction / not yet production. No production tier exists for
this clank. See `CLOUD_READINESS_BLOCKERS.md` for the architectural
decisions this deployment implements (migration strategy, SQLite
coordination model).

## Per-release identity (fill in every time)

| Field | Value |
|---|---|
| development branch | `feature/cloud-migration-hetzner` (merged) |
| candidate commit | (fill in at merge) |
| candidate image | `watch-clank:<short-sha>` |
| staging state path | Docker named volume `watch_clank_staging_data` — fresh cloud baseline, never the Windows soak DB |
| staging schedule | disabled by default; enable only for a deliberate soak run |
| migration | run explicitly via `docker compose -f docker-compose.staging.yml run --rm migrate` before the first pipeline run on a fresh volume, and again before switching to any future revision that adds a migration |
| rollback image | previous candidate's image, kept loaded |

## Procedure

1. **Build on Hetzner from the merged GitHub revision**:
   `GIT_REVISION=$(git rev-parse HEAD) IMAGE_TAG=$(git rev-parse --short HEAD) docker compose -f docker-compose.staging.yml build`
2. **Provenance verification**: confirm `org.opencontainers.image.revision`
   (OCI label) == `python -m scripts.identity`'s `source_revision` == the
   GitHub commit SHA.
3. **Migrate the fresh volume explicitly** (required — the pipeline refuses
   to run on schema mismatch, including a fresh/uninitialized DB, by
   design): `docker compose -f docker-compose.staging.yml run --rm migrate`
4. **Verify schema**: confirm `migrate`'s output reports the expected head
   reached.
5. **Real collector cycle**: `docker compose -f docker-compose.staging.yml run --rm watch-clank`
6. **Persistence + idempotency**: a second real run against the same
   volume should not fabricate new watches/events on unchanged upstream
   content.
7. **Locking**: this app has its own internal run-lock
   (`app/services/run_lock.py`), but its stale-lock detection is
   PID-liveness-based, which is **not reliable across separate Docker
   containers** -- every container is PID 1 in its own namespace, so one
   container's fresh lock looks "dead" to another and gets removed. Proven
   with a real deliberate overlap test on Hetzner (two separate `docker
   compose run` invocations, fired truly simultaneously, both completed as
   full writers before this was caught). The internal lock still provides
   value for single-process crash recovery; it is not sufficient by itself
   for this deployment model. **An external `flock` wrapper around the
   cron invocation is required** (same proven pattern as Chinese Tech
   Wire) -- see `deploy_run.sh` on the host. Verified after adding it: a
   second invocation is refused immediately (no output, container never
   starts) while the first completes normally.
8. **No production promotion** in the current maturity policy — this stays
   under construction / staging-soak until the user explicitly reclassifies it.

## Known, carried-forward risk (not resolved by this deployment)

**Akamai bot protection on Casio Japan's catalogue pages** may behave
differently from a Hetzner egress IP than from the prior local/residential
network origin this was tested from. The pipeline already classifies this
correctly as `BLOCKED` (not `FAILED`) when it occurs, and continues — this
is a named, accepted risk to observe during the Hetzner soak, not a defect
to fix as part of this deployment.

## Rollback

Same `.deployed-id` → immutable image → same persistent volume pattern as
every other clank in this fleet. Rollback does not imply schema
compatibility across a migration boundary — always check `migrate`'s
output before assuming an older image works against a newer schema.
