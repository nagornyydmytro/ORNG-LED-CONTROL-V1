"""Live Effects cover all fixtures; Beam last_valid hold (no auto-home)."""

from __future__ import annotations

import math

from orng_led.config import default_config_dir, load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.beam_transform import semantic_to_physical
from orng_led.engine.engine import Engine, FakeClock
from orng_led.engine.intents import Rgbw, StageIntent
from orng_led.engine.layers import apply_color_hit, apply_strobe, apply_white_hit
from orng_led.presets.store import PresetStore

PRESET_IDS = tuple(f"P{i:02d}" for i in range(1, 11))


def _programs() -> dict:
    return PresetStore.load(default_config_dir() / "presets").programs()


def _engine(preset_id: str = "P05") -> Engine:
    show = load_show_config()
    engine = Engine(show=show, presets=_programs(), active_preset_id=preset_id, clock=FakeClock())
    engine.set_blackout(False)
    return engine


def _beam_poses(engine: Engine) -> dict[str, tuple[float, float]]:
    return {
        fx.id: (engine.beam_motion[fx.id].pan, engine.beam_motion[fx.id].tilt)
        for fx in engine.show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }


def _beam_last_valid(engine: Engine) -> dict[str, tuple[float, float]]:
    return {
        fx.id: (
            engine.beam_motion[fx.id].last_valid_pan,
            engine.beam_motion[fx.id].last_valid_tilt,
        )
        for fx in engine.show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }


def _assert_poses_unchanged(
    before: dict[str, tuple[float, float]], after: dict[str, tuple[float, float]]
) -> None:
    assert before.keys() == after.keys()
    for fixture_id, (pan, tilt) in before.items():
        assert math.isclose(after[fixture_id][0], pan, abs_tol=1e-9), fixture_id
        assert math.isclose(after[fixture_id][1], tilt, abs_tol=1e-9), fixture_id


def _role_value(show, frame, fixture_id: str, role: ChannelRole) -> int | None:
    fixture = next(fx for fx in show.patch.fixtures if fx.id == fixture_id)
    profile = show.profile_for(fixture)
    for channel in profile.channels:
        if channel.role is role:
            return frame[global_channel(fixture.start_address, channel.local) - 1]
    return None


def _physical_in_range(engine: Engine) -> None:
    for fixture in engine.show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        state = engine.beam_motion[fixture.id]
        phys_pan, phys_tilt = semantic_to_physical(
            fixture.spatial, pan=state.last_valid_pan, tilt=state.last_valid_tilt
        )
        assert fixture.spatial.pan_min - 1e-9 <= phys_pan <= fixture.spatial.pan_max + 1e-9
        assert fixture.spatial.tilt_min - 1e-9 <= phys_tilt <= fixture.spatial.tilt_max + 1e-9


def test_live_effect_lights_fixture_absent_from_episode() -> None:
    """White Hit must light rear fixtures even when the pad base has none (NONE)."""
    engine = _engine("P01")
    engine.select_preset("NONE")
    engine.tick(dt_s=0.0, wall_dt_s=0.0)
    base = engine.tick(dt_s=0.0, wall_dt_s=0.0)
    assert _role_value(engine.show, base.frame, "bar_1", ChannelRole.DIMMER) in (None, 0)
    assert _role_value(engine.show, base.frame, "par_1", ChannelRole.DIMMER) in (None, 0)

    engine.trigger_white_hit()
    snap = engine.tick(dt_s=0.02, wall_dt_s=0.02)
    for fixture_id in ("par_1", "bar_1"):
        dimmer = _role_value(engine.show, snap.frame, fixture_id, ChannelRole.DIMMER)
        assert dimmer is not None and dimmer > 200, fixture_id
    # Beams have no Master Dimmer mapping — full bright via FIXED + RGB.
    for fixture_id in ("beam_left", "beam_right"):
        fixed = _role_value(engine.show, snap.frame, fixture_id, ChannelRole.FIXED)
        red = _role_value(engine.show, snap.frame, fixture_id, ChannelRole.RED)
        assert fixed == 255, fixture_id
        assert red is not None and red > 200, fixture_id


def test_live_effects_apply_to_all_configured_rear_fixtures() -> None:
    show = load_show_config()
    stage = apply_white_hit(StageIntent(), show)
    rear = [fx for fx in show.patch.fixtures if fx.kind is not FixtureKind.FACE_PAR]
    assert set(stage.fixtures) == {fx.id for fx in rear}
    for fixture in rear:
        intent = stage.fixtures[fixture.id]
        if fixture.kind is FixtureKind.BEAM:
            assert intent.pan is None and intent.tilt is None  # type: ignore[union-attr]
            assert intent.dimmer > 0.9  # type: ignore[union-attr]
        elif fixture.kind is FixtureKind.PAR:
            assert intent.intensity > 0.9  # type: ignore[union-attr]
        elif fixture.kind is FixtureKind.BAR:
            assert intent.dimmer > 0.9  # type: ignore[union-attr]


def test_parking_live_effects_never_move_beam_axes() -> None:
    """White/Color Hit and Drop park axes; Strobe/Sweep keep episode motion."""
    engine = _engine("P05")
    # Drive beams away from home so hold is observable.
    for _ in range(40):
        engine.tick(dt_s=0.25, wall_dt_s=0.25)
    before = _beam_poses(engine)
    assert any(
        not math.isclose(pan, engine.show.patch.fixture(fid).spatial.home_pan, abs_tol=0.02)
        or not math.isclose(tilt, engine.show.patch.fixture(fid).spatial.home_tilt, abs_tol=0.02)
        for fid, (pan, tilt) in before.items()
    )

    parking = [
        lambda: engine.trigger_white_hit(),
        lambda: engine.trigger_color_hit(Rgbw(r=1, g=0, b=0)),
        lambda: engine.drop_press(),
    ]
    for trigger in parking:
        snap_before = _beam_poses(engine)
        trigger()
        for _ in range(5):
            engine.tick(dt_s=0.02, wall_dt_s=0.02)
            _assert_poses_unchanged(snap_before, _beam_poses(engine))
        engine.drop_release()
        # Expire timed hits; motion may resume afterward from the frozen pose.
        engine.tick(dt_s=2.0, wall_dt_s=2.0)


def test_strobe_and_sweep_keep_episode_beam_motion() -> None:
    engine = _engine("P05")
    for _ in range(20):
        engine.tick(dt_s=0.25, wall_dt_s=0.25)

    engine.strobe_press()
    before = _beam_poses(engine)
    for _ in range(12):
        engine.tick(dt_s=0.25, wall_dt_s=0.25)
    after_strobe = _beam_poses(engine)
    assert after_strobe != before
    engine.strobe_release()

    engine.trigger_sweep_hit(Rgbw(r=0, g=0, b=1))
    before_sweep = _beam_poses(engine)
    for _ in range(8):
        engine.tick(dt_s=0.05, wall_dt_s=0.05)
    after_sweep = _beam_poses(engine)
    assert after_sweep != before_sweep


def test_missing_beam_in_episode_holds_last_valid_not_home() -> None:
    engine = _engine("P05")
    for _ in range(50):
        engine.tick(dt_s=0.2, wall_dt_s=0.2)
    held = _beam_last_valid(engine)
    homes = {
        fx.id: (float(fx.spatial.home_pan), float(fx.spatial.home_tilt))
        for fx in engine.show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }
    # Episode boundaries can still blend beams from the neighbour card — use
    # NONE so the hold path is tested without transition residue.
    engine.select_preset("NONE")
    for _ in range(30):
        engine.tick(dt_s=0.5, wall_dt_s=0.5)
        now = _beam_last_valid(engine)
        _assert_poses_unchanged(held, now)
        for fid, (pan, tilt) in now.items():
            # Must not drift back to home.
            if not (
                math.isclose(held[fid][0], homes[fid][0], abs_tol=1e-6)
                and math.isclose(held[fid][1], homes[fid][1], abs_tol=1e-6)
            ):
                assert not (
                    math.isclose(pan, homes[fid][0], abs_tol=0.01)
                    and math.isclose(tilt, homes[fid][1], abs_tol=0.01)
                ), f"{fid} drifted toward home"


def test_beam_returns_in_later_episode_from_last_valid() -> None:
    engine = _engine("P05")
    for _ in range(40):
        engine.tick(dt_s=0.25, wall_dt_s=0.25)
    parked = _beam_poses(engine)
    engine.select_preset("NONE")
    for _ in range(10):
        engine.tick(dt_s=0.2, wall_dt_s=0.2)
    _assert_poses_unchanged(parked, _beam_poses(engine))
    engine.select_preset("P08", reset_clock=True)
    # First ticks must leave from parked pose (continuous), not home/zero.
    engine.tick(dt_s=0.0, wall_dt_s=0.0)
    _assert_poses_unchanged(parked, _beam_poses(engine))
    engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
    for fid, (pan, tilt) in parked.items():
        assert abs(engine.beam_motion[fid].pan - pan) < 0.2
        assert abs(engine.beam_motion[fid].tilt - tilt) < 0.2


def test_none_blackout_preset_switch_calib_end_no_zero_or_forced_home() -> None:
    engine = _engine("P03")
    for _ in range(30):
        engine.tick(dt_s=0.3, wall_dt_s=0.3)
    parked = _beam_poses(engine)
    homes = {
        fx.id: (float(fx.spatial.home_pan), float(fx.spatial.home_tilt))
        for fx in engine.show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }

    engine.set_blackout(True)
    engine.tick(dt_s=0.1, wall_dt_s=0.1)
    _assert_poses_unchanged(parked, _beam_poses(engine))
    engine.set_blackout(False)

    engine.select_preset("NONE")
    engine.tick(dt_s=0.1, wall_dt_s=0.1)
    _assert_poses_unchanged(parked, _beam_poses(engine))

    engine.select_preset("P07", reset_clock=True)
    engine.tick(dt_s=0.0, wall_dt_s=0.0)
    _assert_poses_unchanged(parked, _beam_poses(engine))

    # Calib-end style retain under NONE: session pose held, never forced home.
    engine.select_preset("NONE")
    fid = next(iter(parked))
    engine.beam_motion[fid] = BeamMotionState(
        pan=0.41, tilt=0.62, last_valid_pan=0.41, last_valid_tilt=0.62
    )
    engine.tick(dt_s=0.5, wall_dt_s=0.5)
    assert math.isclose(engine.beam_motion[fid].last_valid_pan, 0.41, abs_tol=1e-6)
    assert math.isclose(engine.beam_motion[fid].last_valid_tilt, 0.62, abs_tol=1e-6)
    assert not (
        math.isclose(engine.beam_motion[fid].pan, homes[fid][0], abs_tol=0.01)
        and math.isclose(engine.beam_motion[fid].tilt, homes[fid][1], abs_tol=0.01)
    )


def test_full_cycle_and_live_fx_respect_min_max() -> None:
    for preset_id in PRESET_IDS:
        engine = _engine(preset_id)
        for _ in range(0, 60):
            engine.tick(dt_s=3.0, wall_dt_s=3.0)
            _physical_in_range(engine)
        engine.trigger_white_hit()
        engine.tick(dt_s=0.05, wall_dt_s=0.05)
        _physical_in_range(engine)
        engine.trigger_sweep_hit()
        engine.tick(dt_s=0.1, wall_dt_s=0.1)
        _physical_in_range(engine)


def test_live_effect_start_end_no_single_frame_axis_jump() -> None:
    engine = _engine("P06")
    for _ in range(35):
        engine.tick(dt_s=0.25, wall_dt_s=0.25)
    before = _beam_poses(engine)
    engine.trigger_white_hit()
    # Immediate tick at FX start — no single-frame jump.
    engine.tick(dt_s=0.0, wall_dt_s=0.0)
    _assert_poses_unchanged(before, _beam_poses(engine))
    engine.tick(dt_s=0.05, wall_dt_s=0.05)
    _assert_poses_unchanged(before, _beam_poses(engine))
    # Step through the white-hit window; axes stay frozen.
    for _ in range(12):
        engine.tick(dt_s=0.02, wall_dt_s=0.02)
        _assert_poses_unchanged(before, _beam_poses(engine))
    # First frame after expiry is still continuous (no home/zero jump).
    engine.tick(dt_s=0.0, wall_dt_s=0.0)
    _assert_poses_unchanged(before, _beam_poses(engine))
    for _fid, (pan, tilt) in before.items():
        assert not (pan == 0.0 and tilt == 0.0)


def test_after_live_effect_episode_light_restores_position_continuous() -> None:
    engine = _engine("P05")
    engine.tick(dt_s=2.0, wall_dt_s=2.0)
    before_hit = engine.tick(dt_s=0.0, wall_dt_s=0.0)
    dimmer_before = _role_value(engine.show, before_hit.frame, "par_1", ChannelRole.DIMMER)
    poses = _beam_poses(engine)

    engine.trigger_white_hit()
    during = engine.tick(dt_s=0.02, wall_dt_s=0.02)
    dimmer_during = _role_value(engine.show, during.frame, "par_1", ChannelRole.DIMMER)
    assert dimmer_during is not None and dimmer_during >= (dimmer_before or 0)
    _assert_poses_unchanged(poses, _beam_poses(engine))

    # Stay inside the white-hit window after the initial 0.02s tick (0.18s total).
    for _ in range(7):
        assert engine.overlays.white_hit_until is not None
        engine.tick(dt_s=0.02, wall_dt_s=0.02)
        _assert_poses_unchanged(poses, _beam_poses(engine))
    # Cross expiry with motion dt=0 so axes cannot step.
    while engine.overlays.white_hit_until is not None:
        engine.tick(dt_s=0.0, wall_dt_s=0.02)
        _assert_poses_unchanged(poses, _beam_poses(engine))
    after = engine.tick(dt_s=0.0, wall_dt_s=0.0)
    _assert_poses_unchanged(poses, _beam_poses(engine))
    assert after.white_hit_active is False


def test_color_hit_and_strobe_intents_hold_beam_axes() -> None:
    from orng_led.engine.intents import BeamIntent

    show = load_show_config()
    stage = apply_color_hit(StageIntent(), show, Rgbw(r=0, g=1, b=0))
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            intent = stage.fixtures[fixture.id]
            assert intent.pan is None and intent.tilt is None  # type: ignore[union-attr]
    # Empty stage: strobe invents no axes.
    strobe = apply_strobe(StageIntent(), show, now=0.0, speed=0.7)
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            intent = strobe.fixtures[fixture.id]
            assert intent.pan is None and intent.tilt is None  # type: ignore[union-attr]
    # Episode look present: strobe preserves pan/tilt.
    base = StageIntent(
        fixtures={
            fx.id: BeamIntent(pan=0.3, tilt=0.7, dimmer=0.5, color=Rgbw(r=1), shutter_open=True)
            for fx in show.patch.fixtures
            if fx.kind is FixtureKind.BEAM
        }
    )
    strobe_over_episode = apply_strobe(base, show, now=0.0, speed=0.7)
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            intent = strobe_over_episode.fixtures[fixture.id]
            assert intent.pan == 0.3 and intent.tilt == 0.7  # type: ignore[union-attr]
