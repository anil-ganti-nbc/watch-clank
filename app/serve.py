"""Supported Watch Clank dashboard launcher.

Profiles (explicit, never implicit):
- ``read-only`` (default): the fail-closed Phase 0 posture. Reads work from
  loopback; every mutation is 403 because no mutation authority is
  installed.
- ``local-operator``: additionally installs the launcher-scoped local
  operator authority (app.local_operator), allowing exactly the four
  operator-safe POST route families -- event QC, specialist lead QC,
  run-one, run-all-safe -- from loopback only. Notification delivery is a
  server-settings question and stays whatever this environment configures;
  the field-test packaged launcher is the profile that strips webhook
  secrets, not this one.

Direct ``uvicorn app.main:app`` bypasses this launcher entirely and always
gets the fail-closed default.
"""

from __future__ import annotations

import argparse


def prepare_app(profile: str):
    """Build the ASGI app for a launch profile. Kept separate from uvicorn
    startup so tests (and operators auditing the wiring) can verify exactly
    what each profile installs without running a server."""
    from app.main import app

    if profile == "local-operator":
        # Import lazily so the read-only default never even loads the
        # authority module.
        from app.local_operator import install_local_operator_authority

        install_local_operator_authority(app)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument("--settings", help="explicit absolute Discovery-only settings")
    parser.add_argument(
        "--profile",
        choices=["read-only", "local-operator"],
        default="read-only",
        help="read-only (default) = fail-closed Phase 0; local-operator = allow "
        "operator-safe mutations (QC, corrections, run-one, run-all-safe) from loopback",
    )
    args = parser.parse_args()
    if args.discovery_only:
        if not args.settings or args.host != '127.0.0.1' or args.profile != 'read-only':
            parser.error('Discovery requires explicit --settings, 127.0.0.1 and read-only profile')
        from app.discovery_cli import load
        from app.discovery_dashboard import serve
        serve(load(args.settings), port=8790 if args.port is None else args.port)
        return
    if args.settings:
        parser.error('--settings is only supported with --discovery-only')
    import uvicorn

    from app.core.config import Settings
    settings = Settings(app_host=args.host, app_port=8765 if args.port is None else args.port)
    prepare_app(args.profile)
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
