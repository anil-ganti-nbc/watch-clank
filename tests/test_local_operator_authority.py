"""Adversarial security + acceptance tests for the launcher-scoped local
operator mutation authority (Phase 0 reconciliation, 2026-08-21).

Phase 0 (bf87c7d) made every dashboard mutation fail closed unless
``app.state.phase0_mutation_authorizer`` is installed -- and nothing
installed one, killing the entire local field-test workflow. These tests
pin the restored model:

- unsupported launch paths (direct uvicorn / bare app import) stay
  fail-closed;
- the supported launchers install the REAL authorizer (never a blanket
  ``lambda _: True``), which re-proves loopback client + loopback Host on
  every mutation and only on an explicit route allowlist;
- spoofing via Host headers, forwarded headers, or client addresses is
  rejected; there is no proxy architecture and none is trusted.
"""

import contextlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "native" / "macos"))
import launcher  # noqa: E402

LOOPBACK = ("127.0.0.1", 50000)
LAN = ("192.168.1.50", 50000)


def _post(client: TestClient, path: str, host: str = "127.0.0.1:8765", **kwargs):
    return client.post(path, headers={"host": host}, **kwargs)


# --- fail-closed default -----------------------------------------------------


def test_unsupported_app_is_fail_closed():
    """Direct uvicorn-style usage (no supported launcher): loopback reads
    work, EVERY mutation -- including all four operator families -- is 403,
    and provenance reports it honestly."""
    from app.main import app

    app.state.phase0_mutation_authorizer = None
    with TestClient(app, client=LOOPBACK) as client:
        assert client.get("/", headers={"host": "127.0.0.1:8765"}).status_code == 200
        for path in (
            "/api/qc/review/1",
            "/api/qc/lead-review/1",
            "/operations/run/timex_news",
            "/operations/run-all-safe",
            "/operations/not-a-real-mutation",
        ):
            assert _post(client, path).status_code == 403, path
        runtime = client.get("/api/runtime", headers={"host": "127.0.0.1:8765"})
        assert runtime.json()["mutation_authority"] == "NONE"
        assert runtime.json()["read_only"] is True


# --- the real authority, installed by a supported launcher -------------------


@pytest.fixture()
def operator_client(monkeypatch):
    """The actual installation path used by the macOS field-test launcher
    and `python -m app.serve --profile local-operator` -- no monkeypatched
    lambdas standing in for security logic."""
    from app.local_operator import install_local_operator_authority
    from app.main import app

    # Field-test profile: routes use the mockable local-collection thread
    # instead of spawning real collector subprocesses from tests.
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    install_local_operator_authority(app)
    monkeypatch.setattr(app.state, "phase0_mutation_authorizer", app.state.phase0_mutation_authorizer)
    with TestClient(app, client=LOOPBACK) as client:
        yield client


def test_local_operator_allows_exactly_the_allowlist(operator_client, monkeypatch):
    from app.main import _local_collection

    monkeypatch.setattr(_local_collection, "start", lambda collector_id, cli_args: True)
    monkeypatch.setattr(_local_collection, "start_all", lambda jobs: True)
    monkeypatch.setattr(
        _local_collection,
        "snapshot",
        lambda: {"status": "RUNNING", "running": True, "mode": "single", "collector_id": "timex_news"},
    )

    # The four operator-safe families pass containment (route-level results
    # may be 202/404/etc., but never the middleware's 403).
    assert _post(operator_client, "/operations/run/timex_news").status_code == 202
    assert _post(operator_client, "/operations/run-all-safe").status_code == 202
    assert _post(operator_client, "/api/qc/review/1").status_code != 403
    assert _post(operator_client, "/api/qc/lead-review/1").status_code != 403

    # Anything else is denied even from loopback.
    assert _post(operator_client, "/operations/not-a-real-mutation").status_code == 403
    assert _post(operator_client, "/api/qc/review/not-an-int").status_code == 403
    assert _post(operator_client, "/operations/run/../escape").status_code in (403, 404)

    # Non-POST methods never mutate.
    assert operator_client.delete(
        "/operations/run/timex_news", headers={"host": "127.0.0.1:8765"}
    ).status_code == 403

    runtime = operator_client.get("/api/runtime", headers={"host": "127.0.0.1:8765"})
    assert runtime.json()["mutation_authority"] == "LOCAL_OPERATOR"
    assert runtime.json()["read_only"] is False


def test_lan_client_rejected_even_with_loopback_host_spoof():
    from app.local_operator import install_local_operator_authority
    from app.main import app

    install_local_operator_authority(app)
    with TestClient(app, client=LAN) as client:
        assert _post(client, "/operations/run-all-safe").status_code == 403
        # X-Forwarded-* must not rescue a non-loopback client: there is no
        # proxy architecture and forwarded headers are never consulted.
        assert (
            client.post(
                "/operations/run-all-safe",
                headers={
                    "host": "127.0.0.1:8765",
                    "X-Forwarded-For": "127.0.0.1",
                    "X-Real-IP": "127.0.0.1",
                },
            ).status_code
            == 403
        )


def test_loopback_client_with_lan_host_header_rejected(operator_client):
    assert _post(operator_client, "/operations/run-all-safe", host="192.168.1.20:8765").status_code == 403


def test_malformed_host_header_rejected(operator_client):
    assert _post(operator_client, "/operations/run-all-safe", host="not a host").status_code == 403
    assert _post(operator_client, "/operations/run-all-safe", host="").status_code == 403


def test_localhost_and_ipv6_loopback_accepted(operator_client, monkeypatch):
    from app.main import _local_collection

    monkeypatch.setattr(_local_collection, "start_all", lambda jobs: True)
    monkeypatch.setattr(
        _local_collection,
        "snapshot",
        lambda: {"status": "RUNNING", "running": True, "mode": "batch"},
    )
    # Middleware acceptance is the invariant under test: alternate loopback
    # Host spellings must reach the route (202), never the containment 403.
    assert _post(operator_client, "/operations/run-all-safe", host="localhost:8765").status_code == 202
    assert (
        operator_client.post(
            "/operations/run-all-safe", headers={"host": "[::1]:8765"}
        ).status_code
        == 202
    )


def test_wildcard_bind_configuration_still_rejected():
    """Even if someone forces uvicorn onto a wildcard address, the config
    validator refuses non-loopback APP_HOST and the middleware still denies
    remote clients."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="must be loopback"):
        Settings(app_host="0.0.0.0")


# --- launcher wiring ---------------------------------------------------------


def test_macos_launcher_installs_real_authority_and_strips_secrets(tmp_path, monkeypatch):
    """Acceptance of the actual field-test startup sequence: notification
    secrets injected BEFORE startup are stripped, and the launcher installs
    the real authority module (source-pinned so it cannot silently regress
    to a blanket allow-lambda)."""
    import os

    # configure_environment mutates process-global state (environ + cwd);
    # snapshot and restore so nothing leaks into other tests.
    saved_environ = dict(os.environ)
    saved_cwd = os.getcwd()
    try:
        monkeypatch.setenv("DISCORD_EDITORIAL_WEBHOOK_URL", "https://discord.example/hook")
        monkeypatch.setenv("SOME_WEBHOOK_URL", "https://example.example/hook")
        launcher.configure_environment(tmp_path)

        assert "DISCORD_EDITORIAL_WEBHOOK_URL" not in os.environ
        assert "SOME_WEBHOOK_URL" not in os.environ
        assert os.environ["EDITORIAL_NOTIFICATIONS_ENABLED"] == "false"
        assert os.environ["APP_HOST"] == "127.0.0.1"
        assert "DATABASE_URL" in os.environ and "watch_clank.db" in os.environ["DATABASE_URL"]
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)
        os.chdir(saved_cwd)

    source = (Path(__file__).parents[1] / "native/macos/launcher.py").read_text()
    assert "install_local_operator_authority(app)" in source


def test_serve_profiles_install_distinctly():
    """`python -m app.serve` default stays fail-closed; only the explicit
    local-operator profile installs authority."""
    from app import serve
    from app.local_operator import request_is_local_operator_mutation
    from app.main import app

    # app.state is process-global and other tests may have installed the
    # authority; reset it to prove what each profile ACTUALLY installs.
    with contextlib.suppress(AttributeError):
        del app.state.phase0_mutation_authorizer

    app_ro = serve.prepare_app("read-only")
    assert getattr(app_ro.state, "phase0_mutation_authorizer", None) is None

    app_op = serve.prepare_app("local-operator")
    assert app_op.state.phase0_mutation_authorizer is request_is_local_operator_mutation
