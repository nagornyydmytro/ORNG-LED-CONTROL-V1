"""Speed-limited Beam pan/tilt interpolation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BeamMotionState:
    pan: float = 0.5
    tilt: float = 0.5


@dataclass(frozen=True)
class BeamMotionLimits:
    """Normalized units per second. Provisional until hardware calibration."""

    max_pan_speed: float = 0.35
    max_tilt_speed: float = 0.25


def approach(current: float, target: float, max_delta: float) -> float:
    target = max(0.0, min(1.0, target))
    delta = target - current
    if abs(delta) <= max_delta:
        return target
    return current + math_copysign(max_delta, delta)


def math_copysign(magnitude: float, signed: float) -> float:
    return magnitude if signed >= 0 else -magnitude


def step_beam(
    state: BeamMotionState,
    target_pan: float,
    target_tilt: float,
    dt_s: float,
    limits: BeamMotionLimits,
) -> BeamMotionState:
    if dt_s < 0:
        raise ValueError("dt_s must be >= 0")
    state.pan = approach(state.pan, target_pan, limits.max_pan_speed * dt_s)
    state.tilt = approach(state.tilt, target_tilt, limits.max_tilt_speed * dt_s)
    return state
