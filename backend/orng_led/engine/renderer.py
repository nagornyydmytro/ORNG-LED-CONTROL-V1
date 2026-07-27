"""Map semantic intents to local/global DMX via fixture profiles."""

from __future__ import annotations

from orng_led.config.models import (
    ChannelRole,
    FixtureInstance,
    FixtureKind,
    FixtureProfile,
    ShowConfig,
)
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.frame import empty_frame, write_channel
from orng_led.engine.intents import BarIntent, BeamIntent, FixtureIntent, ParIntent, StageIntent


def _role_map(profile: FixtureProfile) -> dict[ChannelRole, list[tuple[int, int | None]]]:
    mapping: dict[ChannelRole, list[tuple[int, int | None]]] = {}
    for channel in profile.channels:
        mapping.setdefault(channel.role, []).append((channel.local, channel.segment_index))
    return mapping


def _write_role(
    frame: list[int],
    fixture: FixtureInstance,
    roles: dict[ChannelRole, list[tuple[int, int | None]]],
    role: ChannelRole,
    value: float,
) -> None:
    entries = roles.get(role)
    if not entries:
        return
    local, _ = entries[0]
    write_channel(frame, global_channel(fixture.start_address, local), value * 255.0)


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
    _write_role(frame, fixture, roles, ChannelRole.STROBE, intent.strobe)


def render_bar(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: BarIntent,
) -> None:
    roles = _role_map(profile)
    _write_role(frame, fixture, roles, ChannelRole.DIMMER, intent.dimmer)
    _write_role(frame, fixture, roles, ChannelRole.STROBE, intent.strobe)
    invert = fixture.spatial.invert_segments
    segments = list(intent.segments)
    if len(segments) < 8:
        segments.extend([0.0] * (8 - len(segments)))
    if invert:
        segments = list(reversed(segments[:8]))
    for local, segment_index in roles.get(ChannelRole.SEGMENT, []):
        if segment_index is None:
            continue
        level = segments[segment_index - 1]
        write_channel(frame, global_channel(fixture.start_address, local), level * 255.0)


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
        local, _ = entries[0]
        write_channel(frame, global_channel(fixture.start_address, local), value)

    _write_role(frame, fixture, roles, ChannelRole.DIMMER, intent.dimmer)
    _write_role(frame, fixture, roles, ChannelRole.COLOR, intent.color)
    shutter = 1.0 if intent.shutter_open else 0.0
    _write_role(frame, fixture, roles, ChannelRole.SHUTTER, shutter)
    _write_role(frame, fixture, roles, ChannelRole.STROBE, intent.strobe)


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
            continue
        if fixture.kind is FixtureKind.FACE_PAR and not isinstance(intent, ParIntent):
            continue
        profile = show.profile_for(fixture)
        render_fixture(frame, fixture, profile, intent, beam_motion)
    return frame
