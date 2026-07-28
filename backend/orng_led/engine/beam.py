"""Speed-limited Beam pan/tilt interpolation with last-valid hold."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BeamMotionState:
    """Runtime pose for one moving head.

    ``pan`` / ``tilt`` are the current interpolated semantic pose.
    ``last_valid_pan`` / ``last_valid_tilt`` are the last committed values
    re-sent whenever no new motion command is present. Home is only used to
    seed these fields when a head has never moved under program control.
    """

    pan: float = 0.5
    tilt: float = 0.5
    last_valid_pan: float | None = None
    last_valid_tilt: float | None = None

    def __post_init__(self) -> None:
        if self.last_valid_pan is None:
            self.last_valid_pan = float(self.pan)
        if self.last_valid_tilt is None:
            self.last_valid_tilt = float(self.tilt)

    @classmethod
    def from_home(cls, home_pan: float, home_tilt: float) -> BeamMotionState:
        pan = float(home_pan)
        tilt = float(home_tilt)
        return cls(pan=pan, tilt=tilt, last_valid_pan=pan, last_valid_tilt=tilt)

    def commit_valid(self) -> None:
        self.last_valid_pan = float(self.pan)
        self.last_valid_tilt = float(self.tilt)

    def hold_last_valid(self) -> None:
        """Re-assert last valid as the current pose (no motion)."""
        self.pan = float(self.last_valid_pan if self.last_valid_pan is not None else self.pan)
        self.tilt = float(self.last_valid_tilt if self.last_valid_tilt is not None else self.tilt)


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
    state.commit_valid()
    return state
