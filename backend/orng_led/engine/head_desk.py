"""Operator joystick for both moving heads.

The stick replaces preset and auto pan/tilt while it is enabled. The center,
where the stick starts, is the highest pose the stick had after up and down
were separated: saved home. The top of the stick rises another 90° from
there, the travel that existed before that split. The bottom is 90° down
from the center, into the room. The heads hang from the ceiling, so
stick-down lowers physical tilt. Full right is 90° toward par 4, the
operator's right on stage.

These heads are 8-bit over 540° of pan and 270° of tilt. The saved min/max
still clamp the result.
"""

from __future__ import annotations

from dataclasses import dataclass

from orng_led.config.models import FixtureInstance, FixtureKind, ShowConfig
from orng_led.engine.beam_transform import inverse_axis, transform_axis
from orng_led.engine.intents import BeamIntent, Rgbw, StageIntent

PAN_CIRCLE_DEG = 540.0
TILT_CIRCLE_DEG = 270.0
_PAN_90 = 90.0 / PAN_CIRCLE_DEG
_TILT_90 = 90.0 / TILT_CIRCLE_DEG
# Joystick right raises physical pan. From the desk that is stage right, toward par 4.
_TOWARD_STAGE_RIGHT = 1.0
# The heads hang from the ceiling, so stick-down lowers physical tilt into the room.
_TILT_DOWN = -1.0

HEAD_COLORS: dict[str, Rgbw | None] = {
    "red": Rgbw(r=1.0, g=0.0, b=0.0),
    "white": Rgbw(r=1.0, g=1.0, b=1.0, w=1.0),
    "violet": Rgbw(r=0.45, g=0.0, b=1.0),
    "blue": Rgbw(r=0.0, g=0.15, b=1.0),
    "cyan": Rgbw(r=0.0, g=1.0, b=1.0),
    "none": None,
}


@dataclass
class HeadDesk:
    enabled: bool = False
    light: bool = False
    scene: bool = True
    color: str = "red"
    x: float = 0.0
    y: float = 0.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _shift(home: float, delta: float, *, offset: float, invert: bool, axis_min: float, axis_max: float) -> float:
    home_physical = transform_axis(
        home, offset=offset, invert=invert, axis_min=axis_min, axis_max=axis_max
    )
    physical = _clamp(home_physical + delta, axis_min, axis_max)
    return inverse_axis(
        physical, offset=offset, invert=invert, axis_min=axis_min, axis_max=axis_max
    )


def pose(fixture: FixtureInstance, x: float, y: float) -> tuple[float, float]:
    """Semantic pan/tilt for one head. ``x`` is -1..1, ``y`` is 0 at the top.

    ``y`` 0.5 is the center and stays at home. Above that, tilt rises by the
    same 90° the stick used before up and down were split. Below it, tilt
    falls into the room.
    """
    spatial = fixture.spatial
    stick_x = _clamp(x, -1.0, 1.0)
    stick_y = _clamp(y, 0.0, 1.0)
    pan = _shift(
        float(spatial.home_pan),
        _TOWARD_STAGE_RIGHT * stick_x * _PAN_90,
        offset=float(spatial.pan_offset),
        invert=bool(spatial.pan_invert),
        axis_min=float(spatial.pan_min),
        axis_max=float(spatial.pan_max),
    )
    tilt = _shift(
        float(spatial.home_tilt),
        _TILT_DOWN * (2.0 * stick_y - 1.0) * _TILT_90,
        offset=float(spatial.tilt_offset),
        invert=bool(spatial.tilt_invert),
        axis_min=float(spatial.tilt_min),
        axis_max=float(spatial.tilt_max),
    )
    return pan, tilt


def apply_head_desk(stage: StageIntent, desk: HeadDesk, show: ShowConfig) -> StageIntent:
    """Force both heads onto the stick. Scene colour stays until the light switch is on."""
    if not desk.enabled:
        return stage
    color_name = desk.color if desk.color in HEAD_COLORS else "red"
    chosen = HEAD_COLORS[color_name]
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        pan, tilt = pose(fixture, desk.x, desk.y)
        current = stage.fixtures.get(fixture.id)
        if not isinstance(current, BeamIntent):
            current = BeamIntent()
        if not desk.light:
            stage.fixtures[fixture.id] = BeamIntent(
                pan=pan,
                tilt=tilt,
                dimmer=current.dimmer,
                color=current.color,
                wheel=current.wheel,
                shutter_open=current.shutter_open,
                strobe=current.strobe,
            )
            continue
        if chosen is None:
            stage.fixtures[fixture.id] = BeamIntent(pan=pan, tilt=tilt, dimmer=0.0, color=Rgbw())
            continue
        dimmer = current.dimmer if desk.scene else 1.0
        stage.fixtures[fixture.id] = BeamIntent(
            pan=pan,
            tilt=tilt,
            dimmer=dimmer,
            color=chosen,
            wheel=current.wheel,
            shutter_open=True,
            strobe=current.strobe if desk.scene else 0.0,
        )
    return stage
