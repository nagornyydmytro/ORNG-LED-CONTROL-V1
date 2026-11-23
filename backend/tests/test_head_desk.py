"""Head desk stick: home is the center, and it replaces the scene's pan."""

from __future__ import annotations

import pytest

from orng_led.config import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam_transform import semantic_to_physical
from orng_led.engine.engine import Engine
from orng_led.engine.head_desk import _PAN_90, _TILT_90, pose
from orng_led.engine.intents import BeamIntent, Rgbw, StageIntent


def _beam(show, ident: str):
    return next(fixture for fixture in show.patch.fixtures if fixture.id == ident)


def test_center_of_the_stick_is_the_previous_top() -> None:
    show = load_show_config()
    fixture = _beam(show, "beam_left")
    pan, tilt = pose(fixture, 0, 0.5)
    assert pan == fixture.spatial.home_pan
    assert tilt == fixture.spatial.home_tilt


def test_top_of_the_stick_restores_the_old_upward_travel() -> None:
    show = load_show_config()
    fixture = _beam(show, "beam_left")
    _, home_tilt = semantic_to_physical(
        fixture.spatial,
        pan=float(fixture.spatial.home_pan),
        tilt=float(fixture.spatial.home_tilt),
    )
    pan, tilt = pose(fixture, 0, 0)
    _, moved_tilt = semantic_to_physical(fixture.spatial, pan=pan, tilt=tilt)
    assert pan == pytest.approx(fixture.spatial.home_pan)
    assert moved_tilt == pytest.approx(min(fixture.spatial.tilt_max, home_tilt + _TILT_90))
    assert moved_tilt > home_tilt


def test_right_moves_toward_par_four_and_stays_inside_calibration() -> None:
    show = load_show_config()
    fixture = _beam(show, "beam_left")
    home_pan, home_tilt = semantic_to_physical(
        fixture.spatial,
        pan=float(fixture.spatial.home_pan),
        tilt=float(fixture.spatial.home_tilt),
    )
    pan, tilt = pose(fixture, 1, 1)
    moved_pan, moved_tilt = semantic_to_physical(fixture.spatial, pan=pan, tilt=tilt)
    assert moved_pan == pytest.approx(min(fixture.spatial.pan_max, home_pan + _PAN_90))
    assert moved_pan > home_pan
    assert moved_tilt == pytest.approx(max(fixture.spatial.tilt_min, home_tilt - _TILT_90))
    assert moved_tilt < home_tilt
    left_pan, _ = pose(fixture, -1, 0)
    moved_left, _ = semantic_to_physical(fixture.spatial, pan=left_pan, tilt=float(fixture.spatial.home_tilt))
    assert moved_left == pytest.approx(max(fixture.spatial.pan_min, home_pan - _PAN_90))
    assert moved_left < home_pan


def test_enabled_desk_ignores_the_scene_pan() -> None:
    show = load_show_config()
    beams = {
        fixture.id: BeamIntent(pan=0.99, tilt=0.99, dimmer=0.2, color=Rgbw(r=0, g=1, b=0))
        for fixture in show.patch.fixtures
        if fixture.kind is FixtureKind.BEAM
    }
    engine = Engine(show=show, auto_base=StageIntent(fixtures=beams))
    engine.set_head_desk(enabled=True, light=True, scene=False, color="red", x=0, y=0.5)
    engine.tick(dt_s=0.05)
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        motion = engine.beam_motion[fixture.id]
        assert motion.pan == pytest.approx(fixture.spatial.home_pan)
        assert motion.tilt == pytest.approx(fixture.spatial.home_tilt)
        assert motion.pan != pytest.approx(0.99)
