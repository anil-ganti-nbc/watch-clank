# Version/Identity/Health Adapter — Design Sketch (DRAFT, not implemented)

Status: architecture prep only. No code in this repo has been changed to
implement any of the below. Do not merge a working version of this without a
separate, explicit decision to leave Tier D prep and start real cloud work on
Watch Clank.

## What already exists

`app/main.py` defines `GET /health` (lines ~251-283 as of commit `14712d9`):

```python
@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Real dependency checks. Returns non-200 when unhealthy."""
    issues = []
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        issues.append(f"database: {exc}")
    for table in ("watches", "snapshot_blobs", "snapshot_fetches",
                  "source_observations", "collector_runs"):
        try:
            db.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        except Exception:
            issues.append(f"missing_table:{table}")
    try:
        root = settings.resolved_snapshot_root
        if not root.exists() or not root.is_dir():
            issues.append("snapshot_root_inaccessible")
    except Exception as exc:
        issues.append(f"snapshot_root: {exc}")

    body = {"status": "ok" if not issues else "unhealthy",
             "service": "watch-clank", "stage": 1, "issues": issues}
    code = 200 if not issues else 503
    return JSONResponse(content=body, status_code=code)
```

## Assessment: does this already meet Stage 0.5 truthfulness semantics?

Mostly yes, and it's a relative strength worth noting honestly:

- It does **real** dependency checks (DB connectivity, required tables exist,
  snapshot root exists/is a dir) rather than a bare `{"status": "ok"}` liveness
  stub.
- It returns a genuine non-200 (`503`) when a real problem exists, and reports
  *which* check failed via `issues`, not just a boolean.
- This is already ahead of a naive "the process didn't crash" health check —
  it would have caught the exact soak-day migration outage described in
  HANDOFF.md (`no such table: source_component_states`) via the
  `missing_table:` check, had it been queried during that window.

What it does **not** currently do, which a full version/identity/health
contract would add:

1. **No version/build identity in the payload.** `"stage": 1` is a
   hand-maintained literal, not derived from anything (not from
   `app.version` which FastAPI already has as `"0.1.0"`, not from a git SHA,
   not from an image tag/build timestamp). Two different deployed copies of
   this service would report identical `/health` bodies with no way to tell
   them apart.
2. **No distinction between liveness and readiness.** A single endpoint
   conflates "process is up" with "dependencies are reachable" with "schema
   is current." For a one-shot pipeline container this distinction matters
   less; for the dashboard service (if ever run long-lived) it would matter
   for orchestration.
3. **No explicit schema-version check.** The `missing_table:` check would
   catch a *table that doesn't exist yet* but would not catch the actual
   soak-day failure mode as cleanly as checking Alembic's `alembic_version`
   table against the code's expected head revision — it happens to overlap
   in this one case because the missing table and the missing migration
   coincided, but that's incidental, not a designed schema-drift check.
4. **No soak/scheduler-state visibility.** Nothing in `/health` reflects
   run-lock state, `is_locked`, last successful run timestamp, or the
   `RUNNING`/`STALE` distinction already computed elsewhere in
   `app/main.py`'s dashboard route and `app/services/run_lock.py`. That data
   already exists and is displayed on `/` — `/health` doesn't surface any of
   it, so an external prober can't tell "healthy but stale" from "healthy and
   fresh" without scraping HTML.

## Sketch: additive fields (draft, not implemented)

If Watch Clank ever moves past Tier D prep, a version/identity adapter would
plausibly add fields like:

```jsonc
{
  "status": "ok",
  "service": "watch-clank",
  "stage": 1,
  "issues": [],

  // --- proposed additive identity block ---
  "version": {
    "app_version": "0.1.0",       // from FastAPI app.version / pyproject
    "git_sha": "14712d9",          // build-time injected, e.g. via --build-arg
    "image_tag": null,             // set by CI/registry, null when run outside a container
    "built_at": null               // ISO8601, build-time injected
  },
  "schema": {
    "alembic_head_expected": "003_release_leads",
    "alembic_head_actual": "003_release_leads",
    "in_sync": true
  },
  "operations": {
    "is_locked": false,
    "latest_run_status": "PARTIAL",
    "latest_run_at": "2026-08-08T14:12:25Z",
    "stale_run_threshold_minutes": 45
  }
}
```

Hook points if this were ever implemented (not done here):

- `app/main.py`'s existing `health()` function is the natural single place to
  extend — it already has a `db: Session` dependency and `settings` in scope,
  so `schema` and `operations` blocks could reuse the same session rather
  than opening a new one.
- `version.git_sha` / `built_at` would need to come from build-time
  environment variables (e.g. `ARG GIT_SHA` in a real Dockerfile, written to
  an env var or a small generated `app/_version.py`) — there is no
  installed-package metadata to introspect at runtime today since this isn't
  published as a wheel anywhere.
- `schema.alembic_head_expected` would need either a small parse of
  `alembic/versions/` at import time (fragile) or a maintained constant next
  to `app/models/__init__.py` bumped alongside each new migration — worth
  scoping carefully rather than guessing here.
- None of this requires touching `alembic/`, `app/collectors/`,
  `app/parsers/`, `app/normalization/`, or `app/services/` — it is additive
  to `app/main.py` and `app/core/config.py` only.

## Explicit non-scope

This document does not propose *when* to implement the above, does not
modify `/health`, and does not conflict with HANDOFF.md's "do not start Stage
2 — continue soak test" instruction. It is filed purely so a future session
doesn't have to re-derive this assessment from scratch.
