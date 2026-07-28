"""H001: safe Arm / Disarm — Mock / RecordingSocket / 127.0.0.1 only."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.main import create_app
from orng_led.output import OutputError, RecordingSocket, TransportKind

VENUE_CONTROLLER_IP = "2.0.0.11"
SAFE_TEST_IP = "127.0.0.1"


def _runtime() -> AppRuntime:
    return AppRuntime.create(autostart_loop=False)


def _activate_network(runtime: AppRuntime, sock: RecordingSocket) -> None:
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    assert runtime.output.allow_real_network is True
    assert runtime.output.udp_active is True
    assert all(address[0] != VENUE_CONTROLLER_IP for _, address in sock.sent)


def test_arm_impossible_in_mock() -> None:
    runtime = _runtime()
    assert runtime.output.transport_kind is TransportKind.MOCK
    with pytest.raises(OutputError, match="Mock|заблоковано"):
        runtime.arm_output(confirmed=True)
    assert runtime.output.armed is False


def test_arm_impossible_when_udp_off() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.output.allow_real_network = True
    runtime.engine.set_blackout(True)
    # Transport still Mock → UDP inactive.
    with pytest.raises(OutputError, match="Mock|UDP|заблоковано"):
        runtime.arm_output(confirmed=True)
    assert runtime.output.armed is False
    assert sock.sent == []


def test_arm_impossible_without_confirmed() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    _activate_network(runtime, sock)
    with pytest.raises(OutputError, match="confirmed"):
        runtime.arm_output(confirmed=False)
    assert runtime.output.armed is False


def test_arm_impossible_when_blackout_off() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    _activate_network(runtime, sock)
    runtime.engine.set_blackout(False)
    with pytest.raises(OutputError, match="Blackout|заблоковано"):
        runtime.arm_output(confirmed=True)
    assert runtime.output.armed is False


def test_arm_with_prepared_nonzero_source_keeps_wire_zero() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.enter_raw_tester()
    runtime.engine.set_blackout(True)
    runtime.raw_tester_set(channel=1, value=64)
    runtime.raw_tester_set(channel=2, value=255)

    state = runtime.build_state()
    assert state.output.source_nonzero_channels == 2
    assert state.output.source_frame_sum == 64 + 255
    assert state.output.wire_nonzero_channels == 0
    assert state.output.wire_frame_sum == 0
    assert state.output.frame_sum == 0
    assert state.output.nonzero_channels == 0

    _activate_network(runtime, sock)
    # Raw session stays active with prepared values; wire stays zero.
    state = runtime.build_state()
    assert state.raw_tester["active"] is True
    assert state.output.source_nonzero_channels == 2
    assert state.output.wire_nonzero_channels == 0
    assert state.engine.blackout is True

    before = len(sock.sent)
    state, _ = runtime.arm_output(confirmed=True)
    assert state.output.armed is True
    assert state.engine.blackout is True
    assert state.output.transport == "artnet"
    assert state.output.udp_active is True
    assert state.output.source_nonzero_channels == 2
    assert state.output.wire_nonzero_channels == 0
    assert state.output.wire_frame_sum == 0
    assert all(packet[-512:] == bytes(512) for packet, _ in sock.sent[before:])
    assert all(address[0] != VENUE_CONTROLLER_IP for _, address in sock.sent)


def test_armed_plus_blackout_zeros_preset_and_raw() -> None:
    """Blackout zeroes preset base and Raw wire; Live FX may still light (see mapping tests)."""
    runtime = _runtime()
    sock = RecordingSocket()
    _activate_network(runtime, sock)
    runtime.arm_output(confirmed=True)
    assert runtime.output.armed is True
    assert runtime.engine.overlays.blackout is True

    # Raw under Blackout stays dark on the wire.
    bright = [0] * 512
    bright[0] = 200
    runtime.output.publish(bright, from_raw=True)
    assert sock.sent[-1][0][-512:] == bytes(512)

    # Engine tick with Blackout and no Live FX → zero wire.
    runtime.tick(dt_s=0.05)
    assert sock.sent[-1][0][-512:] == bytes(512)
    assert runtime.build_state().output.wire_nonzero_channels == 0


def test_disarm_sends_zeros_and_blocks_nonzero() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    _activate_network(runtime, sock)
    runtime.arm_output(confirmed=True)
    runtime.engine.set_blackout(False)
    runtime.tick(dt_s=0.05)

    before = len(sock.sent)
    state, _ = runtime.disarm_output()
    assert state.output.armed is False
    assert state.engine.blackout is True
    assert state.output.transport == "artnet"
    assert state.output.udp_active is True
    assert state.output.network_allowed is True
    assert len(sock.sent) > before
    assert all(packet[-512:] == bytes(512) for packet, _ in sock.sent[before:])

    runtime.engine.set_blackout(False)
    runtime.tick(dt_s=0.05)
    assert sock.sent[-1][0][-512:] == bytes(512)
    assert runtime.output.armed is False


def test_source_and_wire_counters_separate_on_api() -> None:
    runtime = _runtime()
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        assert client.post("/api/setup/raw-tester/enter", json={}).status_code == 200
        assert client.post("/api/commands/blackout", json={"enabled": True}).status_code == 200
        assert (
            client.post(
                "/api/setup/raw-tester/set",
                json={"channel": 1, "value": 64},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/setup/raw-tester/set",
                json={"channel": 2, "value": 255},
            ).status_code
            == 200
        )
        state = client.get("/api/state").json()
        assert state["output"]["source_nonzero_channels"] == 2
        assert state["output"]["source_frame_sum"] == 319
        assert state["output"]["wire_nonzero_channels"] == 0
        assert state["output"]["wire_frame_sum"] == 0
        assert state["output"]["frame_sum"] == 0
        assert state["output"]["nonzero_channels"] == 0


def test_api_arm_disarm_endpoints() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        denied = client.post("/api/output/arm", json={"confirmed": False})
        assert denied.status_code == 409

        mock_denied = client.post("/api/output/arm", json={"confirmed": True})
        assert mock_denied.status_code == 409

        blockers = client.get("/api/output/arm-blockers").json()
        assert blockers["ok"] is False
        assert any("Mock" in item or "UDP" in item for item in blockers["blockers"])

        # Activate via runtime (injected socket) — API activate would open real UDP.
        runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
        blockers = client.get("/api/output/arm-blockers").json()
        assert blockers["ok"] is True

        armed = client.post("/api/output/arm", json={"confirmed": True})
        assert armed.status_code == 200
        body = armed.json()["state"]
        assert body["output"]["armed"] is True
        assert body["engine"]["blackout"] is True
        assert body["output"]["wire_nonzero_channels"] == 0

        disarmed = client.post("/api/output/disarm", json={})
        assert disarmed.status_code == 200
        body = disarmed.json()["state"]
        assert body["output"]["armed"] is False
        assert body["output"]["transport"] == "artnet"
        assert body["output"]["udp_active"] is True
        assert body["engine"]["blackout"] is True

    assert all(address[0] != VENUE_CONTROLLER_IP for _, address in sock.sent)
