"""Backend smoke tests for health endpoint."""

from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.main import create_app


def test_health_returns_ok() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["service"] == "orng-led-control"
        assert payload["transport"] == "mock"
        assert payload["output_armed"] is False
        assert payload["artnet_network_enabled"] is False
        assert "version" in payload
