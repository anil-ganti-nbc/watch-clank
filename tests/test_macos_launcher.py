from pathlib import Path

from fastapi.testclient import TestClient


def test_field_test_is_read_only_and_reports_provenance(monkeypatch):
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    monkeypatch.setenv("WATCH_CLANK_RELEASE_CHANNEL", "field-test")
    monkeypatch.setenv("WATCH_CLANK_BUILD_REVISION", "test-revision")
    monkeypatch.setenv("WATCH_CLANK_STATE_ROOT", "/tmp/watch-state")
    from app.main import app

    with TestClient(app) as client:
        runtime = client.get("/api/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["read_only"] is True
        assert runtime.json()["revision"] == "test-revision"
        assert runtime.headers["X-Watch-Clank-Mode"] == "FIELD TEST / READ ONLY"
        blocked = client.post("/operations/run-all-safe")
        assert blocked.status_code == 403


def test_launcher_has_no_host_specific_path_or_secret_material():
    source = (Path(__file__).parents[1] / "native/macos/launcher.py").read_text()
    assert "/Users/" not in source
    assert "discord.com/api/webhooks" not in source
    assert '"127.0.0.1"' in source
    assert 'EDITORIAL_NOTIFICATIONS_ENABLED="false"' in source
