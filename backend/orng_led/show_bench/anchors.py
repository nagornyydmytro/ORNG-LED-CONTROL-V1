"""Short clips: the same clicks through Auto and Preset-BPM.

Synthetic. A frame is placed at its show-clock time. The wav stays on the
sample clock. Neither timeline is shifted to hide a miss.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from orng_led.audio.bpm import click_track
from orng_led.audio.planner import AutoAction, AutoEpisode
from orng_led.engine.clock import FRAME_DT, FPS
from orng_led.presets.models import MUSICAL_CYCLE_VERSION, EpisodeCard, LookAction, PresetDocument
from orng_led.presets.program import YamlPresetProgram
from orng_led.show_bench.corpus import RATE, even_house
from orng_led.show_bench.render import HEIGHT, WIDTH, render_frame
from orng_led.show_bench.session import (
    _Encoder,
    _frame_levels,
    _heard_line,
    _index,
    open_stand,
)
from orng_led.simulator.decode import decode_simulator_view

OFFSET_S = 0.17
DELAY_S = 0.04


def _clicks(bpm: float, seconds: float) -> np.ndarray:
    lead = np.zeros(int(OFFSET_S * RATE), dtype=np.float64)
    body = np.asarray(click_track(bpm, seconds, RATE), dtype=np.float64)
    return np.concatenate((lead, body))


def _click_times(bpm: float, seconds: float) -> list[float]:
    step = 60.0 / float(bpm)
    moment = OFFSET_S
    times: list[float] = []
    while moment < OFFSET_S + seconds - 1e-9:
        times.append(moment)
        moment += step
    return times


def _hold_pulse(runtime: object, cycle: float) -> None:
    episode = AutoEpisode(
        ident=1,
        look="pulse",
        groups=("par", "bar", "beam"),
        theme="orange",
        intensity=0.85,
        start_beat=0.0,
        duration_beats=256,
        actions=(AutoAction("picture", "pulse", cycle),),
        transition="bar",
        effects=(),
        end_early=("signal_loss", "blackout"),
        reason="scenario",
        section="groove",
    )
    planner = runtime.bpm.planner
    planner.current = episode
    planner.upcoming = None
    planner.advance = lambda *args, **kwargs: episode  # type: ignore[method-assign]
    planner.strobe_at = lambda *args, **kwargs: 0.0  # type: ignore[method-assign]


def _install_pulse(runtime: object, cycle: int) -> None:
    document = PresetDocument(
        id="ANCHOR",
        label="anchor",
        builtin=False,
        musical_version=MUSICAL_CYCLE_VERSION,
        episodes=[
            EpisodeCard(
                id="e",
                duration_s=12,
                groups=["par"],
                palette="deep_red",
                effect="pulse",
                speed=0.5,
                intensity=1.0,
                transition="cut",
                cycle_beats=cycle,  # type: ignore[arg-type]
                actions=[
                    LookAction(
                        id="p",
                        groups=["par"],
                        effect="pulse",
                        speed=0.5,
                        intensity=1.0,
                        cycle_beats=cycle,  # type: ignore[arg-type]
                    )
                ],
            )
        ],
    )
    program = YamlPresetProgram(document)
    runtime.engine.presets[program.id] = program
    runtime.engine.select_preset(program.id, reset_clock=True)
    runtime.engine.set_blackout(False)
    runtime.engine.set_master_brightness(1.0)


def _wait_window(runtime: object, job_id: int) -> None:
    import time

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        runtime.bpm.poll(0.0)
        windows = runtime.bpm.diagnostics().get("windows") or []
        latest = windows[-1].get("window") if windows else None
        if isinstance(latest, int) and latest >= job_id:
            return
        time.sleep(0.005)


def _peaks(times: list[float], levels: list[float]) -> list[float]:
    found: list[float] = []
    for index in range(1, len(levels) - 1):
        if levels[index] >= levels[index - 1] and levels[index] > levels[index + 1] and levels[index] > 0.2:
            found.append(times[index])
    return found


def _pulse_miss(rows: list[dict[str, object]], clicks: list[float]) -> dict[str, object]:
    locked = [row for row in rows if row.get("locked")]
    if len(locked) < 3:
        return {"locked": False, "peaks": 0, "max_miss_s": None}
    start = float(locked[0]["show"]) + 0.2
    times = [float(row["show"]) for row in rows]
    levels = [float(row["par"]) for row in rows]
    peaks = [
        peak
        for peak in _peaks(times, levels)
        if start <= peak <= clicks[-1]
    ]
    misses = [min(abs(peak - click) for click in clicks) for peak in peaks]
    return {
        "locked": True,
        "bpm": locked[-1].get("bpm"),
        "peaks": len(peaks),
        "max_miss_s": None if not misses else round(max(misses), 4),
        "median_miss_s": None if not misses else round(float(np.median(misses)), 4),
    }


def _phase_errors(rows: list[dict[str, object]], kicks: list[float], bpm: float) -> dict[str, object]:
    """Error of the accepted phase against kick times. Not a brightness claim."""
    period = 60.0 / bpm
    locked = [row for row in rows if row.get("locked") and row.get("phase") is not None]
    if not locked:
        return {"kicks": 0, "median_s": None, "max_s": None}
    errors: list[float] = []
    for kick in kicks:
        row = min(locked, key=lambda item: abs(float(item["show"]) - kick))
        if abs(float(row["show"]) - kick) > FRAME_DT:
            continue
        phase = float(row["phase"]) % 1.0
        errors.append(min(phase, 1.0 - phase) * period)
    if not errors:
        return {"kicks": 0, "median_s": None, "max_s": None}
    ordered = sorted(errors)
    return {
        "kicks": len(errors),
        "median_s": round(float(np.median(ordered)), 4),
        "p95_s": round(ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))], 4),
        "max_s": round(ordered[-1], 4),
    }


def _drive(
    runtime: object,
    samples: np.ndarray,
    out_dir: Path,
    *,
    label: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    runtime.engine.set_master_brightness(1.0)
    placements = {item.fixture_id: item for item in runtime.show.layout.placements}
    encoder = _Encoder(out_dir / "picture.mp4", RATE)
    total = samples.size / RATE
    cursor = 0.0
    rows: list[dict[str, object]] = []
    slots: dict[int, np.ndarray] = {}
    try:
        while cursor + 1e-6 < total:
            end = min(total, cursor + FRAME_DT)
            block = samples[int(cursor * RATE) : int(round(end * RATE))]
            if block.size == 0:
                break
            before = int(runtime.bpm._window_id)
            runtime.bpm.submit_block(block.tolist(), RATE, captured_at=end, now=end + DELAY_S)
            if int(runtime.bpm._window_id) != before:
                _wait_window(runtime, int(runtime.bpm._window_id))
            transport = runtime.output.transport
            snap = runtime.tick(FRAME_DT)
            frame = list(transport.frames[-1]) if transport.frames else list(snap.frame)
            transport.frames.clear()
            view = decode_simulator_view(runtime.show, frame)
            levels = _frame_levels(view)
            heard, row = _heard_line(runtime)
            clock = float(runtime.bpm.clock_s)
            phase = None
            if runtime.bpm.follower.locked:
                phase = round(float(runtime.bpm.follower.phase_at(clock)) % 1.0, 4)
            record = {"t": round(clock, 4), "show": round(clock, 4), "phase": phase, **row, **levels}
            rows.append(record)
            image = render_frame(view, placements, clock_s=clock, score=label, heard=heard)
            slots[int(round(clock / FRAME_DT))] = image
            cursor = end
    finally:
        last = max(slots) if slots else -1
        black = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        for index in range(last + 1):
            encoder.write(slots.get(index, black))
        from orng_led.show_bench.corpus import _write_wav

        wav_path = out_dir / "program-span.wav"
        span = int(round((last + 1) / FPS * RATE)) if last >= 0 else 0
        audio = np.zeros(max(span, samples.size), dtype=np.float32)
        audio[: samples.size] = samples.astype(np.float32)
        _write_wav(wav_path, audio[:span] if span else audio, RATE)
        encoder.close(program_wav=wav_path if span else None)
        runtime.bpm._stop_worker()
    summary = {
        "label": label,
        "frames": last + 1,
        "program_s": round((last + 1) / FPS, 3) if last >= 0 else 0.0,
        "delay_s": DELAY_S,
        "locked": any(bool(row.get("locked")) for row in rows),
        "video": str(out_dir / "picture.mp4"),
    }
    (out_dir / "trace.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return summary, rows


def _write_note(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def record(bench: Path) -> None:
    runs = bench / "runs"
    clicks = _clicks(120.0, 6.0)
    click_at = _click_times(120.0, 6.0)
    auto, root = open_stand()
    try:
        _hold_pulse(auto, 1.0)
        auto_summary, auto_rows = _drive(auto, clicks, runs / "anchor-auto", label="scenario auto pulse")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    preset, root = open_stand()
    try:
        preset.bpm.configure(enabled=True, scope="preset", mode="beat")
        _install_pulse(preset, 1)
        preset_summary, preset_rows = _drive(
            preset, clicks, runs / "anchor-preset", label="scenario preset pulse"
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    house_audio = even_house()[: int(8.0 * RATE)]
    house, root = open_stand()
    try:
        house_summary, house_rows = _drive(
            house, house_audio, runs / "anchor-house", label="corpus even house"
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    auto_miss = _pulse_miss(auto_rows, click_at)
    preset_miss = _pulse_miss(preset_rows, click_at)
    kicks = [index * 60.0 / 124.0 for index in range(int(8.0 * 124.0 / 60.0))]
    house_phase = _phase_errors(house_rows, kicks, 124.0)
    auto_summary["miss"] = auto_miss
    preset_summary["miss"] = preset_miss
    house_summary["phase_error"] = house_phase
    (runs / "anchor-auto" / "summary.json").write_text(json.dumps(auto_summary, indent=2), encoding="utf-8")
    (runs / "anchor-preset" / "summary.json").write_text(json.dumps(preset_summary, indent=2), encoding="utf-8")
    (runs / "anchor-house" / "summary.json").write_text(json.dumps(house_summary, indent=2), encoding="utf-8")
    _write_note(
        runs / "anchor-auto" / "observations.md",
        [
            "# Сценарий: Auto pulse, клик 120",
            "",
            "Синтетический click_track. Не фонограмма. Задержка "
            f"{DELAY_S:.2f} с заложена в фазу (`now = captured_at + delay`).",
            "Кадр стоит на времени шоу. Звук — на времени отсчётов. Метки не сдвигались.",
            "",
            f"Кадры {auto_summary['frames']}, программа {auto_summary['program_s']} с.",
            f"Пики яркости после захвата: {auto_miss}. Допуск кадра {FRAME_DT:.4f} с.",
            "",
        ],
    )
    _write_note(
        runs / "anchor-preset" / "observations.md",
        [
            "# Сценарий: Preset-BPM pulse, тот же клик",
            "",
            "Тот же click_track и та же задержка. Цикл 1. Кадр на времени шоу, звук на времени отсчётов.",
            "",
            f"Кадры {preset_summary['frames']}, программа {preset_summary['program_s']} с.",
            f"Пики яркости после захвата: {preset_miss}. Допуск кадра {FRAME_DT:.4f} с.",
            "",
        ],
    )
    _write_note(
        runs / "anchor-house" / "observations.md",
        [
            "# Корпус: even_house, штатный Auto",
            "",
            "Синтез стенда, не коммерческая запись. Удары генератора на 124. "
            "Рисунок выбирает планировщик, поэтому яркость здесь не считается опорой.",
            "Ниже — ошибка принятой фазы относительно удара, не raw==accepted.",
            "",
            f"Кадры {house_summary['frames']}, программа {house_summary['program_s']} с, lock {house_summary['locked']}.",
            f"Ошибка фазы на ударах: {house_phase}.",
            "",
        ],
    )
    _index(bench)


if __name__ == "__main__":
    record(Path(__file__).resolve().parents[3] / "show-bench")
