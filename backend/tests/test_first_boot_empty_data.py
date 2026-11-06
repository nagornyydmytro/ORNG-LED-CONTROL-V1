"""First boot with empty data root must not crash-loop."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from orng_led.main import create_app
from orng_led.scenes.bootstrap_show import EMPTY_SCENE_ID


def test_first_boot_empty_data_root(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    # Completely empty: no scenes/, no runtime.yaml, no cloud, no token.
    monkeypatch.setenv("ORNG_CONFIG_ROOT", str(data))
    monkeypatch.setenv("ORNG_DATA_ROOT", str(data))
    monkeypatch.delenv("ORNG_CLOUD_URL", raising=False)
    monkeypatch.delenv("ORNG_DEVICE_TOKEN", raising=False)

    app = create_app(autostart_loop=False)
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["ready"] is True
        assert health["blackout"] is True
        assert health["output_armed"] is False
        assert health["degraded"] is True
        assert health["scene_id"] == EMPTY_SCENE_ID
        assert health["artnet_network_enabled"] is False
        assert "safe_bootstrap" in (health.get("scene_error") or "")

        # Pairing/cloud status surface must remain reachable in degraded mode.
        pair = client.get("/api/cloud/status")
        assert pair.status_code == 200
        assert pair.status_code != 500
