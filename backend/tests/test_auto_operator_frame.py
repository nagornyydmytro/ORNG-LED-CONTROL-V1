"""Auto operator changes, proved on rendered ORNG BAR frames.

configure → look → renderer. Isolated Mock patch, no Art-Net.
click_track is not used. This is not a venue take.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from orng_led.audio.bpm import TEMPO_HOLD_S, TEMPO_RELEASE_S, TempoReading
from orng_led.audio.planner import AutoAction, AutoEffect
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.clock import FRAME_DT
from orng_led.engine.intents import BarIntent, ParIntent
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view


def _pose(show: object) -> dict[str, BeamMotionState]:
    return {
        fixture.id: BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
        for fixture in show.patch.fixtures
        if fixture.kind is FixtureKind.BEAM
    }


def _lock(service: BpmService, now: float = 20.0) -> None:
    service._now = now
    service.follower.push(TempoReading(120.0, 0.0, 1.0), now=now)
    service.follower.push(TempoReading(120.0, 0.0, 1.0), now=now)
    service.section.section = "groove"
    service.section.level = 0.85
    service.section.shown = 0.85


def _ready(tmp_path: Path) -> tuple[BpmService, object]:
    show = load_show_config()
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True, scope="auto", palette="orange", character="rhythmic")
    _lock(service)
    return service, show


def _pin_wave(service: BpmService, *, cycle: float = 2.0) -> None:
    current = service.planner.current
    assert current is not None
    service.planner.current = replace(
        current,
        look="vertical_wave",
        groups=("bar",),
        actions=(AutoAction("picture", "vertical_wave", cycle),),
        cast=current.cast,
    )
    if service.planner.upcoming is not None:
        nxt = service.planner.upcoming
        service.planner.upcoming = replace(
            nxt,
            look="vertical_wave",
            groups=("bar",),
            actions=(AutoAction("picture", "vertical_wave", cycle),),
        )


def _stage(service: BpmService, show: object):
    return service.look(list(show.patch.fixtures), show.layout.placements)


def _frame(show: object, stage) -> list[int]:
    return render_stage(show, stage, _pose(show))


RATE = 44100


def _hum(seconds: float = 0.5, amp: float = 0.2, rate: int = RATE) -> list[float]:
    return [amp] * max(1, int(round(float(seconds) * rate)))


def _drive_sound(service: BpmService, until: float, samples: list[float] | None = None) -> None:
    """Keep energy without a new tempo vote. Isolated feed, not a venue take."""
    block = _hum() if samples is None else samples
    now = float(service._now)
    target = float(until)
    while now + 1e-9 < target:
        step = min(0.5, target - now)
        now += step
        service.feed(block, RATE, dt_s=step, now=now, energy_samples=block)


def _bar_dmx(show: object, frame: list[int]) -> tuple[tuple[int, ...], ...]:
    rows = []
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BAR:
            continue
        profile = show.profile_for(fixture)
        values = []
        for channel in profile.channels:
            if channel.role not in {
                ChannelRole.SEGMENT,
                ChannelRole.SEGMENT_COLOR,
                ChannelRole.DIMMER,
            }:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            values.append(int(frame[index]))
        rows.append(tuple(values))
    return tuple(rows)


def _dmx_sequence(look: str, *, cycle: float, amount: float) -> tuple[tuple[tuple[int, ...], ...], ...]:
    show = load_show_config()
    from orng_led.audio.auto_show import build_auto_stage

    frames = []
    step = 1.0 / 32.0
    beat = 0.0
    while beat <= 4.0 + 1e-9:
        stage = build_auto_stage(
            list(show.patch.fixtures),
            "orange",
            "groove",
            0.0,
            amount,
            look=look,
            beats=beat,
            cycle_beats=cycle,
            placements=show.layout.placements,
            groups=("bar",),
            colour="orange",
            cast="bars",
        )
        frames.append(_bar_dmx(show, _frame(show, stage)))
        beat = round(beat + step, 6)
    return tuple(frames)


def test_configure_period_changes_future_speed_not_the_picture(tmp_path: Path) -> None:
    service, show = _ready(tmp_path)
    _stage(service, show)
    _pin_wave(service, cycle=2.0)
    ident = service.planner.current.ident
    start = service.planner.current.start_beat
    service.poll(0.375)
    beats = service.follower.action_beats_at(service.clock_s)
    assert 0.55 < beats < 0.95
    old_phase = (beats / 2.0) % 1.0
    before_stage = _stage(service, show)
    before_frame = _frame(show, before_stage)
    before = _bar_dmx(show, before_frame)
    assert service.planner.current.picture_cycle == pytest.approx(2.0)
    service.configure(period=8)
    after_stage = _stage(service, show)
    after_frame = _frame(show, after_stage)
    after = _bar_dmx(show, after_frame)
    assert service.planner.current.ident == ident
    assert service.planner.current.start_beat == start
    assert service.planner.current.picture_cycle == pytest.approx(8.0)
    assert service.planner.upcoming is not None
    assert service.planner.upcoming.picture_cycle == pytest.approx(8.0)
    picture = next(item for item in service.planner.current.actions if item.role == "picture")
    phase = ((beats - picture.origin_beats) / picture.cycle_beats) % 1.0
    assert phase == pytest.approx(old_phase, abs=1e-6)
    assert (beats / 8.0) % 1.0 != pytest.approx(old_phase, abs=1e-3)
    assert after == before
    view_before = decode_simulator_view(show, before_frame)
    view_after = decode_simulator_view(show, after_frame)
    assert [item.segments for item in view_before.bars] == [item.segments for item in view_after.bars]
    # 0.5 s at 120 BPM is one beat: cycle 8 advances 0.125, not a triangle mirror.
    service.poll(0.5)
    later_stage = _stage(service, show)
    later = _bar_dmx(show, _frame(show, later_stage))
    later_beats = service.follower.action_beats_at(service.clock_s)
    later_picture = next(item for item in service.planner.current.actions if item.role == "picture")
    later_phase = ((later_beats - later_picture.origin_beats) / later_picture.cycle_beats) % 1.0
    assert later != after
    assert later_phase == pytest.approx((old_phase + 0.125) % 1.0, abs=0.05)
    service.configure(period="auto")
    auto_stage = _stage(service, show)
    auto_beats = service.follower.action_beats_at(service.clock_s)
    assert service.planner.current.picture_cycle == pytest.approx(2.0)
    assert service.planner.upcoming.picture_cycle == pytest.approx(2.0)
    auto_picture = next(item for item in service.planner.current.actions if item.role == "picture")
    auto_phase = ((auto_beats - auto_picture.origin_beats) / auto_picture.cycle_beats) % 1.0
    assert auto_phase == pytest.approx(later_phase, abs=0.05)
    assert _bar_dmx(show, _frame(show, auto_stage)) == later


def test_configure_character_rewrites_actions_not_the_ident(tmp_path: Path) -> None:
    service, show = _ready(tmp_path)
    _stage(service, show)
    ident = service.planner.current.ident
    start = service.planner.current.start_beat
    before = tuple(action.cycle_beats for action in service.planner.current.actions)
    service.configure(character="smooth")
    _stage(service, show)
    assert service.planner.current.ident == ident
    assert service.planner.current.start_beat == start
    assert service.planner.current.character == "smooth"
    after = tuple(action.cycle_beats for action in service.planner.current.actions)
    assert after != before
    assert service.planner.current.picture_cycle == pytest.approx(8.0)


def test_real_release_keeps_auto_moving_without_a_false_lock(tmp_path: Path) -> None:
    service, show = _ready(tmp_path)
    origin = float(service._now)
    service.planner.current = None
    _stage(service, show)
    service.configure(cast="bars")
    locked = _stage(service, show)
    assert service.follower.locked is True
    assert all(not isinstance(intent, ParIntent) for intent in locked.fixtures.values())
    _drive_sound(service, origin + 4.0)
    free = _stage(service, show)
    assert service.follower.locked is False
    assert service.follower.releasing is False
    assert service.follower.bpm is None
    assert service.status()["bpm"] is None
    assert service.status()["locked"] is False
    assert service.status()["signal"] is True
    assert service.planner.current.clock == "freewheel"
    assert service.planner.current.cast == "bars"
    assert all(not isinstance(intent, ParIntent) for intent in free.fixtures.values())
    assert any(isinstance(intent, BarIntent) for intent in free.fixtures.values())
    _pin_wave(service, cycle=2.0)
    ident = service.planner.current.ident
    start = service.planner.current.start_beat
    parked = _stage(service, show)
    frame_4 = _frame(show, parked)
    beats_4 = service.follower.action_beats_at(service.clock_s)
    assert beats_4 == pytest.approx(8.0, abs=0.15)
    assert beats_4 != pytest.approx(origin % 8.0)
    quiet = BpmService(tmp_path, "quiet")
    quiet.configure(enabled=True, scope="auto")
    assert quiet.status()["signal"] is False
    assert quiet.follower.action_beats_at(quiet.clock_s) == pytest.approx(0.0)
    _drive_sound(service, origin + 4.5)
    moved = _stage(service, show)
    frame_moved = _frame(show, moved)
    assert service.planner.current.ident == ident
    assert service.planner.current.start_beat == start
    assert service.planner.current.clock == "freewheel"
    assert service.status()["bpm"] is None
    assert _bar_dmx(show, frame_moved) != _bar_dmx(show, frame_4)
    _drive_sound(service, origin + 8.0)
    later = _stage(service, show)
    beats_8 = service.follower.action_beats_at(service.clock_s)
    assert service.follower.bpm is None
    assert service.follower.locked is False
    assert service.planner.current.ident == ident
    assert service.planner.current.clock == "melodic"
    assert service.status()["bpm"] is None
    assert service.status()["signal"] is True
    assert beats_8 == pytest.approx(16.0, abs=0.2)
    assert beats_8 > beats_4 + 1.0
    assert beats_8 != pytest.approx(8.0, abs=0.5)
    assert beats_8 != pytest.approx((origin + 8.0) % 8.0)
    silence = [0.0] * int(0.5 * RATE)
    service.feed(silence, RATE, dt_s=0.5, now=float(service._now) + 0.5, energy_samples=silence)
    assert service._signal is False
    assert service.status()["signal"] is False
    assert service.follower.bpm is None
    assert service.follower.locked is False
    service.follower.push(TempoReading(120.0, 0.0, 1.0), now=service.clock_s)
    service.follower.push(TempoReading(120.0, 0.0, 1.0), now=service.clock_s)
    back = _stage(service, show)
    assert service.follower.locked is True
    assert service.planner.current.clock == "melodic"
    assert service.status()["bpm"] is not None
    assert service.follower.action_beats_at(service.clock_s) >= beats_8 - 0.12
    view = decode_simulator_view(show, _frame(show, back))
    assert all(item.intensity <= 0.02 for item in view.pars if not item.face)
    preset = BpmService(tmp_path, "preset")
    preset.configure(enabled=True, scope="preset")
    preset._now = 0.0
    preset.follower.push(TempoReading(120.0, 0.0, 1.0), now=0.0)
    preset.follower.push(TempoReading(120.0, 0.0, 1.0), now=0.0)
    preset.poll(TEMPO_HOLD_S + TEMPO_RELEASE_S + 0.1)
    assert preset.follower.locked is False
    assert preset.follower.bpm is None
    assert preset.show_factor(1.0) is None


def test_cast_bars_holds_through_hold_and_signal_loss(tmp_path: Path) -> None:
    service, show = _ready(tmp_path)
    _stage(service, show)
    _pin_wave(service, cycle=2.0)
    service.configure(cast="bars")
    _stage(service, show)
    service.follower.begin_release(service._now)
    held = _stage(service, show)
    assert service.follower.releasing is True
    assert service.planner.current.cast == "bars"
    assert all(not isinstance(intent, ParIntent) for intent in held.fixtures.values())
    assert any(isinstance(intent, BarIntent) for intent in held.fixtures.values())
    service.section.input_lost = True
    lost = _stage(service, show)
    assert service.planner.current.reason == "signal_loss"
    assert service.planner.current.cast == "bars"
    assert "bar" in service.planner.current.groups
    assert all(not isinstance(intent, ParIntent) for intent in lost.fixtures.values())
    view = decode_simulator_view(show, _frame(show, lost))
    assert all(item.intensity <= 0.02 for item in view.pars if not item.face)


def test_auto_flash_has_on_off_and_does_not_paint_white_warm(tmp_path: Path) -> None:
    from orng_led.audio.auto_show import build_auto_stage

    service, show = _ready(tmp_path)
    fixtures = list(show.patch.fixtures)
    on_stage = build_auto_stage(
        fixtures,
        "orange",
        "riser",
        0.0,
        0.6,
        look="wash",
        beats=0.0,
        cycle_beats=4.0,
        placements=show.layout.placements,
        groups=("par", "bar"),
        colour="orange",
        cast="all",
        flash_hold=1.0,
    )
    off_stage = build_auto_stage(
        fixtures,
        "orange",
        "riser",
        0.0,
        0.6,
        look="wash",
        beats=0.0,
        cycle_beats=4.0,
        placements=show.layout.placements,
        groups=("par", "bar"),
        colour="orange",
        cast="all",
        flash_hold=0.0,
    )
    pars = [intent for intent in on_stage.fixtures.values() if isinstance(intent, ParIntent)]
    bars = [intent for intent in on_stage.fixtures.values() if isinstance(intent, BarIntent)]
    assert pars and all(item.intensity == pytest.approx(0.6) for item in pars)
    assert bars and all(item.dimmer == pytest.approx(0.6) for item in bars)
    assert all(item.strobe == 0.0 for item in (*pars, *bars))
    orange = pars[0].color
    assert orange.r > orange.b
    assert orange.g < 0.9
    off_pars = [intent for intent in off_stage.fixtures.values() if isinstance(intent, ParIntent)]
    off_bars = [intent for intent in off_stage.fixtures.values() if isinstance(intent, BarIntent)]
    assert off_pars and all(item.intensity == pytest.approx(0.0) for item in off_pars)
    assert off_bars and all(item.dimmer == pytest.approx(0.0) for item in off_bars)
    for intent in on_stage.fixtures.values():
        if hasattr(intent, "pan"):
            assert intent.pan is None and intent.tilt is None and intent.strobe == 0.0
    view = decode_simulator_view(show, _frame(show, on_stage))
    assert all(item.intensity <= 0.02 for item in view.faces)
    assert service.enabled is True


def test_look_flash_has_on_off_and_blackout_wins(tmp_path: Path) -> None:
    """A lone riser must not arm strobe; blackout still extinguishes the look."""
    service, show = _ready(tmp_path)
    _stage(service, show)
    current = service.planner.current
    assert current is not None
    service.section.section = "riser"
    service.planner.current = replace(
        current,
        look="wash",
        groups=("par", "bar"),
        section="riser",
        character="rhythmic",
        cast="all",
        actions=(AutoAction("picture", "wash", 4.0),),
        effects=(AutoEffect("flash", "auto", current.start_beat, 8.0, "rise"),),
    )
    on_stage = _stage(service, show)
    on_pars = [item for item in on_stage.fixtures.values() if isinstance(item, ParIntent)]
    on_bars = [item for item in on_stage.fixtures.values() if isinstance(item, BarIntent)]
    assert on_pars and on_bars
    assert all(item.intensity > 0.05 for item in on_pars)
    assert all(item.dimmer > 0.05 for item in on_bars)
    orange = on_pars[0].color
    assert orange.r > orange.b
    beats = service.follower.beats_at(service._now)
    assert service.planner.flash_gate(beats, wall_s=service._now) is None
    assert service.planner._series_on is False
    service._now = float(service._now) + FRAME_DT
    off_stage = _stage(service, show)
    off_pars = [item for item in off_stage.fixtures.values() if isinstance(item, ParIntent)]
    off_bars = [item for item in off_stage.fixtures.values() if isinstance(item, BarIntent)]
    # Next frame stays the wash — a riser does not flip the look into strobe-off.
    assert off_pars and off_bars
    assert all(item.intensity > 0.05 for item in off_pars)
    assert all(item.dimmer > 0.05 for item in off_bars)
    assert all(item.strobe == pytest.approx(0.0) for item in off_pars)
    assert service.planner.flash_gate(
        service.follower.beats_at(service._now), wall_s=service._now
    ) is None
    dark = service.look(list(show.patch.fixtures), show.layout.placements, blackout=True)
    assert service.planner.flash_gate(service.follower.beats_at(service._now), wall_s=service._now) is None
    assert all(getattr(item, "strobe", 0.0) == 0.0 for item in dark.fixtures.values())


def test_zero_intensity_is_dark_and_low_level_keeps_the_wave() -> None:
    show = load_show_config()
    from orng_led.audio.auto_show import build_auto_stage

    wave = {
        "look": "vertical_wave",
        "beats": 1.0,
        "cycle_beats": 2.0,
        "placements": show.layout.placements,
        "groups": ("bar",),
        "colour": "orange",
        "cast": "bars",
    }
    dark = build_auto_stage(list(show.patch.fixtures), "orange", "groove", 0.0, 0.0, **wave)
    low = build_auto_stage(list(show.patch.fixtures), "orange", "groove", 0.0, 0.35, **wave)
    full = build_auto_stage(list(show.patch.fixtures), "orange", "groove", 0.0, 1.0, **wave)
    assert dark.fixtures == {} or all(
        (isinstance(item, BarIntent) and item.dimmer == 0.0) for item in dark.fixtures.values()
    )
    low_bars = [item for item in low.fixtures.values() if isinstance(item, BarIntent)]
    full_bars = [item for item in full.fixtures.values() if isinstance(item, BarIntent)]
    assert low_bars and full_bars
    assert [item.segments for item in low_bars] == [item.segments for item in full_bars]
    assert all(item.dimmer == pytest.approx(0.35) for item in low_bars)
    wash0 = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "break",
        0.0,
        0.0,
        look="wash",
        beats=0.0,
        cycle_beats=4.0,
        placements=show.layout.placements,
        groups=("par", "bar"),
        colour="orange",
        cast="all",
    )
    wash_pars = [item for item in wash0.fixtures.values() if isinstance(item, ParIntent)]
    assert wash_pars and all(item.intensity == pytest.approx(0.0) for item in wash_pars)


def test_cascade_up_is_not_compound_sine_and_pulse_by_y_moves() -> None:
    show = load_show_config()
    from orng_led.audio.auto_show import build_auto_stage

    for amount in (0.35, 0.7, 1.0):
        cascade = _dmx_sequence("cascade_up", cycle=4.0, amount=amount)
        compound = _dmx_sequence("compound_sine_cascade", cycle=4.0, amount=amount)
        pulse = _dmx_sequence("pulse_by_y", cycle=4.0, amount=amount)
        assert cascade != compound, amount
        assert len(set(pulse)) > 1, amount
        early = build_auto_stage(
            list(show.patch.fixtures),
            "orange",
            "groove",
            0.0,
            amount,
            look="pulse_by_y",
            beats=0.25,
            cycle_beats=4.0,
            placements=show.layout.placements,
            groups=("bar",),
            colour="orange",
            cast="bars",
        )
        later = build_auto_stage(
            list(show.patch.fixtures),
            "orange",
            "groove",
            0.0,
            amount,
            look="pulse_by_y",
            beats=1.0,
            cycle_beats=4.0,
            placements=show.layout.placements,
            groups=("bar",),
            colour="orange",
            cast="bars",
        )
        segs0 = [item.segments for item in early.fixtures.values() if isinstance(item, BarIntent)]
        segs1 = [item.segments for item in later.fixtures.values() if isinstance(item, BarIntent)]
        assert segs0 and segs1
        assert segs0 != segs1, amount
        assert any(0.0 in row and 1.0 in row for row in segs0), amount
    wave = _dmx_sequence("vertical_wave", cycle=4.0, amount=0.85)
    assert len(set(wave)) > 1
    # Shared canvas: the four bars stay aligned on the unsynchronised wave.

    stage = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.85,
        look="vertical_wave",
        beats=1.0,
        cycle_beats=2.0,
        placements=show.layout.placements,
        groups=("bar",),
        colour="orange",
        cast="bars",
    )
    bars = [stage.fixtures[name] for name in ("bar_1", "bar_2", "bar_3", "bar_4")]
    assert all(isinstance(item, BarIntent) for item in bars)
    assert bars[0].segments == bars[3].segments
