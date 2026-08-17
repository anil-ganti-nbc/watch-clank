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

from dataclasses import dataclass

# Matches app.services.health.KNOWN_COLLECTORS exactly -- deliberately
# imported from there rather than redefined, so the two can never drift.
from app.services.health import KNOWN_COLLECTORS


@dataclass(frozen=True)
class CollectorControl:
    collector_id: str
    display_name: str
    layer: str  # "OFFICIAL" or "SPECIALIST"
    cli_args: tuple[str, ...]  # appended to `python -m scripts.run_pipeline --live`


_CONTROLS: dict[str, CollectorControl] = {
    "casio_multi": CollectorControl("casio_multi", "Casio (intl news + Japan catalogue)", "OFFICIAL", ()),
    "casio_uk_sitemap": CollectorControl(
        "casio_uk_sitemap", "Casio products (UK sitemap-delta, no price/availability)", "OFFICIAL",
        ("--experimental-product", "casio_uk"),
    ),
    "citizen_news": CollectorControl("citizen_news", "Citizen news", "OFFICIAL", ("--experimental-brand", "citizen")),
    "citizen_products": CollectorControl(
        "citizen_products", "Citizen products (US)", "OFFICIAL", ("--experimental-product", "citizen")
    ),
    "citizen_de_products": CollectorControl(
        "citizen_de_products", "Citizen products (Germany)", "OFFICIAL", ("--experimental-product", "citizen_de")
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

# "RUN ALL SAFE COLLECTORS" -- every registered collector is included. There
# is currently no collector considered unsafe-to-run-on-demand (casio_japan
# is Akamai-BLOCKED but that's bundled inside casio_multi and fails closed,
# not unsafe to attempt). If a future collector needs to be excluded from
# bulk runs, exclude it here explicitly with a reason, not silently.
SAFE_COLLECTOR_IDS: tuple[str, ...] = tuple(KNOWN_COLLECTORS)


def get_control(collector_id: str) -> CollectorControl:
    if collector_id not in _CONTROLS:
        raise KeyError(f"no CollectorControl for {collector_id!r} -- refusing to guess a CLI invocation")
    return _CONTROLS[collector_id]


def all_controls() -> list[CollectorControl]:
    return [_CONTROLS[cid] for cid in KNOWN_COLLECTORS]
