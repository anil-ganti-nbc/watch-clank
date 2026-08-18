import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "native" / "macos"))
import launcher  # noqa: E402


def test_field_test_allows_selected_collection_and_run_all_but_blocks_other_mutation(monkeypatch):
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    monkeypatch.setenv("WATCH_CLANK_RELEASE_CHANNEL", "field-test")
    monkeypatch.setenv("WATCH_CLANK_BUILD_REVISION", "test-revision")
    monkeypatch.setenv("WATCH_CLANK_STATE_ROOT", "/tmp/watch-state")
    import app.main as main_module

    monkeypatch.setattr(main_module, "_require_loopback", lambda request: None)
    # start_all is mocked to avoid spawning a real background thread full of
    # real subprocess calls -- this test only proves the middleware/route
    # allow this request through, not the batch-execution logic itself
    # (covered separately, with a faked subprocess runner, in test_web.py).
    monkeypatch.setattr(main_module._local_collection, "start_all", lambda jobs: True)
    monkeypatch.setattr(
        main_module._local_collection,
        "snapshot",
        lambda: {"status": "RUNNING", "running": True, "mode": "batch", "total": 19, "completed": 0},
    )
    with TestClient(main_module.app) as client:
        runtime = client.get("/api/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["read_only"] is False
        assert runtime.json()["local_collection"] is True
        assert runtime.json()["external_delivery"] is False
        assert runtime.json()["revision"] == "test-revision"
        assert runtime.headers["X-Watch-Clank-Mode"] == "FIELD TEST / LOCAL COLLECTION / DELIVERY DISABLED"

        # RUN ALL SAFE COLLECTORS is local-only, no-delivery, same category
        # as a single COLLECT -- it must be allowed through the mutation
        # boundary in field-test mode (2026-08-18: the operator asked for
        # Windows Control Centre's RUN ALL parity on macOS).
        allowed = client.post("/operations/run-all-safe")
        assert allowed.status_code == 202
        assert allowed.json()["mode"] == "batch"

        # Something genuinely outside the allow-list is still blocked.
        blocked = client.post("/operations/not-a-real-mutation")
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


def test_run_all_overlap_with_single_collector_is_refused(monkeypatch):
    """RUN ALL and a single RUN NOW/COLLECT share one lock -- neither may
    start while the other is active, since both write to the same local
    database via the same collector subprocess mechanism."""
    monkeypatch.setenv("WATCH_CLANK_FIELD_TEST", "1")
    import app.main as main_module

    monkeypatch.setattr(main_module, "_require_loopback", lambda request: None)
    with TestClient(main_module.app) as client:
        monkeypatch.setattr(main_module._local_collection, "start", lambda collector_id, cli_args: True)
        started = client.post("/operations/run/timex_news")
        assert started.status_code == 202

        monkeypatch.setattr(main_module._local_collection, "start_all", lambda jobs: False)
        monkeypatch.setattr(
            main_module._local_collection,
            "snapshot",
            lambda: {"status": "RUNNING", "running": True, "mode": "single", "collector_id": "timex_news"},
        )
        blocked = client.post("/operations/run-all-safe")
        assert blocked.status_code == 409


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


def test_evidence_views_are_local_snapshot_drilldown():
    root = Path(__file__).parents[1]
    index = (root / "app/templates/evidence.html").read_text()
    detail = (root / "app/templates/evidence_detail.html").read_text()
    assert "/evidence/{{ f.id }}" in index
    assert "fetch.blob.filepath" in detail
    launcher_source = (root / "native/macos/launcher.py").read_text()
    assert 'SNAPSHOT_STORAGE_ROOT=str(root / "snapshots")' in launcher_source


# --- 2026-08-18: build-time git-SHA provenance (was silently always the
# "local development build" fallback for every real Finder/`open` launch,
# since that never forwards the building shell's exported env vars) -------


def test_build_revision_prefers_bundled_file(tmp_path, monkeypatch):
    (tmp_path / "build_revision.txt").write_text("abc1234\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "resource_root", lambda: tmp_path)
    monkeypatch.delenv("WATCH_CLANK_PACKAGED_REVISION", raising=False)
    assert launcher.build_revision() == "abc1234"


def test_build_revision_falls_back_to_env_var_when_no_bundled_file(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "resource_root", lambda: tmp_path)
    monkeypatch.setenv("WATCH_CLANK_PACKAGED_REVISION", "env-revision")
    assert launcher.build_revision() == "env-revision"


def test_build_revision_falls_back_to_default_when_neither_present(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "resource_root", lambda: tmp_path)
    monkeypatch.delenv("WATCH_CLANK_PACKAGED_REVISION", raising=False)
    assert launcher.build_revision() == "local development build"


def test_build_script_computes_git_sha_by_default():
    source = (Path(__file__).parents[1] / "native/macos/build.sh").read_text()
    assert "git rev-parse --short HEAD" in source
    assert "build_revision.txt" in source
