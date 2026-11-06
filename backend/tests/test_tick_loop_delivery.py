"""Wall-clock frame delivery of a live _tick_loop.

Manual tick(FRAME_DT) and an even show-clock are not this measurement.
MockTransport only. Not Art-Net, not a Pi.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

from orng_led.api.runtime import AppRuntime
from orng_led.api.ws import SocketPump
from orng_led.audio.preview import preview_clip
from orng_led.config.io import load_show_config
from orng_led.engine.clock import FRAME_DT
from orng_led.output.mock import MockTransport
from tests.test_musical_session import _wait_lock, click_track

RATE = 44100


def _gaps(stamps: list[float]) -> list[float]:
    return [b - a for a, b in zip(stamps, stamps[1:], strict=False)]


def _pct(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def test_live_tick_loop_spawn_worker_two_clients_active_preview(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path, show=load_show_config())
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(False)
    runtime.engine.set_master_brightness(1.0)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    stamps: list[float] = []
    original = runtime.output.transport.send_frame

    def timed(frame: list[int]) -> None:
        stamps.append(time.monotonic())
        original(frame)

    runtime.output.transport.send_frame = timed  # type: ignore[method-assign]
    runtime.bpm._start_worker()
    preview_holder: dict[str, object] = {}

    class _Socket:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.payloads.append(payload)

    def feed_realtime() -> None:
        clicks = click_track(120.0, 8.0, RATE)
        hop = 0.5
        hop_n = int(hop * RATE)
        start = time.monotonic()
        index = 0
        while time.monotonic() - start < 3.2:
            begin = (index * hop_n) % max(hop_n, len(clicks) - hop_n)
            runtime.bpm.submit_block(
                clicks[begin : begin + hop_n],
                RATE,
                captured_at=time.monotonic(),
            )
            time.sleep(hop)
            index += 1

    def run_preview() -> None:
        clip = preview_clip(
            runtime.show,
            "vertical_wave",
            cycle_beats=4.0,
            palette="orange",
            intensity="full",
            bpm=120.0,
            frames=0,
        )
        preview_holder.update(clip)

    async def run() -> tuple[_Socket, _Socket]:
        one = _Socket()
        two = _Socket()
        pump1 = SocketPump(one)  # type: ignore[arg-type]
        pump2 = SocketPump(two)  # type: ignore[arg-type]
        runtime.attach_socket(pump1)
        runtime.attach_socket(pump2)
        feeder = threading.Thread(target=feed_realtime, name="orng-feed", daemon=True)
        preview = threading.Thread(target=run_preview, name="orng-preview", daemon=True)
        runtime._running = True
        runtime._last_wall = time.monotonic() - FRAME_DT
        tick = asyncio.create_task(runtime._tick_loop())
        feeder.start()
        preview.start()
        await asyncio.sleep(3.4)
        runtime._running = False
        tick.cancel()
        try:
            await tick
        except asyncio.CancelledError:
            pass
        feeder.join(timeout=2.0)
        preview.join(timeout=8.0)
        runtime.request_broadcast()
        await asyncio.sleep(0.05)
        await pump1.close()
        await pump2.close()
        return one, two

    try:
        client_a, client_b = asyncio.run(run())
        locked = _wait_lock(runtime.bpm, click_track(120.0, 6.0, RATE))
        gaps = _gaps(stamps)
        live = gaps[8:] if len(gaps) > 16 else gaps
        assert live, f"no live-loop deliveries stamps={len(stamps)}"
        p95 = _pct(live, 0.95)
        worst = max(live)
        assert len(stamps) >= 50, f"deliveries={len(stamps)} want ~30fps over 3s"
        assert p95 < 0.09, f"p95 wall gap {p95:.3f}s is not a live loop (FRAME_DT={FRAME_DT})"
        assert worst < 0.20, f"max wall gap {worst:.3f}s while preview/worker ran"
        assert client_a.payloads and client_b.payloads
        frames = preview_holder.get("frames")
        assert isinstance(frames, list) and frames, preview_holder
        assert preview_holder.get("canonical") is True
        assert locked is True
        assert runtime.output.armed is False
    finally:
        runtime.bpm._stop_worker()


def test_live_tick_delayed_worker_does_not_stall_frames_or_fx(
    tmp_path: Path, monkeypatch
) -> None:
    """Slow or late compact results stay off the live _tick_loop path."""
    from queue import Queue

    from orng_led.audio.auto_show import SliceHearing
    from tests.test_musical_session import _ok_payload

    calls = {"n": 0}

    def boom(*_args: object, **_kwargs: object) -> SliceHearing:
        calls["n"] += 1
        time.sleep(0.15)
        return SliceHearing(0.2, 1.0)

    monkeypatch.setattr("orng_led.audio.service.analyze_slice", boom)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path, show=load_show_config())
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(True)
    runtime.engine.set_master_brightness(1.0)
    runtime.apply_bpm_sync(enabled=True, scope="preset", palette="orange")
    runtime.bpm._use_worker = True
    runtime.bpm._results = Queue(maxsize=1)
    runtime.bpm._jobs = Queue(maxsize=1)
    stamps: list[float] = []
    original = runtime.output.transport.send_frame

    def timed(frame: list[int]) -> None:
        stamps.append(time.monotonic())
        original(frame)

    runtime.output.transport.send_frame = timed  # type: ignore[method-assign]
    released: dict[str, float | bool] = {}
    born = runtime.bpm._generation
    from orng_led.audio.tempo_worker import put_latest

    seed = _ok_payload(runtime.bpm, captured_at=0.0, sample_now=0.0, generation=born)

    def _stamp(captured_at: float, sample_now: float, generation: int, enqueue_mono: float) -> tuple:
        return seed[:4] + (captured_at, sample_now, generation, enqueue_mono) + seed[8:]

    def inject() -> None:
        start = time.monotonic()
        while time.monotonic() - start < 1.6:
            now = runtime.bpm.clock_s
            wall = time.monotonic()
            put_latest(runtime.bpm._results, _stamp(now - 10.0, now, born, wall - 10.0))
            time.sleep(0.04)
            put_latest(runtime.bpm._results, _stamp(now, now, born - 1, wall))
            time.sleep(0.04)

    def poke_fx() -> None:
        time.sleep(0.35)
        runtime.apply_strobe("press")
        t0 = time.monotonic()
        runtime.apply_strobe("release")
        released["dt"] = time.monotonic() - t0
        released["held"] = bool(runtime.engine.overlays.strobe_held)

    async def run() -> None:
        runtime._running = True
        runtime._last_wall = time.monotonic() - FRAME_DT
        tick = asyncio.create_task(runtime._tick_loop())
        feeder = threading.Thread(target=inject, name="orng-inject", daemon=True)
        fx = threading.Thread(target=poke_fx, name="orng-fx", daemon=True)
        feeder.start()
        fx.start()
        await asyncio.sleep(1.7)
        runtime._running = False
        tick.cancel()
        try:
            await tick
        except asyncio.CancelledError:
            pass
        feeder.join(timeout=1.0)
        fx.join(timeout=1.0)

    asyncio.run(run())
    gaps = _gaps(stamps)
    live = gaps[8:] if len(gaps) > 16 else gaps
    assert live, f"no live-loop deliveries stamps={len(stamps)}"
    p95 = _pct(live, 0.95)
    worst = max(live)
    assert calls["n"] == 0
    assert runtime.bpm.follower.locked is False
    assert released.get("held") is False
    assert "dt" in released
    assert float(released["dt"]) < 0.08
    assert p95 < 0.050, f"p95 wall gap {p95:.3f}s with delayed worker results"
    assert worst < 0.12, f"max wall gap {worst:.3f}s with delayed worker results"
