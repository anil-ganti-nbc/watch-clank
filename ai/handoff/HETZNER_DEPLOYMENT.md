# Hetzner deployment — 2026-08-14/15

## Starting state (re-verified, not assumed)

Host `204.168.142.1` (`ubuntu-4gb-hel1-1`, Helsinki). Root SSH intentionally
disabled; access via the `anilganti` user (SSH key already provisioned on
this Mac from the prior sprint). **New finding this sprint: `anilganti` is
already a member of the `docker` group** — every `docker` command in this
deployment ran without sudo/root. `sudo -n` (non-interactive) confirmed
unavailable (`a password is required`); no sudo password was requested or
used anywhere in this deployment.

Confirmed identical to the prior sprint's findings, still true:
- Image `watch-clank:fcb5e91` (built `2026-08-10T07:38:28Z`, commit
  `fcb5e9180483af32c42db598462f1fd62c120ee5`), volume
  `watch_clank_staging_data`.
- Schema pinned at `003_release_leads` — no `specialist_leads`, no
  `operational_epochs`.
- `casio_multi` running SUCCESS roughly every 90 minutes continuously
  (last observed pre-deployment: 2026-08-14 18:10:02 UTC, 22 watches, 0
  events ever), via a mechanism still not directly observable without root
  (no systemd unit or timer existed for watch-clank; most likely
  `docker run --rm` in root's crontab — unconfirmed, root access was not
  attempted).

## Backup (before any change)

```
docker run --rm -v watch_clank_staging_data:/data:ro -v ~/watch-clank-backups:/backup \
  alpine cp /data/watch_clank.db /backup/watch_clank.db.pre-d0ee4e9-20260814T182158Z
```

Verified independently: `PRAGMA integrity_check` = `ok`, 22 watches, 73
`collector_runs` rows, matching the live pre-deployment state exactly.
This backup file remains on the host at
`~/watch-clank-backups/watch_clank.db.pre-d0ee4e9-20260814T182158Z` —
machine-local runtime state, correctly not committed to Git.

## Deploy mechanism: Docker, not a bare-metal venv

The box already runs every Clank except `smartphone-clank` as Docker
containers against named volumes; switching Watch Clank to a bare-metal
venv would mean migrating real historical data out of Docker for no
functional benefit, and would need root to write `/opt` and
`/etc/systemd/system` (unavailable). Instead:

1. **Cloned from GitHub directly on the host** (never copied source files
   manually): `git clone https://github.com/anil-ganti-nbc/watch-clank.git ~/watch-clank`,
   verified `git rev-parse HEAD` == `d0ee4e95aac7fd2e9908579880cd0a3b728d44cf`
   (matches the SHA pushed earlier this sprint — see the repo's own commit
   history for the two commits this sprint produced,
   `Hall of Shame sprint...` / `Add Seiko Japan retail collector...`, both
   already on `main` before this deployment began).
2. **Built the image on the host**, tagged with the exact SHA:
   `docker build --build-arg GIT_REVISION=d0ee4e9... -t watch-clank:d0ee4e9 .`
   — `docker inspect` confirms `org.opencontainers.image.revision` matches
   exactly.
3. **Reused the existing volume** (`watch_clank_staging_data`) — the 22
   pre-existing watches and 73 collector-run history are preserved, not
   replaced.

## Migration

```
docker run --rm -v watch_clank_staging_data:/data -e DATABASE_URL=sqlite:////data/watch_clank.db \
  watch-clank:d0ee4e9 python -m scripts.migrate
```

`003_release_leads` → `004_specialist_leads` → `005_specialist_lead_correlation_type`
→ `006_operational_epochs` → `007_specialist_lead_editorial_freshness`, all
in one clean run, `migration successful`. Re-verified after: `integrity_check`
= `ok`, 22 watches unchanged, all expected tables present including the
new `specialist_leads`/`operational_epochs`.

## Source-scoped baseline (Phase 4 discipline)

`casio_multi` already had real, non-baseline history on this DB and was
**not** baselined — it kept its existing ability to fire real events for
anything genuinely new. Every other of the (now) 17 registered collectors
had never run on this DB before and was force-baselined, then
repeat-verified:

| Source | Baseline result | Repeat result |
|---|---|---|
| citizen_news | SUCCESS, 9 new watches, 0 events | 0 new, 0 events |
| seiko_jp_news (brand) | SUCCESS, 0 new watches, 0 events | 0 new, 0 events |
| timex_news (brand) | SUCCESS, 10 new watches, 0 events | 0 new, 0 events |
| citizen_products | SUCCESS, 449 new watches, 0 events | 0 new, 0 events |
| citizen_de_products | SUCCESS, 329 new watches, 0 events | `ZERO_ITEMS` (pre-existing citizen_de design: known URLs are fully excluded, not deprioritized, from any given run — nothing left to fetch, a healthy terminal state, not an error) |
| seiko_products | SUCCESS, 222 new watches, 0 events | 0 new, 0 events |
| **seiko_jp_products** | SUCCESS, 956 new watches, 0 events | 0 new, 0 events |
| timex_products | SUCCESS, 1445 new watches, 0 events | 0 new, 0 events |
| **casio_uk_sitemap** | SUCCESS, 710 new watches, 0 events | 0 new, 0 events |
| casioblog / gcentral / plus9time / monochrome / deployant / fratello / watchtime (all 7 specialist RSS) | all SUCCESS, real new leads (1-20 each), 0 events | all 0 new leads |

No historical newsroom events, no stale specialist alerts, no fake
NEW_REFERENCE/NEW_REGION flood at any point — confirmed by direct query
(`select event_type, count(*) from events` = empty) after the full
baseline+repeat sequence, before any timer was enabled.

## Systemd (Phase 5) — real, verified, not just files

Root-owned `/etc/systemd/system` was not an option (no root). Used
**`systemctl --user`** with **`loginctl enable-linger anilganti`**
(self-service, no root needed for one's own account — confirmed this
works even though `sudo -n` does not) so the user's systemd instance keeps
running unattended after SSH logout.

Units were **generated**, not hand-written, via
`scripts/systemd/docker/render_units.py` (new this sprint, see that file
and `scripts/systemd/docker/README.md`) — reads
`app.services.collector_registry`/`app.services.health` (the same registry
the web dashboard's RUN NOW buttons use) as the single source of truth, so
a collector added to the codebase automatically gets a correctly configured
unit pair here with nothing to hand-maintain or forget.

```
docker run --rm -v ~/rendered-units:/out -w /app watch-clank:d0ee4e9 \
  python scripts/systemd/docker/render_units.py --out-dir /out
cp ~/rendered-units/*.{service,timer} ~/.config/systemd/user/
echo 'WATCH_CLANK_IMAGE=watch-clank:d0ee4e9' > ~/.config/watch-clank/docker.env
loginctl enable-linger anilganti
systemctl --user daemon-reload
```

**Verified working end-to-end**, not just "loaded": manually started
`watch-clank-watchtime-rss.service` first and confirmed a real live RSS
fetch + DB write + clean exit via `journalctl --user`, before enabling
anything else. Then enabled all 17 timers:

```
systemctl --user enable --now watch-clank-<name>.timer   # x17
```

`enable --now` fires each unit immediately once (matching `OnBootSec=`).
**All 17 fired real live runs on the spot** — final state, verified via
`docker run ... python -m scripts.status`:

```
Schema:       OK (007_specialist_lead_editorial_freshness)
DB integrity: OK (ok)
Total watches: 4177
Stale RUNNING rows: 0
Active locks: none
Sources: all 17 HEALTHY
```

`systemctl --user list-timers 'watch-clank-*'` confirms correct `NEXT`
firing times per cadence (45 min RSS lanes, 90 min news lanes, 6h product
lanes, 12h sitemap-delta lanes) — a real, unattended, self-scheduling
deployment, not a one-time manual run.

## Known limitation, disclosed honestly

**The original invisible casio_multi invocation mechanism (the pre-existing
`watch-clank:fcb5e91` image + whatever fires it, most likely root's
crontab) was not found or disabled — no root access was available or
attempted.** It is very likely still firing on its own ~90-minute cadence
against the **same** volume, now migrated to schema 007. This is safe, not
corrupting: the migrations 004-007 were purely additive (no column/table
the old code touches was altered or removed), and `casio_multi`'s own
`RunLockService` lock is scoped by `collector_id`, so a collision with the
new systemd-scheduled `casio_multi` run would resolve as one side getting
`SKIPPED_OVERLAP`, never data corruption. It is, however, **redundant** —
two independent processes polling the same Casio sources. **Recommended
follow-up for the operator** (who has root): `sudo crontab -l -u root` (or
check `/etc/cron.d/`) to locate and remove the old invocation, now fully
superseded by `watch-clank-casio-multi.timer`. The old image
(`watch-clank:fcb5e91`, `watch-clank:soak-local`) was deliberately left in
place rather than deleted, in case that investigation needs it.

## Discord authority — unchanged, unambiguous

No `~/.config/watch-clank/secrets.env` was created (none existed to carry
forward — the prior sprint found no webhook configured anywhere). Every
generated unit references `DISCORD_EDITORIAL_WEBHOOK_URL`/
`DISCORD_HEALTH_WEBHOOK_URL` via pass-through only (`-e VARNAME`, no
value) — with no `secrets.env`, both resolve empty, `DiscordNotifier.
editorial_enabled` is `False` by construction
(`editorial_notifications_enabled AND bool(webhook_url)`), and nothing
sends. Hetzner remains the intended **future** authority once a real
webhook is deliberately added — not flipped this sprint, per the explicit
precondition list (verified running current collectors ✅, correctly
baselined ✅, fresh-event generation verified ✅, dedup verified ✅ — the
one precondition **not** met is "Windows can be disabled as editorial
sender without losing collection," which requires Windows access this
session did not have).

## Rollback path

1. `docker run --rm -v watch_clank_staging_data:/data -v ~/watch-clank-backups:/backup alpine cp /backup/watch_clank.db.pre-d0ee4e9-20260814T182158Z /data/watch_clank.db`
2. `systemctl --user disable --now watch-clank-*.timer`
3. Old image `watch-clank:fcb5e91` is still present locally if a full
   image rollback is ever needed (`docker run ... watch-clank:fcb5e91 ...`
   against the restored pre-migration backup file only — never against a
   post-migration-007 database).
