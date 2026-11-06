"""Limited Auto head paths. Live output stays off until a later safe check.

``HEAD_PATHS_LIVE`` stays false. A preset span is a software limit taken from
the motion the current scene's presets already command. It is not proof that
a new trajectory is physically safe, and it does not widen calibration.

Zeros, home, inversion and the DMX conversion stay in the existing beam
transform. This module writes semantic pan/tilt once. It does not invert.
"""

from __future__ import annotations

import math

from orng_led.config.models import FixtureKind
from orng_led.engine.intents import BeamIntent, StageIntent

# Live paths run only while blackout is off. Blackout keeps pan/tilt out of the
# frame, because a blackout frame still forwards axis channels.
HEAD_PATHS_LIVE = True
# Wide preset formulas in presets/program.py. Glow (±0.16 / ±0.08) sits inside.
# Left heads use ``1 - sweep``, so the span already includes both sides of 0.5.
PRESET_PAN = (0.5 - 0.42, 0.5 + 0.42)
PRESET_TILT = (0.44 - 0.20, 0.44 + 0.20)
BOUNDS_ORIGIN = "preset_wide_sweep_0.42_tilt_0.20_intersect_calibration"
_MAX_PAN_PER_BEAT = 0.04
_MAX_TILT_PER_BEAT = 0.025
_MAX_PAN_ACCEL = 0.02
_MAX_TILT_ACCEL = 0.012
_state: dict[str, tuple[float, float, float, float, float]] = {}


def reset() -> None:
    _state.clear()


def bounds_for(fixture: object) -> dict[str, object]:
    """Reachable preset extrema ∩ calibration. Missing data leaves the axis still."""
    spatial = getattr(fixture, "spatial", None)
    if spatial is None:
        return {
            "pan": _still("pan:no_spatial"),
            "tilt": _still("tilt:no_spatial"),
            "origin": BOUNDS_ORIGIN,
        }
    return {
        "pan": _axis(PRESET_PAN, spatial.pan_min, spatial.pan_max, spatial.home_pan, "pan"),
        "tilt": _axis(PRESET_TILT, spatial.tilt_min, spatial.tilt_max, spatial.home_tilt, "tilt"),
        "origin": BOUNDS_ORIGIN,
        "max_pan_speed": float(spatial.max_pan_speed),
        "max_tilt_speed": float(spatial.max_tilt_speed),
    }


def target(
    beats: float,
    side: float,
    section: str,
    *,
    look: str = "",
    pan_span: tuple[float, float] | None = None,
    tilt_span: tuple[float, float] | None = None,
    beat_period_s: float = 0.5,
    max_pan_speed: float = 0.35,
    max_tilt_speed: float = 0.25,
) -> tuple[float | None, float | None]:
    """One continuous pose. ``side`` is -1 (stage left) or +1 (stage right)."""
    period = _period(look, section)
    phase = (float(beats) / period) % 1.0
    pan_wave, tilt_wave = _shape(look, phase)
    pan = _pose(pan_span, pan_wave, side, period, beat_period_s, max_pan_speed, _MAX_PAN_PER_BEAT)
    tilt = _pose(tilt_span, tilt_wave, 1.0, period, beat_period_s, max_tilt_speed, _MAX_TILT_PER_BEAT)
    return pan, tilt


def limited(previous: tuple[float, float], nxt: tuple[float, float], beats: float) -> tuple[float, float]:
    span = max(1e-3, abs(float(beats)))
    return (
        _toward(previous[0], nxt[0], _MAX_PAN_PER_BEAT * span),
        _toward(previous[1], nxt[1], _MAX_TILT_PER_BEAT * span),
    )


def preview_heads(
    show: object,
    stage: StageIntent,
    *,
    beats: float,
    section: str,
    look: str = "",
    beat_period_s: float = 0.5,
) -> StageIntent:
    """Paint a continuous path into a preview stage. Live calls do not use this."""
    fixtures = getattr(getattr(show, "patch", None), "fixtures", ())
    painted = dict(stage.fixtures)
    for fixture in fixtures:
        if getattr(fixture, "kind", None) is not FixtureKind.BEAM:
            continue
        intent = painted.get(fixture.id)
        if not isinstance(intent, BeamIntent):
            continue
        bounds = bounds_for(fixture)
        pan_box = bounds["pan"]
        tilt_box = bounds["tilt"]
        assert isinstance(pan_box, dict) and isinstance(tilt_box, dict)
        side = -1.0 if _stage_left(fixture) else 1.0
        goal = target(
            beats,
            side,
            section,
            look=look,
            pan_span=None if pan_box["still"] else (float(pan_box["lo"]), float(pan_box["hi"])),
            tilt_span=None if tilt_box["still"] else (float(tilt_box["lo"]), float(tilt_box["hi"])),
            beat_period_s=beat_period_s,
            max_pan_speed=float(bounds.get("max_pan_speed") or 0.35),
            max_tilt_speed=float(bounds.get("max_tilt_speed") or 0.25),
        )
        previous = _state.get(fixture.id)
        home_pan = float(getattr(fixture.spatial, "home_pan", 0.5))
        home_tilt = float(getattr(fixture.spatial, "home_tilt", 0.5))
        if previous is None:
            pose = (home_pan, home_tilt)
            vpan = 0.0
            vtilt = 0.0
        else:
            span = max(1e-3, float(beats) - previous[2])
            vpan, vtilt = previous[3], previous[4]
            pan = previous[0]
            tilt = previous[1]
            if goal[0] is not None:
                vpan = _slew(vpan, (goal[0] - previous[0]) / span, _MAX_PAN_ACCEL * span, _MAX_PAN_PER_BEAT)
                pan = _clamp_span(previous[0] + vpan * span, pan_box)
            if goal[1] is not None:
                vtilt = _slew(vtilt, (goal[1] - previous[1]) / span, _MAX_TILT_ACCEL * span, _MAX_TILT_PER_BEAT)
                tilt = _clamp_span(previous[1] + vtilt * span, tilt_box)
            pose = (pan, tilt)
        _state[fixture.id] = (pose[0], pose[1], float(beats), vpan, vtilt)
        painted[fixture.id] = BeamIntent(
            pan=None if pan_box["still"] else pose[0],
            tilt=None if tilt_box["still"] else pose[1],
            dimmer=intent.dimmer,
            color=intent.color,
            strobe=0.0,
        )
    return StageIntent(fixtures=painted)


def _stage_left(fixture: object) -> bool:
    spatial = getattr(fixture, "spatial", None)
    side = str(getattr(spatial, "side", "") or "")
    if side == "left":
        return True
    if side == "right":
        return False
    return "left" in str(getattr(fixture, "id", ""))


def _period(look: str, section: str) -> float:
    from orng_led.audio.library import BY_ID

    item = BY_ID.get(look)
    if item is not None and "beam" in item.groups and item.program == "beat":
        return max(2.0, float(item.cycles[0]))
    if item is not None and item.program == "smooth":
        return max(8.0, float(item.cycles[-1]) if item.cycles else 8.0)
    return 32.0 if section == "break" else 16.0


def _shape(look: str, phase: float) -> tuple[float, float]:
    """Continuous at the seam. Light timing is not this function."""
    sine = math.sin(phase * math.tau)
    cosine = math.cos(phase * math.tau)
    if "rise" in look:
        return 0.0, sine
    if "arc" in look:
        return 0.35 * sine, math.sin(phase * math.tau + 1.2)
    if "oval" in look:
        return sine, cosine
    if "meet" in look or "converge" in look:
        return sine, 0.25 * sine
    return sine, 0.45 * sine


def _pose(
    span: tuple[float, float] | None,
    wave: float,
    side: float,
    period: float,
    beat_period_s: float,
    max_speed: float,
    artistic: float,
) -> float | None:
    if span is None:
        return None
    lo, hi = float(span[0]), float(span[1])
    center = (lo + hi) / 2.0
    room = (hi - lo) / 2.0
    per_beat = min(float(artistic), max(0.0, float(max_speed)) * max(0.05, float(beat_period_s)))
    max_amp = per_beat * float(period) / math.tau
    amp = min(room, max_amp)
    return center + float(side) * amp * float(wave)


def _axis(
    preset: tuple[float, float],
    cal_min: float,
    cal_max: float,
    home: float,
    name: str,
) -> dict[str, object]:
    lo = max(float(preset[0]), float(cal_min))
    hi = min(float(preset[1]), float(cal_max))
    if hi - lo < 0.02:
        return _still(f"{name}:calibration_excludes_preset")
    return {
        "lo": lo,
        "hi": hi,
        "still": False,
        "crosses_home": lo < float(home) < hi,
        "home": float(home),
        "reason": "preset_span",
    }


def _still(reason: str) -> dict[str, object]:
    return {"lo": None, "hi": None, "still": True, "crosses_home": False, "reason": reason}


def _clamp_span(value: float, box: dict[str, object]) -> float:
    if box.get("still") or box.get("lo") is None or box.get("hi") is None:
        return float(value)
    return max(float(box["lo"]), min(float(box["hi"]), float(value)))


def _toward(origin: float, goal: float, limit: float) -> float:
    delta = float(goal) - float(origin)
    if delta > limit:
        delta = limit
    elif delta < -limit:
        delta = -limit
    return max(0.0, min(1.0, origin + delta))


def _slew(velocity: float, desired: float, accel: float, speed: float) -> float:
    delta = float(desired) - float(velocity)
    if delta > accel:
        delta = accel
    elif delta < -accel:
        delta = -accel
    nxt = float(velocity) + delta
    if nxt > speed:
        nxt = speed
    elif nxt < -speed:
        nxt = -speed
    return nxt
