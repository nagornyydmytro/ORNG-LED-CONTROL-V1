"""Limited Auto head paths.

``HEAD_PATHS_LIVE`` is on. ``BpmService.look`` calls ``preview_heads`` when
blackout is off and sweep is not held. A preset span is a software limit taken
from the motion the current scene's presets already command. It is not proof
that a new trajectory is physically safe, and it does not widen calibration.

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
# Semantic program span comes from the scene's preset documents. Calibration is
# applied afterwards, in physical units, and converted back. It is not a second
# semantic clamp.
BOUNDS_ORIGIN = "scene_preset_extrema"
_extrema_cache: tuple[object, dict[str, tuple[float, float, float, float]]] | None = None
_MAX_PAN_PER_BEAT = 0.04
_MAX_TILT_PER_BEAT = 0.025
_MAX_PAN_ACCEL = 0.02
_MAX_TILT_ACCEL = 0.012


class HeadRig:
    """Motion memory for one consumer.

    Runtime, a preview clip and another scene do not share this. Resetting it
    does not clear anyone else's path.
    """

    def __init__(self) -> None:
        self._state: dict[str, tuple[float, float, float, float, float]] = {}

    def reset(self) -> None:
        self._state.clear()

    def pose(self, fixture_id: str) -> tuple[float, float] | None:
        row = self._state.get(fixture_id)
        if row is None:
            return None
        return (row[0], row[1])


def bounds_for(fixture: object) -> dict[str, object]:
    """Preset extrema of this scene, intersected with calibration in physical units."""
    spatial = getattr(fixture, "spatial", None)
    if spatial is None:
        return {
            "pan": _still("pan:no_spatial"),
            "tilt": _still("tilt:no_spatial"),
            "origin": BOUNDS_ORIGIN,
        }
    span = _span_for(str(getattr(fixture, "id", "")))
    if span is None:
        return {
            "pan": _still("pan:no_preset_motion"),
            "tilt": _still("tilt:no_preset_motion"),
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
    """One continuous pose. ``side`` is -1 (stage left) or +1 (stage right)."""
    period = _period(look, section)
    phase = (float(beats) / period) % 1.0
    pan_wave, tilt_wave = _shape(look, phase)
    pan = _pose(
        pan_span,
        pan_wave,
        side,
        period,
        beat_period_s,
        max_pan_speed,
        _MAX_PAN_PER_BEAT,
        rest=home_pan if "rise" in look else None,
    )
    del home_tilt
    tilt = _pose(
        tilt_span,
        tilt_wave,
        1.0,
        period,
        beat_period_s,
        max_tilt_speed,
        _MAX_TILT_PER_BEAT,
    )
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
    rig: HeadRig | None = None,
) -> StageIntent:
    """Paint a continuous path. A missing rig is a one-frame path, not shared memory."""
    motion = rig if rig is not None else HeadRig()
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
            home_pan=float(getattr(fixture.spatial, "home_pan", 0.5)),
            home_tilt=float(getattr(fixture.spatial, "home_tilt", 0.5)),
        )
        previous = motion._state.get(fixture.id)
        home_pan = float(getattr(fixture.spatial, "home_pan", 0.5))
        home_tilt = float(getattr(fixture.spatial, "home_tilt", 0.5))
        if previous is not None and float(beats) + 1e-6 < previous[2]:
            previous = None
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
        motion._state[fixture.id] = (pose[0], pose[1], float(beats), vpan, vtilt)
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
    rest: float | None = None,
) -> float | None:
    if span is None:
        return None
    lo, hi = float(span[0]), float(span[1])
    if rest is not None and abs(float(wave)) <= 1e-12:
        return max(lo, min(hi, float(rest)))
    center = (lo + hi) / 2.0
    room = (hi - lo) / 2.0
    per_beat = min(float(artistic), max(0.0, float(max_speed)) * max(0.05, float(beat_period_s)))
    max_amp = per_beat * float(period) / math.tau
    amp = min(room, max_amp)
    return center + float(side) * amp * float(wave)


def _sample_turns() -> list[float]:
    """Phases that hit the authored sine peaks, plus the quadrant points."""
    turns = {0.0, 0.25, 0.5, 0.75}
    for freq, phase in ((1.0, 0.0), (0.63, 0.7), (0.41, 0.5)):
        for step in range(8):
            turn = (math.pi / 2.0 + step * math.pi - phase) / (2.0 * math.pi * freq)
            turns.add(turn % 1.0)
    return sorted(turns)


def _scene_extrema() -> dict[str, tuple[float, float, float, float]]:
    """Semantic pan/tilt extrema of the beam intents this scene's presets emit."""
    global _extrema_cache
    from orng_led.config.io import load_show_config
    from orng_led.engine.intents import BeamIntent
    from orng_led.presets.io import default_presets_dir, load_presets_dir
    from orng_led.presets.program import YamlPresetProgram

    directory = default_presets_dir()
    if not directory.is_dir():
        return {}
    stamp = tuple(
        (path.name, path.stat().st_mtime_ns) for path in sorted(directory.glob("*.yaml"))
    )
    if _extrema_cache is not None and _extrema_cache[0] == stamp:
        return _extrema_cache[1]
    show = load_show_config()
    found: dict[str, list[float]] = {}
    for document in load_presets_dir(directory).values():
        program = YamlPresetProgram(document)
        elapsed = 0.0
        for episode in document.episodes:
            moment = elapsed + float(episode.duration_s) * 0.5
            for turn in _sample_turns():
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
                        [float(intent.pan), float(intent.pan), float(intent.tilt), float(intent.tilt)],
                    )
                    row[0] = min(row[0], float(intent.pan))
                    row[1] = max(row[1], float(intent.pan))
                    row[2] = min(row[2], float(intent.tilt))
                    row[3] = max(row[3], float(intent.tilt))
            elapsed += float(episode.duration_s)
    result = {key: (row[0], row[1], row[2], row[3]) for key, row in found.items()}
    _extrema_cache = (stamp, result)
    return result


def _span_for(fixture_id: str) -> tuple[float, float, float, float] | None:
    table = _scene_extrema()
    if fixture_id in table:
        return table[fixture_id]
    if not table:
        return None
    return (
        min(item[0] for item in table.values()),
        max(item[1] for item in table.values()),
        min(item[2] for item in table.values()),
        max(item[3] for item in table.values()),
    )


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
