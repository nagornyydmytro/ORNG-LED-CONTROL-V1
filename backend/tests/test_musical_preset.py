"""Musical cycles inside one preset, and one drop event per episode step.

Frames are rendered in memory. Nothing here arms Art-Net or writes a scene.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest
from pydantic import ValidationError

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import SectionTracker, StructuralEvent, analyze_slice
from orng_led.audio.bpm import MIN_EPISODE_BEATS, click_track
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.engine import Engine, FakeClock
from orng_led.engine.intents import BeamIntent
from orng_led.engine.timing import effect_rate_hz
from orng_led.presets.models import (
    MUSICAL_CYCLE_VERSION,
    EpisodeCard,
    LookAction,
    PresetDocument,
)
from orng_led.presets.musical import adapted_cycle_beats
from orng_led.presets.program import YamlPresetProgram

RATE = 44100
HOP_S = 0.25


def _program(*episodes: EpisodeCard) -> YamlPresetProgram:
    document = PresetDocument(
        id="MUS",
        label="musical",
        builtin=False,
        musical_version=MUSICAL_CYCLE_VERSION,
        episodes=list(episodes),
    )
    return YamlPresetProgram(document)


def _mixed_episode() -> EpisodeCard:
    return EpisodeCard(
        id="mix",
        duration_s=18,
        groups=["all_rear"],
        palette="deep_red",
        effect="pulse",
        speed=0.5,
        intensity=1.0,
        transition="cut",
        actions=[
            LookAction(
                id="left-1",
                groups=["left", "par"],
                palette="deep_red",
                effect="pulse",
                speed=0.5,
                intensity=1.0,
                cycle_beats=1,
            ),
            LookAction(
                id="bar-2",
                groups=["bar"],
                palette="cool_blue",
                effect="chase",
                speed=0.5,
                intensity=1.0,
                cycle_beats=2,
            ),
            LookAction(
                id="right-4",
                groups=["right", "par"],
                palette="violet",
                effect="wave",
                speed=0.5,
                intensity=1.0,
                cycle_beats=4,
            ),
            LookAction(
                id="beam-still",
                groups=["beam"],
                palette="warm_orange",
                effect="static",
                speed=0.4,
                intensity=1.0,
            ),
        ],
    )


def _fixture(show, kind: FixtureKind, *, groups: tuple[str, ...] = ()):
    wanted = set(groups)
    for fixture in show.patch.fixtures:
        if not fixture.enabled or fixture.kind is not kind:
            continue
        if wanted and not wanted.issubset(set(fixture.groups)):
            continue
        return fixture
    raise AssertionError((kind, groups))


def _role_index(show, fixture, role: ChannelRole, *, segment: int | None = None) -> int:
    profile = show.profiles[fixture.profile_id]
    for channel in profile.channels:
        if channel.role is not role:
            continue
        if segment is not None and channel.segment_index != segment:
            continue
        return global_channel(fixture.start_address, channel.local) - 1
    raise AssertionError((fixture.id, role, segment))


def _engine(show, program: YamlPresetProgram) -> Engine:
    engine = Engine(
        show=show,
        presets={program.id: program},
        active_preset_id=program.id,
        clock=FakeClock(),
    )
    engine.set_blackout(False)
    engine.set_master_brightness(1.0)
    return engine


def _dimmer_at(engine: Engine, index: int, beats: float) -> int:
    engine.musical_beats = beats
    frame = engine.render_at(engine.clock.time(), dt_s=0.0).frame
    return frame[index]


def _rising(values: list[int], on: int = 80, off: int = 30) -> int:
    count = 0
    hot = False
    for value in values:
        if value >= on and not hot:
            count += 1
            hot = True
        elif value < off:
            hot = False
    return count


def _series(engine: Engine, index: int, bpm: float, beats: float, steps: int = 160) -> list[int]:
    values: list[int] = []
    for step in range(steps):
        moment = beats * step / steps
        values.append(_dimmer_at(engine, index, moment))
    del bpm
    return values


def _wall_series(engine: Engine, index: int, bpm: float, seconds: float, steps: int = 80) -> list[int]:
    values: list[int] = []
    for step in range(steps):
        beats = (seconds * step / steps) * bpm / 60.0
        values.append(_dimmer_at(engine, index, beats))
    return values


def _write_preview(path: Path, rows: list[list[int]]) -> None:
    width = len(rows[0])
    scale = 4
    band = 14
    gap = 4
    height = len(rows) * (band + gap)
    pixels = bytearray(width * scale * height * 3)
    for row_index, row in enumerate(rows):
        for x, value in enumerate(row):
            level = max(0, min(255, int(value)))
            red = level
            green = int(level * 0.35)
            blue = int(level * 0.05)
            y0 = row_index * (band + gap)
            for sy in range(band):
                y = height - 1 - (y0 + sy)
                for sx in range(scale):
                    offset = (y * width * scale + x * scale + sx) * 3
                    pixels[offset : offset + 3] = bytes((blue, green, red))
    image_width = width * scale
    row_bytes = image_width * 3
    stride = (row_bytes + 3) & ~3
    raw = bytearray()
    for y in range(height):
        start = y * row_bytes
        raw.extend(pixels[start : start + row_bytes])
        raw.extend(b"\x00" * (stride - row_bytes))
    header = struct.pack("<2sIHHI", b"BM", 54 + len(raw), 0, 0, 54)
    info = struct.pack("<IIIHHIIIIII", 40, image_width, height, 1, 24, 0, len(raw), 0, 0, 0, 0)
    path.write_bytes(header + info + raw)


def _lock(runtime: AppRuntime, bpm: float = 120.0) -> None:
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    follower = runtime.bpm.follower
    moment = runtime.bpm.clock_s
    follower.bpm = bpm
    follower.locked = True
    follower._started = True
    follower._beats = 0.0
    follower._at = moment
    follower._fresh_until = moment + 60.0
    follower._release_until = None


def _install(runtime: AppRuntime, program: YamlPresetProgram) -> None:
    runtime.engine.presets[program.id] = program
    runtime.engine.select_preset(program.id, reset_clock=True)


def test_old_cards_adapt_once_and_orng_bar_is_not_rewritten() -> None:
    assert adapted_cycle_beats("static", 0.5) is None
    assert adapted_cycle_beats("pulse", 0.5) == 2
    assert adapted_cycle_beats("chase", 1.0) == 1
    assert adapted_cycle_beats("breathe", 0.0) == 8
    again = adapted_cycle_beats("pulse", 0.5)
    assert again == 2
    preset = Path(__file__).resolve().parents[2] / "config" / "scenes" / "orng-bar" / "presets" / "P01.yaml"
    text = preset.read_text(encoding="utf-8")
    assert "cycle_beats" not in text
    assert "musical_version" not in text
    with pytest.raises(ValidationError):
        PresetDocument(
            id="BAD",
            label="bad",
            builtin=False,
            musical_version=2,
            episodes=[EpisodeCard(id="e", duration_s=2, transition="cut")],
        )


def test_one_episode_runs_one_two_and_four_beat_cycles(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    show = runtime.engine.show
    program = _program(_mixed_episode())
    engine = _engine(show, program)
    left = _fixture(show, FixtureKind.PAR, groups=("left",))
    right = _fixture(show, FixtureKind.PAR, groups=("right",))
    bar = _fixture(show, FixtureKind.BAR)
    left_ch = _role_index(show, left, ChannelRole.DIMMER)
    right_ch = _role_index(show, right, ChannelRole.DIMMER)
    bar_ch = _role_index(show, bar, ChannelRole.SEGMENT_COLOR, segment=1)

    # A 1-beat cycle is back to the same channel one beat later. A 4-beat cycle is not.
    assert _dimmer_at(engine, left_ch, 0.0) == _dimmer_at(engine, left_ch, 1.0)
    assert _dimmer_at(engine, right_ch, 0.0) != _dimmer_at(engine, right_ch, 1.0)
    assert _dimmer_at(engine, bar_ch, 0.0) == _dimmer_at(engine, bar_ch, 2.0)
    assert _dimmer_at(engine, bar_ch, 0.0) != _dimmer_at(engine, bar_ch, 1.0)

    for bpm in (90.0, 120.0, 180.0):
        for index, period in ((left_ch, 1), (bar_ch, 2), (right_ch, 4)):
            series = _series(engine, index, bpm, 8.0)
            assert _rising(series) == pytest.approx(8 / period, abs=1)

    rows = [
        _wall_series(engine, index, bpm, 4.0)
        for bpm in (90.0, 120.0, 180.0)
        for index in (left_ch, bar_ch, right_ch)
    ]
    preview = tmp_path / "musical-cycles.bmp"
    _write_preview(preview, rows)
    assert preview.stat().st_size > 54

    still = EpisodeCard(
        id="still",
        duration_s=8,
        groups=["par"],
        palette="deep_red",
        effect="static",
        speed=0.2,
        intensity=0.8,
        transition="cut",
    )
    quiet = _engine(show, _program(still))
    fixture = _fixture(show, FixtureKind.PAR)
    channel = _role_index(show, fixture, ChannelRole.DIMMER)
    assert _dimmer_at(quiet, channel, 0.0) == _dimmer_at(quiet, channel, 4.0)

    home = program.evaluate(0.0, show, movement_turns=0.2, musical_beats=0.0)
    later = program.evaluate(0.0, show, movement_turns=0.2, musical_beats=4.0)
    home_pan = {
        key: intent.pan
        for key, intent in home.fixtures.items()
        if isinstance(intent, BeamIntent)
    }
    later_pan = {
        key: intent.pan
        for key, intent in later.fixtures.items()
        if isinstance(intent, BeamIntent)
    }
    assert home_pan
    assert later_pan.keys() == home_pan.keys()
    for key, pan in home_pan.items():
        assert later_pan[key] == pytest.approx(pan)


def test_without_bpm_the_authored_rate_is_unchanged(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    episode = EpisodeCard(
        id="e",
        duration_s=18,
        groups=["all_rear"],
        palette="deep_red",
        effect="pulse",
        speed=0.5,
        intensity=1.0,
        transition="cut",
    )
    _install(runtime, _program(episode))
    runtime.apply_bpm_sync(enabled=False)
    start = runtime.engine.effect_phase_turns
    for _ in range(20):
        runtime.tick(0.05)
    assert runtime.engine.musical_beats is None
    assert runtime.engine.effect_phase_turns - start == pytest.approx(
        effect_rate_hz(0.5, "pulse") * 1.0, rel=0.02
    )


def test_tempo_glides_and_a_lost_lock_returns_to_authored_speed(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    episode = EpisodeCard(
        id="e",
        duration_s=30,
        groups=["par"],
        palette="deep_red",
        effect="pulse",
        speed=0.5,
        intensity=1.0,
        transition="cut",
        cycle_beats=4,
    )
    _install(runtime, _program(episode))
    _lock(runtime, 100.0)
    follower = runtime.bpm.follower
    for step in range(80):
        follower._commit(runtime.bpm.clock_s)
        follower.bpm = 100.0 + 40.0 * (step / 80.0)
        follower.locked = True
        follower._fresh_until = runtime.bpm.clock_s + 30.0
        runtime.tick(0.05)
    expected = 0.0
    for step in range(80):
        expected += (100.0 + 40.0 * (step / 80.0)) / 60.0 * 0.05
    assert runtime.engine.effect_phase_turns == pytest.approx(expected / 4.0, rel=0.08)
    parked = runtime.engine.effect_phase_turns
    follower.bpm = None
    follower.locked = False
    follower._release_until = None
    follower._started = False
    for _ in range(20):
        runtime.tick(0.05)
    assert runtime.engine.musical_beats is None
    assert runtime.engine.effect_phase_turns - parked == pytest.approx(
        effect_rate_hz(0.5, "pulse"), rel=0.05
    )


def _events_of(samples: list[float]) -> list[StructuralEvent]:
    tracker = SectionTracker()
    hop = int(RATE * HOP_S)
    found: list[StructuralEvent] = []
    beat = 0.5
    for start in range(0, len(samples) - hop + 1, hop):
        hearing = analyze_slice(samples[start : start + hop], RATE)
        tracker.push(
            hearing.rms,
            HOP_S,
            onset=hearing.onset,
            hearing=hearing,
            beat_period_s=beat,
        )
        found.extend(tracker.take_events())
    return found


def _accelerating(seconds: float) -> list[float]:
    total = int(seconds * RATE)
    samples = [0.0] * total
    moment = 0.0
    while moment < seconds:
        start = int(moment * RATE)
        for offset in range(min(48, total - start)):
            samples[start + offset] = max(
                samples[start + offset],
                0.9 * (1.0 - offset / 48.0),
            )
        frac = min(1.0, moment / max(seconds * 0.65, 0.1))
        moment += 60.0 / (120.0 * (1.0 + 3.0 * frac))
    return samples


def _accent() -> list[float]:
    samples = [0.0] * int(0.45 * RATE)
    tau = 0.07 * RATE
    for offset in range(len(samples)):
        env = 0.98 * math.exp(-offset / tau)
        samples[offset] = env * math.sin(2.0 * math.pi * 55.0 * offset / RATE)
    return samples


def _sine(seconds: float, amp: float, freq: float = 220.0) -> list[float]:
    count = int(seconds * RATE)
    return [amp * math.sin(2.0 * math.pi * freq * i / RATE) for i in range(count)]


def _ramp(seconds: float, start: float, end: float) -> list[float]:
    count = int(seconds * RATE)
    return [
        (start + (end - start) * (index / max(count - 1, 1)))
        * math.sin(2.0 * math.pi * 180.0 * index / RATE)
        for index in range(count)
    ]


def _noise(seconds: float, amp: float, seed: int) -> list[float]:
    count = int(seconds * RATE)
    value = seed & 0x7FFFFFFF or 1
    samples: list[float] = []
    for _ in range(count):
        value = (1103515245 * value + 12345) & 0x7FFFFFFF
        samples.append(((value / 0x7FFFFFFF) * 2.0 - 1.0) * amp)
    return samples


def _thump(seconds: float) -> list[float]:
    count = int(seconds * RATE)
    samples = [0.15 * math.sin(2.0 * math.pi * 90.0 * i / RATE) for i in range(count)]
    for start in range(0, count, int(0.1 * RATE)):
        for offset in range(min(60, count - start)):
            samples[start + offset] = 0.98 * (1.0 - offset / 60.0)
    return samples


def test_a_drop_is_one_event_and_noise_is_not() -> None:
    drops = _events_of(
        click_track(120, 3.0, RATE) + _accelerating(3.5) + _accent() + click_track(120, 2.0, RATE)
    )
    assert len(drops) == 1
    assert drops[0].kind == "drop"
    assert drops[0].ident == 1
    assert _events_of(_noise(5.0, 0.5, 99)) == []
    assert _events_of(_sine(4.0, 0.05) + _sine(2.0, 0.45)) == []


def test_one_drop_steps_once_and_a_duplicate_does_not(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    first = EpisodeCard(id="a", duration_s=8, groups=["par"], transition="cut", effect="pulse")
    second = EpisodeCard(id="b", duration_s=8, groups=["bar"], transition="cut", effect="wave")
    third = EpisodeCard(id="c", duration_s=8, groups=["beam"], transition="cut", effect="chase")
    _install(runtime, _program(first, second, third))
    _lock(runtime)
    runtime.bpm.section._clock = 3.0
    runtime.bpm.section._begin_drop()
    runtime.tick(0.05)
    assert runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index == 0
    for _ in range(30):
        runtime.tick(0.05)
    assert runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index == 1
    event = StructuralEvent("drop", 1, 3.0)
    runtime.bpm.section._events.append(event)
    for _ in range(40):
        runtime.tick(0.05)
    assert runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index == 1


def test_an_event_on_the_boundary_does_not_step_twice(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    episodes = [
        EpisodeCard(id=name, duration_s=2, groups=["par"], transition="cut", effect="static")
        for name in ("a", "b", "c")
    ]
    _install(runtime, _program(*episodes))
    _lock(runtime)
    runtime.engine.preset_elapsed_s = 1.99
    runtime.bpm.section._clock = 4.0
    runtime.bpm.section._begin_drop()
    runtime._ingest_musical_events(0.05)
    assert runtime.engine.preset_elapsed_s == pytest.approx(1.99)
    runtime.engine.tick(dt_s=0.05, wall_dt_s=0.05)
    snap = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0)
    assert snap.episode_index == 1
    assert snap.episode_time_s < 0.1


def test_a_manual_seek_retires_an_older_event(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    episodes = [
        EpisodeCard(id=name, duration_s=8, groups=["par"], transition="cut", effect="pulse")
        for name in ("a", "b", "c")
    ]
    _install(runtime, _program(*episodes))
    _lock(runtime)
    runtime.bpm.section._clock = 2.0
    runtime.bpm.section._begin_drop()
    runtime.apply_seek_episode(2)
    for _ in range(40):
        runtime.tick(0.05)
    assert runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index == 2


def test_none_and_a_single_episode_do_not_wait_for_a_drop(tmp_path: Path) -> None:
    from orng_led.engine.presets import NONE_PRESET_ID

    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    _lock(runtime)
    runtime.engine.select_preset(NONE_PRESET_ID, reset_clock=True)
    runtime.bpm.section._begin_drop()
    runtime.tick(0.2)
    assert runtime.engine.active_preset_id == NONE_PRESET_ID

    episode = EpisodeCard(id="only", duration_s=1.5, groups=["par"], transition="cut", effect="pulse")
    _install(runtime, _program(episode))
    _lock(runtime)
    runtime.bpm.section._begin_drop()
    for _ in range(40):
        runtime.tick(0.05)
    snap = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0)
    assert snap.episode_index == 0
    assert runtime.engine.preset_elapsed_s > 1.5

    first = EpisodeCard(id="a", duration_s=1.2, groups=["par"], transition="cut", effect="static")
    second = EpisodeCard(id="b", duration_s=1.2, groups=["bar"], transition="cut", effect="static")
    _install(runtime, _program(first, second))
    runtime.apply_bpm_sync(enabled=False)
    for _ in range(40):
        runtime.tick(0.05)
    assert runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index == 1


def test_held_fx_stay_independent_and_blackout_wins(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    episodes = [
        EpisodeCard(id=name, duration_s=8, groups=["all_rear"], transition="cut", effect="pulse", cycle_beats=4)
        for name in ("a", "b")
    ]
    _install(runtime, _program(*episodes))
    _lock(runtime)
    runtime.engine.set_blackout(False)
    runtime.engine.set_face(True)
    runtime.engine.strobe_press()
    origin = runtime.engine.strobe_speed_at_press
    runtime.engine.nudge_live_fx_speed(0.1)
    nudged = runtime.engine.overlays.strobe_speed
    assert nudged == pytest.approx(origin + 0.1)
    phase = runtime.engine.overlays.strobe_phase
    runtime.bpm.follower.bpm = 180.0
    runtime.tick(0.05)
    runtime.apply_seek_episode(1)
    runtime.tick(0.05)
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.face_on is True
    assert runtime.engine.overlays.strobe_speed == pytest.approx(nudged)
    assert runtime.engine.overlays.strobe_phase > phase
    runtime.engine.strobe_release()
    assert runtime.engine.overlays.strobe_speed == pytest.approx(origin)
    runtime.engine.set_blackout(True)
    runtime.engine.strobe_press()
    runtime.engine.set_face(True)
    frame = runtime.engine.tick(0.05).frame
    assert sum(frame) == 0
    assert runtime.output.armed is False


def test_minimum_episode_uses_two_beats() -> None:
    assert MIN_EPISODE_BEATS == 2
