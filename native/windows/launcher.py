"""Thin Windows launcher for the Watch Clank dashboard.

Not a port of native/macos/launcher.py -- that one manages an isolated
field-test state root (its own SQLite DB, snapshot/log directories under
Application Support, a stripped-down env). This one is a small PyInstaller
entry point that reproduces exactly what the existing (hand-written, already
working) `_Launchers\\Watch Clank Dashboard.cmd` does today:

    python -m app.serve --host 127.0.0.1 --port 8765 --profile local-operator

A frozen double-click exe has no CLI args to parse, so the host/port/profile
here are hardcoded to those same defaults rather than read from argparse.
Swap in `app.serve.prepare_app("local-operator")` (which installs
app.local_operator.install_local_operator_authority) plus a manual
uvicorn.Server so we get a readiness check, an optional browser-open, and
clean shutdown on Ctrl+C / Ctrl+Break -- app.serve.main()'s `uvicorn.run(...)`
call blocks forever with none of that.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
import urllib.request
import webbrowser

# A frozen, console=False PyInstaller exe has no attached console, so
# sys.stdout/stderr are None -- both our own print() calls and uvicorn's
# default logging setup (which calls .isatty() on the configured stream)
# crash with AttributeError/ValueError otherwise. Give them a real,
# discarding stream before anything else touches them.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

HOST = "127.0.0.1"
PORT = 8765
PROFILE = "local-operator"


def wait_for_ready(url: str, server, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.should_exit:
            return False
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.1)
    return False


def main() -> int:
    import uvicorn

    from app.serve import prepare_app

    app = prepare_app(PROFILE)

    url = f"http://{HOST}:{PORT}"
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="watch-clank-dashboard", daemon=False)
    thread.start()

    stop = threading.Event()

    def request_stop(*_args: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    try:
        signal.signal(signal.SIGBREAK, request_stop)  # Windows console close/Ctrl+Break
    except AttributeError:
        pass

    try:
        if wait_for_ready(url, server):
            print(f"Watch Clank dashboard -> {url}")
            if os.environ.get("WATCH_CLANK_NO_BROWSER") != "1":
                webbrowser.open(url)
        else:
            print("Dashboard did not become ready in time.")
        while not stop.wait(0.25) and thread.is_alive():
            pass
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=15)


if __name__ == "__main__":
    raise SystemExit(main())
