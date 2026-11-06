"""Blackout ArtDMX is assembled from a dark universe (axes/FIXED only)."""

from __future__ import annotations

from orng_led.api.runtime import AppRuntime
from orng_led.engine.show_whitelist import assert_blackout_universe
from orng_led.output import RecordingSocket

SAFE_TEST_IP = "127.0.0.1"


def _armed(runtime: AppRuntime) -> RecordingSocket:
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    runtime.arm_output(confirmed=True)
    return sock


def _dmx(packet: bytes) -> list[int]:
    return list(packet[-512:])


def test_raw_channel_512_is_dropped_on_first_and_next_artdmx() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    sock = _armed(runtime)
    runtime.engine.set_blackout(False)
    runtime.enter_raw_tester()
    runtime.raw_tester_set(channel=512, value=211)
    runtime.tick(dt_s=0.05)
    lit = _dmx(sock.sent[-1][0])
    assert lit[511] == 211

    runtime.apply_blackout(True)
    runtime.tick(dt_s=0.05)
    first = _dmx(sock.sent[-1][0])
    assert first[511] == 0
    assert_blackout_universe(runtime.show, first)

    runtime.tick(dt_s=0.05)
    nxt = _dmx(sock.sent[-1][0])
    assert nxt[511] == 0
    assert_blackout_universe(runtime.show, nxt)
    assert runtime.raw_tester.frame[511] == 211


def test_ordinary_preset_blackout_keeps_only_allowed_exceptions() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    sock = _armed(runtime)
    runtime.engine.set_blackout(False)
    runtime.engine.select_preset("P05")
    runtime.tick(dt_s=0.2)
    runtime.apply_blackout(True)
    runtime.tick(dt_s=0.05)
    first = _dmx(sock.sent[-1][0])
    assert_blackout_universe(runtime.show, first)
    runtime.tick(dt_s=0.05)
    assert_blackout_universe(runtime.show, _dmx(sock.sent[-1][0]))


def test_calibration_blackout_keeps_only_allowed_exceptions() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    sock = _armed(runtime)
    runtime.engine.set_blackout(False)
    runtime.begin_beam_calibration_test("beam_left", confirmed=True)
    runtime.tick(dt_s=0.05)
    runtime.apply_blackout(True)
    runtime.tick(dt_s=0.05)
    first = _dmx(sock.sent[-1][0])
    assert_blackout_universe(runtime.show, first)
    runtime.tick(dt_s=0.05)
    assert_blackout_universe(runtime.show, _dmx(sock.sent[-1][0]))
