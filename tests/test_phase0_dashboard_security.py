import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_dashboard_configuration_rejects_non_loopback_host(host):
    with pytest.raises(ValidationError, match="must be loopback"):
        Settings(app_host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_dashboard_configuration_accepts_loopback_host(host):
    assert Settings(app_host=host).app_host == host
