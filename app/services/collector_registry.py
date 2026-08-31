"""Web Operations registry: maps each collector_id health.py already knows
about to the exact scripts/run_pipeline.py invocation that runs it.

This is the single place that mapping lives. The Windows Control Centre
needed manual buttons added after real UI/source drift (see HANDOFF.md) --
the explicit brief for this sprint is not to repeat that mistake. Every
entry here is verified against real collector COLLECTOR_ID constants
(app/collectors/*.py) and scripts/run_pipeline.py's actual argument
dispatch, not guessed from naming convention.

Deliberately NOT auto-derived from argparse or the collector modules
themselves -- that would let a new collector silently gain a working
RUN NOW button without a deliberate decision that it belongs on the web
control surface. Every entry here is a reviewed, explicit inclusion.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Matches app.services.health.KNOWN_COLLECTORS exactly -- deliberately
# imported from there rather than redefined, so the two can never drift.
from app.services.health import KNOWN_COLLECTORS

# Single source of truth for which collectors are still in soak/EXPERIMENTAL
# maturity (see WATCH_SOAK_CONTRACT.md) -- imported rather than redefined so
# the eligibility gate below can never drift from the delivery-silence gate
# in app.services.delivery_gate.
from app.services.delivery_gate import EXPERIMENTAL_MATURITY_COLLECTORS


@dataclass(frozen=True)
class CollectorControl:
    collector_id: str
    display_name: str
    layer: str  # "OFFICIAL" or "SPECIALIST"
    cli_args: tuple[str, ...]  # appended to `python -m scripts.run_pipeline --live`

    @property
    def is_experimental(self) -> bool:
        return self.collector_id in EXPERIMENTAL_MATURITY_COLLECTORS


_CONTROLS: dict[str, CollectorControl] = {
    "casio_multi": CollectorControl("casio_multi", "Casio (intl news + Japan catalogue)", "OFFICIAL", ()),
    "casio_uk_sitemap": CollectorControl(
        "casio_uk_sitemap", "Casio products (UK sitemap-delta, no price/availability)", "OFFICIAL",
        ("--experimental-product", "casio_uk"),
    ),
    "casio_jp_sitemap": CollectorControl(
        "casio_jp_sitemap", "Casio products (Japan sitemap-delta, no price/availability)", "OFFICIAL",
        ("--experimental-product", "casio_jp"),
    ),
    "casio_europe_sitemap": CollectorControl(
        "casio_europe_sitemap", "Casio products (Europe sitemap-delta, no price/availability)", "OFFICIAL",
        ("--experimental-product", "casio_europe"),
    ),
    "citizen_news": CollectorControl("citizen_news", "Citizen news", "OFFICIAL", ("--experimental-brand", "citizen")),
    "citizen_products": CollectorControl(
        "citizen_products", "Citizen products (US)", "OFFICIAL", ("--experimental-product", "citizen")
    ),
    "seiko_jp_news": CollectorControl("seiko_jp_news", "Seiko news", "OFFICIAL", ("--experimental-brand", "seiko")),
    "seiko_products": CollectorControl(
        "seiko_products", "Seiko products", "OFFICIAL", ("--experimental-product", "seiko")
    ),
    "seiko_jp_products": CollectorControl(
        "seiko_jp_products", "Seiko products (Japan)", "OFFICIAL", ("--experimental-product", "seiko_jp")
    ),
    "timex_news": CollectorControl("timex_news", "Timex news", "OFFICIAL", ("--experimental-brand", "timex")),
    "timex_products": CollectorControl(
        "timex_products", "Timex products", "OFFICIAL", ("--experimental-product", "timex")
    ),
    # 2026-08-25 expansion wave (EXPERIMENTAL): sitemap/Shopify-family
    # brands. Registered here so render_units.py emits canonical systemd
    # units and the dashboard/health surface knows them -- see
    # WATCH_SOAK_CONTRACT.md for soak/promotion rules.
    "tissot_sitemap": CollectorControl(
        "tissot_sitemap", "Tissot products (US sitemap-delta, no price/availability)", "OFFICIAL",
        ("--experimental-product", "tissot"),
    ),
    "timex_uk_products": CollectorControl(
        "timex_uk_products", "Timex products (UK Shopify, regional lane, GBP)", "OFFICIAL",
        ("--experimental-product", "timex_uk"),
    ),
    "casioblog_rss": CollectorControl(
        "casioblog_rss", "CASIOBLOG", "SPECIALIST", ("--experimental-specialist", "casioblog")
    ),
    "gcentral_rss": CollectorControl(
        "gcentral_rss", "G-Central", "SPECIALIST", ("--experimental-specialist", "gcentral")
    ),
    "plus9time_rss": CollectorControl(
        "plus9time_rss", "Plus9Time", "SPECIALIST", ("--experimental-specialist", "plus9time")
    ),
    "monochrome_rss": CollectorControl(
        "monochrome_rss", "Monochrome Watches", "SPECIALIST", ("--experimental-specialist", "monochrome")
    ),
    "deployant_rss": CollectorControl(
        "deployant_rss", "Deployant", "SPECIALIST", ("--experimental-specialist", "deployant")
    ),
    "fratello_rss": CollectorControl(
        "fratello_rss", "Fratello", "SPECIALIST", ("--experimental-specialist", "fratello")
    ),
    "watchtime_rss": CollectorControl(
        "watchtime_rss", "WatchTime", "SPECIALIST", ("--experimental-specialist", "watchtime")
    ),
    "great_gshock_world_atom": CollectorControl(
        "great_gshock_world_atom", "Great G-Shock World", "SPECIALIST",
        ("--experimental-specialist", "great_gshock_world"),
    ),
    "gear_patrol_rss": CollectorControl(
        "gear_patrol_rss", "Gear Patrol", "SPECIALIST", ("--experimental-specialist", "gear_patrol")
    ),
}

# Sanity check at import time, not just documentation: every KNOWN_COLLECTORS
# entry must have a control mapping, and vice versa -- if health.py gains a
# collector this registry doesn't know about, fail loudly at startup rather
# than silently missing a RUN NOW button (the exact drift class this sprint
# was told not to repeat).
_missing_controls = set(KNOWN_COLLECTORS) - set(_CONTROLS)
_extra_controls = set(_CONTROLS) - set(KNOWN_COLLECTORS)
if _missing_controls:
    raise RuntimeError(
        f"collector_registry.py is missing CollectorControl entries for: {sorted(_missing_controls)} "
        "-- health.py's KNOWN_COLLECTORS and this registry must stay in sync."
    )
if _extra_controls:
    raise RuntimeError(
        f"collector_registry.py has entries not in health.py's KNOWN_COLLECTORS: {sorted(_extra_controls)}"
    )

# "RUN ALL SAFE COLLECTORS" -- eligibility, not just delivery.
#
# 2026-08-27 operator closeout decision: EXPERIMENTAL-maturity collectors
# (WATCH_SOAK_CONTRACT.md) must not be silently swept into a bulk local run
# by default. app.services.delivery_gate.experimental_delivery_blocked()
# already keeps their events externally silent, but that is a downstream
# defense -- "does this collector's evidence ring Discord" -- and was never
# meant to double as "should RUN ALL invoke this collector at all." Explicit
# operator instruction: "Do not rely on delivery gating as the UI
# eligibility mechanism." So this is a second, independent gate at the
# eligibility layer itself.
#
# Every OTHER registered collector is still considered safe-to-run-on-demand
# (casio_japan is Akamai-BLOCKED but that's bundled inside casio_multi and
# fails closed, not unsafe to attempt). If a future finalized collector
# needs to be excluded from bulk runs for some other reason, exclude it here
# explicitly with a reason, not silently.
#
# Nothing is ripped out: an EXPERIMENTAL collector remains fully wired and
# individually runnable via RUN NOW / COLLECT (see get_control/all_controls
# and /operations/run/{collector_id} in app/main.py) -- soak evidence
# collection still works exactly as before, one collector at a time. Only
# its default membership in the *bulk* "Run all" set changes.
#
# Config-driven re-enable for a deliberate soak-via-Run-All decision: set
# WATCH_CLANK_RUN_ALL_INCLUDE_EXPERIMENTAL=1 (env/.env) to fold the current
# EXPERIMENTAL_MATURITY_COLLECTORS set back into Run All. Off by default.
# Requires a process restart to take effect, matching every other
# env-sourced config value in this app (see app.core.config.Settings).


def _resolve_safe_collector_ids() -> tuple[str, ...]:
    if os.getenv("WATCH_CLANK_RUN_ALL_INCLUDE_EXPERIMENTAL") == "1":
        return tuple(KNOWN_COLLECTORS)
    return tuple(cid for cid in KNOWN_COLLECTORS if cid not in EXPERIMENTAL_MATURITY_COLLECTORS)


SAFE_COLLECTOR_IDS: tuple[str, ...] = _resolve_safe_collector_ids()


def get_control(collector_id: str) -> CollectorControl:
    if collector_id not in _CONTROLS:
        raise KeyError(f"no CollectorControl for {collector_id!r} -- refusing to guess a CLI invocation")
    return _CONTROLS[collector_id]


def all_controls() -> list[CollectorControl]:
    return [_CONTROLS[cid] for cid in KNOWN_COLLECTORS]
