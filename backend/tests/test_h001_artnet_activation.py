"""H001: safe Art-Net activation — Mock/loopback only, never 2.0.0.11."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.engine.frame import empty_frame
from orng_led.engine.show_whitelist import assert_lights_dark
from orng_led.main import create_app
from orng_led.output import OutputError, RecordingSocket, TransportKind
from orng_led.output.artnet import parse_artdmx_header

# Venue controller IP — tests must never send a datagram there.
VENUE_CONTROLLER_IP = "2.0.0.11"
SAFE_TEST_IP = "127.0.0.1"


def _runtime() -> AppRuntime:
    return AppRuntime.create(autostart_loop=False)


def test_startup_does_not_create_udp_or_send_packets() -> None:
    runtime = _runtime()
    assert runtime.output.transport_kind is TransportKind.MOCK
    assert runtime.output.armed is False
    assert runtime.output.allow_real_network is False
    assert runtime.output.udp_active is False
    assert runtime.engine.overlays.blackout is True
    frame = runtime._published_frame()
    assert_lights_dark(runtime.show, frame)
    runtime.tick(dt_s=1.0 / 30.0)
    assert runtime.output.transport_kind is TransportKind.MOCK
    assert runtime.output.udp_active is False


def test_yaml_artnet_preference_does_not_auto_activate() -> None:
    """Saving Art-Net in YAML must leave runtime on Mock with network off."""
    runtime = _runtime()
    app = runtime.show.app.model_dump(mode="json")
    app["transport"] = "artnet"
    app["artnet"]["target_ip"] = VENUE_CONTROLLER_IP
    app["artnet"]["universe"] = 0
    app["output_armed"] = True
    runtime.save_app_config(app)

    assert runtime.show.app.transport.value == "artnet"
    assert runtime.show.app.artnet.target_ip == VENUE_CONTROLLER_IP
    assert runtime.output.transport_kind is TransportKind.MOCK
    assert runtime.output.armed is False
    assert runtime.output.allow_real_network is False
    assert runtime.output.udp_active is False


def test_activation_auto_enforces_blackout_then_activates() -> None:
    """Activation atomically turns Blackout on; unsafe leftover state is cleared."""
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.engine.set_blackout(False)
    state, _ = runtime.activate_artnet_network(confirmed=True, allow_real_udp=False)
    assert state.engine.blackout is True
    assert state.output.transport == "artnet"
    assert state.output.armed is False
    for packet, _ in sock.sent:
        assert_lights_dark(runtime.show, list(packet[-512:]))


def test_activation_blocked_without_target_ip() -> None:
    runtime = _runtime()
    runtime.output.target_ip = None
    runtime.show.app.artnet.target_ip = None
    runtime.engine.set_blackout(True)
    with pytest.raises(OutputError, match="заблоковано|target_ip"):
        runtime.activate_artnet_network(confirmed=True, allow_real_udp=False)
    assert runtime.output.transport_kind is TransportKind.MOCK


def test_activation_allows_prepared_raw_tester() -> None:
    runtime = _runtime()
    blockers = runtime.output.activation_blockers(
        published_frame=empty_frame(),
        raw_tester_active=True,
    )
    assert not any("Raw tester" in item for item in blockers)


def test_activation_requires_confirmation() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    with pytest.raises(OutputError, match="confirmation"):
        runtime.activate_artnet_network(confirmed=False, allow_real_udp=False)
    assert sock.sent == []


def test_safe_activation_sends_only_zeros_via_injected_socket() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.output.configure_artnet(
        target_ip=SAFE_TEST_IP,
        universe=0,
        udp_port=6454,
        injected_socket=sock,
    )
    state, replay = runtime.activate_artnet_network(
        confirmed=True,
        allow_real_udp=False,
    )
    assert replay is False
    assert state.output.transport == "artnet"
    assert state.output.armed is False
    assert state.output.network_allowed is False
    assert state.output.udp_active is True
    assert state.engine.blackout is True
    assert_lights_dark(runtime.show, runtime.build_state().frame)
    assert len(sock.sent) >= 1
    for packet, address in sock.sent:
        assert address == (SAFE_TEST_IP, 6454)
        assert address[0] != VENUE_CONTROLLER_IP
        assert parse_artdmx_header(packet)["universe"] == 0
        assert_lights_dark(runtime.show, list(packet[-512:]))

    # Show clock may leave blackout; wire must stay zero while disarmed.
    runtime.engine.set_blackout(False)
    runtime.engine.select_preset("P10", reset_clock=True)
    runtime.tick(dt_s=0.1)
    assert runtime.output.armed is False
    last_packet = sock.sent[-1][0]
    assert_lights_dark(runtime.show, list(last_packet[-512:]))


def test_nonzero_impossible_without_arm_and_blackout_off() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    # allow_real_udp=True marks artnet_network_enabled while still using the
    # injected RecordingSocket (never opens a venue socket).
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    baseline = len(sock.sent)

    runtime.engine.set_blackout(False)
    runtime.tick(dt_s=0.05)
    assert_lights_dark(runtime.show, list(sock.sent[-1][0][-512:]))

    # Arm is refused while Blackout is off.
    with pytest.raises(OutputError, match="Blackout|заблоковано"):
        runtime.arm_output(confirmed=True)

    runtime.engine.set_blackout(True)
    runtime.arm_output(confirmed=True)
    assert runtime.output.armed is True
    runtime.tick(dt_s=0.05)
    assert_lights_dark(runtime.show, list(sock.sent[-1][0][-512:]))

    runtime.engine.set_blackout(False)
    bright_ticks = 0
    for _ in range(8):
        runtime.tick(dt_s=0.05)
        if any(sock.sent[-1][0][-512:]):
            bright_ticks += 1
    assert bright_ticks >= 1
    assert len(sock.sent) > baseline


def test_deactivate_and_shutdown_send_zeros_then_mock() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=False)
    assert runtime.output.transport_kind is TransportKind.ARTNET

    state, _ = runtime.deactivate_artnet_network()
    assert state.output.transport == "mock"
    assert state.output.armed is False
    assert state.output.network_allowed is False
    assert state.output.udp_active is False
    assert state.engine.blackout is True
    assert sock.closed is True
    for packet, _ in sock.sent:
        assert_lights_dark(runtime.show, list(packet[-512:]))
    assert all(address[0] != VENUE_CONTROLLER_IP for _, address in sock.sent)


def test_api_activate_deactivate_endpoints_use_safe_defaults() -> None:
    runtime = _runtime()
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        health = client.get("/api/health").json()
        assert health["transport"] == "mock"
        assert health["output_armed"] is False
        assert health["artnet_network_enabled"] is False

        denied = client.post("/api/output/activate-artnet", json={"confirmed": False})
        assert denied.status_code == 409

        blockers = client.get("/api/output/activation-blockers").json()
        assert "blockers" in blockers

        state = client.get("/api/state").json()
        assert state["output"]["transport"] == "mock"
        assert state["output"]["preferred_transport"] in {"mock", "artnet"}
        assert state["output"]["udp_active"] is False
        assert_lights_dark(runtime.show, state["frame"])
        assert state["engine"]["blackout"] is True


def test_activation_without_socket_or_flag_rolls_back_to_mock() -> None:
    runtime = _runtime()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP)
    runtime.engine.set_blackout(True)
    with pytest.raises(OutputError, match="Real UDP|safety zero|Failed"):
        runtime.output.activate_artnet(
            explicit=True,
            confirmed=True,
            allow_real_udp=False,
            zero_count=1,
        )
    assert runtime.output.transport_kind is TransportKind.MOCK
    assert runtime.output.allow_real_network is False
    assert runtime.output.armed is False
