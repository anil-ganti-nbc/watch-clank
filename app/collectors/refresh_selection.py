"""Bounded catalogue refresh: unseen URLs first, oldest observations next."""
from __future__ import annotations

from datetime import UTC, datetime

from app.collectors.base import DiscoveredItem
from app.core.time import ensure_utc


def order_known_refresh(
    items: list[DiscoveredItem], last_observed_by_url: dict[str, datetime] | None
) -> list[DiscoveredItem]:
    """Use durable observation times, so restart/failed runs cannot advance a cursor.

    Missing timestamps sort first. Without observation evidence the caller's
    document order is preserved (offline callers and the discovery-only lane).
    This changes selection, never creates stock or launch evidence.
    """
    if last_observed_by_url is None:
        return items
    oldest = datetime.min.replace(tzinfo=UTC)
    return sorted(items, key=lambda item: (
        ensure_utc(last_observed_by_url[item.url]) if item.url in last_observed_by_url else oldest,
        item.url,
    ))
