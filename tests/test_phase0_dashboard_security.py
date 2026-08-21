import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_dashboard_configuration_rejects_non_loopback_host(host):
    with pytest.raises(ValidationError, match="must be loopback"):
        Settings(app_host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_dashboard_configuration_accepts_loopback_host(host):
    assert Settings(app_host=host).app_host == host


def test_direct_uvicorn_style_app_still_rejects_remote_request():
    from app.main import app

    app.state.phase0_network_authorizer = None
    app.state.phase0_mutation_authorizer = None
    with TestClient(app, client=("192.168.1.50", 50000)) as client:
        assert client.get("/", headers={"host": "192.168.1.20:8765"}).status_code == 403


def test_loopback_dashboard_is_read_only_without_authenticated_profile():
    from app.main import app

    app.state.phase0_network_authorizer = None
    app.state.phase0_mutation_authorizer = None
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/qc/review/1",
            headers={"host": "127.0.0.1:8765"},
            json={"disposition": "useful"},
        )
    assert response.status_code == 403
