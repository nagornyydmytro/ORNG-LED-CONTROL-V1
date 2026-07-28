"""Authoritative semantic ↔ physical Beam pan/tilt transforms.

Presets and Live Effects always speak scene-normalized pan/tilt (0..1).
Per-fixture SpatialPlacement holds invert, offset, safe range and home.
Renderer and simulator MUST call these helpers — never reimplement invert.
"""

from __future__ import annotations

from dataclasses import dataclass

from orng_led.config.models import ChannelRole, FixtureInstance, FixtureProfile, SpatialPlacement


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass(frozen=True)
class AxisPhysical:
    """Normalized physical axis after calibration transform (still 0..1 scaled into min–max)."""

    normalized: float
    coarse: int
    fine: int | None
    value_16bit: int | None


def transform_axis(
    semantic: float,
    *,
    offset: float,
    invert: bool,
    axis_min: float,
    axis_max: float,
) -> float:
    """semantic 0..1 → physical normalized inside [axis_min, axis_max]."""
    value = clamp01(float(semantic) + float(offset))
    if invert:
        value = 1.0 - value
    span = float(axis_max) - float(axis_min)
    physical = float(axis_min) + value * span
    return clamp(physical, axis_min, axis_max)


def inverse_axis(
    physical: float,
    *,
    offset: float,
    invert: bool,
    axis_min: float,
    axis_max: float,
) -> float:
    """physical normalized → semantic scene 0..1."""
    span = float(axis_max) - float(axis_min)
    if span <= 1e-9:
        value = 0.0
    else:
        value = (clamp(physical, axis_min, axis_max) - float(axis_min)) / span
    value = clamp01(value)
    if invert:
        value = 1.0 - value
    return clamp01(value - float(offset))


def semantic_to_physical(
    spatial: SpatialPlacement,
    *,
    pan: float,
    tilt: float,
) -> tuple[float, float]:
    physical_pan = transform_axis(
        pan,
        offset=spatial.pan_offset,
        invert=spatial.pan_invert,
        axis_min=spatial.pan_min,
        axis_max=spatial.pan_max,
    )
    physical_tilt = transform_axis(
        tilt,
        offset=spatial.tilt_offset,
        invert=spatial.tilt_invert,
        axis_min=spatial.tilt_min,
        axis_max=spatial.tilt_max,
    )
    return physical_pan, physical_tilt


def physical_to_semantic(
    spatial: SpatialPlacement,
    *,
    pan: float,
    tilt: float,
) -> tuple[float, float]:
    semantic_pan = inverse_axis(
        pan,
        offset=spatial.pan_offset,
        invert=spatial.pan_invert,
        axis_min=spatial.pan_min,
        axis_max=spatial.pan_max,
    )
    semantic_tilt = inverse_axis(
        tilt,
        offset=spatial.tilt_offset,
        invert=spatial.tilt_invert,
        axis_min=spatial.tilt_min,
        axis_max=spatial.tilt_max,
    )
    return semantic_pan, semantic_tilt


def split_16bit(normalized: float) -> tuple[int, int]:
    value = max(0, min(65535, int(round(clamp01(normalized) * 65535))))
    return (value >> 8) & 0xFF, value & 0xFF


def encode_axis_dmx(normalized: float, *, has_fine: bool) -> AxisPhysical:
    physical = clamp01(normalized)
    if has_fine:
        coarse, fine = split_16bit(physical)
        return AxisPhysical(
            normalized=physical,
            coarse=coarse,
            fine=fine,
            value_16bit=(coarse << 8) | fine,
        )
    coarse = max(0, min(255, int(round(physical * 255.0))))
    return AxisPhysical(normalized=physical, coarse=coarse, fine=None, value_16bit=None)


def decode_axis_dmx(coarse: int, fine: int | None) -> float:
    if fine is None:
        return clamp01(int(coarse) / 255.0)
    value = ((int(coarse) & 0xFF) << 8) | (int(fine) & 0xFF)
    return clamp01(value / 65535.0)


def _first_local(profile: FixtureProfile, role: ChannelRole) -> int | None:
    for channel in profile.channels:
        if channel.role is role:
            return channel.local
    return None


def pan_tilt_role_locals(profile: FixtureProfile) -> dict[str, int | None]:
    return {
        "pan_coarse": _first_local(profile, ChannelRole.PAN_COARSE),
        "pan_fine": _first_local(profile, ChannelRole.PAN_FINE),
        "tilt_coarse": _first_local(profile, ChannelRole.TILT_COARSE),
        "tilt_fine": _first_local(profile, ChannelRole.TILT_FINE),
    }


def missing_pan_tilt_roles(profile: FixtureProfile) -> list[str]:
    locals_map = pan_tilt_role_locals(profile)
    missing: list[str] = []
    if locals_map["pan_coarse"] is None:
        missing.append("Pan Coarse")
    if locals_map["tilt_coarse"] is None:
        missing.append("Tilt Coarse")
    return missing


def encode_fixture_pan_tilt(
    fixture: FixtureInstance,
    profile: FixtureProfile,
    *,
    semantic_pan: float,
    semantic_tilt: float,
) -> dict[str, object]:
    """Return DMX bytes + diagnostics for one Beam's pan/tilt."""
    physical_pan, physical_tilt = semantic_to_physical(
        fixture.spatial, pan=semantic_pan, tilt=semantic_tilt
    )
    roles = pan_tilt_role_locals(profile)
    pan_enc = encode_axis_dmx(physical_pan, has_fine=roles["pan_fine"] is not None)
    tilt_enc = encode_axis_dmx(physical_tilt, has_fine=roles["tilt_fine"] is not None)
    return {
        "semantic_pan": clamp01(semantic_pan),
        "semantic_tilt": clamp01(semantic_tilt),
        "physical_pan": physical_pan,
        "physical_tilt": physical_tilt,
        "pan": pan_enc,
        "tilt": tilt_enc,
        "roles": roles,
    }
