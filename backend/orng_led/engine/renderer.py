"""Map semantic intents to local/global DMX via fixture profiles."""

from __future__ import annotations

from orng_led.config.models import (
    DEFAULT_COLOR_PALETTE,
    ChannelDefinition,
    ChannelRole,
    FixtureInstance,
    FixtureKind,
    FixtureProfile,
    ShowConfig,
)
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.frame import empty_frame, write_channel
from orng_led.engine.intents import (
    BarIntent,
    BeamIntent,
    FixtureIntent,
    ParIntent,
    Rgbw,
    StageIntent,
)

_PALETTE_RGB: dict[str, tuple[float, float, float]] = {
    "off": (0.0, 0.0, 0.0),
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "white": (1.0, 1.0, 1.0),
    "amber": (1.0, 0.55, 0.0),
    "cyan": (0.0, 1.0, 1.0),
    "purple": (0.7, 0.0, 1.0),
}


def _role_map(profile: FixtureProfile) -> dict[ChannelRole, list[ChannelDefinition]]:
    mapping: dict[ChannelRole, list[ChannelDefinition]] = {}
    for channel in profile.channels:
        mapping.setdefault(channel.role, []).append(channel)
    return mapping


def _write_local(
    frame: list[int],
    fixture: FixtureInstance,
    local: int,
    value: float | int,
) -> None:
    if isinstance(value, float):
        dmx = value * 255.0
    else:
        dmx = float(value)
    write_channel(frame, global_channel(fixture.start_address, local), dmx)


def _write_role(
    frame: list[int],
    fixture: FixtureInstance,
    roles: dict[ChannelRole, list[ChannelDefinition]],
    role: ChannelRole,
    value: float,
) -> None:
    entries = roles.get(role)
    if not entries:
        return
    _write_local(frame, fixture, entries[0].local, value)


def _palette_table(channel: ChannelDefinition) -> dict[str, int]:
    if channel.palette:
        return {str(key).lower(): int(value) for key, value in channel.palette.items()}
    return dict(DEFAULT_COLOR_PALETTE)


def _nearest_palette_name(color: Rgbw) -> str:
    best_name = "off"
    best_distance = 1e9
    for name, (r, g, b) in _PALETTE_RGB.items():
        distance = (color.r - r) ** 2 + (color.g - g) ** 2 + (color.b - b) ** 2
        if distance < best_distance:
            best_distance = distance
            best_name = name
    if color.r + color.g + color.b < 0.05:
        return "off"
    return best_name


def _palette_dmx(channel: ChannelDefinition, color: Rgbw, *, active: bool) -> int:
    table = _palette_table(channel)
    if not active:
        return int(table.get("off", 0))
    name = _nearest_palette_name(color)
    return int(table.get(name, table.get("white", 64)))


def _apply_fixed_and_unused(
    frame: list[int], fixture: FixtureInstance, profile: FixtureProfile
) -> None:
    for channel in profile.channels:
        if channel.role is ChannelRole.FIXED and channel.fixed_value is not None:
            _write_local(frame, fixture, channel.local, channel.fixed_value)
        # UNUSED / UNKNOWN intentionally left at 0.


def render_par(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: ParIntent,
) -> None:
    roles = _role_map(profile)
    color = intent.color.scaled(intent.intensity)
    _write_role(frame, fixture, roles, ChannelRole.DIMMER, intent.intensity)
    _write_role(frame, fixture, roles, ChannelRole.RED, color.r)
    _write_role(frame, fixture, roles, ChannelRole.GREEN, color.g)
    _write_role(frame, fixture, roles, ChannelRole.BLUE, color.b)
    _write_role(frame, fixture, roles, ChannelRole.WHITE, color.w)
    _write_role(frame, fixture, roles, ChannelRole.AMBER, color.r * 0.4)
    _write_role(frame, fixture, roles, ChannelRole.STROBE, intent.strobe)
    _apply_fixed_and_unused(frame, fixture, profile)


def render_bar(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: BarIntent,
) -> None:
    roles = _role_map(profile)
    color = intent.color.scaled(intent.dimmer)
    _write_role(frame, fixture, roles, ChannelRole.DIMMER, intent.dimmer)
    _write_role(frame, fixture, roles, ChannelRole.RED, color.r)
    _write_role(frame, fixture, roles, ChannelRole.GREEN, color.g)
    _write_role(frame, fixture, roles, ChannelRole.BLUE, color.b)
    _write_role(frame, fixture, roles, ChannelRole.WHITE, color.w)
    _write_role(frame, fixture, roles, ChannelRole.STROBE, intent.strobe)

    invert = fixture.spatial.invert_segments
    segments = list(intent.segments)
    if len(segments) < 8:
        segments.extend([0.0] * (8 - len(segments)))
    if invert:
        segments = list(reversed(segments[:8]))

    for channel in roles.get(ChannelRole.SEGMENT, []):
        if channel.segment_index is None:
            continue
        level = segments[channel.segment_index - 1]
        _write_local(frame, fixture, channel.local, level)

    for channel in roles.get(ChannelRole.SEGMENT_COLOR, []):
        if channel.segment_index is None:
            continue
        level = segments[channel.segment_index - 1]
        value = _palette_dmx(channel, intent.color, active=level > 0.05 and intent.dimmer > 0.02)
        _write_local(frame, fixture, channel.local, value)

    for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
        value = _palette_dmx(channel, intent.color, active=intent.dimmer > 0.02)
        _write_local(frame, fixture, channel.local, value)
    for channel in roles.get(ChannelRole.COLOR, []):
        value = _palette_dmx(channel, intent.color, active=intent.dimmer > 0.02)
        _write_local(frame, fixture, channel.local, value)

    _apply_fixed_and_unused(frame, fixture, profile)


def _split_16bit(normalized: float) -> tuple[int, int]:
    value = max(0, min(65535, int(round(normalized * 65535))))
    return (value >> 8) & 0xFF, value & 0xFF


def render_beam(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: BeamIntent,
    motion: BeamMotionState,
) -> None:
    roles = _role_map(profile)
    pan = motion.pan
    tilt = motion.tilt
    if fixture.spatial.pan_invert:
        pan = 1.0 - pan
    if fixture.spatial.tilt_invert:
        tilt = 1.0 - tilt

    pan_hi, pan_lo = _split_16bit(pan)
    tilt_hi, tilt_lo = _split_16bit(tilt)

    for role, value in (
        (ChannelRole.PAN_COARSE, pan_hi),
        (ChannelRole.PAN_FINE, pan_lo),
        (ChannelRole.TILT_COARSE, tilt_hi),
        (ChannelRole.TILT_FINE, tilt_lo),
    ):
        entries = roles.get(role)
        if not entries:
            continue
        _write_local(frame, fixture, entries[0].local, value)

    color = intent.color.scaled(intent.dimmer)
    _write_role(frame, fixture, roles, ChannelRole.DIMMER, intent.dimmer)
    _write_role(frame, fixture, roles, ChannelRole.RED, color.r)
    _write_role(frame, fixture, roles, ChannelRole.GREEN, color.g)
    _write_role(frame, fixture, roles, ChannelRole.BLUE, color.b)
    for channel in roles.get(ChannelRole.COLOR, []):
        value = _palette_dmx(channel, intent.color, active=intent.dimmer > 0.02)
        _write_local(frame, fixture, channel.local, value)
    for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
        value = _palette_dmx(channel, intent.color, active=intent.dimmer > 0.02)
        _write_local(frame, fixture, channel.local, value)
    shutter = 1.0 if intent.shutter_open else 0.0
    _write_role(frame, fixture, roles, ChannelRole.SHUTTER, shutter)
    _write_role(frame, fixture, roles, ChannelRole.STROBE, intent.strobe)
    _apply_fixed_and_unused(frame, fixture, profile)


def render_fixture(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: FixtureIntent,
    beam_motion: dict[str, BeamMotionState],
) -> None:
    if isinstance(intent, ParIntent):
        render_par(frame, fixture, profile, intent)
    elif isinstance(intent, BarIntent):
        render_bar(frame, fixture, profile, intent)
    elif isinstance(intent, BeamIntent):
        motion = beam_motion.get(fixture.id) or BeamMotionState()
        render_beam(frame, fixture, profile, intent, motion)
    else:
        raise TypeError(f"Unsupported intent type: {type(intent)!r}")


def render_stage(
    show: ShowConfig,
    stage: StageIntent,
    beam_motion: dict[str, BeamMotionState],
) -> list[int]:
    frame = empty_frame()
    for fixture in show.patch.fixtures:
        intent = stage.fixtures.get(fixture.id)
        if intent is None:
            # Still apply fixed channel values when fixture has no look.
            profile = show.profile_for(fixture)
            _apply_fixed_and_unused(frame, fixture, profile)
            continue
        if fixture.kind is FixtureKind.FACE_PAR and not isinstance(intent, ParIntent):
            continue
        profile = show.profile_for(fixture)
        render_fixture(frame, fixture, profile, intent, beam_motion)
    return frame
