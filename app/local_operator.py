"""Launcher-scoped local operator mutation authority (Phase 0 reconciliation).

Phase 0 remediation (bf87c7d) made the dashboard fail-closed: every non-
GET/HEAD/OPTIONS request is denied unless ``app.state.phase0_mutation_
authorizer`` is installed. Nothing in production code ever installed one,
so the entire local field-test workflow -- QC triage, corrections, run-one,
run-all-safe -- was dead in every launch profile (verified at runtime
against the real server, not inferred). This module is the narrow
restoration, with the security posture Phase 0 intended:

- Authority is installed ONLY by an explicit supported launcher path:
  the macOS field-test launcher (WATCH_CLANK_FIELD_TEST=1) or
  ``python -m app.serve --profile local-operator``.
- Direct ``uvicorn app.main:app`` and any other unsupported launch path
  stay fail-closed: no authorizer in app.state means no mutations.
- The authorizer re-proves, per request: loopback client address AND a
  loopback/localhost Host header. Forwarded headers (X-Forwarded-For,
  X-Real-IP, X-Forwarded-Host) are deliberately ignored -- there is no
  proxy architecture here and trusting them would be spoofable.
- Only an explicit allowlist of operator-safe routes is authorized,
  derived from the app's actual POST surface (there are exactly four POST
  route families today; a new one must be added HERE deliberately, never
  inherited implicitly):
    POST /api/qc/review/{event_id}       event QC incl. corrections
    POST /api/qc/lead-review/{lead_id}   specialist lead QC incl. corrections
    POST /operations/run/{collector_id}  run one safe collector
    POST /operations/run-all-safe        batch-run all safe collectors
  Everything else -- including any future POST -- stays 403.

Notification delivery is NOT part of this authority: the field-test
launcher strips DISCORD*/WEBHOOK* env vars and sets
EDITORIAL_NOTIFICATIONS_ENABLED=false before this module is ever reached,
so authorized local mutations can write the local database but can never
send anything off-machine.
"""

from __future__ import annotations

import ipaddress
import re

from fastapi import Request

# Operator-safe mutation routes. Anchored, explicit, and closed-ended on
# purpose: "starts with" matching would silently authorize future routes.
_LOCAL_OPERATOR_ROUTES = (
    re.compile(r"^/api/qc/review/\d+$"),
    re.compile(r"^/api/qc/lead-review/\d+$"),
    re.compile(r"^/operations/run/[A-Za-z0-9_]+$"),
    re.compile(r"^/operations/run-all-safe$"),
)


def _loopback(value: str | None) -> bool:
    """Same semantics as app.main._loopback: a literal loopback IP or the
    'localhost' name. Never consults forwarded headers."""
    if not value:
        return False
    value = value.strip().strip("[]")
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value.lower() == "localhost"


def request_is_local_operator_mutation(request: Request) -> bool:
    """True only for a POST from a loopback client, to a loopback Host,
    on the explicit operator allowlist."""
    if request.method != "POST":
        return False
    client_host = request.client.host if request.client else None
    host_header = request.headers.get("host", "").rsplit(":", 1)[0]
    if not _loopback(client_host) or not _loopback(host_header):
        return False
    path = request.url.path
    return any(pattern.match(path) for pattern in _LOCAL_OPERATOR_ROUTES)


def install_local_operator_authority(app) -> None:
    """Install the local-operator mutation authorizer on ``app.state``.

    Idempotent. Called only by supported launchers that have already bound
    loopback and (for the field-test profile) stripped notification
    secrets. Unsupported launch paths never call this, so they remain
    fail-closed by construction.
    """
    app.state.phase0_mutation_authorizer = request_is_local_operator_mutation


def mutation_authority(app) -> str:
    """Provenance label for /api/runtime and the dashboard header.

    LOCAL_OPERATOR -- this instance may execute operator-safe mutations
        from loopback (installed by a supported launcher).
    NONE -- fail-closed Phase 0 default; reads only.
    """
    authorizer = getattr(app.state, "phase0_mutation_authorizer", None)
    if authorizer is request_is_local_operator_mutation:
        return "LOCAL_OPERATOR"
    if authorizer is not None:
        return "CUSTOM"
    return "NONE"
