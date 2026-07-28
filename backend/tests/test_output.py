"""Mock/Art-Net transport integration and output safety tests."""

from __future__ import annotations

import pytest

from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.engine import Engine, FakeClock, create_engine
from orng_led.engine.frame import empty_frame
from orng_led.output import (
    FailsafeReason,
    MockTransport,
    OutputController,
    OutputError,
    RecordingSocket,
    TransportKind,
    UdpArtNetTransport,
    build_artdmx_packet,
    create_output_controller,
    parse_artdmx_header,
    release_held_controls,
)


def test_controller_starts_mock_disarmed() -> None:
    controller = create_output_controller()
    status = controller.status()
    assert status.transport is TransportKind.MOCK
    assert status.armed is False
    assert status.network_allowed is False


def test_artnet_cannot_be_enabled_implicitly() -> None:
    controller = create_output_controller()
    controller.configure_artnet(target_ip="192.168.0.50", injected_socket=RecordingSocket())
    with pytest.raises(OutputError, match="implicitly"):
        controller.switch_to_artnet(explicit=False)
    assert controller.transport_kind is TransportKind.MOCK


def test_artnet_arm_requires_explicit_action_and_artnet_transport() -> None:
    controller = create_output_controller()
    with pytest.raises(OutputError, match="explicit"):
        controller.arm(explicit=False)
    with pytest.raises(OutputError, match="Only Art-Net"):
        controller.arm(explicit=True)


def test_mock_publish_records_frames() -> None:
    controller = create_output_controller()
    frame = empty_frame()
    frame[5] = 42
    controller.publish(frame)
    assert isinstance(controller.transport, MockTransport)
    assert controller.transport.last_frame == frame
    assert controller.frames_sent == 1


def test_blackout_publish_is_zero_frame() -> None:
    controller = create_output_controller()
    controller.engine.set_blackout(True)
    frame = controller.publish_engine_tick()
    assert frame == [0] * DMX_UNIVERSE_SIZE
    assert controller.transport.last_frame == frame


def test_udp_artnet_with_recording_socket_no_real_network() -> None:
    sock = RecordingSocket()
    transport = UdpArtNetTransport(
        target_ip="10.0.0.20",
        universe=3,
        allow_real_network=False,
        socket=sock,
    )
    frame = empty_frame()
    frame[0] = 1
    transport.send_frame(frame)
    assert len(sock.sent) == 1
    packet, address = sock.sent[0]
    assert address == ("10.0.0.20", 6454)
    header = parse_artdmx_header(packet)
    assert header["universe"] == 3
    assert header["sequence"] == 1
    assert packet == build_artdmx_packet(frame, sequence=1, universe=3)


def test_udp_refuses_real_socket_without_allow_flag() -> None:
    transport = UdpArtNetTransport(target_ip="127.0.0.1", allow_real_network=False)
    with pytest.raises(OutputError, match="Real UDP"):
        transport.send_frame(empty_frame())


def test_armed_artnet_controller_sends_via_injected_socket() -> None:
    engine = create_engine()
    sock = RecordingSocket()
    controller = OutputController(engine=engine)
    controller.configure_artnet(
        target_ip="192.168.1.10",
        universe=0,
        injected_socket=sock,
    )
    controller.switch_to_artnet(explicit=True)
    controller.arm(explicit=True)
    frame = empty_frame()
    frame[10] = 99
    controller.publish(frame)
    assert len(sock.sent) == 1
    assert parse_artdmx_header(sock.sent[0][0])["length"] == 512


def test_disarmed_artnet_sends_only_zero_frames() -> None:
    """While Art-Net is active but disarmed, the wire must carry zeros only."""
    controller = create_output_controller()
    sock = RecordingSocket()
    controller.configure_artnet(target_ip="127.0.0.1", injected_socket=sock)
    controller.engine.set_blackout(True)
    controller.switch_to_artnet(explicit=True)
    assert controller.armed is False

    bright = empty_frame()
    bright[10] = 99
    controller.publish(bright)
    assert len(sock.sent) == 1
    packet, address = sock.sent[0]
    assert address == ("127.0.0.1", 6454)
    assert packet[-512:] == bytes(512)
    assert controller.transport.last_frame == [0] * DMX_UNIVERSE_SIZE


def test_nonzero_requires_armed_and_blackout_off() -> None:
    controller = create_output_controller()
    sock = RecordingSocket()
    controller.configure_artnet(target_ip="10.255.0.2", injected_socket=sock)
    controller.switch_to_artnet(explicit=True)
    controller.engine.set_blackout(False)
    controller.arm(explicit=True)

    bright = empty_frame()
    bright[3] = 40
    controller.publish(bright)
    assert controller.transport.last_frame == bright

    controller.engine.set_blackout(True)
    controller.publish(bright)
    assert controller.transport.last_frame == [0] * DMX_UNIVERSE_SIZE

    controller.engine.set_blackout(False)
    controller.disarm()
    assert controller.transport_kind is TransportKind.MOCK
    assert controller.armed is False
    assert controller.allow_real_network is False
    assert len(sock.sent) >= 2
    assert all(packet[-512:] == bytes(512) for packet, _ in sock.sent[1:])


def test_shutdown_emits_zero_frames_and_returns_to_mock() -> None:
    engine = create_engine()
    sock = RecordingSocket()
    controller = OutputController(engine=engine)
    controller.configure_artnet(target_ip="192.168.1.10", injected_socket=sock)
    controller.switch_to_artnet(explicit=True)
    controller.arm(explicit=True)
    engine.strobe_press()
    assert engine.overlays.strobe_held is True

    emitted = controller.shutdown(zero_count=3)
    assert len(emitted) == 3
    assert all(frame == [0] * DMX_UNIVERSE_SIZE for frame in emitted)
    assert len(sock.sent) == 3
    assert all(parse_artdmx_header(packet)["length"] == 512 for packet, _ in sock.sent)
    assert controller.transport_kind is TransportKind.MOCK
    assert controller.armed is False
    assert engine.overlays.strobe_held is False
    assert engine.overlays.blackout is True


def test_disconnect_focus_visibility_release_strobe() -> None:
    engine = Engine(show=create_engine().show, clock=FakeClock())
    controller = OutputController(engine=engine)
    engine.strobe_press()
    assert engine.overlays.strobe_held is True

    result = controller.on_disconnect()
    assert result["strobe_was_held"] is True
    assert engine.overlays.strobe_held is False

    engine.strobe_press()
    controller.on_focus_loss()
    assert engine.overlays.strobe_held is False

    engine.strobe_press()
    controller.on_visibility_hidden()
    assert engine.overlays.strobe_held is False

    engine.strobe_press()
    engine.on_control_disconnect()
    assert engine.overlays.strobe_held is False


def test_strobe_timeout_failsafe() -> None:
    engine = Engine(show=create_engine().show, clock=FakeClock())
    engine.strobe_press()
    engine.tick(dt_s=8.0)
    assert engine.overlays.strobe_held is False


def test_release_held_controls_preserves_preset_clock() -> None:
    engine = create_engine()
    engine.tick(dt_s=3.5)
    elapsed = engine.preset_elapsed_s
    engine.strobe_press()
    release_held_controls(engine, FailsafeReason.DISCONNECT)
    assert engine.preset_elapsed_s == elapsed
    assert engine.overlays.strobe_held is False


def test_health_reports_safe_defaults() -> None:
    from fastapi.testclient import TestClient

    from orng_led.api.runtime import AppRuntime
    from orng_led.main import create_app

    runtime = AppRuntime.create(autostart_loop=False)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        payload = client.get("/api/health").json()
        assert payload["transport"] == "mock"
        assert payload["output_armed"] is False
        assert payload["artnet_network_enabled"] is False
