"""Deterministic engine tests: clock, layers, cycle, beam motion."""

from __future__ import annotations

import math

import pytest

from orng_led.config import global_channel, load_show_config
from orng_led.config.models import FixtureKind
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.engine import CYCLE_DURATION_S, EPISODE_DURATION_S, Engine, FakeClock
from orng_led.engine.beam import BeamMotionLimits, BeamMotionState, step_beam
from orng_led.engine.clock import FRAME_DT
from orng_led.engine.presets import cycle_position


def _fixture_channels(show, fixture_id: str) -> range:
    fixture = next(fx for fx in show.patch.fixtures if fx.id == fixture_id)
    profile = show.profiles[fixture.profile_id]
    first = fixture.start_address
    last = fixture.start_address + profile.footprint - 1
    return range(first, last + 1)


def _channel_values(frame: list[int], channels: range) -> list[int]:
    return [frame[ch - 1] for ch in channels]


def test_same_preset_time_same_frame() -> None:
    show = load_show_config()
    engine = Engine(show=show, clock=FakeClock())
    frame_a = engine.frame_at_preset_time(12.5)
    frame_b = engine.frame_at_preset_time(12.5)
    assert frame_a == frame_b
    assert len(frame_a) == DMX_UNIVERSE_SIZE
    assert all(0 <= value <= 255 for value in frame_a)


def test_frame_values_always_in_range_over_cycle() -> None:
    show = load_show_config()
    engine = Engine(show=show, clock=FakeClock())
    for _step in range(0, int(CYCLE_DURATION_S * 2), 17):
        snap = engine.tick(dt_s=FRAME_DT * 3)
        assert len(snap.frame) == DMX_UNIVERSE_SIZE
        assert all(0 <= value <= 255 for value in snap.frame)
        assert snap.episode_index == cycle_position(snap.preset_time_s).episode_index


def test_blackout_zeros_all_channels_but_clock_continues() -> None:
    from orng_led.engine.show_whitelist import assert_lights_dark

    show = load_show_config()
    engine = Engine(show=show, clock=FakeClock())
    engine.tick(dt_s=1.0)
    before_preset_time = engine.preset_elapsed_s
    assert any(value > 0 for value in engine.render_at(engine.clock.time()).frame)

    engine.set_blackout(True)
    black = engine.tick(dt_s=2.0)
    assert_lights_dark(show, black.frame)
    assert engine.preset_elapsed_s == before_preset_time + 2.0

    engine.set_blackout(False)
    restored = engine.tick(dt_s=0.0)
    # After blackout, non-zero look returns at current preset time.
    assert any(value > 0 for value in restored.frame)


def test_white_hit_and_strobe_do_not_affect_face() -> None:
    show = load_show_config()
    engine = Engine(show=show, clock=FakeClock())
    engine.set_face(True, brightness=0.7)
    base = engine.tick(dt_s=0.5)
    face_before_1 = _channel_values(base.frame, _fixture_channels(show, "face_par_1"))
    face_before_2 = _channel_values(base.frame, _fixture_channels(show, "face_par_2"))
    assert any(value > 0 for value in face_before_1)

    engine.trigger_white_hit()
    hit = engine.tick(dt_s=0.05)
    assert _channel_values(hit.frame, _fixture_channels(show, "face_par_1")) == face_before_1
    assert _channel_values(hit.frame, _fixture_channels(show, "face_par_2")) == face_before_2

    # Rear PAR should change under white hit.
    rear = _channel_values(hit.frame, _fixture_channels(show, "par_1"))
    assert any(value > 0 for value in rear)

    engine.strobe_press()
    strobe = engine.tick(dt_s=0.05)
    assert _channel_values(strobe.frame, _fixture_channels(show, "face_par_1")) == face_before_1
    assert _channel_values(strobe.frame, _fixture_channels(show, "face_par_2")) == face_before_2


def test_overlay_does_not_reset_preset_selection_or_clock() -> None:
    show = load_show_config()
    engine = Engine(show=show, clock=FakeClock())
    engine.select_preset("P05")
    engine.tick(dt_s=5.0)
    preset_time = engine.preset_elapsed_s
    preset_id = engine.active_preset_id

    engine.trigger_white_hit()
    engine.strobe_press()
    engine.tick(dt_s=0.1)
    engine.strobe_release()
    engine.tick(dt_s=0.2)

    assert engine.active_preset_id == preset_id
    assert math.isclose(engine.preset_elapsed_s, preset_time + 0.3)


def test_cycle_boundary_wraps_to_episode_zero() -> None:
    show = load_show_config()
    engine = Engine(show=show, clock=FakeClock())
    near_end = CYCLE_DURATION_S - 0.01
    engine.preset_elapsed_s = near_end
    before = engine.render_at(0.0)
    assert before.episode_index == 9

    engine.tick(dt_s=0.02)
    after = engine.render_at(engine.clock.time())
    assert after.episode_index == 0
    assert after.preset_time_s < EPISODE_DURATION_S


def test_beam_motion_is_speed_limited() -> None:
    limits = BeamMotionLimits(max_pan_speed=0.2, max_tilt_speed=0.1)
    state = BeamMotionState(pan=0.0, tilt=0.0)
    step_beam(state, target_pan=1.0, target_tilt=1.0, dt_s=0.5, limits=limits)
    assert math.isclose(state.pan, 0.1)  # 0.2 * 0.5
    assert math.isclose(state.tilt, 0.05)  # 0.1 * 0.5
    assert state.pan < 1.0
    assert state.tilt < 1.0


def test_engine_beam_does_not_teleport() -> None:
    show = load_show_config()
    engine = Engine(
        show=show,
        clock=FakeClock(),
        beam_limits=BeamMotionLimits(max_pan_speed=0.25, max_tilt_speed=0.25),
    )
    left = next(fx for fx in show.patch.fixtures if fx.id == "beam_left")
    profile = show.profiles[left.profile_id]
    pan_channels = [ch for ch in profile.channels if ch.role.value == "pan_coarse"]
    if not pan_channels:
        pytest.skip("Beam pan not mapped yet — assign via «Налаштування каналів»")
    pan_local = pan_channels[0].local
    pan_global = global_channel(left.start_address, pan_local)

    # Force a large target jump by rendering far-apart times with small dt.
    engine.preset_elapsed_s = 0.0
    first = engine.tick(dt_s=FRAME_DT)
    first_pan = first.frame[pan_global - 1]

    # Jump preset time abruptly but only allow one short motion step.
    engine.preset_elapsed_s = CYCLE_DURATION_S / 2
    second = engine.tick(dt_s=FRAME_DT)
    second_pan = second.frame[pan_global - 1]

    # Coarse byte cannot jump the full 0..255 range in one limited step.
    assert abs(second_pan - first_pan) < 80


def test_face_only_in_face_layer() -> None:
    show = load_show_config()
    engine = Engine(show=show, clock=FakeClock())
    engine.set_face(False)
    off = engine.tick(dt_s=1.0)
    assert all(
        value == 0 for value in _channel_values(off.frame, _fixture_channels(show, "face_par_1"))
    )

    engine.set_face(True, brightness=1.0)
    on = engine.tick(dt_s=0.0)
    assert any(
        value > 0 for value in _channel_values(on.frame, _fixture_channels(show, "face_par_1"))
    )


def test_live_fx_compose_over_blackout() -> None:
    """Blackout zeroes the preset base; Live Effects still render on top."""
    show = load_show_config()
    engine = Engine(show=show, clock=FakeClock())
    engine.set_face(True, brightness=1.0)
    engine.strobe_press()
    engine.set_blackout(True)
    snap = engine.tick(dt_s=0.1)
    assert snap.blackout is True
    assert engine.overlays.face_on is True
    assert engine.overlays.strobe_held is True
    assert any(value > 0 for value in snap.frame)


def test_kinds_cover_twelve_fixtures() -> None:
    show = load_show_config()
    kinds = {fx.kind for fx in show.patch.fixtures}
    assert kinds == {
        FixtureKind.PAR,
        FixtureKind.BAR,
        FixtureKind.BEAM,
        FixtureKind.FACE_PAR,
    }
    assert len(show.patch.fixtures) == 12
