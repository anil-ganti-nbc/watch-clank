# CLAUDE HETZNER DEPLOYMENT HANDOFF — Watch Clank Collector Expansion

Generated 2026-08-25 by ox-alpha. You (Claude) hold the Hetzner credentials
and will perform deployment. Everything below is scoped so the job is boring.

## Revision to deploy

- Repo: https://github.com/anil-ganti-nbc/watch-clank
- Branch: main
- Exact SHA: recorded in `GITHUB_RELEASE.md` at repo root (also in the
  programme report). Deploy ONLY that SHA. Verify with:
  `git rev-parse HEAD` on the host after checkout, and compare against
  `git ls-remote origin main`.

## STEP 0 — inspect before touching anything

On the Hetzner host (ubuntu-4gb-hel1-1, per fleet.yaml inventory), record:

1. Checkout path: expected `/home/anilganti/watch-clank` — CONFIRM, don't assume.
2. Current deployed SHA: `cd <path> && git rev-parse HEAD`
3. Branch/ref: `git branch --show-current && git status --short`
   - If uncommitted local changes exist: STOP. Record them and ask the owner.
4. Service manager: `systemctl --user list-timers | grep watch-clank`
   Expected per fleet inventory: systemd --user `watch-clank-*.timer` x19.
5. Canonical scheduler: confirm exactly ONE timer set exists.
   Any duplicate/legacy timers (`crontab -l`, `sudo systemctl list-timers`)
   must be reported before proceeding.
6. Active DB path: `grep DATABASE_URL .env` (expected sqlite:///...watch_clank.db)
7. Alembic revision: `.venv/bin/python -m alembic current`

## STEP 1 — backup FIRST (before any code change)

```sh
cp <db-path> <db-path>.bak-pre-expansion-$(date -u +%Y%m%dT%H%M%SZ)
sqlite3 "file:<db-path>?mode=ro" "PRAGMA integrity_check;"   # expect: ok
sqlite3 "file:<db-path>?mode=ro" "SELECT COUNT(*) FROM event_reviews;
SELECT COUNT(*) FROM watches; SELECT COUNT(*) FROM source_observations;"
```

Record all counts. These are the rollback baseline.

## STEP 2 — deploy

```sh
cd /home/anilganti/watch-clank
git fetch origin
git checkout <EXACT-SHA-from-GITHUB_RELEASE.md>
# dependencies: only if uv.lock changed vs previous SHA (check git diff):
uv sync --locked   # do NOT broadly upgrade; locked-only
# migrations: check first
python -m alembic history --revision-range=<old-sha-head>:head  # if empty, skip
python -m alembic upgrade head    # only additive migrations expected
```

## STEP 3 — hard prohibitions

- Do NOT copy the Windows DB to Hetzner. The two databases are independent by design.
- Do NOT initialise a fresh DB over production.
- Do NOT overwrite/delete QC rows, EventReviews, correction history.
- Do NOT create a second scheduler/timer system; reuse existing canonical timers.
- Do NOT modify collector semantics during deployment (no hotfixes on the host).
- Do NOT upgrade dependencies beyond what uv.lock pins.

## STEP 4 — post-deploy validation

```sh
python -m pytest -m "not live" -q          # expect 449 passed, 2 skipped
python -m scripts.status                    # health snapshot truthful?
```

Then trigger ONE controlled manual run of each new experimental collector via
the exact production invocation path:

```sh
python -m scripts.run_pipeline --live --experimental-product tissot
python -m scripts.run_pipeline --live --experimental-product timex_uk
```

Expected first-run behaviour: SUCCESS status, discoveries persisted,
**zero Events** (auto-baseline). If events appear on run 1: STOP and report —
that is a regression of Fleet Law 1.

## STEP 5 — re-enable timers

Re-enable the existing user timers for the new collectors using the SAME
canonical mechanism as the existing x19 timers (clone cadence pattern).
Do not invent new job names beyond the established convention.

## Probes still required FROM Hetzner vantage (Windows network blocked)

For each, capture HTTP status + content-type + first 200 bytes. GO criteria noted.

| Target | Probe | GO looks like | BLOCKED looks like |
|---|---|---|---|
| Hamilton | `curl -sI https://www.hamiltonwatch.com/sitemap.xml` | 200 + XML sitemap index | 403 / Akamai denial page |
| Longines | `curl -sI https://www.longines.com/robots.txt` then `/sitemap.xml` | 200 + Sitemap: directives | connection reset / timeout |
| Swatch | `curl -s https://www.swatch.com/robots.txt` then sitemap | 200 + XML | "Access Denied" edge page |
| Bulova SFCC | `https://www.bulova.com/us/en/sitemap_index.xml`; also demandware search endpoint used by site JS | XML product URLs w/ SKUs | HTML error page |
| Orient US | browser-like GET `https://orientwatchusa.com/collections/all` from cloud IP | 200 + Shopify-shaped HTML/JSON | Cloudflare challenge interstitial |

Report probe results verbatim (status, content-type, body head) before any
collector is written against them.

## Soak activation (after deploy validation passes)

Enable EXPERIMENTAL soak for: `tissot`, `timex_uk`. Cadence: mirror the
existing `timex_products` timer (360 min) for both. First 4 runs are
initial-fill silent by design (code-enforced); from run 5 onward, genuine
deltas surface as honestly-labelled FIRST_SEEN candidates for QC.
No Discord/editorial routing changes: experimental lane threshold config
already gates alerts; verify `DISCORD_EXPERIMENTAL_MIN_SCORE` unchanged.
