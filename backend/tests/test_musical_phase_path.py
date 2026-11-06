"""Plan clock vs action phase, proved on decoded MockTransport frames.

Isolated, disarmed. Not a USB or venue light check.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import AUTO_CYCLE_BEATS, build_auto_stage
from orng_led.audio.bpm import (
    PHASE_PULL,
    TEMPO_HOLD_S,
    TEMPO_RELEASE_S,
    TempoFollower,
    TempoReading,
    click_track,
)
from orng_led.audio.planner import AutoAction, AutoEpisode, EpisodePlanner
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.clock import FRAME_DT
from orng_led.output.mock import MockTransport
from orng_led.presets.models import EpisodeCard, LookAction, MUSICAL_CYCLE_VERSION, PresetDocument
from orng_led.presets.program import YamlPresetProgram
from orng_led.simulator.decode import decode_simulator_view

RATE = 44100
HOP = 0.2
OFFSET_S = 0.17
DELAY_S = 0.04


def _clicks(bpm: float, seconds: float, offset_s: float = OFFSET_S) -> list[float]:
    lead = [0.0] * int(offset_s * RATE)
    return lead + click_track(bpm, seconds, RATE)


def _click_times(bpm: float, seconds: float, offset_s: float = OFFSET_S) -> list[float]:
    step = 60.0 / float(bpm)
    moment = offset_s
    times: list[float] = []
    while moment < offset_s + seconds - 1e-9:
        times.append(moment)
        moment += step
    return times


def _pulse_episode(cycle: float) -> AutoEpisode:
    return AutoEpisode(
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


def _hold_pulse(runtime: AppRuntime, cycle: float = 1.0) -> AutoEpisode:
    episode = _pulse_episode(cycle)
    planner = runtime.bpm.planner
    planner.current = episode
    planner.upcoming = None
    planner.advance = lambda *args, **kwargs: episode  # type: ignore[method-assign]
    planner.strobe_at = lambda *args, **kwargs: 0.0  # type: ignore[method-assign]
    return episode


def _par_mean(runtime: AppRuntime, frame: list[int]) -> float:
    view = decode_simulator_view(runtime.engine.show, frame)
    pars = [item.intensity for item in view.pars if not item.face]
    return sum(pars) / max(len(pars), 1)


def _peaks(times: list[float], levels: list[float]) -> list[float]:
    found: list[float] = []
    for index in range(1, len(levels) - 1):
        if levels[index] >= levels[index - 1] and levels[index] > levels[index + 1]:
            found.append(times[index])
    return found


def _miss(peaks: list[float], expected: list[float], *, start: float, stop: float) -> list[float]:
    window = [moment for moment in expected if start <= moment <= stop]
    misses: list[float] = []
    for moment in window:
        nearest = min(peaks, key=lambda peak: abs(peak - moment))
        misses.append(abs(nearest - moment))
    return misses


def _install_pulse(runtime: AppRuntime, cycle: int = 1) -> None:
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


def _live_par(stage, fixtures) -> str:
    for fixture in fixtures:
        if fixture.kind is FixtureKind.PAR and str(fixture.id) in stage.fixtures:
            return str(fixture.id)
    raise AssertionError(tuple(stage.fixtures))


def _buffer_phase(elapsed: float, bpm: float, offset: float, delay: float) -> float:
    period = 60.0 / float(bpm)
    return ((float(elapsed) - float(delay) - float(offset)) % period) / period


def _beat_crossings(times: list[float], action_beats: list[float], *, cycle: float = 1.0) -> list[float]:
    found: list[float] = []
    period = float(cycle)
    prev_turns = action_beats[0] / period
    for index in range(1, len(action_beats)):
        turns = action_beats[index] / period
        if math.floor(turns + 1e-9) > math.floor(prev_turns + 1e-9):
            target = math.floor(turns + 1e-9)
            span = turns - prev_turns
            if span > 1e-9:
                frac = (target - prev_turns) / span
                found.append(times[index - 1] + frac * (times[index] - times[index - 1]))
        prev_turns = turns
    return found


def _drive_known(
    runtime: AppRuntime,
    bpm: float,
    seconds: float,
    *,
    offset: float = OFFSET_S,
    delay: float = DELAY_S,
    cycle: float = 1.0,
    hop: float = 0.5,
) -> tuple[list[float], list[float], list[float], list[float], float | None]:
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    _hold_pulse(runtime, cycle)
    origin = runtime.engine.clock.time()
    times: list[float] = []
    levels: list[float] = []
    actions: list[float] = []
    plans: list[float] = []
    locked_at: float | None = None
    elapsed = 0.0
    frames = max(1, int(round(hop / FRAME_DT)))
    while elapsed < seconds - 1e-9:
        now = runtime.bpm.clock_s
        runtime.bpm.follower.push(
            TempoReading(bpm, _buffer_phase(elapsed, bpm, offset, delay), 1.0),
            now=now,
            latency_s=delay,
        )
        for _ in range(frames):
            snap = runtime.tick(FRAME_DT)
            elapsed = runtime.engine.clock.time() - origin
            times.append(elapsed)
            levels.append(_par_mean(runtime, snap.frame))
            actions.append(runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s))
            plans.append(runtime.bpm.follower.beats_at(runtime.bpm.clock_s))
            if runtime.bpm.follower.locked and locked_at is None:
                locked_at = elapsed
    return times, levels, actions, plans, locked_at


def test_action_beats_keep_the_long_cycle_when_phase_pulls() -> None:
    follower = TempoFollower()
    follower.push(TempoReading(120, 0.0, 1.0), now=0.0)
    follower.push(TempoReading(120, 0.0, 1.0), now=0.0)
    assert follower.locked is True
    actions: list[float] = []
    plans: list[float] = []
    for step in range(12):
        moment = 0.1 * (step + 1)
        plan_before = follower.beats_at(moment)
        follower.push(TempoReading(120, 0.9, 1.0), now=moment)
        assert follower.beats_at(moment) >= plan_before - 1e-9
        assert abs(follower.beats_at(moment) - plan_before) <= PHASE_PULL + 1e-6
        plans.append(follower.beats_at(moment))
        actions.append(follower.action_beats_at(moment))
    gaps = [actions[index + 1] - actions[index] for index in range(len(actions) - 1)]
    assert all(gap > 0.05 for gap in gaps)
    last = actions[-1]
    cycle_phase = (last / 4.0) % 1.0
    beat_frac = follower.phase_at(1.2)
    assert cycle_phase != pytest.approx(beat_frac, abs=0.02)
    assert abs(follower.action_beats_at(1.2) - follower.beats_at(1.2)) <= 12 * PHASE_PULL + 1e-6


def test_look_uses_action_beats_not_plan_beats(tmp_path: Path) -> None:
    show = load_show_config()
    service = BpmService(tmp_path, "room")
    service.configure(enabled=True, scope="auto", palette="orange")
    follower = service.follower
    follower.bpm = 120.0
    follower.locked = True
    follower._started = True
    follower._beats = 8.0
    follower._at = 0.0
    follower._phase = 0.2
    follower._action_offset = 0.2
    service._now = 0.0
    episode = _pulse_episode(4.0)
    service.planner.current = episode
    service.planner.upcoming = None
    service.planner.advance = lambda *args, **kwargs: episode  # type: ignore[method-assign]
    service.planner.strobe_at = lambda *args, **kwargs: 0.0  # type: ignore[method-assign]
    lamps = list(show.patch.fixtures)
    placed = show.layout.placements
    stage = service.look(lamps, placed)
    with_offset = build_auto_stage(
        lamps,
        "orange",
        "groove",
        0.2,
        0.85,
        beats=8.2,
        cycle_beats=4,
        look="pulse",
        groups=("par", "bar", "beam"),
        placements=placed,
    )
    plan_only = build_auto_stage(
        lamps,
        "orange",
        "groove",
        0.2,
        0.85,
        beats=8.0,
        cycle_beats=4,
        look="pulse",
        groups=("par", "bar", "beam"),
        placements=placed,
    )
    folded = build_auto_stage(
        lamps,
        "orange",
        "groove",
        0.2,
        0.85,
        beats=None,
        cycle_beats=4,
        look="pulse",
        groups=("par", "bar", "beam"),
        placements=placed,
    )
    par_id = _live_par(stage, lamps)
    assert stage.fixtures[par_id].intensity == pytest.approx(with_offset.fixtures[par_id].intensity)
    assert stage.fixtures[par_id].intensity != pytest.approx(plan_only.fixtures[par_id].intensity)
    assert with_offset.fixtures[par_id].intensity != pytest.approx(folded.fixtures[par_id].intensity)


def test_auto_pulse_phrase_uses_a_half_beat() -> None:
    from orng_led.audio.library import BY_ID
    from orng_led.audio.planner import _picture_cycle

    cycles = BY_ID["pulse"].cycles
    assert 0.5 in cycles
    assert all(cycle in AUTO_CYCLE_BEATS for cycle in cycles)
    assert _picture_cycle("phrase", 8, "pulse", "rhythmic", None) == 0.5
    assert 0.5 * 60.0 / 180.0 > 2 * FRAME_DT


def test_planner_phrases_are_not_a_single_look() -> None:
    planner = EpisodePlanner(seed=3)
    looks: set[str] = set()
    beat = 0.0
    for _ in range(32):
        episode = planner.advance(beat, section="groove", level=0.7, theme="orange")
        looks.add(episode.look)
        assert episode.picture_cycle > 0.0
        beat = episode.start_beat + episode.duration_beats
    assert len(looks) > 1


def test_half_beat_stage_peaks_twice_per_beat() -> None:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    placed = show.layout.placements
    first = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.8,
        beats=0.0,
        cycle_beats=0.5,
        look="pulse",
        placements=placed,
    )
    half = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.8,
        beats=0.5,
        cycle_beats=0.5,
        look="pulse",
        placements=placed,
    )
    mid = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.8,
        beats=0.25,
        cycle_beats=0.5,
        look="pulse",
        placements=placed,
    )
    par_id = _live_par(first, fixtures)
    assert first.fixtures[par_id].intensity == pytest.approx(half.fixtures[par_id].intensity)
    assert first.fixtures[par_id].intensity > mid.fixtures[par_id].intensity


def test_program_speed_is_not_bpm_or_episode_length(tmp_path: Path) -> None:
    runtime = _fresh(tmp_path)
    _install_pulse(runtime, cycle=4)
    runtime.preview_speed = 5.0
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    follower = runtime.bpm.follower
    moment = runtime.bpm.clock_s
    follower.bpm = 120.0
    follower.locked = True
    follower._started = True
    follower._beats = 0.0
    follower._at = moment
    follower._fresh_until = moment + 60.0
    elapsed0 = runtime.engine.preset_elapsed_s
    runtime.tick(1.0)
    assert runtime.engine.preset_elapsed_s - elapsed0 == pytest.approx(1.0, abs=0.05)
    assert runtime.engine.effect_rate_scale == pytest.approx(1.0)
    assert runtime.engine.musical_beats == pytest.approx(follower.action_beats_at(runtime.bpm.clock_s))
    assert runtime.engine.effect_phase_turns == pytest.approx(
        runtime.engine.musical_beats / 4.0, abs=0.02
    )
    runtime.apply_bpm_sync(enabled=False)
    follower.bpm = None
    follower.locked = False
    follower._started = False
    follower._release_until = None
    follower._action_offset = 0.0
    runtime.preview_speed = 5.0
    start = runtime.engine.effect_phase_turns
    elapsed = runtime.engine.preset_elapsed_s
    runtime.tick(0.2)
    assert runtime.engine.musical_beats is None
    assert runtime.engine.preset_elapsed_s - elapsed == pytest.approx(0.2, abs=0.02)
    assert runtime.engine.effect_phase_turns - start > 0.2


def test_preset_musical_beats_carry_the_heard_offset(tmp_path: Path) -> None:
    runtime = _fresh(tmp_path)
    _install_pulse(runtime, cycle=4)
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    follower = runtime.bpm.follower
    moment = runtime.bpm.clock_s
    follower.bpm = 120.0
    follower.locked = True
    follower._started = True
    follower._beats = 6.0
    follower._at = moment
    follower._phase = 0.2
    follower._action_offset = 0.2
    follower._fresh_until = moment + 60.0
    runtime.tick(0.0)
    assert runtime.engine.musical_beats == pytest.approx(6.2, abs=0.02)
    assert runtime.engine.effect_phase_turns == pytest.approx(6.2 / 4.0, abs=0.02)
    assert runtime.engine.effect_phase_turns != pytest.approx(0.2, abs=0.02)
    runtime.engine.strobe_press()
    phase = runtime.engine.overlays.strobe_phase
    follower._beats = 14.0
    runtime.tick(FRAME_DT)
    assert runtime.engine.bpm_fx is False
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.strobe_phase != pytest.approx(runtime.engine.musical_beats)
    assert abs(runtime.engine.overlays.strobe_phase - phase) < 0.2


@pytest.mark.parametrize("bpm", [90.0, 120.0, 150.0])
def test_decoded_auto_peaks_track_known_offset_beats(tmp_path: Path, bpm: float) -> None:
    runtime = _fresh(tmp_path)
    times, levels, actions, plans, locked_at = _drive_known(runtime, bpm, 8.0)
    assert locked_at is not None
    capture_s = locked_at
    assert max(levels) > 0.05
    peaks = _peaks(times, levels)
    expected = [moment + DELAY_S for moment in _click_times(bpm, 8.0)]
    start = locked_at + 2.0
    stop = times[-1] - 60.0 / bpm
    misses = _miss(peaks, expected, start=start, stop=stop)
    clock_hits = _beat_crossings(times, actions)
    clock_miss = _miss(peaks, clock_hits, start=start, stop=stop)
    assert misses, f"capture={capture_s:.3f}s no peaks after lock"
    assert clock_miss and max(clock_miss) <= FRAME_DT + 1e-6
    assert max(misses) <= FRAME_DT + 1e-6, (
        f"capture={capture_s:.3f}s miss={max(misses):.4f}s bpm={bpm}"
    )
    assert plans[-1] >= plans[0] - 1e-9
    assert capture_s < 1.2


def test_click_track_peaks_follow_action_beats_after_capture(tmp_path: Path) -> None:
    runtime = _fresh(tmp_path)
    samples = _clicks(120.0, 8.0)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    _hold_pulse(runtime, 1.0)
    origin = runtime.engine.clock.time()
    cursor = 0.0
    total = len(samples) / RATE
    times = []
    levels = []
    actions = []
    locked_at = None
    frames = max(1, int(round(HOP / FRAME_DT)))
    while cursor + HOP <= total + 1e-9:
        now = runtime.bpm.clock_s
        runtime.bpm.submit_block(
            samples[int(cursor * RATE) : int((cursor + HOP) * RATE)],
            RATE,
            captured_at=now - DELAY_S,
            now=now,
        )
        for _ in range(frames):
            snap = runtime.tick(FRAME_DT)
            times.append(runtime.engine.clock.time() - origin)
            levels.append(_par_mean(runtime, snap.frame))
            actions.append(runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s))
            if runtime.bpm.follower.locked and locked_at is None:
                locked_at = times[-1]
        cursor += HOP
    assert locked_at is not None
    peaks = _peaks(times, levels)
    clock_hits = _beat_crossings(times, actions)
    misses = _miss(peaks, clock_hits, start=locked_at + 0.6, stop=times[-1] - 0.5)
    assert misses and max(misses) <= FRAME_DT + 1e-6, (
        f"capture={locked_at:.3f}s action-clock miss={max(misses) if misses else None}"
    )


def test_decoded_half_beat_accel_relock_and_octave(tmp_path: Path) -> None:
    runtime = _fresh(tmp_path)
    times, levels, actions, plans, locked_at = _drive_known(
        runtime, 120.0, 6.0, cycle=0.5
    )
    assert locked_at is not None
    peaks = _peaks(times, levels)
    period = 0.25
    half_expected = []
    moment = OFFSET_S + DELAY_S
    while moment < 6.0:
        half_expected.append(moment)
        moment += period
    misses = _miss(peaks, half_expected, start=locked_at + 1.2, stop=times[-1] - 0.4)
    clock_miss = _miss(
        peaks, _beat_crossings(times, actions, cycle=0.5), start=locked_at + 1.2, stop=times[-1] - 0.4
    )
    assert clock_miss and max(clock_miss) <= FRAME_DT + 1e-6
    assert misses and max(misses) <= FRAME_DT + 1e-6, f"half-beat miss={max(misses) if misses else None}"
    assert all(plans[index + 1] + 1e-9 >= plans[index] for index in range(len(plans) - 1))

    runtime = _fresh(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    _hold_pulse(runtime, 1.0)
    origin = runtime.engine.clock.time()
    times = []
    levels = []
    actions = []
    plans = []
    locked_at = None
    elapsed = 0.0
    now = runtime.bpm.clock_s
    for _ in range(2):
        runtime.bpm.follower.push(
            TempoReading(100.0, _buffer_phase(elapsed, 100.0, OFFSET_S, DELAY_S), 1.0),
            now=now,
            latency_s=DELAY_S,
        )
    runtime.bpm.follower._fresh_until = now + 60.0
    bpm = 100.0
    while elapsed < 8.0:
        now = runtime.bpm.clock_s
        runtime.bpm.follower.push(
            TempoReading(bpm, _buffer_phase(elapsed, bpm, OFFSET_S, DELAY_S), 1.0),
            now=now,
            latency_s=DELAY_S,
        )
        runtime.bpm.follower._fresh_until = now + 60.0
        for _ in range(int(round(0.5 / FRAME_DT))):
            snap = runtime.tick(FRAME_DT)
            elapsed = runtime.engine.clock.time() - origin
            times.append(elapsed)
            levels.append(_par_mean(runtime, snap.frame))
            actions.append(runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s))
            plans.append(runtime.bpm.follower.beats_at(runtime.bpm.clock_s))
            if runtime.bpm.follower.locked and locked_at is None:
                locked_at = elapsed
        bpm = min(150.0, 100.0 + 6.0 * (elapsed / 8.0))
    assert locked_at is not None
    peaks = _peaks(times, levels)
    clock_hits = _beat_crossings(times, actions)
    misses = _miss(peaks, clock_hits, start=locked_at + 1.5, stop=times[-1] - 0.4)
    assert misses and max(misses) <= FRAME_DT + 1e-6
    assert all(plans[index + 1] + 1e-9 >= plans[index] for index in range(len(plans) - 1))

    runtime = _fresh(tmp_path)
    times, levels, actions, plans, locked_at = _drive_known(runtime, 120.0, 3.0)
    ident0 = runtime.bpm.planner.current.ident if runtime.bpm.planner.current else None
    assert locked_at is not None
    gap = TEMPO_HOLD_S + TEMPO_RELEASE_S + 0.3
    for _ in range(int(gap / FRAME_DT)):
        runtime.tick(FRAME_DT)
        plans.append(runtime.bpm.follower.beats_at(runtime.bpm.clock_s))
    assert runtime.bpm.follower.locked is False
    times, levels, actions, more_plans, relocked_at = _drive_known(runtime, 120.0, 6.0)
    plans.extend(more_plans)
    assert relocked_at is not None
    assert all(plans[index + 1] + 1e-9 >= plans[index] for index in range(len(plans) - 1))
    if ident0 is not None and runtime.bpm.planner.current is not None:
        assert runtime.bpm.planner.current.ident == ident0
    peaks = _peaks(times, levels)
    expected = [moment + DELAY_S for moment in _click_times(120.0, 6.0)]
    misses = _miss(peaks, expected, start=relocked_at + 1.2, stop=times[-1] - 0.5)
    assert misses and max(misses) <= FRAME_DT + 1e-6, f"relock miss={max(misses) if misses else None}"

    runtime = _fresh(tmp_path)
    times, levels, actions, plans, locked_at = _drive_known(runtime, 90.0, 3.0)
    assert locked_at is not None
    before = plans[-1]
    origin = runtime.engine.clock.time()
    elapsed = 0.0
    later_times: list[float] = []
    later_levels: list[float] = []
    later_actions: list[float] = []
    while elapsed < 6.0:
        now = runtime.bpm.clock_s
        runtime.bpm.follower.push(
            TempoReading(180.0, _buffer_phase(elapsed, 180.0, OFFSET_S, DELAY_S), 1.0),
            now=now,
            latency_s=DELAY_S,
        )
        runtime.bpm.follower._fresh_until = now + 60.0
        for _ in range(int(round(0.5 / FRAME_DT))):
            snap = runtime.tick(FRAME_DT)
            elapsed = runtime.engine.clock.time() - origin
            later_times.append(elapsed)
            later_levels.append(_par_mean(runtime, snap.frame))
            later_actions.append(runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s))
            assert runtime.bpm.follower.beats_at(runtime.bpm.clock_s) >= before - 1e-9
    assert runtime.bpm.follower.bpm == pytest.approx(180.0, abs=1.0)
    peaks = _peaks(later_times, later_levels)
    clock_hits = _beat_crossings(later_times, later_actions)
    misses = _miss(peaks, clock_hits, start=1.5, stop=later_times[-1] - 0.3)
    assert misses and max(misses) <= FRAME_DT + 1e-6, f"octave miss={max(misses) if misses else None}"
