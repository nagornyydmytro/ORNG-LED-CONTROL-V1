"""Backend smoke tests for the L002 scaffold."""

from fastapi.testclient import TestClient

from orng_led.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "orng-led-control"
    assert payload["transport"] == "mock"
    assert payload["output_armed"] is False
    assert "version" in payload
