"""Automatic layers on decoded frames. Home show, Mock, output disarmed.

Synthetic clicks and a tone exercise the analyser. A pause event in the
scenario test is not that recording. Nothing here is a fixture acceptance.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from orng_led.audio.auto_show import DROP_ACCENT_S, StructuralEvent, build_auto_stage
from orng_led.audio.bpm import TempoReading, click_track
from orng_led.audio.library import COMPOSITIONS
from orng_led.audio.planner import MOTIF_HOLD, AutoAction, EpisodePlanner
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

RATE = 48000


def _pose(show: object) -> dict[str, BeamMotionState]:
    return {
        fixture.id: BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
        for fixture in show.patch.fixtures
        if fixture.kind is FixtureKind.BEAM
    }


def _view(show: object, stage: object):
    return decode_simulator_view(show, render_stage(show, stage, _pose(show)))


def _stage(show: object, look: str, level: float, **kwargs):
    return build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        kwargs.pop("section", "groove"),
        0.0,
        level,
        look=look,
        beats=kwargs.pop("beats", 0.5),
        cycle_beats=4.0,
        placements=show.layout.placements,
        groups=kwargs.pop("groups", None),
        **kwargs,
    )


def _tone(seconds: float, hz: float = 220.0, amp: float = 0.08) -> list[float]:
    count = int(seconds * RATE)
    return [amp * math.sin(2.0 * math.pi * hz * index / RATE) for index in range(count)]


def test_catalog_zero_soft_and_full_on_decoded_frames() -> None:
    show = load_show_config()
    unequal = False
    assert len(COMPOSITIONS) >= 100
    for item in COMPOSITIONS:
        groups = set(item.groups)
        for level, beat in ((0.0, 0.5), (0.0, 1.5), (0.35, 0.5), (1.0, 0.5)):
            view = _view(show, _stage(show, item.id, level, beats=beat))
            assert all(face.intensity <= 0.02 for face in view.faces)
            assert all(len(bar.segments) == 8 for bar in view.bars)
            if "beam" not in groups:
                assert all(beam.dimmer <= 0.02 for beam in view.beams)
            if "par" not in groups:
                assert all(par.intensity <= 0.02 for par in view.pars)
            if "bar" not in groups:
                assert all(max(bar.segments) <= 0.02 and bar.dimmer <= 0.02 for bar in view.bars)
            if level == 0.0:
                assert all(beam.dimmer <= 0.02 for beam in view.beams), item.id
                assert all(par.intensity <= 0.02 for par in view.pars), item.id
                assert all(max(bar.segments) <= 0.02 for bar in view.bars), item.id
            else:
                ceiling = 0.37 if level == 0.35 else 1.01
                assert all(par.intensity <= ceiling + 1e-6 for par in view.pars), item.id
                assert all(bar.dimmer <= ceiling + 1e-6 for bar in view.bars), item.id
                assert all(beam.dimmer <= 0.02 or beam.dimmer >= 0.98 for beam in view.beams)
            if level == 1.0 and len(view.bars) >= 2:
                patterns = [tuple(round(value, 2) for value in bar.segments) for bar in view.bars]
                if any(max(pattern) > 0.05 for pattern in patterns) and len(set(patterns)) > 1:
                    unequal = True
    assert unequal


def test_drop_accent_stays_inside_the_operator_level() -> None:
    show = load_show_config()
    accent = _view(
        show,
        _stage(show, "accent", 0.35, section="drop", drop_age=0.1, beats=0.0),
    )
    assert accent.pars
    assert max(par.intensity for par in accent.pars) == pytest.approx(0.35, abs=0.05)
    assert max(par.intensity for par in accent.pars) < 0.9
    for item in COMPOSITIONS:
        for level, ceiling in ((0.0, 0.02), (0.35, 0.37), (1.0, 1.01)):
            view = _view(
                show,
                _stage(show, item.id, level, section="drop", drop_age=0.1, beats=0.0),
            )
            assert all(par.intensity <= ceiling + 1e-6 for par in view.pars), (item.id, level)
            assert all(bar.dimmer <= ceiling + 1e-6 for bar in view.bars), (item.id, level)
            if level == 0.0:
                assert all(max(bar.segments) <= 0.02 for bar in view.bars), item.id
                assert all(beam.dimmer <= 0.02 for beam in view.beams), item.id


def test_confirmed_drop_is_not_dissolved_by_the_entry_blend() -> None:
    show = load_show_config()
    planner = EpisodePlanner(seed=1)
    planner.advance(0.0, section="groove", level=0.7, theme="orange", locked=True, wall_s=0.0)
    planner.advance(
        1.2,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        wall_s=1.2,
        events=(StructuralEvent("drop", 1, 1.2),),
    )
    landed = planner.advance(
        2.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        wall_s=2.0,
    )
    assert landed is not None
    assert landed.reason == "drop_next"
    assert landed.look == "hit_pars"
    origin, weight = planner.blend(2.0)
    assert origin is None
    assert weight == pytest.approx(1.0)
    plain = _view(
        show,
        _stage(show, landed.look, 0.7, section="drop", drop_age=0.0, beats=0.0),
    )
    peak = max(
        [par.intensity for par in plain.pars] + [bar.dimmer for bar in plain.bars],
        default=0.0,
    )
    assert peak == pytest.approx(0.7, abs=0.15)
    assert peak > 0.4


def test_zero_does_not_return_as_a_drop_or_a_flash() -> None:
    show = load_show_config()
    dropped = _view(
        show,
        _stage(show, "wash", 0.0, section="drop", drop_age=0.1),
    )
    flashed = _view(show, _stage(show, "wash", 0.0, section="riser", flash_hold=1.0))
    soft = _view(show, _stage(show, "wash", 0.35, section="riser", flash_hold=1.0))
    assert all(par.intensity <= 0.02 for par in dropped.pars)
    assert all(par.intensity <= 0.02 for par in flashed.pars)
    assert all(max(bar.segments) <= 0.02 for bar in flashed.bars)
    assert soft.pars and max(par.intensity for par in soft.pars) <= 0.37
    assert soft.pars and max(par.intensity for par in soft.pars) > 0.2
    assert all(beam.dimmer <= 0.02 for beam in flashed.beams)
    held = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "drop",
        0.2,
        0.0,
        drop_age=DROP_ACCENT_S + 0.05,
        look="heads_only_oval",
        beats=0.4,
        placements=show.layout.placements,
    )
    dark = _view(show, held)
    assert all(beam.dimmer <= 0.02 for beam in dark.beams)
    assert all(intent.pan is None and intent.tilt is None for intent in held.fixtures.values() if hasattr(intent, "pan"))


def test_pause_dims_heads_without_becoming_blackout(tmp_path: Path) -> None:
    show = load_show_config()
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True, scope="auto", palette="orange", character="rhythmic")
    service._now = 20.0
    service.follower.push(TempoReading(120.0, 0.0, 1.0), now=20.0)
    service.follower.push(TempoReading(120.0, 0.0, 1.0), now=20.0)
    service.section.section = "groove"
    service.section.level = 0.8
    service.section.shown = 0.8
    service.look(list(show.patch.fixtures), show.layout.placements)
    current = service.planner.current
    assert current is not None
    from dataclasses import replace

    service.planner.current = replace(
        current,
        look="heads_only_oval",
        groups=("beam",),
        duration_beats=1000.0,
        actions=(AutoAction("picture", "heads_only_oval", 8.0),),
    )
    service.section._events.append(StructuralEvent("pause", 9, 20.0))
    stage = service.look(list(show.patch.fixtures), show.layout.placements)
    view = _view(show, stage)
    assert service.planner.dimming(service._now) is False
    assert service.planner._blackout is False
    assert any(beam.dimmer > 0.2 for beam in view.beams)


def test_smooth_entry_is_a_frame_blend_and_then_holds() -> None:
    show = load_show_config()
    planner = EpisodePlanner(seed=1)
    from dataclasses import replace

    opened_first = planner.advance(0.0, section="groove", level=0.7, theme="orange", locked=True, wall_s=0.0)
    planner.current = replace(
        opened_first,
        look="accent",
        actions=(AutoAction("picture", "accent", 1.0),),
        duration_beats=1000.0,
    )
    planner.advance(
        1.0,
        section="groove",
        level=0.55,
        theme="orange",
        locked=False,
        hearing="melodic",
        wall_s=10.0,
    )
    opened = planner.advance(
        2.0,
        section="groove",
        level=0.55,
        theme="orange",
        locked=False,
        hearing="melodic",
        wall_s=14.2,
    )
    origin, weight = planner.blend(14.2)
    assert opened.clock == "melodic"
    assert origin and weight == pytest.approx(0.0)
    early = _view(
        show,
        _stage(show, opened.look, 0.55, mix=weight, mix_from=origin),
    )
    _origin, mid_weight = planner.blend(14.6)
    mid = _view(
        show,
        _stage(show, opened.look, 0.55, mix=mid_weight, mix_from=origin),
    )
    assert mid_weight == pytest.approx(0.4 / 1.5)
    departed = _view(show, _stage(show, origin, 0.55))
    arrived = _view(show, _stage(show, opened.look, 0.55))
    early_peak = max((par.intensity for par in early.pars), default=0.0)
    mid_peak = max((par.intensity for par in mid.pars), default=0.0)
    old_peak = max((par.intensity for par in departed.pars), default=0.0)
    new_peak = max((par.intensity for par in arrived.pars), default=0.0)
    assert early_peak == pytest.approx(old_peak, abs=0.03)
    if abs(old_peak - new_peak) > 0.08:
        assert min(old_peak, new_peak) - 0.02 <= mid_peak <= max(old_peak, new_peak) + 0.02
        assert abs(mid_peak - early_peak) > 0.02
    still = planner.advance(
        3.0,
        section="groove",
        level=0.55,
        theme="orange",
        locked=True,
        hearing="rhythmic",
        wall_s=16.0,
    )
    assert still.clock == "melodic"
    assert planner._smooth_since == pytest.approx(14.2)


def test_motif_changes_after_a_phrase_without_resetting_time_or_jumping_to_half() -> None:
    planner = EpisodePlanner(seed=4)
    first = planner.advance(0.0, section="groove", level=0.7, theme="orange", locked=True, wall_s=0.0)
    from dataclasses import replace

    from orng_led.audio.planner import AutoAction

    planner.current = replace(
        first,
        actions=(AutoAction("picture", first.look, 8.0),),
    )
    seen = {first.look}
    beat = first.start_beat + first.duration_beats
    mark = planner._mark
    cycle = 8.0
    ladder = (0.5, 1.0, 2.0, 4.0, 8.0)
    for step in range(5):
        episode = planner.advance(
            beat,
            section="groove",
            level=0.7,
            theme="orange",
            locked=True,
            wall_s=float(step + 1),
        )
        assert planner._mark + 1e-9 >= mark
        mark = planner._mark
        before = min(range(len(ladder)), key=lambda index: abs(ladder[index] - cycle))
        after = min(range(len(ladder)), key=lambda index: abs(ladder[index] - episode.picture_cycle))
        assert abs(after - before) <= 1
        cycle = float(episode.picture_cycle)
        if beat + 1e-9 < MOTIF_HOLD["rhythmic"][1]:
            assert episode.look == first.look
        seen.add(episode.look)
        beat = episode.start_beat + episode.duration_beats
    assert first.look in seen
    opened = planner._mark
    planner.request_next_look(opened, locked=True)
    assert planner._mark == pytest.approx(opened)
    landed = planner.advance(
        opened,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        wall_s=12.0,
    )
    landed_index = min(range(len(ladder)), key=lambda index: abs(ladder[index] - landed.picture_cycle))
    kept_index = min(range(len(ladder)), key=lambda index: abs(ladder[index] - cycle))
    assert abs(landed_index - kept_index) <= 1
    planner.advance(
        landed.start_beat,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        texture_id=1,
        wall_s=13.0,
    )
    assert planner._mark + 1e-9 >= opened
    assert planner.upcoming is None or planner.upcoming.reason == "develop" or any(
        row["kind"] == "develop" for row in planner.journal()
    )


def test_series_needs_a_fresh_grid_and_stops_on_dim_or_drop() -> None:
    planner = EpisodePlanner(seed=6)
    planner.advance(
        0.0,
        section="riser",
        level=0.95,
        theme="orange",
        character="impact",
        locked=True,
        wall_s=0.0,
    )
    assert planner.flash_gate(0.0, wall_s=0.0) is None
    planner.advance(
        1.0,
        section="groove",
        level=0.9,
        theme="orange",
        locked=False,
        drive=True,
        drive_id=2,
        drive_density=0.8,
        beat_period_s=0.5,
        wall_s=1.0,
    )
    assert planner._series_on is False
    planner.advance(
        2.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        drive=True,
        drive_id=3,
        drive_density=0.7,
        beat_period_s=0.5,
        wall_s=2.0,
    )
    assert planner._series_on is True
    planner.advance(
        3.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        drive=True,
        drive_id=3,
        drive_density=0.7,
        beat_period_s=0.5,
        wall_s=3.0,
        events=(StructuralEvent("pause", 1, 3.0),),
    )
    assert planner._series_on is True
    assert planner._blackout is False
    assert planner.dimming(3.0) is False
    planner.advance(
        12.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        drive=True,
        drive_id=3,
        drive_density=0.7,
        beat_period_s=0.5,
        wall_s=4.0,
    )
    assert planner._series_on is False
    planner.advance(
        22.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        drive=True,
        drive_id=4,
        drive_density=0.7,
        beat_period_s=0.5,
        wall_s=8.0,
    )
    assert planner._series_on is True
    planner.advance(
        23.0,
        section="drop",
        level=0.9,
        theme="orange",
        locked=True,
        wall_s=21.0,
        events=(StructuralEvent("drop", 2, 21.0),),
    )
    assert planner._series_on is False


def test_rendered_program_loses_the_grid_and_a_pause_from_the_analyser(tmp_path: Path) -> None:
    """Synthetic clicks, then a tone. The pause comes from the section ear."""
    show = load_show_config()
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True, scope="auto", palette="orange", character="rhythmic")
    fixtures = list(show.patch.fixtures)
    clicks = click_track(120, 2.0, RATE)
    tone = _tone(2.0, amp=0.08)
    now = 0.0

    def push(samples: list[float]) -> object:
        nonlocal now
        step = len(samples) / RATE
        now += step
        service.feed(samples, RATE, dt_s=step, now=now)
        return service.look(fixtures, show.layout.placements)

    push(clicks)
    lit = _view(show, push(clicks))
    assert service.follower.locked is True
    assert service.planner._blackout is False
    assert any(par.intensity > 0.05 for par in lit.pars) or any(
        max(bar.segments) > 0.05 for bar in lit.bars
    )
    for _ in range(2):
        frame = push(tone)
        assert service.planner.dimming(service._now) is False
        assert service.planner._blackout is False
    silent = _view(show, push([0.0] * int(1.7 * RATE)))
    assert service.section.level == 0.0
    assert service.planner._blackout is False
    assert all(par.intensity <= 0.02 for par in silent.pars)
    assert all(beam.dimmer <= 0.02 for beam in silent.beams)
    assert all(max(bar.segments) <= 0.02 for bar in silent.bars)
    push(clicks)
    again = _view(show, push(clicks))
    assert service.follower.locked is True
    assert service.planner._blackout is False
    assert any(par.intensity > 0.02 for par in again.pars) or any(
        max(bar.segments) > 0.02 for bar in again.bars
    )
