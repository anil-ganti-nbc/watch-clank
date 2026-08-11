# Watch Clank on Linux/systemd

These unit files are **templates to review and adapt**, not a deployment
this repository performs automatically. Nothing here is installed by any
script — you must copy, edit the paths, and enable them yourself.

## Files

- `watch-clank.service` / `watch-clank.timer` — the Casio production lane
  (mirrors the Windows Task Scheduler task `WatchClank-CasioJapan`, same
  90-minute interval, same exit-code contract).
- `watch-clank-experimental.service` / `watch-clank-experimental.timer` —
  the experimental Citizen/Seiko news-discovery lane. Copy the `.service`
  file and change the `--experimental-brand citizen` argument to `seiko` for
  a second instance if you want both running independently.

## Install (adapt paths first)

```bash
sudo cp scripts/systemd/watch-clank*.service scripts/systemd/watch-clank*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now watch-clank.timer
# Only enable the experimental timer once you've reviewed some manual runs:
sudo systemctl enable --now watch-clank-experimental.timer
```

## Secrets and environment

Both units load `EnvironmentFile=-/opt/watch-clank/.env` (the leading `-`
means "don't fail if the file is missing"). Populate `.env` from
`.env.example` and never commit it — same policy as `.gitignore` already
enforces for the Windows path. Discord webhook URLs
(`DISCORD_EDITORIAL_WEBHOOK_URL`, `DISCORD_HEALTH_WEBHOOK_URL`) belong here,
not in any unit file or source.

## Database path (Windows + Linux/cloud running simultaneously)

**Decision: independent databases, not shared/synchronised state.**

If both a Windows machine and a Linux/cloud host are collecting at the same
time, they each write to their own local SQLite file
(`data/watch_clank.db`), with their own snapshot storage under
`data/snapshots/`. This project deliberately does **not** attempt to
synchronise two SQLite databases over a network — SQLite's WAL mode assumes
a single writer host, and cross-host sync would require either a shared
network filesystem (fragile with SQLite locking) or a real replication
layer, neither of which this sprint builds. Building that would be exactly
the kind of premature infrastructure the project's own philosophy rejects
(see HANDOFF.md §55/56).

Practical implication: if you run both simultaneously, you get two
independent observation histories with possibly-different watch/lead ids
for the same real-world reference. That's an acceptable and explicit
tradeoff for now — it is not a bug. When you settle on one permanent host,
retire the other and treat its database as historical record (do not merge
databases automatically).

## Logs

Same files as Windows, just a different OS path:
`data/logs/scheduled-wrapper.log` and `data/logs/scheduled-python.log`.

## Overlap protection

Each collector_id has its own DB-row + lock-file check
(`app/services/run_lock.py::RunLockService`), scoped by `collector_id` since
the Sprint 2 fix (previously `casio_multi` runs were invisible to the
overlap/stale-recovery logic, which had `collector_id="casio_japan"`
hardcoded — see HANDOFF.md's Sprint 2 checkpoint). A second instance
starting on the same DB while one is already running gets `SKIPPED_OVERLAP`,
not a crash or duplicate write.
