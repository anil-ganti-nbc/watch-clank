"""Base collector types and utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiscoveredItem:
    """A URL discovered by a collector that should be fetched."""

    url: str
    title: str | None = None
    reference_hint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    """Result of fetching a single URL."""

    url: str
    success: bool
    status_code: int | None = None
    content_type: str | None = None
    payload: bytes | None = None
    error: str | None = None
    elapsed_ms: int | None = None


@dataclass
class CollectorRunResult:
    """Summary produced by a collector run (no DB writes)."""

    collector_id: str
    collector_version: str
    region: str
    trust_score: float
    discovered: list[DiscoveredItem] = field(default_factory=list)
    fetched: list[FetchResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
