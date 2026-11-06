"""Frame path stays independent of a slow client, heartbeat and desk connect.

Local runtime only. This does not start Art-Net and does not touch a venue.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

from orng_led.api.runtime import AppRuntime
from orng_led.api.ws import SocketPump
from orng_led.cloud.agent import CloudDeviceAgent
from orng_led.engine.clock import FRAME_DT
from orng_led.input.contract import InputAction, InputEvent, InputSource


def _gaps(stamps: list[float]) -> list[float]:
    return [b - a for a, b in zip(stamps, stamps[1:], strict=False)]


def test_desk_role_is_not_read_from_disk_on_every_frame(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    from orng_led.desktop import desk_role as desk_module

    calls = {"n": 0}
    real = desk_module.saved_desk_role

    def counting(root: Path) -> str | None:
        calls["n"] += 1
        time.sleep(0.05)
        return real(root)

    monkeypatch.setattr(desk_module, "saved_desk_role", counting)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.build_state()
    started = time.perf_counter()
    runtime.build_state()
    runtime.build_state()
    assert calls["n"] == 1
    assert time.perf_counter() - started < 0.04


def test_slow_client_and_diagnostics_do_not_stall_frames(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    def slow(_runtime: object) -> dict[str, int]:
        time.sleep(0.45)
        return {"service_uptime_s": 1}

    monkeypatch.setattr(
        "orng_led.raspberry.diagnostics.heartbeat_venue_fields",
        slow,
    )
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    stamps: list[float] = []

    def send_frame(frame: list[int]) -> None:
        stamps.append(time.monotonic())
        if len(frame) != 512:
            raise AssertionError("frame")

    runtime.output.transport.send_frame = send_frame  # type: ignore[method-assign]
    agent = CloudDeviceAgent(runtime)

    class _Socket:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send_json(self, _payload: dict) -> None:
            self.started.set()
            await self.release.wait()

    async def run() -> SocketPump:
        socket = _Socket()
        pump = SocketPump(socket)  # type: ignore[arg-type]
        runtime.attach_socket(pump)
        for index in range(40):
            pump.offer_command({"type": "error", "n": index})
        assert pump._commands.qsize() <= 32
        runtime._running = True
        runtime._last_wall = time.monotonic() - FRAME_DT
        tick = asyncio.create_task(runtime._tick_loop())

        class _Ws:
            async def send(self, _text: str) -> None:
                return None

        await agent._heartbeat_once(_Ws())
        await asyncio.sleep(0.2)
        runtime._running = False
        tick.cancel()
        try:
            await tick
        except asyncio.CancelledError:
            pass
        socket.release.set()
        await pump.close()
        return pump

    asyncio.run(run())
    gaps = _gaps(stamps)
    assert len(gaps) >= 8
    assert max(gaps) < 0.12


def test_lock_loss_keeps_the_episode_and_manual_fx() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.apply_select_preset("P01")
    runtime.engine.strobe_press()
    runtime.engine.overlays.strobe_speed = 0.35
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    follower = runtime.bpm.follower
    follower.bpm = 100.0
    follower.locked = True
    follower._started = True
    follower._at = runtime.bpm.clock_s
    follower._fresh_until = runtime.bpm.clock_s - 1.0
    before = runtime.engine.preset_elapsed_s
    for _ in range(90):
        runtime.tick(0.05)
    assert runtime.engine.preset_elapsed_s > before + 2.0
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.strobe_speed == 0.35
    assert runtime.bpm.follower.locked is False


def test_release_of_one_source_keeps_the_other_hold() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None
    first = dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_PRESS,
            source=InputSource.UI,
            source_id="web:a",
        )
    )
    second = dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_PRESS,
            source=InputSource.UI,
            source_id="web:b",
        )
    )
    assert first.accepted is True
    assert second.accepted is True
    assert runtime.engine.overlays.strobe_held is True
    released = dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_RELEASE,
            source=InputSource.UI,
            source_id="web:a",
        )
    )
    assert released.accepted is True
    assert runtime.engine.overlays.strobe_held is True
    assert dispatcher.holds.any_held("strobe") is True
    done = dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_RELEASE,
            source=InputSource.UI,
            source_id="web:b",
        )
    )
    assert done.accepted is True
    assert runtime.engine.overlays.strobe_held is False


def test_blackout_and_release_still_run_while_auto_owns_the_look() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(True)
    runtime.apply_bpm_sync(enabled=True, scope="auto", mode="beat")
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None
    blackout = dispatcher.dispatch(
        InputEvent(
            action=InputAction.BLACKOUT_TOGGLE,
            source=InputSource.UI,
            source_id="web:desk",
        )
    )
    release = dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_RELEASE,
            source=InputSource.UI,
            source_id="web:desk",
        )
    )
    blocked = dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_PRESS,
            source=InputSource.UI,
            source_id="web:desk",
        )
    )
    assert blackout.accepted is True
    assert runtime.engine.overlays.blackout is False
    assert release.reason != "auto_keyboard_blocked"
    assert blocked.accepted is True
    assert runtime.engine.overlays.strobe_held is True


def test_opening_and_closing_the_desk_does_not_change_the_show() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.apply_select_preset("P01")
    runtime.preview_speed = 2.5
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    runtime.engine.set_blackout(True)
    preset = runtime.engine.active_preset_id

    class _Desk:
        def offer_state(self, _payload: dict) -> None:
            return None

    desk = _Desk()
    runtime.subscribe(desk)  # type: ignore[arg-type]
    runtime.request_broadcast()
    runtime.unsubscribe(desk)  # type: ignore[arg-type]
    runtime.on_ws_disconnect("web:desk")
    assert runtime.preview_speed == 2.5
    assert runtime.engine.active_preset_id == preset
    assert runtime.bpm.enabled is True
    assert runtime.bpm.scope == "preset"
    assert runtime.engine.overlays.blackout is True


def test_desk_snapshot_does_not_run_inside_the_frame_call(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    started = threading.Event()
    release = threading.Event()
    real = runtime.build_state

    def slow(*args: object, **kwargs: object):
        started.set()
        release.wait(1.0)
        return real(*args, **kwargs)

    runtime.build_state = slow  # type: ignore[method-assign]

    class _Desk:
        def offer_state(self, _payload: dict) -> None:
            return None

    async def run() -> float:
        runtime.subscribe(_Desk())  # type: ignore[arg-type]
        began = time.perf_counter()
        runtime.request_broadcast()
        return time.perf_counter() - began

    elapsed = asyncio.run(run())
    assert elapsed < 0.05
    assert started.wait(0.5)
    release.set()
    runtime._shutting_down = True
    runtime._broadcast_event.set()


def test_a_blocked_send_does_not_raise() -> None:
    from orng_led.output.udp import UdpArtNetTransport

    class _Blocked:
        def sendto(self, _data: bytes, _address: tuple[str, int]) -> int:
            raise BlockingIOError()

        def close(self) -> None:
            return None

    transport = UdpArtNetTransport(
        target_ip="127.0.0.1",
        allow_real_network=True,
        socket=_Blocked(),  # type: ignore[arg-type]
    )
    transport.send_frame([0] * 512)
    assert transport.send_blocked == 1
    assert transport.packets_sent == 0


def test_keyboard_scan_does_not_block_the_loop() -> None:
    from orng_led.input.linux_keyboard import LinuxKeyboardAdapter

    runtime = AppRuntime.create(autostart_loop=False)
    adapter = LinuxKeyboardAdapter(runtime)

    def slow_scan() -> list[dict[str, str]]:
        time.sleep(0.3)
        return []

    adapter._discover = slow_scan  # type: ignore[method-assign]

    async def run() -> float:
        task = asyncio.create_task(adapter._reconcile_devices())
        began = time.perf_counter()
        await asyncio.sleep(0.05)
        elapsed = time.perf_counter() - began
        await task
        return elapsed

    assert asyncio.run(run()) < 0.2


def test_heartbeat_snapshot_does_not_block_the_loop() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    agent = CloudDeviceAgent(runtime)

    def slow_message() -> str:
        time.sleep(0.3)
        return "{}"

    agent._heartbeat_message = slow_message  # type: ignore[method-assign]

    class _Ws:
        async def send(self, _text: str) -> None:
            return None

    async def run() -> float:
        task = asyncio.create_task(agent._heartbeat_once(_Ws()))
        began = time.perf_counter()
        await asyncio.sleep(0.05)
        elapsed = time.perf_counter() - began
        await task
        return elapsed

    assert asyncio.run(run()) < 0.2
