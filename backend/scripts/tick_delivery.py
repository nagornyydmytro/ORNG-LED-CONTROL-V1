"""Wall time between MockTransport sends from the real _tick_loop.

Spawn worker, two socket clients, an active preview. Startup gaps stay in
the report and are not folded into the warmed figures. Output stays disarmed.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

from orng_led.api.runtime import AppRuntime
from orng_led.api.ws import SocketPump
from orng_led.audio.preview import preview_clip
from orng_led.config.io import load_show_config
from orng_led.engine.clock import FRAME_DT
from orng_led.output.mock import MockTransport
from tests.test_musical_session import click_track

RATE = 44100
OUT = Path(__file__).resolve().parents[2] / "show-bench" / "runs" / "tick"


def _gaps(stamps: list[float]) -> list[float]:
    return [b - a for a, b in zip(stamps, stamps[1:], strict=False)]


def _pct(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def _stats(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "n": len(samples),
        "p50": round(_pct(samples, 0.50), 4),
        "p95": round(_pct(samples, 0.95), 4),
        "p99": round(_pct(samples, 0.99), 4),
        "max": round(max(samples), 4),
    }


def main() -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=OUT / "config", show=load_show_config())
    if not isinstance(runtime.output.transport, MockTransport):
        raise SystemExit("transport is not mock")
    runtime.engine.set_blackout(True)
    runtime.engine.set_master_brightness(1.0)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange", character="rhythmic", intensity="full")
    stamps: list[float] = []
    marks: list[tuple[str, float]] = []
    original = runtime.output.transport.send_frame

    def timed(frame: list[int]) -> None:
        stamps.append(time.monotonic())
        original(frame)

    runtime.output.transport.send_frame = timed  # type: ignore[method-assign]
    runtime.bpm._start_worker()
    worker = runtime.bpm._worker
    preview_holder: dict[str, object] = {}

    class _Socket:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.payloads.append(payload)

    seconds = float(os.environ.get("ORNG_TICK_SECONDS", "8.8"))

    def feed() -> None:
        clicks = click_track(120.0, 12.0, RATE)
        hop_n = int(0.5 * RATE)
        start = time.monotonic()
        index = 0
        while time.monotonic() - start < max(0.5, seconds - 0.3):
            begin = (index * hop_n) % max(hop_n, len(clicks) - hop_n)
            runtime.bpm.submit_block(clicks[begin : begin + hop_n], RATE, captured_at=time.monotonic())
            time.sleep(0.5)
            index += 1

    def preview() -> None:
        clip = preview_clip(runtime.show, "vertical_wave", cycle_beats=4.0, palette="orange", bpm=120.0)
        preview_holder.update(clip)

    def operate() -> None:
        time.sleep(1.2)
        deadline = time.monotonic() + max(6.0, seconds - 1.5)
        while time.monotonic() < deadline:
            marks.append(("blackout_off", time.monotonic()))
            runtime.engine.set_blackout(False)
            time.sleep(1.2)
            marks.append(("strobe_press", time.monotonic()))
            runtime.apply_strobe("press")
            time.sleep(0.4)
            marks.append(("strobe_release", time.monotonic()))
            runtime.apply_strobe("release")
            time.sleep(1.0)
            marks.append(("settings", time.monotonic()))
            runtime.apply_bpm_sync(character="impact", intensity="soft", period=4)
            time.sleep(1.6)
            marks.append(("settings_back", time.monotonic()))
            runtime.apply_bpm_sync(character="rhythmic", intensity="full", period="auto")
            time.sleep(1.2)
            if seconds <= 30.0:
                break
            time.sleep(8.0)
        marks.append(("blackout_on", time.monotonic()))
        runtime.engine.set_blackout(True)

    async def run() -> tuple[int, int]:
        one = _Socket()
        two = _Socket()
        pump1 = SocketPump(one)  # type: ignore[arg-type]
        pump2 = SocketPump(two)  # type: ignore[arg-type]
        runtime.attach_socket(pump1)
        runtime.attach_socket(pump2)
        feeder = threading.Thread(target=feed, name="orng-feed", daemon=True)
        painter = threading.Thread(target=preview, name="orng-preview", daemon=True)
        desk = threading.Thread(target=operate, name="orng-desk", daemon=True)
        runtime._running = True
        runtime._last_wall = time.monotonic() - FRAME_DT
        tick = asyncio.create_task(runtime._tick_loop())
        feeder.start()
        painter.start()
        desk.start()
        await asyncio.sleep(float(os.environ.get("ORNG_TICK_SECONDS", "8.8")))
        runtime._running = False
        tick.cancel()
        try:
            await tick
        except asyncio.CancelledError:
            pass
        feeder.join(timeout=2.0)
        painter.join(timeout=8.0)
        desk.join(timeout=2.0)
        await pump1.close()
        await pump2.close()
        return len(one.payloads), len(two.payloads)

    try:
        clients = asyncio.run(run())
    finally:
        runtime.bpm._stop_worker()
    alive = bool(worker is not None and worker.is_alive())
    gaps = _gaps(stamps)
    origin = stamps[0] if stamps else 0.0
    startup = gaps[:8]
    warmed = gaps[8:]
    pauses = [
        {"at_s": round(stamps[index] - origin, 3), "gap_s": round(gap, 4)}
        for index, gap in enumerate(gaps, start=1)
        if gap > 0.1
    ]
    report = {
        "transport": "mock",
        "armed": bool(runtime.output.armed),
        "frame_dt_s": FRAME_DT,
        "deliveries": len(stamps),
        "clients": {"a": clients[0], "b": clients[1]},
        "preview_frames": len(preview_holder.get("frames") or []),
        "startup_gaps_s": [round(item, 4) for item in startup],
        "startup": _stats(startup),
        "warmed": _stats(warmed),
        "all_gaps": _stats(gaps),
        "pauses_over_100ms": pauses,
        "worker_alive_after_stop": alive,
        "marks": [{"name": name, "at_s": round(stamp - origin, 3)} for name, stamp in marks],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "delivery.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("deliveries", "startup", "warmed", "all_gaps", "pauses_over_100ms", "worker_alive_after_stop", "armed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
