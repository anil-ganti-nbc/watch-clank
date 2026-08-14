# Watch Clank on Docker + user-level systemd

Alternative to the native-venv, root-owned units in `scripts/systemd/*.service`
(those remain valid for a hypothetical bare-metal Linux install and are
untouched by this directory). This path is for a host that already runs
Watch Clank as a Docker container against a persistent named volume — the
actual situation on the shared Hetzner box, where root SSH is intentionally
disabled and no sudo password is available to the deployment process. See
`ai/handoff/HETZNER_DEPLOYMENT.md` for the full reasoning and the real
deployment this was used for.

## Why user-level systemd, not cron

The Hetzner instance was previously run via an undiscoverable mechanism
(most likely `docker run --rm` in root's crontab — never confirmed, no
root access to check). `systemctl --user` requires no root: `loginctl
enable-linger <user>` (self-service, no sudo needed for your own account)
keeps your user's systemd instance running unattended after logout, and
`systemctl --user daemon-reload` / `enable --now` work exactly like the
system-level equivalents, just scoped to `~/.config/systemd/user/`.

## Generate the unit files

Rather than hand-maintaining ~30 near-identical files, `render_units.py`
generates them from the single source of truth already in the codebase
(`app.services.collector_registry` for CLI args/display names,
`app.services.health.EXPECTED_CADENCE_MINUTES` for schedule) — a new
collector registered there automatically gets a correctly-configured unit
pair here, with no separate file to remember to update.

```bash
python -m scripts.systemd.docker.render_units --out-dir /tmp/units
# or, without package context:
python scripts/systemd/docker/render_units.py --out-dir /tmp/units
```

## Install on the target host

```bash
mkdir -p ~/.config/systemd/user
cp /tmp/units/*.service /tmp/units/*.timer ~/.config/systemd/user/
mkdir -p ~/.config/watch-clank
echo "WATCH_CLANK_IMAGE=watch-clank:<git-sha>" > ~/.config/watch-clank/docker.env
# Optional, only if a real Discord webhook exists for this host:
# printf 'DISCORD_EDITORIAL_WEBHOOK_URL=...\nDISCORD_HEALTH_WEBHOOK_URL=...\n' > ~/.config/watch-clank/secrets.env

loginctl enable-linger "$(whoami)"   # self, no root needed
systemctl --user daemon-reload
```

## First run on a brand-new/upgraded host: baseline before scheduling

Same discipline as the native-venv path — a fresh or newly-migrated DB
must be force-baselined per source before its timer is enabled, or the
entire backlog is misclassified as "new". Run each source manually once:

```bash
IMG=watch-clank:<git-sha>
run() { docker run --rm -e DATABASE_URL=sqlite:////data/watch_clank.db \
  -e SNAPSHOT_STORAGE_ROOT=/data/snapshots -e LOG_DIR=/data/logs \
  -v watch_clank_staging_data:/data "$IMG" python -m scripts.run_pipeline "$@"; }

run --scheduled --force-baseline
run --experimental-brand citizen --force-baseline
run --experimental-brand seiko --force-baseline
run --experimental-product citizen --force-baseline
run --experimental-product citizen_de --force-baseline
run --experimental-product seiko --force-baseline
run --experimental-product seiko_jp --force-baseline
run --experimental-product casio_uk --force-baseline
run --experimental-specialist casioblog --force-baseline
run --experimental-specialist gcentral --force-baseline
run --experimental-specialist plus9time --force-baseline
run --experimental-specialist monochrome --force-baseline
run --experimental-specialist deployant --force-baseline
run --experimental-specialist fratello --force-baseline
run --experimental-specialist watchtime --force-baseline
run --experimental-brand timex --force-baseline
run --experimental-product timex --force-baseline
```

Then run every one of the above a second time *without* `--force-baseline`
and confirm 0 new leads/watches/events before enabling any timer:

```bash
systemctl --user enable --now watch-clank-casio-multi.timer
systemctl --user enable --now watch-clank-casio-uk-sitemap.timer
systemctl --user enable --now watch-clank-citizen-news.timer
systemctl --user enable --now watch-clank-citizen-products.timer
systemctl --user enable --now watch-clank-citizen-de-products.timer
systemctl --user enable --now watch-clank-seiko-jp-news.timer
systemctl --user enable --now watch-clank-seiko-products.timer
systemctl --user enable --now watch-clank-seiko-jp-products.timer
systemctl --user enable --now watch-clank-timex-news.timer
systemctl --user enable --now watch-clank-timex-products.timer
systemctl --user enable --now watch-clank-casioblog-rss.timer
systemctl --user enable --now watch-clank-gcentral-rss.timer
systemctl --user enable --now watch-clank-plus9time-rss.timer
systemctl --user enable --now watch-clank-monochrome-rss.timer
systemctl --user enable --now watch-clank-deployant-rss.timer
systemctl --user enable --now watch-clank-fratello-rss.timer
systemctl --user enable --now watch-clank-watchtime-rss.timer
```

## Verify

```bash
systemctl --user list-timers 'watch-clank-*'
journalctl --user -u watch-clank-casio-uk-sitemap.service --since -1h
```

## Secrets

`~/.config/watch-clank/secrets.env` is machine-local, never committed
(matches every other Clank's `.env` convention). Discord webhook URLs go
there and nowhere else. The service template reads it with a leading `-`
(`EnvironmentFile=-...`), so its absence is not an error — no webhook
configured means notifications stay a safe no-op, not a startup failure.
