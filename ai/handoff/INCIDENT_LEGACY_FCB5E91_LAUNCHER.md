# Incident: legacy pre-migration cron launcher survived the timer migration

**Discovered:** 2026-08-14, multi-day Hetzner soak audit, via an
unintended natural execution: Docker container
`watch-clank-watch-clank-run-e9e53b5dcf88` created 2026-08-14 19:45:01 UTC
from obsolete image `watch-clank:fcb5e91`, exit code 3 approximately one
second later. Not manually triggered. The current `anilganti`
user-systemd 17-timer architecture does not explain this launch.

## Precise root cause

`deploy`'s personal crontab (root-owned spool,
`/var/spool/cron/crontabs/deploy`) still contained two lines predating
the migration to the current per-source `systemctl --user` timer
architecture:

```
10 0,3,6,9,12,15,18,21 * * * /home/deploy/staging/watch-clank/deploy_run.sh >> /home/deploy/staging/watch-clank/logs/cron-$(date -u +\%Y\%m\%d).log 2>&1
45 1,4,7,10,13,16,19,22 * * * /home/deploy/staging/watch-clank/deploy_run.sh >> /home/deploy/staging/watch-clank/logs/cron-$(date -u +\%Y\%m\%d).log 2>&1
```

The second line fires at hour 19, minute 45 — an exact match for the
19:45:01 UTC event (the +1 second is cron dispatch → wrapper startup
latency). Both lines invoke the same wrapper,
`/home/deploy/staging/watch-clank/deploy_run.sh`, an **abandoned staging
checkout** frozen since 2026-08-10 07:38 (every file in that directory
except `logs/` carries that exact mtime):

```sh
#!/bin/sh
set -eu
cd "$(dirname "$0")"
export IMAGE_TAG
IMAGE_TAG="$(cat .deployed-id)"
exec flock -n /tmp/watch-clank-run.lock \
  docker compose -f docker-compose.staging.yml run --rm watch-clank
```

`.deployed-id` in that directory literally contains `fcb5e91` (8 bytes:
7 characters + newline), unchanged since the Aug 10 checkout.
`docker-compose.staging.yml`'s `watch-clank` service resolves
`image: watch-clank:${IMAGE_TAG:-soak-local}` against that value, giving
`watch-clank:fcb5e91` on every invocation. This matches the "reads an
obsolete `.deployed-id`" / "runs from an abandoned staging directory"
hypothesis exactly — not a hardcoded tag, not an old `.env`, not a stale
default.

`docker compose run` (not `up`) explains the observed container name:
compose's `run` naming convention is `<project>-<service>-run-<hash>`,
matching `watch-clank-watch-clank-run-e9e53b5dcf88` exactly (project and
service are both named `watch-clank` in `docker-compose.staging.yml`;
`container_name: watch-clank-soak` is set but ignored by `run`, which
always generates its own name). The compose project shares the same
named volume (`watch_clank_staging_data`) and network (`watch-clank_
default`) as the accepted architecture, which is why the audit correctly
flagged it as touching shared persistent state.

`deploy` has never had `systemd --user` linger enabled and has no active
login session (`loginctl list-users` shows only `root` and `anilganti`,
last `deploy` login was a single session on 2026-08-09) — this rules out
a `deploy`-owned user-systemd timer as an alternative explanation before
the crontab was ever found; a user-systemd instance cannot fire jobs with
no session and no linger.

## Why this specific gap existed

This isn't unique to Watch Clank — `deploy`'s crontab is a fleet-wide
pre-migration deployment mechanism (other Clanks' lines are present and
untouched; smartphone-clank's own line was already disabled 2026-08-10
with the same pattern, `# DISABLED <date> -- superseded by systemd
<service>`, when that Clank made the same migration). Watch Clank's
migration to the new architecture (`ai/handoff/HETZNER_DEPLOYMENT.md`,
2026-08-14) built and verified the new 17-timer architecture but never
located and disabled the old cron entry it was replacing — the two
launchers ran in parallel, invisibly, until this soak audit caught a
natural firing.

## Evidence chain (correlated with the 19:45:01 UTC event)

Read-only journal correlation (`journalctl -g 19.45.01`, regex `.` used
in place of a literal `:` to route around a Hetzner console keyboard bug
that drops Shift on typed symbols — see
[[hetzner-console-keyboard-quirks]] in the operator's memory):

```
Aug 14 19:45:01 ubuntu-4gb-hel1-1 containerd[879]: time="2026-08-14T19:45:01.290443082Z" level=info msg="connecting to shim ..." namespace=moby
Aug 14 19:45:01 ubuntu-4gb-hel1-1 dockerd[957]: time="2026-08-14T19:45:01.405335569Z" level=info msg="sbJoin: ..." ep=watch-clank-watch-clank-run-e9e53b5dcf88 net=watch-clank_default ...
```

`ep=watch-clank-watch-clank-run-e9e53b5dcf88` is byte-for-byte the same
container name reported by the original soak-audit finding. Full chain:

```
deploy's crontab (45 1,4,7,10,13,16,19,22 * * *)
    -> /home/deploy/staging/watch-clank/deploy_run.sh
    -> IMAGE_TAG=$(cat .deployed-id) = "fcb5e91"
    -> docker compose -f docker-compose.staging.yml run --rm watch-clank
    -> watch-clank:fcb5e91
    -> container watch-clank-watch-clank-run-e9e53b5dcf88, created 19:45:01.29-.41Z UTC
```

A day-long journal scan (`--since 2026-08-14 --until 2026-08-15`) further
showed every prior firing of this same cron line, all day, each followed
within ~0-1 second by a `dockerd` sandbox-join log line — the same
signature, repeating on cadence, until the new architecture's timers took
over the box's active workload at 18:38.

## Remediation

Both `deploy`-crontab lines for `watch-clank` were commented out (not
deleted) in place, following the exact convention already established in
that same crontab for smartphone-clank's 2026-08-10 migration:

```
# DISABLED 2026-08-15 -- legacy launcher for obsolete watch-clank:fcb5e91 via stale .deployed-id; superseded by 17 per-source watch-clank-*.timer units under anilganti running watch-clank:c81ebed. See ai/handoff/INCIDENT_LEGACY_FCB5E91_LAUNCHER.md. #10 0,3,6,9,12,15,18,21 * * * /home/deploy/staging/watch-clank/deploy_run.sh >> ...
# DISABLED 2026-08-15 -- legacy launcher for obsolete watch-clank:fcb5e91 via stale .deployed-id; superseded by 17 per-source watch-clank-*.timer units under anilganti running watch-clank:c81ebed. See ai/handoff/INCIDENT_LEGACY_FCB5E91_LAUNCHER.md. #45 1,4,7,10,13,16,19,22 * * * /home/deploy/staging/watch-clank/deploy_run.sh >> ...
```

Verified by `diff` against a full backup of the pre-change crontab
(`/home/anilganti/deploy-crontab-backup-20260815-preremediation.txt` on
the host) that these were the **only** two lines changed — every other
Clank's cron line (free-game-tracker, oem-radar, semiconductor-
intelligence, chinese-tech-wire, feature-phone-clank, smartwatch-clank,
and the already-disabled smartphone-clank line) is byte-for-byte
untouched.

**Deliberately not done, by design:** the wrapper script, the
`docker-compose.staging.yml`, the `.deployed-id` file, the abandoned
staging checkout, and the `watch-clank:fcb5e91` image were all left in
place, untouched, for rollback/forensic comparison. The stray
`/tmp/watch-clank-run.lock` file (the legacy wrapper's `flock` target,
distinct in naming from the current architecture's per-source locks) is
now inert and was also left in place rather than deleted.

## Verification

- **Scheduler inventory after the fix:** `deploy`'s crontab has zero
  active watch-clank lines (both commented); `root`'s own personal
  crontab is empty (`no crontab for root`); `/etc/crontab` and
  `/etc/cron.d/*` contain no watch-clank references (unchanged, verified
  at the start of this audit); no system-level (`systemctl`, not
  `--user`) unit or timer named `watch-clank` exists; `deploy` cannot run
  `systemd --user` timers at all (no linger, no active session). The only
  remaining schedulers capable of launching Watch Clank are the 17
  `anilganti`-owned `watch-clank-*.timer` units.
- **Old launch window (21:10 UTC, the `10 0,3,6,9,12,15,18,21` line)
  passed cleanly**, confirmed at 21:39 UTC: `docker ps -a` on the host
  shows zero containers of any kind (all runs use `--rm`), and no
  `collector_runs` row appeared with any signature resembling the legacy
  path.
- **Natural current-architecture runs observed after the fix:**
  `casio_multi` (id 139, 20:48:49-20:49:03 UTC, SUCCESS),
  `casioblog_rss`/`gcentral_rss`/`fratello_rss`/`monochrome_rss` (ids
  140-143, 20:54:50 UTC, all SUCCESS), and a second full RSS-lane batch
  (ids 144-152, 21:39:53 UTC, all SUCCESS) — the accepted timer
  architecture fired correctly, selected the correct image, and
  completed normally, both before and after the remediation.
- **No duplicate/overlapping executions found** for any collector across
  the entire post-cutover window (18:38 UTC onward) — every collector's
  runs are strictly sequential, never concurrent.
- **Three-way provenance, current accepted deployment:**
  - Git SHA (Hetzner checkout, `~/watch-clank`): `c81ebedbae27b85381cb9b6372220dfd84ab04e2`
  - OCI `org.opencontainers.image.revision` label on `watch-clank:c81ebed`: `c81ebedbae27b85381cb9b6372220dfd84ab04e2`
  - Runtime identity (`app.core.identity.get_identity()["source_revision"]`, queried by actually running the image): `c81ebedbae27b85381cb9b6372220dfd84ab04e2`
  - All three match exactly.
  - Legacy image's own provenance recorded for historical clarity only
    (not redeployed): `watch-clank:fcb5e91` reports
    `source_revision=fcb5e9180483af32c42db598462f1fd62c120ee5`, matching
    its OCI label.
- **DB integrity** (non-destructive: copy of the live SQLite file inside
  a disposable container mounting the named volume read-only, never
  written back): `PRAGMA integrity_check` = `ok`. Schema revision
  (`alembic_version`): `007_specialist_lead_editorial_freshness`
  (current, matches HEAD). No stale/non-terminal-status
  `collector_runs` rows found — the only non-`SUCCESS` statuses present
  (`PARTIAL` for `casio_multi`, `ZERO_ITEMS` for `citizen_de_products`)
  are pre-existing, already-documented, explicitly out-of-scope
  behaviors, not evidence of a stuck run or a lock.

## Persistent state

Untouched throughout: no volume deleted, no rebaseline, no DB
restore/migration/vacuum, no hand-edited SQLite, no manually cleared
locks. The shared `watch_clank_staging_data` volume that both the legacy
and current architectures wrote to was inspected read-only only.

## Access-path note

Root SSH login is disabled on this host and the `anilganti` account's
sudo password was not known this session, so Phase A/B's root-owned
reads (`deploy`'s crontab, `deploy`'s home directory, `journalctl`) were
performed via the Hetzner Cloud web console (a separate, root-authenticated
out-of-band access path, not SSH) operated live by the account owner.
That console has a keyboard bug that drops Shift on typed symbols
(`_`/`:`/`@`/`>`/`|`/`*` all land as their unshifted base character,
confirmed both typed and pasted) — full workarounds recorded in the
operator's own memory system, not repeated here. The crontab fix itself
was installed without hand-typing any of the risky content: the corrected
file was written over a normal, reliable SSH session (as `anilganti`,
using the `docker` group's already-granted access) to
`/home/anilganti/watch-clank-deploy-crontab-fixed.txt`, and the console
operator ran a single symbol-free `crontab -u deploy <path>` to install
it.
