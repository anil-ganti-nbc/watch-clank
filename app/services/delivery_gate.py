"""Fleet-wide experimental delivery-silence gate (canonized 2026-08-25).

Owner decision, closing the gap the Tissot/Timex UK deployment review
exposed: Discord silence for experimental collectors previously depended on
TWO incidental mechanisms stacking — `discord_first_seen_enabled=false`
(gating only FIRST_SEEN events) plus initial-fill suppression. Neither is a
maturity gate. An experimental collector emitting any OTHER event type
(NEW_REGION, PRICE_CHANGE, SOLD_OUT...) at score >= threshold would have
rung production Discord before earning that privilege through soak.

Canon (WATCH_SOAK_CONTRACT.md / FLEET_LAWS Law 3 honesty): external
delivery is a PROMOTION privilege. Collectors in the EXPERIMENTAL maturity
state must be externally silent regardless of event type or score; their
events remain fully visible in dashboard/QC. Only an operator promotion to
a production/official maturity state unlocks editorial delivery.

Implementation: `experimental_delivery_blocked()` is the single runtime
predicate consulted by the notify path alongside the existing per-type and
threshold gates. Maturity is derived from the collector registry's layer +
the soak contract's experimental set — one place, mechanically checked
against WATCH_SOAK_CONTRACT.md by test_production_wiring.py.
"""
from __future__ import annotations

# Collectors currently in EXPERIMENTAL maturity per WATCH_SOAK_CONTRACT.md.
# Promotion review removes ids from this set; nothing is auto-added here —
# inclusion requires an operator decision recorded in the contract.
EXPERIMENTAL_MATURITY_COLLECTORS: frozenset[str] = frozenset({
    "tissot_sitemap",
    "timex_uk_products",
    "goldsmiths_uk_retailer",
})


def experimental_delivery_blocked(collector_id: str | None) -> bool:
    """True when this collector's maturity state denies external delivery.

    Fleet-wide: applies to ANY collector in the experimental maturity set,
    not just specific brands. Unknown collectors are NOT blocked here —
    they have no registered identity at all and are handled by upstream
    validation (an unregistered collector should never reach delivery).
    """
    return collector_id in EXPERIMENTAL_MATURITY_COLLECTORS
