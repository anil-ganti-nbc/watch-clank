"""Generate Docker-flavored systemd user unit files for every registered
Watch Clank collector, from the single source of truth
(app.services.collector_registry / app.services.health) rather than
hand-maintaining ~30 near-identical files that can silently drift.

Why Docker + user-level systemd rather than the native-venv root-owned
units in scripts/systemd/*.service: the Hetzner host already runs Watch
Clank as a Docker container against a persistent named volume
(watch_clank_staging_data), matching every other Clank on that shared box
except smartphone-clank -- switching to a bare-metal venv install would
mean migrating the volume's real historical data out of Docker for no
functional benefit. Root SSH is intentionally disabled on that host and no
sudo password is available to this deployment process, so root-owned
/etc/systemd/system units are not an option; `systemctl --user` with
`loginctl enable-linger` (self-service, no root required) is used instead
-- see ai/handoff/HETZNER_DEPLOYMENT.md for the full reasoning. The
existing scripts/systemd/*.service/.timer templates remain valid and
untouched for a hypothetical bare-metal Linux deployment elsewhere; this
is an additional, alternative deployment path, not a replacement.

Usage:
    python -m scripts.systemd.docker.render_units --out-dir /tmp/units

Then copy the rendered *.service/*.timer files to
~/.config/systemd/user/ on the target host and:
    systemctl --user daemon-reload
    systemctl --user enable --now watch-clank-<slug>.timer
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.services.collector_registry import all_controls  # noqa: E402
from app.services.health import EXPECTED_CADENCE_MINUTES  # noqa: E402

TEMPLATE_DIR = Path(__file__).resolve().parent
SERVICE_TEMPLATE = (TEMPLATE_DIR / "watch-clank-docker.service.template").read_text()
TIMER_TEMPLATE = (TEMPLATE_DIR / "watch-clank-docker.timer.template").read_text()

# casio_multi is the production lane: its own --scheduled entrypoint (lock
# + exit-code contract), not the web UI's --live convenience wrapper. Its
# --scheduled path already defaults qualification_provenance to SCHEDULED
# in scripts/run_pipeline.py, so no explicit flag is needed here.
_SCHEDULED_OVERRIDE = {"casio_multi": ("--scheduled",)}

# 2026-09-03 incident fix (Seiko JP delivery-gating failure): every OTHER
# registered collector is fired via --experimental-brand/--experimental-
# product/--experimental-specialist, whose argparse default for
# --qualification-provenance is "UNKNOWN" (see scripts/run_pipeline.py) --
# unlike --scheduled, these entrypoints have no scheduled-aware default.
# A real systemd-timer firing of any of them IS a genuinely scheduled run;
# leaving provenance unstated made QualificationService.record_execution()
# stamp eligibility_gate=UNKNOWN, which fails closed and silently blocked
# Discord delivery for every eligible event these lanes produced -- ten
# confirmed-eligible Seiko JP NEW_REFERENCE events on 2026-09-02 never
# reached Discord as a result. This is not a maturity/promotion decision
# (that stays governed by app.services.delivery_gate.
# EXPERIMENTAL_MATURITY_COLLECTORS, untouched here) -- it is making the
# timer-fired invocation truthfully declare what it already is.
_QUALIFICATION_PROVENANCE_ARGS = ("--qualification-provenance", "SCHEDULED")


def slug_for(collector_id: str) -> str:
    return collector_id.replace("_", "-")


def human_cadence(minutes: int) -> str:
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" + ("s" if hours != 1 else "")
    return f"{minutes} minutes"


def render(out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for control in all_controls():
        cid = control.collector_id
        slug = slug_for(cid)
        args = _SCHEDULED_OVERRIDE.get(
            cid, ("--live", *control.cli_args, *_QUALIFICATION_PROVENANCE_ARGS)
        )
        cadence_min = EXPECTED_CADENCE_MINUTES.get(cid, 360)

        service = SERVICE_TEMPLATE.format(
            DISPLAY_NAME=control.display_name, SLUG=slug, ARGS=" ".join(args)
        )
        timer = TIMER_TEMPLATE.format(
            DISPLAY_NAME=control.display_name,
            CADENCE_SEC=cadence_min * 60,
            CADENCE_HUMAN=human_cadence(cadence_min),
        )

        service_path = out_dir / f"watch-clank-{slug}.service"
        timer_path = out_dir / f"watch-clank-{slug}.timer"
        service_path.write_text(service)
        timer_path.write_text(timer)
        written.extend([service_path.name, timer_path.name])
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    written = render(args.out_dir)
    print(f"Rendered {len(written)} unit files to {args.out_dir}:")
    for name in sorted(written):
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
