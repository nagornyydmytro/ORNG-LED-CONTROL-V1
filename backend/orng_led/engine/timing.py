"""Explicit clocks and units for the show engine.

Units are never mixed:

* ``duration_s`` / ``time_s``  – seconds of show time (may be preview-scaled).
* ``wall_time_s``             – real monotonic seconds, used by safety timers.
* ``rate_hz``                 – effect frequency in cycles per second.
* ``phase_turns``             – accumulated effect cycles (turns), continuous.
* ``progress``                – 0..1 position inside one episode.

An episode is 18 s long by default; that is the *duration of a look*, never
the duration of one pulse/chase cycle. Effect speed is a semantic 0..1 value
mapped to a real frequency in Hz by :func:`effect_rate_hz`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Semantic speed 0..1 → real effect frequency (Hz), exponential so the calm
# and the aggressive ends of the preset range both stay useful.
MIN_EFFECT_HZ = 0.12
MAX_EFFECT_HZ = 7.0

# Per-effect multiplier on the mapped frequency.
EFFECT_RATE_SCALE: dict[str, float] = {
    "static": 0.0,
    "breathe": 0.55,
    "pulse": 1.0,
    "wave": 0.8,
    "chase": 1.0,
    "mirror_sweep": 1.0,
    # Near-static washes: keep segment climbs readable, not frantic.
    "glow": 0.35,
    "glow_wave": 0.45,
    "glow_rise": 0.55,
    "glow_line": 0.5,
    "glow_pinch": 0.45,
    "glow_eq": 0.5,
}

# Beam movement is physically slow: keep it inside the provisional pan/tilt
# speed limits instead of asking for impossible travel (canon §5.3).
MIN_MOVE_HZ = 0.045
MAX_MOVE_HZ = 0.17


def clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def effect_rate_hz(speed: float, effect: str = "pulse") -> float:
    """Map a semantic 0..1 speed to a real frequency in Hz."""
    speed = clamp01(speed)
    scale = EFFECT_RATE_SCALE.get(effect, 1.0)
    if scale <= 0.0:
        return 0.0
    ratio = MAX_EFFECT_HZ / MIN_EFFECT_HZ
    return MIN_EFFECT_HZ * (ratio**speed) * scale


def movement_rate_hz(speed: float) -> float:
    """Beam sweep frequency in Hz (speed-limited, provisional)."""
    speed = clamp01(speed)
    return MIN_MOVE_HZ + (MAX_MOVE_HZ - MIN_MOVE_HZ) * speed


def cycles_in(seconds: float, rate_hz: float) -> float:
    """Number of effect turns produced by ``seconds`` at ``rate_hz``."""
    return max(0.0, seconds) * max(0.0, rate_hz)


@dataclass(frozen=True)
class ShowClock:
    """Two independent clocks with the same origin.

    ``show_time_s`` may run faster than real time when the operator uses
    preview speed. ``wall_time_s`` never does: every protective timeout is
    measured on it.
    """

    show_time_s: float = 0.0
    wall_time_s: float = 0.0
    preview_speed: float = 1.0

    def advanced(self, wall_dt_s: float) -> ShowClock:
        wall_dt_s = max(0.0, wall_dt_s)
        return ShowClock(
            show_time_s=self.show_time_s + wall_dt_s * max(0.0, self.preview_speed),
            wall_time_s=self.wall_time_s + wall_dt_s,
            preview_speed=self.preview_speed,
        )


def unipolar_sine(phase_turns: float) -> float:
    """0..1 sine wave from accumulated turns."""
    return 0.5 + 0.5 * math.sin(2.0 * math.pi * phase_turns)


def accent(phase_turns: float, sharpness: float = 2.6) -> float:
    """0..1 accent wave: rounded at slow rates, punchy at fast rates."""
    return unipolar_sine(phase_turns) ** sharpness


def saw(phase_turns: float) -> float:
    """0..1 rising ramp, wrapping every turn."""
    return phase_turns - math.floor(phase_turns)
