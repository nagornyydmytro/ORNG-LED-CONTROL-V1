"""H001: Raw tester persists across wizard steps, Art-Net and Arm."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.main import create_app
from orng_led.output import RecordingSocket, TransportKind

VENUE_CONTROLLER_IP = "2.0.0.11"
SAFE_TEST_IP = "127.0.0.1"


def _runtime() -> AppRuntime:
    return AppRuntime.create(autostart_loop=False)


def _prepare_raw_64_255(runtime: AppRuntime) -> None:
    runtime.engine.set_blackout(True)
    runtime.enter_raw_tester()
    runtime.raw_tester_set(channel=1, value=64)
    runtime.raw_tester_set(channel=2, value=255)


def test_raw_source_stable_319_2_under_blackout() -> None:
    runtime = _runtime()
    _prepare_raw_64_255(runtime)
    a = runtime.build_state()
    runtime.tick(dt_s=0.1)
    b = runtime.build_state()
    for state in (a, b):
        assert state.output.source_owner == "raw_tester"
        assert state.output.source_frame_sum == 319
        assert state.output.source_nonzero_channels == 2
        assert state.output.wire_frame_sum == 0
        assert state.output.wire_nonzero_channels == 0
        assert state.raw_tester["active"] is True
        assert state.raw_tester["frame"][0] == 64
        assert state.raw_tester["frame"][1] == 255
        assert state.raw_tester["prepared_channels"] == [
            {"channel": 1, "value": 64},
            {"channel": 2, "value": 255},
        ]


def test_artnet_and_arm_preserve_raw_session() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    _prepare_raw_64_255(runtime)
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)

    state, _ = runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    assert state.output.transport == "artnet"
    assert state.output.udp_active is True
    assert state.output.armed is False
    assert state.engine.blackout is True
    assert state.raw_tester["active"] is True
    assert state.raw_tester["frame"][0] == 64
    assert state.raw_tester["frame"][1] == 255
    assert state.output.source_owner == "raw_tester"
    assert state.output.source_frame_sum == 319
    assert state.output.source_nonzero_channels == 2
    assert state.output.wire_frame_sum == 0
    assert state.output.wire_nonzero_channels == 0

    state, _ = runtime.arm_output(confirmed=True)
    assert state.output.armed is True
    assert state.engine.blackout is True
    assert state.raw_tester["active"] is True
    assert state.raw_tester["frame"][0] == 64
    assert state.raw_tester["frame"][1] == 255
    assert state.output.source_frame_sum == 319
    assert state.output.source_nonzero_channels == 2
    assert state.output.wire_frame_sum == 0
    assert state.output.wire_nonzero_channels == 0
    assert all(packet[-512:] == bytes(512) for packet, _ in sock.sent)
    assert all(address[0] != VENUE_CONTROLLER_IP for _, address in sock.sent)

    # Simulate returning to step 6: session still authoritative in AppState.
    again = runtime.build_state()
    assert again.raw_tester["active"] is True
    assert again.output.transport == "artnet"
    assert again.output.udp_active is True
    assert again.output.armed is True
    assert again.engine.blackout is True
    assert again.output.source_frame_sum == 319
    assert again.output.wire_nonzero_channels == 0


def test_disarm_keeps_raw_and_zero_wire() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    _prepare_raw_64_255(runtime)
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    runtime.arm_output(confirmed=True)
    before = len(sock.sent)

    state, _ = runtime.disarm_output()
    assert state.output.armed is False
    assert state.engine.blackout is True
    assert state.raw_tester["active"] is True
    assert state.output.source_frame_sum == 319
    assert state.output.wire_frame_sum == 0
    assert len(sock.sent) > before
    assert all(packet[-512:] == bytes(512) for packet, _ in sock.sent[before:])


def test_explicit_raw_exit_forces_blackout_and_zeros_before_clear() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    _prepare_raw_64_255(runtime)
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    runtime.arm_output(confirmed=True)
    before = len(sock.sent)

    state = runtime.exit_raw_tester()
    assert state.engine.blackout is True
    assert state.raw_tester["active"] is False
    assert state.raw_tester["frame"] is None
    assert state.output.source_owner != "raw_tester"
    assert state.output.wire_frame_sum == 0
    assert state.output.wire_nonzero_channels == 0
    assert len(sock.sent) > before
    assert all(packet[-512:] == bytes(512) for packet, _ in sock.sent[before:])
    assert all(address[0] != VENUE_CONTROLLER_IP for _, address in sock.sent)


def test_enter_raw_does_not_force_mock_or_disarm() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    runtime.arm_output(confirmed=True)
    assert runtime.output.transport_kind is TransportKind.ARTNET
    assert runtime.output.armed is True

    state = runtime.enter_raw_tester()
    assert state.output.transport == "artnet"
    assert state.output.armed is True
    assert state.output.udp_active is True
    assert state.raw_tester["active"] is True
    assert state.engine.blackout is True


def test_api_raw_persist_across_state_fetches() -> None:
    runtime = _runtime()
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        assert client.post("/api/setup/raw-tester/enter", json={}).status_code == 200
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
        first = client.get("/api/state").json()
        second = client.get("/api/state").json()
        for state in (first, second):
            assert state["raw_tester"]["active"] is True
            assert state["raw_tester"]["frame"][0] == 64
            assert state["raw_tester"]["frame"][1] == 255
            assert state["output"]["source_owner"] == "raw_tester"
            assert state["output"]["source_frame_sum"] == 319
            assert state["output"]["source_nonzero_channels"] == 2
            assert state["output"]["wire_frame_sum"] == 0
            assert state["engine"]["blackout"] is True
