"""Thirty minutes of the local frame loop with a slow client and slow diagnostics.

This is the laptop interpreter and a mock transport. It is not a Raspberry Pi
and it does not open Art-Net.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import tracemalloc
from pathlib import Path

from orng_led.api.runtime import AppRuntime
from orng_led.api.ws import SocketPump
from orng_led.cloud.agent import CloudDeviceAgent
from orng_led.engine.clock import FRAME_DT
from orng_led.input.contract import InputAction, InputEvent, InputSource

DURATION_S = int(os.environ.get("ORNG_SOAK_SECONDS", str(30 * 60)))
RATE = 48000


def _rss_bytes() -> int | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32")
    kernel.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    kernel.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    ok = kernel.K32GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    )
    if not ok:
        return None
    return int(counters.WorkingSetSize)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _summary(gaps: list[float]) -> dict[str, float | int | None]:
    over = [gap for gap in gaps if gap > 0.1]
    return {
        "frames": len(gaps) + 1 if gaps else 0,
        "p50_ms": None if not gaps else round(_percentile(gaps, 0.50) * 1000, 1),
        "p95_ms": None if not gaps else round(_percentile(gaps, 0.95) * 1000, 1),
        "p99_ms": None if not gaps else round(_percentile(gaps, 0.99) * 1000, 1),
        "max_ms": None if not gaps else round(max(gaps) * 1000, 1),
        "gaps_over_100ms": len(over),
    }


class _SlowSocket:
    async def send_json(self, _payload: dict) -> None:
        await asyncio.sleep(0.08)


class _Ws:
    async def send(self, _text: str) -> None:
        await asyncio.sleep(0.05)


def _hold_check(runtime: AppRuntime) -> str | None:
    dispatcher = runtime.input_dispatcher
    if dispatcher is None:
        return "no dispatcher"
    if runtime.bpm.enabled and runtime.bpm.scope == "auto":
        return None
    dispatcher.dispatch(
        InputEvent(action=InputAction.STROBE_PRESS, source=InputSource.UI, source_id="web:soak-a")
    )
    dispatcher.dispatch(
        InputEvent(action=InputAction.STROBE_PRESS, source=InputSource.UI, source_id="web:soak-b")
    )
    dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_RELEASE, source=InputSource.UI, source_id="web:soak-a"
        )
    )
    if not runtime.engine.overlays.strobe_held:
        return "release cleared the other source"
    dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_RELEASE, source=InputSource.UI, source_id="web:soak-b"
        )
    )
    if runtime.engine.overlays.strobe_held:
        return "both releases left the strobe on"
    return None


async def _run() -> dict[str, object]:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.apply_select_preset("P01")
    runtime.preview_speed = 1.5
    runtime.engine.set_blackout(True)
    runtime.apply_bpm_sync(enabled=False)
    preset = runtime.engine.active_preset_id
    stamps: list[float] = []

    def send_frame(frame: list[int]) -> None:
        stamps.append(time.monotonic())
        if len(frame) != 512:
            raise RuntimeError("bad frame")

    runtime.output.transport.send_frame = send_frame  # type: ignore[method-assign]
    pumps = [SocketPump(_SlowSocket()) for _ in range(2)]  # type: ignore[arg-type]
    for pump in pumps:
        runtime.attach_socket(pump)

    from orng_led.raspberry import diagnostics as diag

    real_fields = diag.heartbeat_venue_fields

    def slow_fields(candidate: object) -> dict:
        time.sleep(0.12)
        return real_fields(candidate)  # type: ignore[arg-type]

    diag.heartbeat_venue_fields = slow_fields  # type: ignore[assignment]
    agent = CloudDeviceAgent(runtime)

    stop_audio = threading.Event()

    def audio() -> None:
        while not stop_audio.is_set():
            moment = time.monotonic()
            block = [0.15] * int(0.5 * RATE)
            if runtime.bpm.enabled:
                runtime.bpm.submit_block(block, RATE, captured_at=moment)
            else:
                runtime.bpm.note_live_input(block, RATE, callback_at=moment)
            stop_audio.wait(0.45)

    threading.Thread(target=audio, name="orng-soak-audio", daemon=True).start()
    runtime._running = True
    runtime._last_wall = time.monotonic() - FRAME_DT
    tick = asyncio.create_task(runtime._tick_loop())
    started = time.monotonic()
    phase = "bpm-off"
    phase_marks: list[tuple[str, float]] = [("bpm-off", time.monotonic())]
    rows: list[dict[str, object]] = []
    problems: list[str] = []
    max_tasks = 0
    max_queue = 0
    tracemalloc.start()
    try:
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= DURATION_S:
                break
            if elapsed >= 20 * 60 and phase != "auto":
                runtime.apply_bpm_sync(enabled=True, scope="auto", mode="beat")
                phase = "auto"
                phase_marks.append((phase, time.monotonic()))
            elif elapsed >= 10 * 60 and phase == "bpm-off":
                runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
                phase = "preset-bpm"
                phase_marks.append((phase, time.monotonic()))
            await agent._heartbeat_once(_Ws())
            runtime.request_broadcast()
            problem = _hold_check(runtime)
            if problem:
                problems.append(f"{phase}: {problem}")
            if (
                runtime.preview_speed != 1.5
                or runtime.engine.active_preset_id != preset
                or runtime.engine.overlays.blackout is not True
            ):
                problems.append(
                    f"{phase}: show changed speed={runtime.preview_speed} "
                    f"preset={runtime.engine.active_preset_id} "
                    f"blackout={runtime.engine.overlays.blackout} "
                    f"bpm={runtime.bpm.enabled}:{runtime.bpm.scope}"
                )
            tasks = len(asyncio.all_tasks())
            queued = max(pump._commands.qsize() for pump in pumps)
            max_tasks = max(max_tasks, tasks)
            max_queue = max(max_queue, queued)
            current, peak = tracemalloc.get_traced_memory()
            rss = _rss_bytes()
            if int(elapsed) // 60 != int(rows[-1]["t_s"]) // 60 if rows else True:
                rows.append(
                    {
                        "t_s": round(elapsed, 1),
                        "phase": phase,
                        "tasks": tasks,
                        "command_queue": queued,
                        "traced_mb": round(current / 1_048_576, 2),
                        "traced_peak_mb": round(peak / 1_048_576, 2),
                        "rss_mb": None if rss is None else round(rss / 1_048_576, 1),
                        "frames": len(stamps),
                    }
                )
                print(
                    f"t={elapsed:6.0f}s phase={phase} tasks={tasks} "
                    f"queue={queued} rss={rows[-1]['rss_mb']}",
                    flush=True,
                )
            await asyncio.sleep(2.0)
    finally:
        runtime._running = False
        stop_audio.set()
        tick.cancel()
        try:
            await tick
        except asyncio.CancelledError:
            pass
        for pump in pumps:
            await pump.close()
        tracemalloc.stop()

    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    by_phase: dict[str, dict[str, float | int | None]] = {}
    for index, (name, mark) in enumerate(phase_marks):
        end = phase_marks[index + 1][1] if index + 1 < len(phase_marks) else stamps[-1] + 1
        chosen = [
            b - a
            for a, b in zip(stamps, stamps[1:], strict=False)
            if mark <= b < end
        ]
        by_phase[name] = _summary(chosen)
    return {
        "kind": "local-mock-not-a-pi",
        "duration_s": round(time.monotonic() - started, 1),
        "blackout": runtime.engine.overlays.blackout,
        "preset": runtime.engine.active_preset_id,
        "preview_speed": runtime.preview_speed,
        "bpm_enabled": runtime.bpm.enabled,
        "bpm_scope": runtime.bpm.scope,
        "frames": _summary(gaps),
        "frames_by_phase": by_phase,
        "max_tasks": max_tasks,
        "max_command_queue": max_queue,
        "samples": rows,
        "problems": problems,
    }


def main() -> None:
    report = asyncio.run(_run())
    out = Path(os.environ.get("TEMP", ".")) / "orng-runtime-soak.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "frames",
                    "frames_by_phase",
                    "max_tasks",
                    "max_command_queue",
                    "problems",
                    "duration_s",
                    "preview_speed",
                    "blackout",
                )
            }
        )
    )
    print(out)


if __name__ == "__main__":
    main()
