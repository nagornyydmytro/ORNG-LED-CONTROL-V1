"""Musical layers on one picture: bed, accent, series, then the operator limits.

The comparison clip is a Mock render. It does not open Art-Net.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from orng_led.audio.auto_show import StructuralEvent, build_auto_stage, stack_auto_layers
from orng_led.audio.library import BY_ID
from orng_led.audio.palettes import rgbw_of
from orng_led.audio.planner import EpisodePlanner
from orng_led.audio.settings import series_period_frames
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import BeamIntent, ParIntent
from orng_led.engine.renderer import render_stage
from orng_led.presets.program import YamlPresetProgram
from orng_led.presets.staff import build_staff_preset
from orng_led.show_bench.render import render_frame
from orng_led.show_bench.session import _Encoder
from orng_led.simulator.decode import decode_simulator_view


def _fixtures(show: object) -> list[object]:
    return list(show.patch.fixtures)


def _stage(show: object, look: str, level: float, phase: float = 0.35) -> object:
    item = BY_ID[look]
    return build_auto_stage(
        _fixtures(show),
        "orange",
        "groove",
        phase,
        level,
        look=look,
        beats=None,
        cycle_beats=4.0,
        placements=show.layout.placements,
        groups=item.groups,
    )


def _view(show: object, stage: object) -> object:
    motion = {
        fixture.id: BeamMotionState(pan=0.5, tilt=0.5)
        for fixture in _fixtures(show)
        if fixture.kind is FixtureKind.BEAM
    }
    return decode_simulator_view(show, render_stage(show, stage, motion))


def _stack(show: object, stage: object, **kwargs: object) -> object:
    painted, _spent = stack_auto_layers(
        stage,
        _fixtures(show),
        amount=float(kwargs.get("amount", 0.8)),
        color=rgbw_of("orange"),
        cast_groups=kwargs.get("cast_groups", ("par", "bar", "beam")),
        own_par=bool(kwargs.get("own_par", False)),
        par_bed=bool(kwargs.get("par_bed", True)),
        pulse=bool(kwargs.get("pulse", False)),
        wall_s=float(kwargs.get("wall_s", 1.0)),
        beat_phase=float(kwargs.get("beat_phase", 0.5)),
        attack_phase=kwargs.get("attack_phase"),
        accent=float(kwargs.get("accent", 0.0)),
        accent_ready=kwargs.get("accent_ready"),
        now=float(kwargs.get("now", 1.0)),
        flash=kwargs.get("flash"),
    )
    return painted


def _par_levels(view: object) -> list[float]:
    return [float(item.intensity) for item in view.pars]


def test_confirmed_extra_attacks_move_the_par_phase() -> None:
    from orng_led.audio.auto_show import SectionTracker, SliceHearing

    def phase(high: tuple[float, float], generation: int) -> float:
        tracker = SectionTracker()
        hearing = SliceHearing(
            0.2,
            0.4,
            low_attacks=(0.0,),
            high_attacks=high,
            generation=generation,
        )
        for _ in range(6):
            tracker.push(0.2, 0.4, hearing=hearing, beat_period_s=0.5)
        tracker.observe(2.4, 0.4, 0.0)
        assert tracker.extra_pulse
        assert tracker.extra_phase is not None
        return float(tracker.extra_phase)

    assert phase((0.05, 0.22), 1) != phase((0.05, 0.36), 2)


def test_par_pulse_follows_the_attack_across_decoded_frames() -> None:
    from orng_led.audio.auto_show import SectionTracker, SliceHearing

    show = load_show_config()
    bare = _stage(show, "vertical_wave", 0.8)
    tracker = SectionTracker()
    period = 0.5

    def feed(offset: float, generation: int, origin: float, dt: float = 0.4) -> None:
        hearing = SliceHearing(
            0.2,
            0.4,
            low_attacks=(0.0,),
            high_attacks=(0.05, offset),
            generation=generation,
            origin_s=origin,
            origin_clock="show",
        )
        tracker.push(
            0.2,
            dt,
            hearing=hearing,
            beat_period_s=period,
            now=origin + dt,
            slice_start=origin,
        )

    origin = 0.0
    for _ in range(4):
        feed(0.22, 3, origin)
        origin += 0.4
    assert tracker.extra_pulse
    first = tracker.extra_attack_at
    assert first is not None

    def level_at(moment: float) -> float:
        tracker.observe(moment, (moment / period) % 1.0, 0.0)
        painted = _stack(
            show,
            bare,
            pulse=True,
            wall_s=moment,
            now=moment,
            beat_phase=(moment / period) % 1.0,
            attack_phase=tracker.extra_phase,
        )
        return max(_par_levels(_view(show, painted)))

    peak = level_at(float(first))
    decay = level_at(float(first) + 0.25)
    assert peak > decay + 0.15
    moved = tracker.extra_attack_at
    feed(0.36, 3, origin)
    assert tracker.extra_attack_at != moved
    second = float(tracker.extra_attack_at)
    again = level_at(second)
    assert again > decay + 0.15
    held = tracker.extra_attack_at
    feed(0.36, 3, origin)
    assert tracker.extra_attack_at == held
    feed(0.30, 2, origin + 0.4)
    assert tracker.extra_generation == 3
    assert tracker.extra_attack_at == held
    stale = float(held)
    late = SliceHearing(
        0.2,
        0.4,
        low_attacks=(0.0,),
        high_attacks=(0.05, 0.22),
        generation=3,
        origin_s=tracker._wall - 3.0,
        origin_clock="show",
    )
    tracker.push(
        0.2,
        0.4,
        hearing=late,
        beat_period_s=period,
        now=tracker._wall,
        slice_start=tracker._wall - 3.0,
    )
    assert tracker.extra_attack_at == stale


def test_par_bed_fills_a_bar_picture_and_stays_a_bed() -> None:
    show = load_show_config()
    bare = _stage(show, "vertical_wave", 0.8)
    off = _view(show, _stack(show, bare, par_bed=False))
    bed = _view(show, _stack(show, bare, par_bed=True, pulse=False, wall_s=1.0))
    assert _par_levels(off) == [] or max(_par_levels(off)) < 0.02
    assert min(_par_levels(bed)) > 0.12
    assert max(_par_levels(bed)) - min(_par_levels(bed)) < 0.04
    assert max(max(bar.segments) for bar in bed.bars) > min(min(bar.segments) for bar in bed.bars)
    assert all(item.dimmer < 0.05 for item in bed.beams)
    assert all(item.intensity < 0.02 for item in bed.faces)


def test_pulse_answers_the_confirmed_attack_and_does_not_replace_the_picture() -> None:
    show = load_show_config()
    bare = _stage(show, "vertical_wave", 0.8)
    bed = _par_levels(_view(show, _stack(show, bare, pulse=False, wall_s=0.0)))
    pulse = _par_levels(_view(show, _stack(show, bare, pulse=True, beat_phase=0.5, attack_phase=0.5)))
    moved = _par_levels(_view(show, _stack(show, bare, pulse=True, beat_phase=0.5, attack_phase=0.15)))
    assert max(pulse) > max(bed) + 0.2
    assert max(pulse) > max(moved) + 0.15
    wash = _stage(show, "wash", 0.8, phase=0.2)
    before = {
        fixture_id: intent.intensity
        for fixture_id, intent in wash.fixtures.items()
        if isinstance(intent, ParIntent)
    }
    assert before
    kept = _stack(show, wash, own_par=True, pulse=True, beat_phase=0.5, attack_phase=0.5)
    after = {
        fixture_id: intent.intensity
        for fixture_id, intent in kept.fixtures.items()
        if isinstance(intent, ParIntent)
    }
    assert after == before
    bars_only = _stack(show, bare, cast_groups=("bar",))
    assert not any(isinstance(intent, ParIntent) for intent in bars_only.fixtures.values())
    dark = _stack(show, bare, amount=0.0)
    assert not any(isinstance(intent, ParIntent) for intent in dark.fixtures.values())


def test_accent_slider_scales_one_free_par_and_zero_adds_nothing() -> None:
    show = load_show_config()
    bare = _stage(show, "vertical_wave", 0.8)
    quiet = _par_levels(_view(show, _stack(show, bare, accent=1.0, accent_ready=None)))
    zero = _par_levels(_view(show, _stack(show, bare, accent=0.0, accent_ready=1.0)))
    mid = _view(show, _stack(show, bare, accent=0.5, accent_ready=1.0))
    full = _view(show, _stack(show, bare, accent=1.0, accent_ready=1.0))
    assert max(quiet) - min(quiet) < 0.04
    assert max(zero) - min(zero) < 0.04
    mid_levels = _par_levels(mid)
    full_levels = _par_levels(full)
    assert max(mid_levels) > min(mid_levels) + 0.1
    assert max(full_levels) > max(mid_levels) + 0.12
    assert sorted(mid_levels, reverse=True)[1] == sorted(zero, reverse=True)[0] or (
        max(mid_levels) > max(zero)
    )


def test_series_follows_the_roll_and_not_loudness_character_or_a_drop() -> None:
    planner = EpisodePlanner(seed=2)
    planner.advance(
        0.0,
        section="groove",
        level=1.0,
        theme="orange",
        character="impact",
        locked=True,
        drive=False,
        wall_s=0.0,
    )
    assert planner._series_on is False
    planner.advance(
        8.0,
        section="break",
        level=1.0,
        theme="orange",
        character="impact",
        locked=True,
        drive=False,
        wall_s=4.0,
    )
    assert planner._series_on is False
    planner.advance(
        16.0,
        section="groove",
        level=0.3,
        theme="orange",
        character="rhythmic",
        locked=True,
        drive=True,
        drive_id=7,
        drive_density=0.8,
        beat_period_s=0.5,
        wall_s=8.0,
    )
    assert planner._series_on is True
    planner.advance(
        18.0,
        section="groove",
        level=0.3,
        theme="orange",
        character="rhythmic",
        locked=True,
        drive=True,
        drive_id=7,
        drive_density=0.8,
        beat_period_s=0.5,
        wall_s=9.0,
    )
    assert planner._series_on is True
    planner.advance(
        20.0,
        section="break",
        level=0.0,
        theme="orange",
        character="rhythmic",
        locked=True,
        drive=True,
        drive_id=8,
        drive_density=0.8,
        beat_period_s=0.5,
        wall_s=10.0,
    )
    assert planner._series_on is False
    again = EpisodePlanner(seed=2)
    again.advance(
        4.0,
        section="groove",
        level=0.8,
        theme="orange",
        character="rhythmic",
        locked=True,
        drive=True,
        drive_id=3,
        drive_density=0.6,
        beat_period_s=0.5,
        wall_s=2.0,
    )
    assert again._series_on is True
    dropped = again.advance(
        6.0,
        section="drop",
        level=0.9,
        theme="orange",
        character="rhythmic",
        locked=True,
        drive=True,
        drive_id=3,
        drive_density=0.6,
        beat_period_s=0.5,
        wall_s=3.0,
        events=(StructuralEvent("drop", 1, 3.0),),
    )
    assert again._series_on is False
    assert dropped.role == "accent" or dropped.reason == "drop_next"
    assert again.flash_gate(6.0, wall_s=3.0) == 1.0
    assert again.flash_gate(6.2, wall_s=3.2) in {0.0, None}
    assert again._series_on is False
    assert series_period_frames(0.2, 0.5, "impact") == series_period_frames(0.2, 0.5, "rhythmic")
    assert series_period_frames(1.0, 0.5, "impact") < series_period_frames(0.0, 0.5, "impact")


def test_bars_mirror_and_a_horizontal_sweep_does_not() -> None:
    show = load_show_config()
    wave = _view(show, _stage(show, "vertical_wave", 0.8, phase=0.35))
    sweep = _view(show, _stage(show, "fill_left", 0.8, phase=0.35))
    assert _mirrored(show, wave)
    assert not _mirrored(show, sweep)
    assert wave.pars == [] or all(item.intensity < 0.02 for item in wave.pars)
    assert all(item.dimmer < 0.05 for item in wave.beams)


def test_characters_and_intensities_read_in_the_frame() -> None:
    show = load_show_config()
    pictures = []
    for character in ("smooth", "rhythmic", "impact"):
        planner = EpisodePlanner(seed=4)
        episode = planner.advance(
            8.0,
            section="groove",
            level=0.72,
            theme="orange",
            character=character,
            locked=True,
            wall_s=4.0,
        )
        trail = tuple(
            _signature(_view(show, _stage(show, episode.look, 0.72, phase=phase)))
            for phase in (0.0, 0.25, 0.55, 0.8)
        )
        pictures.append(trail)
    assert len(set(pictures)) == 3
    levels = []
    for level in (0.45, 0.72, 1.0):
        view = _view(show, _stage(show, "vertical_wave", level, phase=0.35))
        levels.append(max(float(bar.dimmer) for bar in view.bars))
    assert levels[0] < levels[1] < levels[2]


def test_preset_beams_do_not_follow_auto_brightness() -> None:
    show = load_show_config()
    motion = {
        fixture.id: BeamMotionState(pan=0.5, tilt=0.5)
        for fixture in _fixtures(show)
        if fixture.kind is FixtureKind.BEAM
    }
    for preset_id in ("P01", "P05", "P09"):
        program = YamlPresetProgram(build_staff_preset(preset_id))
        stage = program.evaluate(30.0, show)
        before = list(render_stage(show, stage, motion, master=1.0))
        for level in (0.2, 1.0):
            auto = _stage(show, "vertical_wave", level)
            render_stage(show, auto, motion, master=level)
        after = list(render_stage(show, stage, motion, master=1.0))
        assert after == before
        for fixture in _fixtures(show):
            if fixture.kind is not FixtureKind.BEAM:
                continue
            intent = stage.fixtures.get(fixture.id)
            if isinstance(intent, BeamIntent):
                assert intent.dimmer == 1.0


def test_one_sound_comparison_is_visible_in_frames_and_video() -> None:
    show = load_show_config()
    bare = _stage(show, "vertical_wave", 0.8, phase=0.4)
    beds = _view(show, _stack(show, bare, par_bed=False))
    lit = _view(show, _stack(show, bare, par_bed=True))
    assert _signature(beds) != _signature(lit)
    out = Path(__file__).resolve().parents[2] / "show-bench" / "runs" / "layers"
    out.mkdir(parents=True, exist_ok=True)
    encoder = _Encoder(out / "comparison.mp4", 48000)
    labels = [
        ("PAR OFF", _stack(show, bare, amount=1.0, par_bed=False)),
        ("PAR BED", _stack(show, bare, amount=1.0, par_bed=True, pulse=False)),
        ("ACCENT 0", _stack(show, bare, amount=1.0, accent=0.0, accent_ready=1.0)),
        ("ACCENT MID", _stack(show, bare, amount=1.0, accent=0.5, accent_ready=1.0)),
        ("ACCENT FULL", _stack(show, bare, amount=1.0, accent=1.0, accent_ready=1.0)),
    ]
    for character in ("smooth", "rhythmic", "impact"):
        planner = EpisodePlanner(seed=4)
        episode = planner.advance(
            8.0,
            section="groove",
            level=0.72,
            theme="orange",
            character=character,
            locked=True,
            wall_s=4.0,
        )
        labels.append((character, _stage(show, episode.look, 0.72, phase=0.4)))
    for level in (0.45, 0.72, 1.0):
        labels.append((f"level {level}", _stage(show, "vertical_wave", level, phase=0.4)))
    frames = []
    for label, stage in labels:
        image = render_frame(
            _view(show, stage),
            _places(show),
            clock_s=4.0,
            score=label,
            heard="120 BPM CLICKS",
        )
        frames.append(image)
        for _ in range(12):
            encoder.write(image)
    encoder.close()
    assert (out / "comparison.mp4").is_file()
    assert (out / "comparison.mp4").stat().st_size > 1000
    def changed(left: int, right: int, floor: int) -> int:
        delta = np.abs(frames[left].astype(int) - frames[right].astype(int)).sum(axis=2)
        return int((delta > floor).sum())

    assert changed(0, 1, 24) > 30
    assert changed(3, 4, 12) > 10
    assert changed(5, 6, 12) > 10
    assert changed(6, 7, 12) > 10
    assert changed(8, 10, 12) > 10


def _signature(view: object) -> tuple[object, ...]:
    bars = tuple(
        (round(float(bar.dimmer), 2), tuple(round(float(level), 2) for level in bar.segments))
        for bar in view.bars
    )
    pars = tuple(round(float(item.intensity), 2) for item in view.pars)
    return bars, pars


def _places(show: object) -> dict[str, object]:
    return {item.fixture_id: item for item in show.layout.placements}


def _mirrored(show: object, view: object) -> bool:
    places = _places(show)
    bars = [(float(places[bar.id].x), bar) for bar in view.bars]
    if len(bars) < 2:
        return False
    center = sum(item[0] for item in bars) / len(bars)
    matched = False
    for origin, bar in bars:
        mirror_x = 2.0 * center - origin
        if abs(origin - center) < 1e-3:
            continue
        other = min(bars, key=lambda item: abs(item[0] - mirror_x))
        if abs(other[0] - mirror_x) > abs(origin - center) * 0.35:
            continue
        matched = True
        if tuple(round(float(level), 2) for level in bar.segments) != tuple(
            round(float(level), 2) for level in other[1].segments
        ):
            return False
    return matched
