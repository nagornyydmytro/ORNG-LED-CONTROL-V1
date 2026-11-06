"""Home-only executor failsafe: blackout last-wins and failed frame-cycle liveness.

Temporary scene copy, MockTransport / RecordingSocket. No real UDP, Pi, or production.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_scene_dir, global_channel
from orng_led.engine.show_whitelist import assert_lights_dark
from orng_led.output import RecordingSocket, TransportKind, parse_artdmx_header
from orng_led.setup.beam_calibration import VISIBLE_BEAM_DIMMER
from orng_led.updater.helper_protocol import HelperError, poll_health

SAFE_IP = "127.0.0.1"
VISIBLE_LEVEL = int(round(VISIBLE_BEAM_DIMMER * 255))


def _temp_runtime(tmp_path: Path) -> AppRuntime:
    cfg = tmp_path / "config"
    shutil.copytree(default_scene_dir(), cfg)
    return AppRuntime.create(autostart_loop=False, config_dir=cfg)


def _map_compatible_head(runtime: AppRuntime) -> None:
    profile = runtime.show.profiles["beam_13ch"].model_dump(mode="json")
    mapped = {
        1: ("pan_coarse", None, None),
        2: ("tilt_coarse", None, None),
        3: ("dimmer", None, None),
        4: ("shutter", None, {"open": 255, "closed": 0}),
        5: ("red", None, None),
        6: ("green", None, None),
        7: ("blue", None, None),
        8: ("white", None, None),
        13: ("fixed", 77, None),
    }
    for channel in profile["channels"]:
        spec = mapped.get(int(channel["local"]))
        if spec is None:
            continue
        role, fixed, controls = spec
        channel["role"] = role
        channel["fixed_value"] = fixed
        channel["control_values"] = controls
    runtime.save_profile(profile)


def _dmx(packet: bytes) -> list[int]:
    header = parse_artdmx_header(packet)
    assert header["length"] == 512
    return list(packet[-512:])


def _head_slots(runtime: AppRuntime, frame: list[int]) -> dict[str, int]:
    fixture = runtime.show.patch.fixture("beam_left")

    def at(local: int) -> int:
        return int(frame[global_channel(fixture.start_address, local) - 1])

    return {
        "pan": at(1),
        "tilt": at(2),
        "dimmer": at(3),
        "shutter": at(4),
        "r": at(5),
        "g": at(6),
        "b": at(7),
        "w": at(8),
        "fixed": at(13),
    }


def _arm_recording(runtime: AppRuntime) -> RecordingSocket:
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    runtime.arm_output(confirmed=True)
    return sock


def test_blackout_extinguishes_cached_visible_calibration(tmp_path: Path) -> None:
    runtime = _temp_runtime(tmp_path)
    _map_compatible_head(runtime)
    sock = _arm_recording(runtime)
    runtime.apply_blackout(False)

    runtime.begin_beam_calibration_test("beam_left", confirmed=True)
    runtime.set_beam_calibration_position(pan=0.7, tilt=0.3)
    runtime.set_beam_calibration_visible(enabled=True, confirmed=True)

    lit = _head_slots(runtime, _dmx(sock.sent[-1][0]))
    assert lit["dimmer"] == VISIBLE_LEVEL
    assert lit["shutter"] == 255
    assert (lit["r"], lit["g"], lit["b"], lit["w"]) == (VISIBLE_LEVEL,) * 4
    assert lit["pan"] > 0 and lit["tilt"] > 0

    runtime.apply_blackout(True)
    first = _head_slots(runtime, _dmx(sock.sent[-1][0]))
    assert first["dimmer"] == 0
    assert first["shutter"] == 0
    assert (first["r"], first["g"], first["b"], first["w"]) == (0, 0, 0, 0)
    assert first["pan"] == lit["pan"]
    assert first["tilt"] == lit["tilt"]
    assert first["fixed"] == 77
    assert_lights_dark(runtime.show, _dmx(sock.sent[-1][0]))
    session = runtime.beam_calibration.as_dict()
    assert session["visible_beam_confirmed"] is False
    assert session["visible_beam_on"] is False

    runtime.tick(dt_s=0.05)
    nxt = _head_slots(runtime, _dmx(sock.sent[-1][0]))
    assert nxt == first
    assert_lights_dark(runtime.show, _dmx(sock.sent[-1][0]))

    runtime.apply_blackout(False)
    runtime.tick(dt_s=0.05)
    resumed = _head_slots(runtime, _dmx(sock.sent[-1][0]))
    assert resumed["dimmer"] == 0
    assert resumed["shutter"] == 0
    assert (resumed["r"], resumed["g"], resumed["b"], resumed["w"]) == (0, 0, 0, 0)
    assert resumed["pan"] == lit["pan"]
    assert resumed["tilt"] == lit["tilt"]
    assert runtime.beam_calibration.visible_beam_confirmed is False

    runtime.set_beam_calibration_visible(enabled=True, confirmed=True)
    confirmed = _head_slots(runtime, _dmx(sock.sent[-1][0]))
    assert confirmed["dimmer"] == VISIBLE_LEVEL
    assert confirmed["shutter"] == 255


def test_blackout_from_keyboard_path_same_as_phone(tmp_path: Path) -> None:
    runtime = _temp_runtime(tmp_path)
    _map_compatible_head(runtime)
    sock = _arm_recording(runtime)
    runtime.apply_blackout(False)
    runtime.begin_beam_calibration_test("beam_left", confirmed=True)
    runtime.set_beam_calibration_position(pan=0.6, tilt=0.4)
    runtime.set_beam_calibration_visible(enabled=True, confirmed=True)
    lit = _head_slots(runtime, _dmx(sock.sent[-1][0]))
    assert lit["dimmer"] == VISIBLE_LEVEL

    # Keyboard / macropad share apply_blackout with the phone command.
    runtime.apply_blackout(None)
    out = _head_slots(runtime, _dmx(sock.sent[-1][0]))
    assert out["dimmer"] == 0
    assert out["pan"] == lit["pan"]
    assert out["tilt"] == lit["tilt"]
    assert runtime.engine.overlays.blackout is True


def _run_loop_until_idle(runtime: AppRuntime, timeout_s: float = 1.0) -> None:
    runtime._running = True
    runtime._last_wall = time.monotonic() - 0.05

    async def run() -> None:
        task = asyncio.create_task(runtime._tick_loop())
        runtime._loop_task = task
        await asyncio.wait_for(task, timeout=timeout_s)

    asyncio.run(run())


def test_engine_exception_is_not_ready_and_sends_safe_dark(tmp_path: Path) -> None:
    runtime = _temp_runtime(tmp_path)
    assert runtime.output.transport.kind is TransportKind.MOCK

    def boom(*, dt_s: float = 0.0) -> None:
        raise RuntimeError("engine exploded")

    runtime.tick = boom  # type: ignore[method-assign]
    _run_loop_until_idle(runtime)
    assert runtime.is_ready is False
    assert runtime.frame_cycle_alive is False
    assert runtime.safe_dark_delivery == "sent"
    assert runtime.output.armed is False
    assert runtime.engine.overlays.blackout is True
    last = runtime.output.transport.last_frame
    assert last is not None
    assert_lights_dark(runtime.show, last)


def test_send_error_marks_undeliverable_and_stops(tmp_path: Path) -> None:
    runtime = _temp_runtime(tmp_path)
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    runtime.arm_output(confirmed=True)
    runtime.apply_blackout(False)

    def boom(_frame: list[int]) -> None:
        raise OSError("network down")

    runtime.output.transport.send_frame = boom  # type: ignore[method-assign]
    _run_loop_until_idle(runtime)
    assert runtime.is_ready is False
    assert runtime.frame_cycle_alive is False
    assert runtime.safe_dark_delivery == "undeliverable"
    assert runtime.output.armed is False
    assert runtime.engine.overlays.blackout is True


def test_shutdown_cleans_already_failed_task(tmp_path: Path) -> None:
    runtime = _temp_runtime(tmp_path)
    runtime._running = True

    async def already_dead() -> None:
        raise RuntimeError("already dead")

    async def run() -> list[list[int]]:
        task = asyncio.create_task(already_dead())
        runtime._loop_task = task
        with pytest.raises(RuntimeError, match="already dead"):
            await task
        assert task.done()
        assert runtime.is_ready is False
        calls: list[int] = []
        original = runtime.output.shutdown

        def wrapped(*, zero_count: int = 3) -> list[list[int]]:
            calls.append(1)
            return original(zero_count=zero_count)

        runtime.output.shutdown = wrapped  # type: ignore[method-assign]
        emitted = await runtime.shutdown()
        assert calls == [1]
        return emitted

    emitted = asyncio.run(run())
    assert runtime.is_ready is False
    assert runtime.output.armed is False
    assert emitted  # cleanup still emitted failsafe frames


def _serve_health(body: dict) -> tuple[HTTPServer, str]:
    payload = json.dumps(body).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/api/health"


def _expect_health_timeout(body: dict, match: str) -> None:
    server, url = _serve_health(body)
    try:
        with pytest.raises(HelperError, match=match):
            poll_health(
                url=url,
                expected_version="1.0.0",
                expected_build_id="abc",
                timeout_s=0.6,
                interval_s=0.05,
            )
    finally:
        server.shutdown()


def test_poll_health_rejects_missing_ready() -> None:
    _expect_health_timeout(
        {
            "frame_cycle_alive": True,
            "blackout": True,
            "output_armed": False,
            "version": "1.0.0",
            "build_id": "abc",
        },
        "ready=None",
    )


def test_poll_health_rejects_string_false() -> None:
    _expect_health_timeout(
        {
            "ready": "false",
            "frame_cycle_alive": True,
            "blackout": True,
            "output_armed": False,
            "version": "1.0.0",
            "build_id": "abc",
        },
        "ready='false'",
    )


def test_poll_health_rejects_missing_frame_cycle() -> None:
    _expect_health_timeout(
        {
            "ready": True,
            "blackout": True,
            "output_armed": False,
            "version": "1.0.0",
            "build_id": "abc",
        },
        "frame_cycle_alive=None",
    )


def test_poll_health_accepts_strict_ready_and_live_cycle() -> None:
    body = {
        "ready": True,
        "frame_cycle_alive": True,
        "blackout": True,
        "output_armed": False,
        "version": "1.0.0",
        "build_id": "abc",
    }
    server, url = _serve_health(body)
    try:
        result = poll_health(
            url=url,
            expected_version="1.0.0",
            expected_build_id="abc",
            timeout_s=1.0,
            interval_s=0.05,
        )
    finally:
        server.shutdown()
    assert result["ready"] is True
    assert result["frame_cycle_alive"] is True
    assert result["build_id"] == "abc"
