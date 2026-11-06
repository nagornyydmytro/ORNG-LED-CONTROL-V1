"""Venue_ready policy: Art-Net + arm under blackout with retry (no silent fail)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.main import create_app
from orng_led.output.contract import TransportKind
from orng_led.output.udp import RecordingSocket
from orng_led.raspberry.venue_ready import (
    backoff_seconds,
    output_policy,
    resolve_startup_preset,
    venue_ready_enabled,
)


@pytest.fixture(autouse=True)
def _clear_venue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORNG_CLOUD_DEVICE_MODE", raising=False)
    monkeypatch.delenv("ORNG_KEEP_OUTPUT_ARMED", raising=False)
    monkeypatch.delenv("ORNG_OUTPUT_POLICY", raising=False)


def _prepare_artnet(rt: AppRuntime) -> RecordingSocket:
    sock = RecordingSocket()
    rt.output.configure_artnet(
        target_ip="127.0.0.1",
        universe=0,
        udp_port=6454,
        injected_socket=sock,
    )
    return sock


def test_output_policy_raspberry_is_venue_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    assert output_policy() == "venue_ready"
    assert venue_ready_enabled() is True


def test_backoff_grows_then_caps() -> None:
    assert backoff_seconds(1) == 1.0
    assert backoff_seconds(2) == 2.0
    assert backoff_seconds(99) == 30.0


def test_startup_preset_from_pad_list() -> None:
    class App:
        pad_presets = ["A01", "A02"]
        startup_preset = None

    assert resolve_startup_preset(App()) == "A01"


def test_venue_ready_arms_under_blackout_with_injected_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    rt = AppRuntime.create(autostart_loop=False)
    assert rt.engine.overlays.blackout is True
    _prepare_artnet(rt)

    with patch(
        "orng_led.raspberry.venue_ready.probe_network",
        return_value=(True, True, None),
    ):
        status = rt._venue_status_obj()
        ok = rt._venue_ready_attempt(status)

    assert ok is True
    assert rt.output.udp_active is True
    assert rt.output.armed is True
    assert rt.engine.overlays.blackout is True
    assert rt.output.transport_kind is TransportKind.ARTNET
    out = rt.venue_ready_status()
    assert out["output_policy"] == "venue_ready"


def test_venue_ready_retries_after_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    rt = AppRuntime.create(autostart_loop=False)
    _prepare_artnet(rt)

    probes = iter(
        [
            (False, False, "ethernet_link_down"),
            (True, True, None),
        ]
    )

    def _probe(**_kwargs):
        return next(probes)

    with patch("orng_led.raspberry.venue_ready.probe_network", side_effect=_probe):
        status = rt._venue_status_obj()
        assert rt._venue_ready_attempt(status) is False
        assert status.state == "waiting_network"
        assert rt._venue_ready_attempt(status) is True
        assert rt.output.armed is True
        assert rt.engine.overlays.blackout is True


def test_venue_never_auto_clears_blackout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    rt = AppRuntime.create(autostart_loop=False)
    _prepare_artnet(rt)
    rt.engine.set_blackout(False)
    with patch(
        "orng_led.raspberry.venue_ready.probe_network",
        return_value=(True, True, None),
    ):
        rt._ensure_venue_output_armed()
    # Arm path may force BO for gate, but must not restore lights automatically.
    assert rt.engine.overlays.blackout is True


def test_publish_failure_forces_blackout_under_venue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    rt = AppRuntime.create(autostart_loop=False)
    rt.engine.set_blackout(False)
    from orng_led.output.contract import OutputError

    with patch.object(rt.output, "publish", side_effect=OutputError("send failed")):
        with pytest.raises(OutputError, match="send failed"):
            rt._publish_frame([0] * 512)
    assert rt.engine.overlays.blackout is True
    status = rt.venue_ready_status()
    assert status["venue_ready_state"] == "degraded"
    assert "artnet_send_failed" in (status["venue_last_error"] or "")


def test_disarm_rearms_under_venue_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    rt = AppRuntime.create(autostart_loop=False)
    _prepare_artnet(rt)
    with patch(
        "orng_led.raspberry.venue_ready.probe_network",
        return_value=(True, True, None),
    ):
        rt._venue_ready_attempt(rt._venue_status_obj())
        assert rt.output.armed is True
        rt.disarm_output()
    assert rt.output.armed is True
    assert rt.engine.overlays.blackout is True


def test_health_exposes_venue_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    rt = AppRuntime.create(autostart_loop=False)
    app = create_app(runtime=rt, autostart_loop=False)
    with TestClient(app) as client:
        health = client.get("/api/health").json()
    assert health["output_policy"] == "venue_ready"
    assert "venue_ready_state" in health
    assert "udp_active" in health


def test_updater_health_window_defers_venue_arm(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Old external helper requires armed=false during apply health — do not arm."""
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    monkeypatch.setenv("ORNG_APP_ROOT", str(tmp_path))
    (tmp_path / "apply-request.json").write_text(
        '{"action":"apply","command_id":"test"}', encoding="utf-8"
    )
    rt = AppRuntime.create(autostart_loop=False)
    _prepare_artnet(rt)
    assert rt._updater_blocks_venue_arm() is True
    with patch(
        "orng_led.raspberry.venue_ready.probe_network",
        return_value=(True, True, None),
    ):
        ok = rt._venue_ready_attempt(rt._venue_status_obj())
    assert ok is False
    assert rt.output.armed is False
    assert rt.engine.overlays.blackout is True
    assert rt.output.udp_active is True
