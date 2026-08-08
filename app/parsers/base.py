"""Base parser interface and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedWatch:
    """Structured result from a parser run on a single product snapshot."""

    reference_raw: str
    manufacturer: str = "Casio"
    brand: str | None = None
    collection: str | None = None
    model_name: str | None = None

    release_date: str | None = None  # ISO date string if available
    movement_type: str | None = None
    caliber_or_module: str | None = None

    solar: bool | None = None
    bluetooth: bool | None = None
    radio_sync: bool | None = None
    gps: bool | None = None

    case_material: str | None = None
    crystal: str | None = None
    water_resistance_m: int | None = None
    limited_edition: bool | None = None

    price: float | None = None
    currency: str | None = None
    availability_status: str | None = None

    extra_specs: dict[str, Any] = field(default_factory=dict)

    # Confidence & diagnostics
    field_confidence: dict[str, float] = field(default_factory=dict)
    parser_warnings: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0

    source_url: str | None = None


@dataclass
class ParseResult:
    """Result of parsing a snapshot (may contain zero or one product for Stage 1)."""

    success: bool
    parser_id: str
    parser_version: str
    watches: list[ParsedWatch] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
