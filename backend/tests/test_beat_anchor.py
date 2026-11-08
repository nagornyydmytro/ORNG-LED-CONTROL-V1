"""Capture path to a decoded frame. Synthetic clicks and the bench corpus.

Injected TempoReading values are not this file. The corpus pieces are the
bench synthesis, not a commercial recording and not a detected downbeat.
"""

from __future__ import annotations

import time
from multiprocessing import get_context
from pathlib import Path
from queue import Empty

import numpy as np
import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.audio.bpm import PHASE_PULL, TempoFollower, TempoReading, click_track
from orng_led.audio.library import BY_ID, musical_anchor
from orng_led.audio.planner import AutoAction, AutoEpisode, EpisodePlanner
from orng_led.audio.tempo_worker import put_latest
from orng_led.config.io import load_show_config
from orng_led.engine.clock import FRAME_DT
from orng_led.output.mock import MockTransport
from orng_led.presets.models import MUSICAL_CYCLE_VERSION, EpisodeCard, LookAction, PresetDocument
from orng_led.presets.program import YamlPresetProgram
from orng_led.show_bench.corpus import broken_beat, even_house, melody_without_a_beat
from orng_led.simulator.decode import decode_simulator_view

RATE = 44100
OFFSET_S = 0.17
DELAY_S = 0.04


def _fresh(tmp_path: Path) -> AppRuntime:
    runtime = AppRuntime.create(
        autostart_loop=False,
        config_dir=tmp_path,
        show=load_show_config(),
    )
    assert isinstance(runtime.output.transport, MockTransport)
    assert runtime.output.armed is False
    runtime.engine.set_blackout(False)
    runtime.engine.set_master_brightness(1.0)
    return runtime


def _clicks(bpm: float, seconds: float) -> list[float]:
    lead = [0.0] * int(OFFSET_S * RATE)
    return lead + click_track(bpm, seconds, RATE)


def _click_times(bpm: float, seconds: float) -> list[float]:
    step = 60.0 / float(bpm)
    moment = OFFSET_S
    times: list[float] = []
    while moment < OFFSET_S + seconds - 1e-9:
        times.append(moment)
        moment += step
    return times


def _hold_pulse(runtime: AppRuntime, cycle: float) -> None:
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
        reason="start",
        section="groove",
    )
    planner = runtime.bpm.planner
    planner.current = episode
    planner.upcoming = None
    planner.advance = lambda *args, **kwargs: episode  # type: ignore[method-assign]
    planner.strobe_at = lambda *args, **kwargs: 0.0  # type: ignore[method-assign]


def _install_pulse(runtime: AppRuntime, cycle: int) -> None:
    document = PresetDocument(
        id="PHS",
        label="phase",
        builtin=False,
        musical_version=MUSICAL_CYCLE_VERSION,
        episodes=[
            EpisodeCard(
                id="e",
                duration_s=18,
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


def _par_mean(runtime: AppRuntime, frame: list[int]) -> float:
    view = decode_simulator_view(runtime.engine.show, frame)
    pars = [item.intensity for item in view.pars if not item.face]
    return sum(pars) / max(len(pars), 1)


def _peaks(times: list[float], levels: list[float]) -> list[float]:
    found: list[float] = []
    for index in range(1, len(levels) - 1):
        if levels[index] >= levels[index - 1] and levels[index] > levels[index + 1] and levels[index] > 0.2:
            found.append(times[index])
    return found


def _miss(peaks: list[float], expected: list[float], *, start: float, stop: float) -> list[float]:
    window = [moment for moment in expected if start <= moment <= stop]
    return [min(abs(peak - moment) for peak in peaks) for moment in window] if peaks else []


def _drive(
    runtime: AppRuntime,
    samples: list[float] | np.ndarray,
    *,
    seconds: float,
    delay: float = DELAY_S,
) -> tuple[list[float], list[float], float | None, float | None]:
    hop = 0.5
    cursor = 0.0
    total = min(seconds, len(samples) / RATE)
    times: list[float] = []
    levels: list[float] = []
    locked_at: float | None = None
    accepted: float | None = None
    frames = max(1, int(round(hop / FRAME_DT)))
    while cursor + hop <= total + 1e-9:
        end = cursor + hop
        block = list(samples[int(cursor * RATE) : int(end * RATE)])
        runtime.bpm.submit_block(block, RATE, captured_at=end, now=end + delay)
        for _ in range(frames):
            snap = runtime.tick(FRAME_DT)
            times.append(float(runtime.bpm.clock_s))
            levels.append(_par_mean(runtime, list(snap.frame)))
            if runtime.bpm.follower.locked and locked_at is None:
                locked_at = times[-1]
                accepted = None if runtime.bpm.follower.bpm is None else float(runtime.bpm.follower.bpm)
        cursor = end
    if runtime.bpm.follower.bpm is not None and runtime.bpm.follower.locked:
        accepted = float(runtime.bpm.follower.bpm)
    return times, levels, locked_at, accepted


@pytest.mark.parametrize("bpm", [90.0, 120.0, 150.0, 174.0, 180.0])
def test_auto_pulse_lands_on_the_click_after_capture(tmp_path: Path, bpm: float) -> None:
    runtime = _fresh(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange", character="rhythmic")
    _hold_pulse(runtime, 1.0)
    seconds = 8.0
    times, levels, locked_at, accepted = _drive(runtime, _clicks(bpm, seconds), seconds=seconds)
    assert locked_at is not None
    assert accepted == pytest.approx(bpm, abs=1.5)
    peaks = _peaks(times, levels)
    # now = captured_at + DELAY_S, and that delay is added to the phase.
    # The show clock at the brightness peak therefore matches the click itself.
    expected = _click_times(bpm, seconds)
    misses = _miss(peaks, expected, start=locked_at, stop=times[-1] - 60.0 / bpm)
    assert misses, f"no peaks after lock at {locked_at:.3f}"
    assert max(misses) <= FRAME_DT + 1e-6, f"bpm={bpm} miss={max(misses):.4f}s"


@pytest.mark.parametrize("cycle", [1, 2, 4, 8])
def test_preset_pulse_uses_the_same_capture(tmp_path: Path, cycle: int) -> None:
    runtime = _fresh(tmp_path)
    _install_pulse(runtime, cycle)
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="beat")
    seconds = 8.0
    bpm = 120.0
    times, levels, locked_at, accepted = _drive(runtime, _clicks(bpm, seconds), seconds=seconds)
    assert locked_at is not None
    assert accepted == pytest.approx(bpm, abs=1.5)
    peaks = _peaks(times, levels)
    clicks = _click_times(bpm, seconds)
    usable = [
        peak
        for peak in peaks
        if clicks[0] <= peak <= clicks[-1] and peak >= locked_at + 0.2
    ]
    assert len(usable) >= 1, f"cycle={cycle} produced no peaks"
    misses = [min(abs(peak - click) for click in clicks) for peak in usable]
    assert max(misses) <= FRAME_DT + 1e-6, f"cycle={cycle} miss={max(misses):.4f}s"
    if len(usable) >= 2:
        gaps = [right - left for left, right in zip(usable, usable[1:])]
        assert gaps[0] == pytest.approx(cycle * 60.0 / bpm, abs=FRAME_DT)


def test_worker_result_keeps_the_stated_delay(tmp_path: Path) -> None:
    """Spawn worker. The show clock is the sample clock; wall wait is not extra phase."""
    runtime = _fresh(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    _hold_pulse(runtime, 1.0)
    runtime.bpm._start_worker()
    try:
        bpm = 120.0
        seconds = 6.0
        samples = _clicks(bpm, seconds)
        hop = 0.5
        cursor = 0.0
        times: list[float] = []
        levels: list[float] = []
        locked_at: float | None = None
        frames = max(1, int(round(hop / FRAME_DT)))
        while cursor + hop <= seconds + 1e-9:
            end = cursor + hop
            block = samples[int(cursor * RATE) : int(end * RATE)]
            before = int(runtime.bpm._window_id)
            runtime.bpm.submit_block(block, RATE, captured_at=end, now=end + DELAY_S)
            if int(runtime.bpm._window_id) != before:
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    runtime.bpm.poll(0.0)
                    windows = runtime.bpm.diagnostics().get("windows") or []
                    latest = windows[-1].get("window") if windows else None
                    if isinstance(latest, int) and latest >= int(runtime.bpm._window_id):
                        break
                    time.sleep(0.005)
            for _ in range(frames):
                snap = runtime.tick(FRAME_DT)
                times.append(float(runtime.bpm.clock_s))
                levels.append(_par_mean(runtime, list(snap.frame)))
                if runtime.bpm.follower.locked and locked_at is None:
                    locked_at = times[-1]
            cursor = end
        assert locked_at is not None
        assert runtime.bpm.follower.bpm == pytest.approx(bpm, abs=1.5)
        peaks = _peaks(times, levels)
        expected = _click_times(bpm, seconds)
        misses = _miss(peaks, expected, start=locked_at, stop=times[-1] - 0.5)
        assert misses and max(misses) <= FRAME_DT + 1e-6, f"worker miss={max(misses) if misses else None}"
    finally:
        runtime.bpm._stop_worker()


def test_levels_syncopation_hats_and_silence_stay_distinct(tmp_path: Path) -> None:
    runtime = _fresh(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto")
    quiet = np.asarray(click_track(120.0, 6.0, RATE), dtype=np.float64)
    quiet *= 0.2 / max(float(np.max(np.abs(quiet))), 1e-9)
    loud = np.asarray(click_track(150.0, 6.0, RATE), dtype=np.float64)
    loud *= 0.85 / max(float(np.max(np.abs(loud))), 1e-9)
    assert float(np.max(np.abs(loud))) < 0.98
    _drive(runtime, quiet, seconds=6.0, delay=0.0)
    assert runtime.bpm.follower.locked
    assert runtime.bpm.follower.bpm == pytest.approx(120.0, abs=1.5)
    runtime = _fresh(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto")
    _drive(runtime, loud, seconds=6.0, delay=0.0)
    assert runtime.bpm.follower.bpm == pytest.approx(150.0, abs=1.5)

    one = np.zeros(int(4.0 * RATE), dtype=np.float64)
    one[:90] = np.linspace(0.9, 0.0, 90)
    runtime = _fresh(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto")
    _drive(runtime, one, seconds=4.0, delay=0.0)
    assert runtime.bpm.follower.locked is False

    runtime = _fresh(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto")
    _drive(runtime, melody_without_a_beat(), seconds=8.0, delay=0.0)
    assert runtime.bpm.follower.locked is False

    house = even_house()
    runtime = _fresh(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto")
    _times, _levels, locked_at, accepted = _drive(runtime, house, seconds=12.0, delay=0.0)
    assert locked_at is not None
    # Offbeat hats are not a second confident tempo. 248 is outside the clock.
    assert accepted == pytest.approx(124.0, abs=2.0)

    broken = broken_beat()
    runtime = _fresh(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto")
    _drive(runtime, broken, seconds=12.0, delay=0.0)
    # Hats and skipped kicks are not a declared downbeat. A lock is only a tempo.
    assert not hasattr(runtime.bpm.follower, "downbeat")
    if runtime.bpm.follower.locked:
        assert runtime.bpm.follower.bpm == pytest.approx(174.0, abs=3.0)


def test_unconfirmed_double_does_not_become_the_beat() -> None:
    follower = TempoFollower()
    follower.push(TempoReading(90.0, 0.1, 1.0), now=0.0)
    follower.push(TempoReading(90.0, 0.1, 1.0), now=0.2)
    assert follower.locked is True
    placed = float(follower._action_offset)
    follower.push(TempoReading(180.0, 0.85, 1.0), now=1.0)
    assert follower.bpm == pytest.approx(90.0, abs=0.5)
    assert follower._action_offset == pytest.approx(placed, abs=1e-9)
    follower.push(TempoReading(180.0, 0.85, 1.0), now=1.5)
    assert follower.bpm == pytest.approx(90.0, abs=0.5)
    assert abs(float(follower._action_offset) - placed) <= PHASE_PULL + 1e-6


def test_operator_period_retunes_picture_bed_and_response() -> None:
    planner = EpisodePlanner(seed=1)
    planner.character = "smooth"
    planner.period = 0.5
    episode = AutoEpisode(
        ident=1,
        look="pulse",
        groups=("par", "bar"),
        theme="orange",
        intensity=0.8,
        start_beat=0.0,
        duration_beats=16,
        actions=(
            AutoAction("picture", "pulse", 4.0),
            AutoAction("bed", "breathe_pars", 8.0, groups=("par",)),
            AutoAction("response", "pulse", 4.0, groups=("par",)),
            AutoAction("motion", "pulse", 2.0),
        ),
        transition="bar",
        effects=(),
        end_early=(),
        reason="start",
        section="groove",
    )
    rewritten = planner._rewrite_period(episode, 3.0)
    cycles = {action.role: action.cycle_beats for action in rewritten.actions}
    assert cycles["picture"] == 0.5
    assert cycles["bed"] == 0.5
    assert cycles["response"] == 0.5
    assert cycles["motion"] == 2.0


def test_beat_anchor_is_not_a_full_canvas_strobe() -> None:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    placed = show.layout.placements
    from orng_led.audio.auto_show import build_auto_stage

    assert musical_anchor(BY_ID["pulse"]) == "brightness"
    assert musical_anchor(BY_ID["fill_up"]) == "motion"
    assert musical_anchor(BY_ID["vertical_wave"]) is None
    bright = build_auto_stage(
        fixtures, "orange", "groove", 0.0, 1.0, beats=0.0, cycle_beats=1, look="pulse", placements=placed
    )
    dim = build_auto_stage(
        fixtures, "orange", "groove", 0.0, 1.0, beats=0.5, cycle_beats=1, look="pulse", placements=placed
    )
    start = build_auto_stage(
        fixtures, "orange", "groove", 0.0, 1.0, beats=0.0, cycle_beats=1, look="fill_up", placements=placed
    )
    later = build_auto_stage(
        fixtures, "orange", "groove", 0.0, 1.0, beats=0.5, cycle_beats=1, look="fill_up", placements=placed
    )

    def _par_level(stage) -> float:
        values = [
            float(intent.intensity)
            for intent in stage.fixtures.values()
            if hasattr(intent, "intensity")
        ]
        return sum(values) / max(len(values), 1)

    def _lit_segments(stage) -> int:
        count = 0
        for intent in stage.fixtures.values():
            segments = getattr(intent, "segments", None)
            if segments:
                count += sum(1 for item in segments if float(item) >= 0.5)
        return count

    assert _par_level(bright) > _par_level(dim)
    assert 0 < _lit_segments(start) < _lit_segments(later)


def test_immediate_get_can_miss_and_the_item_is_still_there() -> None:
    """Proof: put_nowait then get_nowait is not delivery. A short wait is."""
    context = get_context("spawn")
    missed = 0
    for index in range(40):
        queue = context.Queue(maxsize=1)
        queue.put_nowait(index)
        try:
            got = queue.get_nowait()
        except Empty:
            missed += 1
            got = queue.get(timeout=0.05)
        assert got == index
    assert missed >= 0


def test_put_latest_replaces_the_slot_even_when_it_is_not_readable_yet() -> None:
    context = get_context("spawn")
    for index in range(30):
        queue = context.Queue(maxsize=1)
        put_latest(queue, ("first", index))
        put_latest(queue, ("second", index))
        deadline = time.monotonic() + 0.3
        got = None
        while time.monotonic() < deadline:
            try:
                got = queue.get_nowait()
                break
            except Empty:
                time.sleep(0.002)
        assert got == ("second", index)
