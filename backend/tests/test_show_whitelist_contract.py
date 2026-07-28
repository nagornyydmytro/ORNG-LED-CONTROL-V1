"""Show-mode whitelist, Master dimmer-only, Beam home continuity."""

from __future__ import annotations

import math

from orng_led.config import default_config_dir, load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.engine import Engine, FakeClock
from orng_led.engine.intents import BarIntent, Rgbw, StageIntent
from orng_led.engine.renderer import render_stage, safe_dark_frame
from orng_led.engine.show_whitelist import (
    assert_lights_dark,
    assert_show_frame_clean,
    forbidden_nonzero_channels,
)
from orng_led.presets.store import PresetStore

PRESET_IDS = tuple(f"P{i:02d}" for i in range(1, 11))


def _programs() -> dict:
    return PresetStore.load(default_config_dir() / "presets").programs()


def _engine(preset_id: str = "P05") -> Engine:
    show = load_show_config()
    engine = Engine(show=show, presets=_programs(), active_preset_id=preset_id, clock=FakeClock())
    engine.set_blackout(False)
    return engine


def _role_value(show, frame, fixture_id: str, role: ChannelRole) -> int | None:
    fixture = next(fx for fx in show.patch.fixtures if fx.id == fixture_id)
    profile = show.profile_for(fixture)
    for channel in profile.channels:
        if channel.role is role:
            return frame[global_channel(fixture.start_address, channel.local) - 1]
    return None


def _beam_axis_pair(show, frame, fixture_id: str) -> tuple[int | None, int | None]:
    return (
        _role_value(show, frame, fixture_id, ChannelRole.PAN_COARSE),
        _role_value(show, frame, fixture_id, ChannelRole.TILT_COARSE),
    )


def test_p01_p10_full_cycle_whitelist_clean() -> None:
    show = load_show_config()
    for preset_id in PRESET_IDS:
        engine = _engine(preset_id)
        # Sample across the full 180s cycle.
        for _step in range(0, 180, 3):
            snap = engine.tick(dt_s=3.0, wall_dt_s=3.0)
            assert_show_frame_clean(show, snap.frame)
            assert not forbidden_nonzero_channels(show, snap.frame)


def test_live_effects_whitelist_clean() -> None:
    show = load_show_config()
    engine = _engine("P05")
    engine.tick(dt_s=1.0, wall_dt_s=1.0)

    engine.trigger_white_hit()
    assert_show_frame_clean(show, engine.tick(dt_s=0.02, wall_dt_s=0.02).frame)

    engine.trigger_color_hit(Rgbw(r=1, g=0, b=0))
    assert_show_frame_clean(show, engine.tick(dt_s=0.02, wall_dt_s=0.02).frame)

    engine.trigger_sweep_hit(Rgbw(r=0, g=0, b=1))
    assert_show_frame_clean(show, engine.tick(dt_s=0.05, wall_dt_s=0.05).frame)

    engine.strobe_press()
    assert_show_frame_clean(show, engine.tick(dt_s=0.05, wall_dt_s=0.05).frame)
    engine.strobe_release()

    engine.drop_press()
    snap = engine.tick(dt_s=0.05, wall_dt_s=0.05)
    assert_show_frame_clean(show, snap.frame)
    engine.drop_release()

    engine.set_face(True, brightness=0.7)
    assert_show_frame_clean(show, engine.tick(dt_s=0.05, wall_dt_s=0.05).frame)

    engine.set_face(False)
    engine.release_momentary()
    # Expire any timed hits by advancing past their windows.
    engine.tick(dt_s=2.0, wall_dt_s=2.0)
    engine.set_blackout(True)
    dark = engine.tick(dt_s=0.05, wall_dt_s=0.05)
    assert_show_frame_clean(show, dark.frame)
    assert_lights_dark(show, dark.frame)


def test_master_scales_dimmer_only() -> None:
    show = load_show_config()
    engine = _engine("P05")
    engine.set_master_brightness(1.0)
    engine.set_face(True, brightness=1.0)
    full = engine.tick(dt_s=1.0, wall_dt_s=1.0).frame

    engine.set_master_brightness(0.4)
    dim = engine.tick(dt_s=0.0, wall_dt_s=0.0).frame

    # Pick a rear PAR with dimmer + red.
    par = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.PAR)
    d_full = _role_value(show, full, par.id, ChannelRole.DIMMER)
    d_dim = _role_value(show, dim, par.id, ChannelRole.DIMMER)
    r_full = _role_value(show, full, par.id, ChannelRole.RED)
    r_dim = _role_value(show, dim, par.id, ChannelRole.RED)
    assert d_full is not None and d_dim is not None
    assert d_full > 0
    assert abs(d_dim / d_full - 0.4) < 0.08
    # Colour channels must not be multiplied by Master.
    if r_full and r_full > 10:
        assert r_dim == r_full

    face = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.FACE_PAR)
    fd_full = _role_value(show, full, face.id, ChannelRole.DIMMER)
    fd_dim = _role_value(show, dim, face.id, ChannelRole.DIMMER)
    assert fd_full and fd_dim
    assert abs(fd_dim / fd_full - 0.4) < 0.08


def test_bar_segment_and_whole_modes_exclusive() -> None:
    show = load_show_config()
    bar = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.BAR)
    profile = show.profile_for(bar)
    color = Rgbw(r=1, g=0.2, b=0)

    seg_stage = StageIntent(
        fixtures={
            bar.id: BarIntent(
                segments=(1, 0, 1, 0, 1, 0, 1, 0), dimmer=1.0, color=color, whole=False
            )
        }
    )
    whole_stage = StageIntent(
        fixtures={bar.id: BarIntent(segments=(0,) * 8, dimmer=1.0, color=color, whole=True)}
    )
    motion: dict[str, BeamMotionState] = {}
    seg_frame = render_stage(show, seg_stage, motion, master=1.0)
    whole_frame = render_stage(show, whole_stage, motion, master=1.0)

    for channel in profile.channels:
        idx = global_channel(bar.start_address, channel.local) - 1
        if channel.role is ChannelRole.WHOLE_COLOR:
            assert seg_frame[idx] == 0
            assert whole_frame[idx] > 0
        if channel.role is ChannelRole.SEGMENT_COLOR and channel.segment_index is not None:
            # Lit odd segments in segment mode; all zero in whole mode.
            if channel.segment_index % 2 == 1:
                assert seg_frame[idx] > 0
            assert whole_frame[idx] == 0
        if channel.role is ChannelRole.FIXED and channel.fixed_value is not None:
            assert seg_frame[idx] == channel.fixed_value
            assert whole_frame[idx] == channel.fixed_value
        if channel.role is ChannelRole.PROGRAM:
            assert seg_frame[idx] == 0
            assert whole_frame[idx] == 0


def test_beam_uses_saved_home_not_zero() -> None:
    show = load_show_config()
    engine = Engine(show=show, presets=_programs(), active_preset_id="P05", clock=FakeClock())
    engine.select_preset("NONE")
    engine.set_blackout(False)
    snap = engine.tick(dt_s=0.0, wall_dt_s=0.0)
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        assert math.isclose(engine.beam_motion[fixture.id].pan, float(fixture.spatial.home_pan))
        assert math.isclose(engine.beam_motion[fixture.id].tilt, float(fixture.spatial.home_tilt))
        pan, tilt = _beam_axis_pair(show, snap.frame, fixture.id)
        assert pan is not None and tilt is not None
        # Encoded home must not be the physical 0/0 DMX park for calibrated heads.
        assert not (pan == 0 and tilt == 0)


def test_blackout_keeps_beam_axes() -> None:
    show = load_show_config()
    engine = _engine("P05")
    engine.tick(dt_s=2.0, wall_dt_s=2.0)
    before = {
        fx.id: _beam_axis_pair(show, engine.render_at(engine.clock.time()).frame, fx.id)
        for fx in show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }
    engine.set_blackout(True)
    snap = engine.tick(dt_s=0.1, wall_dt_s=0.1)
    assert_lights_dark(show, snap.frame)
    for fixture_id, axes in before.items():
        assert _beam_axis_pair(show, snap.frame, fixture_id) == axes


def test_none_and_preset_switch_no_zero_axes() -> None:
    show = load_show_config()
    engine = _engine("P03")
    engine.tick(dt_s=5.0, wall_dt_s=5.0)
    mid = {
        fx.id: (engine.beam_motion[fx.id].pan, engine.beam_motion[fx.id].tilt)
        for fx in show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }
    # Switch without resetting motion.
    engine.select_preset("P08", reset_clock=True)
    snap = engine.tick(dt_s=0.0, wall_dt_s=0.0)
    for fixture_id, (pan, tilt) in mid.items():
        assert math.isclose(engine.beam_motion[fixture_id].pan, pan)
        assert math.isclose(engine.beam_motion[fixture_id].tilt, tilt)
        wire_pan, wire_tilt = _beam_axis_pair(show, snap.frame, fixture_id)
        assert not (wire_pan == 0 and wire_tilt == 0)

    engine.select_preset("NONE")
    none_snap = engine.tick(dt_s=0.1, wall_dt_s=0.1)
    for fixture_id, (pan, tilt) in mid.items():
        assert math.isclose(engine.beam_motion[fixture_id].pan, pan, abs_tol=0.02)
        assert math.isclose(engine.beam_motion[fixture_id].tilt, tilt, abs_tol=0.02)
        wire_pan, wire_tilt = _beam_axis_pair(show, none_snap.frame, fixture_id)
        assert not (wire_pan == 0 and wire_tilt == 0)


def test_continuous_preset_transition_from_current_pose() -> None:
    show = load_show_config()
    engine = _engine("P02")
    # Drive beams away from home.
    for _ in range(60):
        engine.tick(dt_s=0.25, wall_dt_s=0.25)
    start = {
        fx.id: (engine.beam_motion[fx.id].pan, engine.beam_motion[fx.id].tilt)
        for fx in show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }
    engine.select_preset("P09", reset_clock=True)
    # First frames must leave from start pose (no jump to 0/0 or home).
    for _ in range(3):
        engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
        for fixture_id, (pan, tilt) in start.items():
            assert abs(engine.beam_motion[fixture_id].pan - pan) < 0.25
            assert abs(engine.beam_motion[fixture_id].tilt - tilt) < 0.25
            assert not (
                engine.beam_motion[fixture_id].pan == 0.0
                and engine.beam_motion[fixture_id].tilt == 0.0
            )


def test_beam_respects_min_max() -> None:
    show = load_show_config()
    from orng_led.engine.beam_transform import semantic_to_physical

    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        for pan in (0.0, 0.25, 0.5, 0.75, 1.0):
            for tilt in (0.0, 0.5, 1.0):
                phys_pan, phys_tilt = semantic_to_physical(fixture.spatial, pan=pan, tilt=tilt)
                assert fixture.spatial.pan_min - 1e-9 <= phys_pan <= fixture.spatial.pan_max + 1e-9
                assert (
                    fixture.spatial.tilt_min - 1e-9 <= phys_tilt <= fixture.spatial.tilt_max + 1e-9
                )


def test_safe_dark_keeps_home_axes() -> None:
    show = load_show_config()
    motion = {
        fx.id: BeamMotionState(pan=float(fx.spatial.home_pan), tilt=float(fx.spatial.home_tilt))
        for fx in show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }
    frame = safe_dark_frame(show, motion)
    assert_lights_dark(show, frame)
    assert_show_frame_clean(show, frame)
    for fixture_id in motion:
        pan, tilt = _beam_axis_pair(show, frame, fixture_id)
        assert not (pan == 0 and tilt == 0)


def test_scrub_zeros_forbidden_roles() -> None:
    show = load_show_config()
    frame = [0] * 512
    # Force a forbidden program channel high, then scrub via render_stage path.
    bar = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.BAR)
    profile = show.profile_for(bar)
    program = next((ch for ch in profile.channels if ch.role is ChannelRole.PROGRAM), None)
    if program is None:
        # Mark an unused channel temporarily — scrub uses profile roles.
        unused = next(ch for ch in profile.channels if ch.role is ChannelRole.UNUSED)
        idx = global_channel(bar.start_address, unused.local) - 1
        frame[idx] = 200
        from orng_led.engine.show_whitelist import scrub_show_frame

        scrub_show_frame(show, frame)
        assert frame[idx] == 0
    else:
        idx = global_channel(bar.start_address, program.local) - 1
        frame[idx] = 200
        from orng_led.engine.show_whitelist import scrub_show_frame

        scrub_show_frame(show, frame)
        assert frame[idx] == 0


def test_mapping_fixed_value_used_not_hardcoded() -> None:
    show = load_show_config()
    bar = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.BAR)
    profile = show.profile_for(bar)
    fixed = next(ch for ch in profile.channels if ch.role is ChannelRole.FIXED)
    assert fixed.fixed_value is not None
    stage = StageIntent(
        fixtures={bar.id: BarIntent(dimmer=0.5, color=Rgbw(r=1, g=0, b=0), whole=True)}
    )
    frame = render_stage(show, stage, {}, master=1.0)
    idx = global_channel(bar.start_address, fixed.local) - 1
    assert frame[idx] == int(fixed.fixed_value)
