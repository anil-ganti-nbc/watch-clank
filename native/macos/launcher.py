"""Finder entry point for the isolated Watch Clank field test."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "Watch Clank"


def state_root() -> Path:
    override = os.getenv("WATCH_CLANK_FIELD_TEST_HOME")
    return Path(override).expanduser().resolve() if override else Path.home() / "Library" / "Application Support" / APP_NAME


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def build_revision() -> str:
    """Prefer the bundled build_revision.txt (written by build.sh at
    package time -- see that script's comment) over the env var: launching
    via Finder/`open` does not forward the building shell's exported
    environment to the new process, so the env var alone was silently
    always empty for every real double-click launch."""
    bundled = resource_root() / "build_revision.txt"
    try:
        value = bundled.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    value = os.getenv("WATCH_CLANK_PACKAGED_REVISION", "").strip()
    return value or "local development build"


def configure_environment(root: Path) -> None:
    for relative in ("data", "snapshots", "logs", "reports", "cache", "runtime", "tmp", "config", "feedback"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    for name in tuple(os.environ):
        if "DISCORD" in name.upper() or "WEBHOOK" in name.upper():
            os.environ.pop(name, None)
    os.environ.update(
        DATABASE_URL=f"sqlite:///{root / 'data' / 'watch_clank.db'}",
        SNAPSHOT_STORAGE_ROOT=str(root / "snapshots"),
        LOG_DIR=str(root / "logs"),
        APP_HOST="127.0.0.1",
        EDITORIAL_NOTIFICATIONS_ENABLED="false",
        WATCH_CLANK_INSTANCE="Watch Clank FIELD TEST",
        WATCH_CLANK_FIELD_TEST="1",
        WATCH_CLANK_STATE_ROOT=str(root),
        WATCH_CLANK_RELEASE_CHANNEL="field-test",
        WATCH_CLANK_BUILD_REVISION=build_revision(),
        WATCH_CLANK_ALEMBIC_INI=str(resource_root() / "alembic.ini"),
    )
    os.chdir(root)


def migrate(root: Path) -> None:
    from alembic.config import Config

    from alembic import command

    resources = resource_root()
    config = Config(str(resources / "alembic.ini"))
    config.set_main_option("script_location", str(resources / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{root / 'data' / 'watch_clank.db'}")
    command.upgrade(config, "head")


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(url: str, server, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.should_exit:
            raise RuntimeError("dashboard stopped before readiness")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("dashboard did not become ready")


def main() -> int:
    root = state_root()
    configure_environment(root)
    migrate(root)

    if len(sys.argv) > 1 and sys.argv[1] == "--collector-worker":
        sys.argv = ["scripts.run_pipeline", *sys.argv[2:]]
        try:
            from scripts.run_pipeline import main as run_pipeline_main

            run_pipeline_main()
        except SystemExit as exit_status:
            return int(exit_status.code or 0)
        return 0

    import uvicorn

    from app.main import app

    port = available_port()
    url = f"http://127.0.0.1:{port}"
    marker = root / "runtime" / "dashboard.json"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    thread = threading.Thread(target=server.run, name="watch-clank-dashboard")
    thread.start()
    try:
        wait_ready(url, server)
        marker.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": url}), encoding="utf-8")
        if os.getenv("WATCH_CLANK_NO_BROWSER") != "1":
            subprocess.Popen(["open", url], close_fds=True)
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        while not stop.wait(0.25) and thread.is_alive():
            pass
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        marker.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
