"""Limited Auto head paths.

``HEAD_PATHS_LIVE`` is on. ``BpmService.look`` keeps one rig through blackout
and sweep, so a release continues the path instead of starting at home. A
preset span is a software limit taken from the motion the current scene's
presets already command. It is not proof that a new trajectory is physically
safe, and it does not widen calibration.

The frame reads a snapshot. Building that snapshot is not frame work: one
background job, and a newer scene or input generation discards the older one.
Until that snapshot matches the live inputs, the axis holds its last pose.

Zeros, home, inversion and the DMX conversion stay in the existing beam
transform. This module writes semantic pan/tilt once. It does not invert.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from orng_led.config.models import FixtureKind
from orng_led.engine.intents import BeamIntent, StageIntent

# Live paths stay armed across blackout and sweep. Blackout still drops the
# light in the engine, and a blackout frame keeps the last axis pose.
HEAD_PATHS_LIVE = True
# Semantic program span comes from the scene's preset documents. Calibration is
# applied afterwards, in physical units, and converted back. It is not a second
# semantic clamp.
BOUNDS_ORIGIN = "scene_preset_extrema"


@dataclass(frozen=True)
class HeadTravel:
    """Pan/tilt extrema for one scene revision. The frame only reads this."""

    scene_id: str
    revision: str
    spans: dict[str, tuple[float, float, float, float]]
    generation: int = 0
_MAX_PAN_PER_BEAT = 0.04
_MAX_TILT_PER_BEAT = 0.025
_MAX_PAN_ACCEL = 0.02
_MAX_TILT_ACCEL = 0.012


@dataclass
class _Memory:
    """Software estimate for one head. Not a reading from the motor."""

    pan: float
    tilt: float
    beats: float
    vpan: float
    vtilt: float
    look: str = ""
    blocked: bool = False
    dark_until: float = 0.0


class HeadRig:
    """Motion memory for one consumer.

    Runtime, a preview clip and another scene do not share this. Resetting it
    does not clear anyone else's path. The stored pose is the program's
    estimate, not confirmation that the motor arrived.
    """

    def __init__(self) -> None:
        self._state: dict[str, _Memory] = {}
        self.unlit: frozenset[str] = frozenset()

    def reset(self) -> None:
        self._state.clear()
        self.unlit = frozenset()

    def pose(self, fixture_id: str) -> tuple[float, float] | None:
        row = self._state.get(fixture_id)
        if row is None:
            return None
        return (row.pan, row.tilt)


def bounds_for(fixture: object, travel: HeadTravel | None = None) -> dict[str, object]:
    """Read a prepared snapshot. Never loads YAML or samples presets."""
    spatial = getattr(fixture, "spatial", None)
    if spatial is None:
        return {
            "pan": _still("pan:no_spatial"),
            "tilt": _still("tilt:no_spatial"),
            "origin": BOUNDS_ORIGIN,
        }
    if travel is None:
        return {
            "pan": _still("pan:bounds_not_ready"),
            "tilt": _still("tilt:bounds_not_ready"),
            "origin": BOUNDS_ORIGIN,
            "max_pan_speed": float(getattr(spatial, "max_pan_speed", 0.35)),
            "max_tilt_speed": float(getattr(spatial, "max_tilt_speed", 0.25)),
        }
    span = travel.spans.get(str(getattr(fixture, "id", "")))
    if span is None:
        return {
            "pan": _still("pan:unknown_fixture"),
            "tilt": _still("tilt:unknown_fixture"),
            "origin": BOUNDS_ORIGIN,
            "max_pan_speed": float(getattr(spatial, "max_pan_speed", 0.35)),
            "max_tilt_speed": float(getattr(spatial, "max_tilt_speed", 0.25)),
        }
    return {
        "pan": _axis(span[0], span[1], spatial, "pan"),
        "tilt": _axis(span[2], span[3], spatial, "tilt"),
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
    home_pan: float | None = None,
    home_tilt: float | None = None,
) -> tuple[float | None, float | None]:
    """One prescribed pose for a single head, already clamped to its span.

    ``side`` is -1 (audience left) or +1 (audience right). The pair version
    is ``_mirror_goals``: this helper does not see the other head.
    """
    del section, home_tilt
    name = course_name(look)
    period = _period(look, "groove")
    phase = (float(beats) / period) % 1.0
    course = prescribe(name, phase)
    pan = _place_pan(
        pan_span,
        float(side),
        course.pan,
        period,
        beat_period_s,
        max_pan_speed,
        home=home_pan,
    )
    tilt = _place_tilt(tilt_span, course.tilt, period, beat_period_s, max_tilt_speed)
    return pan, tilt


def limited(previous: tuple[float, float], nxt: tuple[float, float], beats: float) -> tuple[float, float]:
    span = max(1e-3, abs(float(beats)))
    return (
        _toward(previous[0], nxt[0], _MAX_PAN_PER_BEAT * span),
        _toward(previous[1], nxt[1], _MAX_TILT_PER_BEAT * span),
    )


def _mirror_goals(
    fixtures: list[object],
    *,
    beats: float,
    section: str,
    look: str,
    cycle_beats: float | None,
    beat_period_s: float,
    travel: HeadTravel | None,
) -> dict[str, tuple[float | None, float | None, bool]]:
    """Stage-mirror goals. Missing spans hold; they do not copy another head."""
    del section
    rows = _head_rows(fixtures, travel)
    name, period = _fitted_course(look, cycle_beats, beat_period_s, rows)
    phase = 0.0 if period <= 0.0 else (float(beats) / period) % 1.0
    course = prescribe(name, phase)
    pan_room, tilt_rest, tilt_rise, tilt_fall = _common_room(rows)
    pan_amp = _speed_cap(pan_room, period, beat_period_s, _MAX_PAN_PER_BEAT, _TRAVEL_FRACTION[name], rows, "pan")
    tilt_up = _speed_cap(tilt_rise, period, beat_period_s, _MAX_TILT_PER_BEAT, _TRAVEL_FRACTION[name], rows, "tilt")
    tilt_down = _speed_cap(tilt_fall, period, beat_period_s, _MAX_TILT_PER_BEAT, _TRAVEL_FRACTION[name], rows, "tilt")
    goals: dict[str, tuple[float | None, float | None, bool]] = {}
    for row in rows:
        pan = None
        tilt = None
        if row.pan is not None and abs(course.pan) > 1e-6 and pan_amp > 0.0:
            aim = _STAGE_CENTER + row.side * pan_amp * course.pan
            pan = _quantize(_clamp(aim, row.pan[0], row.pan[1]), row.pan[0], row.pan[1])
        elif row.pan is not None:
            pan = _quantize(_clamp(row.home_pan, row.pan[0], row.pan[1]), row.pan[0], row.pan[1])
        if row.tilt is not None:
            if course.tilt >= 0.0:
                tilt = tilt_rest + tilt_up * course.tilt
            else:
                tilt = tilt_rest + tilt_down * course.tilt
            tilt = _quantize(_clamp(tilt, row.tilt[0], row.tilt[1]), row.tilt[0], row.tilt[1])
        goals[row.ident] = (pan, tilt, course.lit)
    return goals


def preview_heads(
    show: object,
    stage: StageIntent,
    *,
    beats: float,
    section: str,
    look: str = "",
    beat_period_s: float = 0.5,
    rig: HeadRig | None = None,
    travel: HeadTravel | None = None,
    cycle_beats: float | None = None,
    blackout: bool = False,
    axes_busy: bool = False,
    grid_lost: bool = False,
) -> StageIntent:
    """Same path the runtime and the preview use. Each rig keeps its own estimate.

    The pose written here is a software estimate inside the preset spans.
    It is not a report that the motor reached it.
    """
    motion = rig if rig is not None else HeadRig()
    fixtures = getattr(getattr(show, "patch", None), "fixtures", ())
    painted = dict(stage.fixtures)
    unlit: set[str] = set()
    beams = [fixture for fixture in fixtures if getattr(fixture, "kind", None) is FixtureKind.BEAM]
    goals = _mirror_goals(
        beams,
        beats=beats,
        section=section,
        look=look,
        cycle_beats=cycle_beats,
        beat_period_s=beat_period_s,
        travel=travel,
    )
    name, period = _fitted_course(look, cycle_beats, beat_period_s, _head_rows(beams, travel))
    phase = 0.0 if period <= 0.0 else (float(beats) / period) % 1.0
    for fixture in beams:
        intent = painted.get(fixture.id)
        if not isinstance(intent, BeamIntent):
            continue
        bounds = bounds_for(fixture, travel)
        pan_box = bounds["pan"]
        tilt_box = bounds["tilt"]
        assert isinstance(pan_box, dict) and isinstance(tilt_box, dict)
        goal = goals.get(str(fixture.id), (None, None, False))
        previous = motion._state.get(fixture.id)
        if previous is not None and float(beats) + 1e-6 < previous.beats:
            previous.beats = float(beats)
        hold = bool(blackout or axes_busy)
        if previous is None and bool(pan_box["still"]) and bool(tilt_box["still"]) and goal[0] is None:
            painted[fixture.id] = BeamIntent(
                pan=None,
                tilt=None,
                dimmer=0.0 if hold or grid_lost else intent.dimmer,
                color=intent.color,
                strobe=0.0,
            )
            if hold or grid_lost:
                unlit.add(str(fixture.id))
            continue
        if previous is None:
            home_pan = float(getattr(fixture.spatial, "home_pan", 0.5))
            home_tilt = float(getattr(fixture.spatial, "home_tilt", 0.5))
            pose = (_clamp_span(home_pan, pan_box), _clamp_span(home_tilt, tilt_box))
            vpan = 0.0
            vtilt = 0.0
            memory = _Memory(pose[0], pose[1], float(beats), 0.0, 0.0)
        else:
            memory = previous
            span = min(1.0, max(1e-3, float(beats) - memory.beats))
            vpan, vtilt = memory.vpan, memory.vtilt
            pan, tilt = memory.pan, memory.tilt
            if not hold and goal[0] is not None:
                vpan = _slew(vpan, (goal[0] - memory.pan) / span, _MAX_PAN_ACCEL * span, _MAX_PAN_PER_BEAT)
                pan = _clamp_span(memory.pan + vpan * span, pan_box)
            if not hold and goal[1] is not None:
                vtilt = _slew(vtilt, (goal[1] - memory.tilt) / span, _MAX_TILT_ACCEL * span, _MAX_TILT_PER_BEAT)
                tilt = _clamp_span(memory.tilt + vtilt * span, tilt_box)
            pose = (pan, tilt)
        changed = memory.look not in {"", look}
        if changed or hold or grid_lost:
            memory.blocked = True
            memory.dark_until = max(memory.dark_until, float(beats) + period * (1.0 - phase))
        elif memory.blocked and float(beats) + 1e-6 >= memory.dark_until:
            memory.blocked = False
        lit = _allow_light(
            name,
            bool(goal[2]),
            pose,
            (memory.pan, memory.tilt) if previous is not None else None,
            blocked=memory.blocked or hold or grid_lost,
            dimmer=float(intent.dimmer),
        )
        memory.pan, memory.tilt = pose
        memory.beats = float(beats)
        memory.vpan, memory.vtilt = vpan, vtilt
        memory.look = look
        motion._state[fixture.id] = memory
        painted[fixture.id] = BeamIntent(
            pan=None if pan_box["still"] or goal[0] is None else pose[0],
            tilt=None if tilt_box["still"] or goal[1] is None else pose[1],
            dimmer=float(intent.dimmer) if lit else 0.0,
            color=intent.color,
            strobe=0.0,
        )
        if not lit:
            unlit.add(str(fixture.id))
    motion.unlit = frozenset(unlit)
    return StageIntent(fixtures=painted)


def _stage_left(fixture: object) -> bool:
    spatial = getattr(fixture, "spatial", None)
    side = str(getattr(spatial, "side", "") or "")
    if side == "left":
        return True
    if side == "right":
        return False
    return "left" in str(getattr(fixture, "id", ""))


_STAGE_CENTER = 0.5
_MECHANICAL = (2.0, 4.0, 8.0)
_COURSE_PERIOD = {"rise": 8.0, "fan": 8.0, "converge": 8.0, "arc": 8.0, "oval": 8.0, "accent": 4.0}
_TRAVEL_FRACTION = {"rise": 0.25, "fan": 0.5, "converge": 0.5, "arc": 0.5, "oval": 0.25, "accent": 0.2}
_QUANTUM = 1.0 / 255.0


@dataclass(frozen=True)
class Course:
    """Prescribed shape. ``pan`` opens outward, ``tilt`` is -1..1 around the rest."""

    name: str
    pan: float
    tilt: float
    lit: bool


@dataclass(frozen=True)
class _HeadRow:
    ident: str
    side: float
    pan: tuple[float, float] | None
    tilt: tuple[float, float] | None
    home_pan: float
    home_tilt: float
    pan_speed: float
    tilt_speed: float


def course_name(look: str) -> str:
    """Which choreography, from the composition role. Not a substring of the id."""
    from orng_led.audio.library import BY_ID

    item = BY_ID.get(look)
    role = "" if item is None else str(item.roles)
    if role in {"rise", "head_pulse"}:
        return "rise"
    if role == "converge":
        return "converge"
    if role == "fan":
        return "fan"
    if role == "arcs":
        return "arc"
    if role == "oval":
        return "oval"
    if role == "pass":
        return "accent"
    return "arc"


def prescribe(name: str, phase: float) -> Course:
    """The path asked for, before the rig's limits. Continuous at 0 and 1."""
    phase = float(phase) % 1.0
    if name == "rise":
        if phase < 0.25:
            return Course(name, 0.0, 0.0, False)
        if phase < 0.5:
            return Course(name, 0.0, _smooth((phase - 0.25) / 0.25), True)
        if phase < 0.75:
            return Course(name, 0.0, 1.0 - _smooth((phase - 0.5) / 0.25), False)
        return Course(name, 0.0, 0.0, False)
    if name == "fan":
        opened = math.sin(phase * math.pi)
        return Course(name, opened, 0.25 * opened, phase < 0.5)
    if name == "converge":
        opened = 1.0 - math.sin(phase * math.pi)
        return Course(name, opened, 0.2 * opened, phase < 0.5)
    if name == "oval":
        return Course(name, math.sin(phase * math.tau), math.cos(phase * math.tau), math.cos(phase * math.tau) > 0.0)
    if name == "accent":
        if phase < 0.2:
            return Course(name, 0.4 * _smooth(phase / 0.2), 0.35 * _smooth(phase / 0.2), False)
        if phase < 0.55:
            return Course(name, 0.4, 0.35, True)
        gone = _smooth((phase - 0.55) / 0.45)
        return Course(name, 0.4 * (1.0 - gone), 0.35 * (1.0 - gone), False)
    bow = math.sin(phase * math.pi)
    return Course("arc", 0.55 * bow, 0.45 * bow, phase < 0.5)


def _smooth(unit: float) -> float:
    unit = max(0.0, min(1.0, float(unit)))
    return unit * unit * (3.0 - 2.0 * unit)


def _period(look: str, section: str, cycle_beats: float | None = None) -> float:
    """Motor cycle. A picture cycle of 1 beat is not a full head travel.

    2, 4 and 8 are the motor cycles. 16 and 32 are not chosen silently, and a
    requested 1 or 1/2 stays on the picture, not on the motor.
    """
    del section
    authored = _COURSE_PERIOD[course_name(look)]
    from orng_led.audio.library import BY_ID

    item = BY_ID.get(look)
    if item is not None:
        for value in item.cycles:
            if float(value) in _MECHANICAL:
                authored = float(value)
                break
    if cycle_beats is None:
        return authored
    asked = float(cycle_beats)
    if asked in _MECHANICAL:
        return asked
    return authored


def _fitted_course(
    look: str,
    cycle_beats: float | None,
    beat_period_s: float,
    rows: list[_HeadRow],
) -> tuple[str, float]:
    """Lengthen 2→4→8 when the path cannot fit. Do not change the picture cycle."""
    name = course_name(look)
    period = _period(look, "groove", cycle_beats)
    if period not in _MECHANICAL:
        period = 8.0
    order = [value for value in _MECHANICAL if value + 1e-9 >= period]
    pan_room, _rest, tilt_rise, _fall = _common_room(rows)
    needed = max(abs(prescribe(name, 0.3).pan) * pan_room, abs(prescribe(name, 0.4).tilt) * tilt_rise)
    for candidate in order:
        reach = _speed_cap(
            max(pan_room, tilt_rise),
            candidate,
            beat_period_s,
            _MAX_TILT_PER_BEAT,
            _TRAVEL_FRACTION[name],
            rows,
            "tilt",
        )
        if needed <= 1e-6 or reach >= min(needed, 0.02):
            return name, candidate
    return name, order[-1] if order else 8.0


def _head_rows(fixtures: list[object], travel: HeadTravel | None) -> list[_HeadRow]:
    rows = []
    for fixture in fixtures:
        bounds = bounds_for(fixture, travel)
        pan_box = bounds["pan"]
        tilt_box = bounds["tilt"]
        if not isinstance(pan_box, dict) or not isinstance(tilt_box, dict):
            continue
        spatial = getattr(fixture, "spatial", None)
        pan_span = None if pan_box["still"] else _inset(float(pan_box["lo"]), float(pan_box["hi"]))
        tilt_span = None if tilt_box["still"] else _inset(float(tilt_box["lo"]), float(tilt_box["hi"]))
        rows.append(
            _HeadRow(
                ident=str(getattr(fixture, "id", "")),
                side=-1.0 if _stage_left(fixture) else 1.0,
                pan=pan_span,
                tilt=tilt_span,
                home_pan=float(getattr(spatial, "home_pan", _STAGE_CENTER)),
                home_tilt=float(getattr(spatial, "home_tilt", _STAGE_CENTER)),
                pan_speed=float(bounds.get("max_pan_speed") or 0.35),
                tilt_speed=float(bounds.get("max_tilt_speed") or 0.25),
            )
        )
    return rows


def _inset(lo: float, hi: float) -> tuple[float, float] | None:
    if float(hi) - float(lo) < 0.02:
        return None
    return (float(lo), float(hi))


def _common_room(rows: list[_HeadRow]) -> tuple[float, float, float, float]:
    """Pan opening both heads can mirror about stage centre, and a shared tilt."""
    pan_hi = 1.0
    saw_pan = False
    for row in rows:
        if row.pan is None:
            continue
        saw_pan = True
        lo, hi = row.pan
        if lo - 1e-9 > _STAGE_CENTER or hi + 1e-9 < _STAGE_CENTER:
            room = 0.0
        elif row.side < 0.0:
            room = max(0.0, _STAGE_CENTER - lo)
        else:
            room = max(0.0, hi - _STAGE_CENTER)
        pan_hi = min(pan_hi, room)
    if not saw_pan:
        pan_hi = 0.0
    lows = [row.tilt[0] for row in rows if row.tilt is not None]
    highs = [row.tilt[1] for row in rows if row.tilt is not None]
    if not lows or max(lows) >= min(highs):
        return pan_hi, _STAGE_CENTER, 0.0, 0.0
    lo = max(lows)
    hi = min(highs)
    homes = [row.home_tilt for row in rows if row.tilt is not None]
    rest = _clamp(sum(homes) / len(homes), lo, hi)
    return pan_hi, rest, max(0.0, hi - rest), max(0.0, rest - lo)


def _speed_cap(
    room: float,
    period: float,
    beat_period_s: float,
    artistic: float,
    fraction: float,
    rows: list[_HeadRow],
    axis: str,
) -> float:
    from orng_led.engine.clock import FRAME_DT

    if room <= 0.0 or not rows:
        return 0.0
    speeds = [row.pan_speed if axis == "pan" else row.tilt_speed for row in rows]
    hardware = min(speeds) if speeds else 0.35
    lag = min(0.25, FRAME_DT / max(0.25, float(beat_period_s)))
    per_beat = min(float(artistic), max(0.0, hardware) * max(0.05, float(beat_period_s)))
    per_beat *= 1.0 - lag
    return min(float(room), per_beat * float(period) * max(0.05, float(fraction)))


def _place_pan(
    span: tuple[float, float] | None,
    side: float,
    unit: float,
    period: float,
    beat_period_s: float,
    max_speed: float,
    *,
    home: float | None = None,
) -> float | None:
    if span is None:
        return None
    lo, hi = float(span[0]), float(span[1])
    if abs(float(unit)) <= 1e-6 and home is not None:
        return _clamp(float(home), lo, hi)
    if lo > _STAGE_CENTER or hi < _STAGE_CENTER:
        outward = (_STAGE_CENTER - lo) if side < 0.0 else (hi - _STAGE_CENTER)
        room = max(0.0, outward)
    else:
        outward = (_STAGE_CENTER - lo) if side < 0.0 else (hi - _STAGE_CENTER)
        room = max(0.0, outward)
    per_beat = min(_MAX_PAN_PER_BEAT, max(0.0, float(max_speed)) * max(0.05, float(beat_period_s)))
    amp = min(room, per_beat * float(period) * 0.5)
    return _clamp(_STAGE_CENTER + float(side) * amp * float(unit), lo, hi)


def _place_tilt(
    span: tuple[float, float] | None,
    unit: float,
    period: float,
    beat_period_s: float,
    max_speed: float,
) -> float | None:
    if span is None:
        return None
    lo, hi = float(span[0]), float(span[1])
    rest = _clamp(_STAGE_CENTER, lo, hi)
    rise = max(0.0, hi - rest)
    fall = max(0.0, rest - lo)
    per_beat = min(_MAX_TILT_PER_BEAT, max(0.0, float(max_speed)) * max(0.05, float(beat_period_s)))
    cap = per_beat * float(period) * 0.5
    if float(unit) >= 0.0:
        return _clamp(rest + min(rise, cap) * float(unit), lo, hi)
    return _clamp(rest + min(fall, cap) * float(unit), lo, hi)


def _allow_light(
    name: str,
    prescribed: bool,
    pose: tuple[float, float],
    previous: tuple[float, float] | None,
    *,
    blocked: bool,
    dimmer: float,
) -> bool:
    """Light only on the part of the move that is supposed to be seen.

    A falling return, a blocked cycle and a dark picture stay dark. This does
    not mean the motor has finished the move.
    """
    if blocked or not prescribed or float(dimmer) <= 0.02:
        return False
    if previous is None:
        return name != "accent"
    if name == "accent":
        return abs(pose[0] - previous[0]) <= _QUANTUM * 4.0 and abs(pose[1] - previous[1]) <= _QUANTUM * 4.0
    if name == "rise":
        return pose[1] + 1e-4 >= previous[1]
    if name in {"fan", "arc", "converge"}:
        return True
    return True


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _quantize(value: float, lo: float, hi: float) -> float:
    """8-bit step, then back inside the window. Not a claim about fine channels."""
    stepped = round(float(value) / _QUANTUM) * _QUANTUM
    return _clamp(stepped, lo, hi)


def _sample_turns() -> list[float]:
    """Phases that hit the authored sine peaks, plus the quadrant points."""
    turns = {0.0, 0.25, 0.5, 0.75}
    for freq, phase in ((1.0, 0.0), (0.63, 0.7), (0.41, 0.5)):
        for step in range(8):
            turn = (math.pi / 2.0 + step * math.pi - phase) / (2.0 * math.pi * freq)
            turns.add(turn % 1.0)
    return sorted(turns)


def motion_loop_beats(look: str, cycle_beats: float) -> float:
    """Picture cycles that cover one full head trajectory. Bars stay on their cycle."""
    from orng_led.audio.library import BY_ID

    cycle = float(cycle_beats)
    item = BY_ID.get(look)
    if item is None or "beam" not in item.groups or cycle <= 0.0:
        return cycle
    period = _period(look, "groove")
    spans = max(1, int(math.ceil((period / cycle) - 1e-9)))
    return cycle * spans


def travel_fingerprint(show: object, documents: object, *, scene_id: str) -> str:
    """Identity of every input that can change a head span. Labels do not."""
    import hashlib
    import json

    fixtures = []
    for fixture in getattr(getattr(show, "patch", None), "fixtures", ()) or ():
        spatial = getattr(fixture, "spatial", None)
        kind = getattr(fixture, "kind", "")
        side = getattr(spatial, "side", "")
        fixtures.append(
            {
                "id": str(getattr(fixture, "id", "")),
                "kind": str(getattr(kind, "value", kind)),
                "groups": list(getattr(fixture, "groups", ()) or ()),
                "profile_id": str(getattr(fixture, "profile_id", "")),
                "side": str(getattr(side, "value", side)),
                "pan_min": float(getattr(spatial, "pan_min", 0.0) or 0.0),
                "pan_max": float(getattr(spatial, "pan_max", 1.0) or 1.0),
                "tilt_min": float(getattr(spatial, "tilt_min", 0.0) or 0.0),
                "tilt_max": float(getattr(spatial, "tilt_max", 1.0) or 1.0),
                "home_pan": float(getattr(spatial, "home_pan", 0.5) or 0.5),
                "home_tilt": float(getattr(spatial, "home_tilt", 0.5) or 0.5),
                "pan_offset": float(getattr(spatial, "pan_offset", 0.0) or 0.0),
                "tilt_offset": float(getattr(spatial, "tilt_offset", 0.0) or 0.0),
                "pan_invert": bool(getattr(spatial, "pan_invert", False)),
                "tilt_invert": bool(getattr(spatial, "tilt_invert", False)),
                "max_pan_speed": float(getattr(spatial, "max_pan_speed", 0.35) or 0.35),
                "max_tilt_speed": float(getattr(spatial, "max_tilt_speed", 0.25) or 0.25),
                "beam_calibration_confirmed": bool(
                    getattr(spatial, "beam_calibration_confirmed", False)
                ),
            }
        )
    fixtures.sort(key=lambda item: str(item["id"]))
    profiles = []
    raw_profiles = getattr(show, "profiles", {}) or {}
    for key in sorted(raw_profiles, key=str):
        profile = raw_profiles[key]
        kind = getattr(profile, "kind", "")
        channels = []
        for channel in getattr(profile, "channels", ()) or ():
            role = getattr(channel, "role", "")
            channels.append(
                {
                    "local": int(getattr(channel, "local", 0) or 0),
                    "role": str(getattr(role, "value", role)),
                    "segment": getattr(channel, "segment_index", None),
                    "fixed": getattr(channel, "fixed_value", None),
                }
            )
        profiles.append(
            {
                "id": str(getattr(profile, "id", key)),
                "kind": str(getattr(kind, "value", kind)),
                "footprint": int(getattr(profile, "footprint", 0) or 0),
                "segment_count": int(getattr(profile, "segment_count", 0) or 0),
                "channels": channels,
            }
        )
    rows = []
    items = documents.values() if isinstance(documents, dict) else documents
    for document in items:
        episodes = []
        for episode in getattr(document, "episodes", ()) or ():
            actions = []
            for action in getattr(episode, "actions", ()) or ():
                actions.append(
                    {
                        "groups": list(getattr(action, "groups", ()) or ()),
                        "effect": str(getattr(action, "effect", "")),
                        "speed": float(getattr(action, "speed", 0.0) or 0.0),
                        "intensity": float(getattr(action, "intensity", 0.0) or 0.0),
                        "cycle": getattr(action, "cycle_beats", None),
                    }
                )
            episodes.append(
                {
                    "duration": float(getattr(episode, "duration_s", 0.0) or 0.0),
                    "groups": list(getattr(episode, "groups", ()) or ()),
                    "effect": str(getattr(episode, "effect", "")),
                    "speed": float(getattr(episode, "speed", 0.0) or 0.0),
                    "intensity": float(getattr(episode, "intensity", 0.0) or 0.0),
                    "cycle": getattr(episode, "cycle_beats", None),
                    "actions": actions,
                }
            )
        rows.append((str(getattr(document, "id", "")), episodes))
    rows.sort(key=lambda item: item[0])
    payload = {
        "scene": str(scene_id),
        "fixtures": fixtures,
        "profiles": profiles,
        "presets": rows,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def prepare_head_travel(
    show: object,
    documents: object,
    *,
    scene_id: str,
    revision: str,
    generation: int = 0,
    should_stop: Callable[[], bool] | None = None,
) -> HeadTravel | None:
    """Sample one scene's presets. A frame never calls this.

    When ``should_stop`` is set, the sampling yields the interpreter often
    enough that a live frame loop can run, and a newer job can abandon this one.
    """
    from orng_led.engine.intents import BeamIntent
    from orng_led.presets.program import YamlPresetProgram

    if should_stop is not None and should_stop():
        return None
    found: dict[str, list[float]] = {}
    items = documents.values() if isinstance(documents, dict) else documents
    last_yield = time.perf_counter()

    def abandon() -> bool:
        nonlocal last_yield
        if should_stop is not None and should_stop():
            return True
        if should_stop is None:
            return False
        now = time.perf_counter()
        if now - last_yield >= 0.008:
            time.sleep(0.001)
            last_yield = time.perf_counter()
        return False

    for document in items:
        if abandon():
            return None
        program = document if hasattr(document, "evaluate") else YamlPresetProgram(document)
        episodes = getattr(document, "episodes", None)
        if episodes is None:
            episodes = getattr(getattr(document, "document", None), "episodes", ())
        elapsed = 0.0
        for episode in episodes:
            if abandon():
                return None
            moment = elapsed + float(episode.duration_s) * 0.5
            for turn in _sample_turns():
                if abandon():
                    return None
                stage = program.evaluate(
                    moment, show, movement_turns=turn, effect_turns=turn
                )
                for fixture_id, intent in stage.fixtures.items():
                    if not isinstance(intent, BeamIntent):
                        continue
                    if intent.pan is None or intent.tilt is None:
                        continue
                    row = found.setdefault(
                        fixture_id,
                        [
                            float(intent.pan),
                            float(intent.pan),
                            float(intent.tilt),
                            float(intent.tilt),
                        ],
                    )
                    row[0] = min(row[0], float(intent.pan))
                    row[1] = max(row[1], float(intent.pan))
                    row[2] = min(row[2], float(intent.tilt))
                    row[3] = max(row[3], float(intent.tilt))
            elapsed += float(episode.duration_s)
    spans = {key: (row[0], row[1], row[2], row[3]) for key, row in found.items()}
    return HeadTravel(
        scene_id=str(scene_id),
        revision=str(revision),
        spans=spans,
        generation=int(generation),
    )


class HeadTravelWorker:
    """One queued range calculation. A newer request replaces the one waiting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: tuple | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="orng-head-travel", daemon=True)
        self._thread.start()

    def submit(self, job: tuple) -> None:
        with self._lock:
            self._pending = job
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.5)
            self._wake.clear()
            while not self._stop.is_set():
                with self._lock:
                    job = self._pending
                    self._pending = None
                if job is None:
                    break
                show, documents, scene_id, revision, generation, done = job

                def stop() -> bool:
                    if self._stop.is_set():
                        return True
                    with self._lock:
                        return self._pending is not None

                travel = prepare_head_travel(
                    show,
                    documents,
                    scene_id=scene_id,
                    revision=revision,
                    generation=generation,
                    should_stop=stop,
                )
                if travel is not None and not self._stop.is_set():
                    done(travel)


def _axis(
    sem_lo: float,
    sem_hi: float,
    spatial: object,
    name: str,
) -> dict[str, object]:
    """Intersect in physical units, then return the semantic window."""
    from orng_led.engine.beam_transform import inverse_axis, transform_axis

    if name == "pan":
        offset = float(getattr(spatial, "pan_offset", 0.0))
        invert = bool(getattr(spatial, "pan_invert", False))
        axis_min = float(spatial.pan_min)
        axis_max = float(spatial.pan_max)
        home = float(spatial.home_pan)
    else:
        offset = float(getattr(spatial, "tilt_offset", 0.0))
        invert = bool(getattr(spatial, "tilt_invert", False))
        axis_min = float(spatial.tilt_min)
        axis_max = float(spatial.tilt_max)
        home = float(spatial.home_tilt)

    def to_physical(semantic: float) -> float:
        return transform_axis(
            semantic,
            offset=offset,
            invert=invert,
            axis_min=axis_min,
            axis_max=axis_max,
        )

    def to_semantic(physical: float) -> float:
        return inverse_axis(
            physical,
            offset=offset,
            invert=invert,
            axis_min=axis_min,
            axis_max=axis_max,
        )

    physical = sorted((to_physical(sem_lo), to_physical(sem_hi)))
    low = max(physical[0], axis_min)
    high = min(physical[1], axis_max)
    if high - low < 0.02:
        return _still(f"{name}:calibration_excludes_preset")
    semantic = sorted((to_semantic(low), to_semantic(high)))
    lo = max(float(sem_lo), semantic[0])
    hi = min(float(sem_hi), semantic[1])
    if hi - lo < 0.02:
        return _still(f"{name}:calibration_excludes_preset")
    return {
        "lo": lo,
        "hi": hi,
        "still": False,
        "crosses_home": lo < home < hi,
        "home": home,
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
