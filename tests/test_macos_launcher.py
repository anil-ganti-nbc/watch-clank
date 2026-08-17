from pathlib import Path

from fastapi.testclient import TestClient


def test_field_test_allows_selected_collection_but_blocks_broad_mutation(monkeypatch):
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    monkeypatch.setenv("WATCH_CLANK_RELEASE_CHANNEL", "field-test")
    monkeypatch.setenv("WATCH_CLANK_BUILD_REVISION", "test-revision")
    monkeypatch.setenv("WATCH_CLANK_STATE_ROOT", "/tmp/watch-state")
    from app.main import app

    with TestClient(app) as client:
        runtime = client.get("/api/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["read_only"] is False
        assert runtime.json()["local_collection"] is True
        assert runtime.json()["external_delivery"] is False
        assert runtime.json()["revision"] == "test-revision"
        assert runtime.headers["X-Watch-Clank-Mode"] == "FIELD TEST / LOCAL COLLECTION / DELIVERY DISABLED"
        blocked = client.post("/operations/run-all-safe")
        assert blocked.status_code == 403


def test_selected_collection_starts_and_overlap_is_refused(monkeypatch):
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    import app.main as main_module

    monkeypatch.setattr(main_module, "_require_loopback", lambda request: None)
    monkeypatch.setattr(main_module._local_collection, "start", lambda collector_id, cli_args: True)
    monkeypatch.setattr(main_module._local_collection, "snapshot", lambda: {"status": "RUNNING", "running": True, "collector_id": "timex_news"})
    with TestClient(main_module.app) as client:
        started = client.post("/operations/run/timex_news")
        assert started.status_code == 202
        assert started.json()["collector_id"] == "timex_news"
        monkeypatch.setattr(main_module._local_collection, "start", lambda collector_id, cli_args: False)
        overlap = client.post("/operations/run/timex_news")
        assert overlap.status_code == 409


def test_launcher_has_no_host_specific_path_or_secret_material():
    source = (Path(__file__).parents[1] / "native/macos/launcher.py").read_text()
    assert "/Users/" not in source
    assert "discord.com/api/webhooks" not in source
    assert '"127.0.0.1"' in source
    assert 'EDITORIAL_NOTIFICATIONS_ENABLED="false"' in source
    assert 'sys.argv[1] == "--collector-worker"' in source


def test_empty_dashboard_has_selected_collector_cta():
    template = (Path(__file__).parents[1] / "app/templates/dashboard.html").read_text()
    assert "No local Watch Clank data yet." in template
    assert "first-collector" in template
    assert "COLLECT" in template
