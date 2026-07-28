"""Beam pan/tilt → 3D direction and stage hit point.

The visual model is three-dimensional, but the control model is unchanged:
only Pan and Tilt exist as DMX axes. Ranges below are PROVISIONAL Mock values
used for visualisation; real limits, home position and invert flags remain
PENDING HARDWARE.

World axes (right-handed, viewer facing the stage):

* ``x`` — audience left → right, 0..1
* ``y`` — floor (0) → top of the stage picture (1)
* ``z`` — downstage (0) → upstage (1)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PROVISIONAL_PAN_RANGE_DEG = 180.0
# Tilt is modelled monotonically: 0 → straight down, 1 → 135° away from
# vertical towards the audience. Real ranges/home are PENDING HARDWARE.
PROVISIONAL_TILT_RANGE_DEG = 135.0


@dataclass(frozen=True)
class BeamVector:
    dir_x: float
    dir_y: float
    dir_z: float
    hit_x: float
    hit_z: float
    throw: float


def beam_direction(pan: float, tilt: float) -> tuple[float, float, float]:
    """Unit direction of the beam for normalized pan/tilt.

    ``tilt = 0`` points straight down; larger tilt swings the ray out towards
    the audience. ``pan`` rotates the head around its vertical axis, so left
    and right stay consistent at every tilt.
    """
    azimuth = math.radians((pan - 0.5) * PROVISIONAL_PAN_RANGE_DEG)
    polar = math.radians(max(0.0, min(1.0, tilt)) * PROVISIONAL_TILT_RANGE_DEG)
    sin_polar = math.sin(polar)
    dir_x = sin_polar * math.sin(azimuth)
    dir_y = -math.cos(polar)
    dir_z = -sin_polar * math.cos(azimuth)
    length = math.sqrt(dir_x * dir_x + dir_y * dir_y + dir_z * dir_z) or 1.0
    return dir_x / length, dir_y / length, dir_z / length


def beam_vector(
    *,
    pan: float,
    tilt: float,
    origin_x: float,
    origin_y: float,
    origin_z: float,
    max_throw: float = 2.5,
) -> BeamVector:
    """Direction plus the geometric floor hit point (y = 0 plane)."""
    dir_x, dir_y, dir_z = beam_direction(pan, tilt)
    if dir_y < -1e-4 and origin_y > 0.0:
        throw = min(max_throw, origin_y / -dir_y)
    else:
        throw = max_throw
    return BeamVector(
        dir_x=dir_x,
        dir_y=dir_y,
        dir_z=dir_z,
        hit_x=origin_x + dir_x * throw,
        hit_z=origin_z + dir_z * throw,
        throw=throw,
    )
