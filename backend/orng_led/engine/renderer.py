"""Map semantic intents to local/global DMX via fixture profiles."""

from __future__ import annotations

from orng_led.config.models import (
    DEFAULT_COLOR_PALETTE,
    DEFAULT_CONTROL_VALUES,
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


def _write_role_byte(
    frame: list[int],
    fixture: FixtureInstance,
    roles: dict[ChannelRole, list[ChannelDefinition]],
    role: ChannelRole,
    value: int,
) -> None:
    entries = roles.get(role)
    if not entries:
        return
    _write_local(frame, fixture, entries[0].local, int(value))


def _palette_table(channel: ChannelDefinition) -> dict[str, int]:
    if channel.palette:
        table = {str(key).lower(): int(value) for key, value in channel.palette.items()}
    else:
        table = dict(DEFAULT_COLOR_PALETTE)
    # Calibrated control_values (e.g. White/Open on a color wheel) override palette.
    if channel.control_values:
        for key, value in channel.control_values.items():
            table[str(key).lower()] = int(value)
    return table


def _control_table(channel: ChannelDefinition) -> dict[str, int]:
    table = dict(DEFAULT_CONTROL_VALUES)
    if channel.control_values:
        table.update(
            {str(key).lower(): int(value) for key, value in channel.control_values.items()}
        )
    # Palette may also carry off/white for dual-purpose channels.
    if channel.palette:
        for key, value in channel.palette.items():
            table.setdefault(str(key).lower(), int(value))
    return table


def _control_byte(channel: ChannelDefinition, key: str, default: int | None = None) -> int:
    table = _control_table(channel)
    if key in table:
        return int(table[key])
    if default is not None:
        return int(default)
    return int(DEFAULT_CONTROL_VALUES.get(key, 0))


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


def _intent_level(intent: FixtureIntent) -> float:
    if isinstance(intent, ParIntent):
        return float(intent.intensity)
    if isinstance(intent, (BarIntent, BeamIntent)):
        return float(intent.dimmer)
    return 0.0


def _is_near_white(color: Rgbw) -> bool:
    return color.r > 0.85 and color.g > 0.85 and color.b > 0.85


def missing_roles_for_intent(
    profile: FixtureProfile,
    intent: FixtureIntent,
) -> list[str]:
    """Human-readable missing mappings that prevent a full physical look."""
    roles = {channel.role for channel in profile.channels}
    level = _intent_level(intent)
    if level <= 0.02 and not (isinstance(intent, BeamIntent) and intent.shutter_open):
        return []

    missing: list[str] = []
    has_rgb = ChannelRole.RED in roles and ChannelRole.GREEN in roles and ChannelRole.BLUE in roles
    has_white = ChannelRole.WHITE in roles
    has_palette = bool(
        roles
        & {
            ChannelRole.COLOR,
            ChannelRole.WHOLE_COLOR,
            ChannelRole.SEGMENT_COLOR,
        }
    )
    has_dimmer = ChannelRole.DIMMER in roles
    has_any_color = has_rgb or has_white or has_palette

    if not has_dimmer and not has_any_color:
        missing.append("Master Dimmer")
    if not has_any_color:
        if isinstance(intent, BeamIntent):
            missing.append("Color Wheel: White/Open")
        elif isinstance(intent, BarIntent):
            missing.append("Whole Fixture Color / Segment Color: White")
        else:
            missing.append("RGB або White")
    elif _is_near_white(intent.color if hasattr(intent, "color") else Rgbw()):
        # White look with only a single primary mapped cannot be true white.
        if has_rgb:
            pass
        elif has_white or has_palette:
            pass
        elif ChannelRole.RED in roles and ChannelRole.GREEN not in roles:
            missing.append("Green/Blue (неповний RGB для білого)")

    if isinstance(intent, BeamIntent) and intent.shutter_open:
        if ChannelRole.SHUTTER not in roles and ChannelRole.STROBE not in roles:
            # Only warn when there is otherwise some output path — heads often need shutter.
            if has_any_color or has_dimmer:
                # Soft: shutter optional if dimmer alone opens the lamp on some heads.
                pass
        elif ChannelRole.SHUTTER in roles:
            shutter_ch = next(ch for ch in profile.channels if ch.role is ChannelRole.SHUTTER)
            table = _control_table(shutter_ch)
            if "open" not in table and shutter_ch.control_values is None:
                # Defaults exist — not incomplete.
                pass

    if ChannelRole.PROGRAM in roles and level > 0.02:
        program_ch = next(ch for ch in profile.channels if ch.role is ChannelRole.PROGRAM)
        table = _control_table(program_ch)
        if "off" not in table and program_ch.control_values is None and program_ch.palette is None:
            # Default off=0 is used; not incomplete.
            pass

    # Completely unmapped footprint: every channel unused.
    if all(ch.role is ChannelRole.UNUSED for ch in profile.channels):
        if isinstance(intent, BeamIntent):
            missing = ["Color Wheel: White/Open", "Master Dimmer"]
        elif isinstance(intent, BarIntent):
            missing = ["Whole Fixture Color / Segment Color: White", "Master Dimmer"]
        else:
            missing = ["жодна функція не призначена (усі канали unused)"]
    elif has_palette and _is_near_white(intent.color if hasattr(intent, "color") else Rgbw()):
        # Palette/wheel present but White/Open never calibrated by operator.
        color_channels = [
            ch
            for ch in profile.channels
            if ch.role in {ChannelRole.COLOR, ChannelRole.WHOLE_COLOR, ChannelRole.SEGMENT_COLOR}
        ]
        for channel in color_channels:
            table = _palette_table(channel)
            if "white" not in table and "open" not in table:
                if isinstance(intent, BeamIntent):
                    missing.append("Color Wheel: White/Open")
                else:
                    missing.append("палітра: White")
                break

    return missing


def fixture_emits_dmx(profile: FixtureProfile, intent: FixtureIntent) -> bool:
    """True when at least one non-fixed role can be written for this intent."""
    if _intent_level(intent) <= 0.02 and not (
        isinstance(intent, BeamIntent) and intent.shutter_open and _intent_level(intent) > 0
    ):
        return False
    roles = {channel.role for channel in profile.channels}
    writable = {
        ChannelRole.DIMMER,
        ChannelRole.RED,
        ChannelRole.GREEN,
        ChannelRole.BLUE,
        ChannelRole.WHITE,
        ChannelRole.AMBER,
        ChannelRole.STROBE,
        ChannelRole.SHUTTER,
        ChannelRole.SEGMENT,
        ChannelRole.SEGMENT_COLOR,
        ChannelRole.WHOLE_COLOR,
        ChannelRole.COLOR,
        ChannelRole.PROGRAM,
    }
    return bool(roles & writable)


def _apply_service_channels(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    roles: dict[ChannelRole, list[ChannelDefinition]],
    *,
    lit: bool,
    shutter_open: bool,
    strobe_level: float,
) -> None:
    """Open shutter / park program when a look is active so dark fixtures can light."""
    if lit:
        for channel in roles.get(ChannelRole.PROGRAM, []):
            _write_local(frame, fixture, channel.local, _control_byte(channel, "off", 0))
        for channel in roles.get(ChannelRole.DIRECTION_MODE, []):
            # Prefer neutral/manual if calibrated; otherwise leave 0.
            if channel.control_values:
                _write_local(
                    frame,
                    fixture,
                    channel.local,
                    _control_byte(channel, "off", _control_byte(channel, "neutral", 0)),
                )
        for channel in roles.get(ChannelRole.MOVEMENT_SPEED, []):
            if channel.control_values:
                _write_local(frame, fixture, channel.local, _control_byte(channel, "neutral", 128))

    for channel in roles.get(ChannelRole.SHUTTER, []):
        key = "open" if shutter_open else "closed"
        default = 255 if shutter_open else 0
        _write_local(frame, fixture, channel.local, _control_byte(channel, key, default))

    # Some fixtures use the strobe channel as open/shutter when not strobing.
    if ChannelRole.STROBE in roles and strobe_level <= 0.02 and lit and shutter_open:
        for channel in roles.get(ChannelRole.STROBE, []):
            table = _control_table(channel)
            if "open" in table:
                _write_local(frame, fixture, channel.local, table["open"])


def _apply_fixed_and_unused(
    frame: list[int], fixture: FixtureInstance, profile: FixtureProfile
) -> None:
    for channel in profile.channels:
        if channel.role is ChannelRole.FIXED and channel.fixed_value is not None:
            _write_local(frame, fixture, channel.local, channel.fixed_value)


def render_par(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: ParIntent,
) -> None:
    roles = _role_map(profile)
    lit = intent.intensity > 0.02
    color = intent.color.scaled(intent.intensity)
    _write_role(frame, fixture, roles, ChannelRole.DIMMER, intent.intensity)
    _write_role(frame, fixture, roles, ChannelRole.RED, color.r)
    _write_role(frame, fixture, roles, ChannelRole.GREEN, color.g)
    _write_role(frame, fixture, roles, ChannelRole.BLUE, color.b)
    _write_role(frame, fixture, roles, ChannelRole.WHITE, color.w)
    _write_role(frame, fixture, roles, ChannelRole.AMBER, color.r * 0.4)
    if intent.strobe > 0.02:
        _write_role(frame, fixture, roles, ChannelRole.STROBE, intent.strobe)
    _apply_service_channels(
        frame,
        fixture,
        profile,
        roles,
        lit=lit,
        shutter_open=lit,
        strobe_level=intent.strobe,
    )
    _apply_fixed_and_unused(frame, fixture, profile)


def render_bar(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: BarIntent,
) -> None:
    roles = _role_map(profile)
    lit = intent.dimmer > 0.02
    color = intent.color.scaled(intent.dimmer)
    _write_role(frame, fixture, roles, ChannelRole.DIMMER, intent.dimmer)
    _write_role(frame, fixture, roles, ChannelRole.RED, color.r)
    _write_role(frame, fixture, roles, ChannelRole.GREEN, color.g)
    _write_role(frame, fixture, roles, ChannelRole.BLUE, color.b)
    _write_role(frame, fixture, roles, ChannelRole.WHITE, color.w)
    if intent.strobe > 0.02:
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
        value = _palette_dmx(channel, intent.color, active=level > 0.05 and lit)
        _write_local(frame, fixture, channel.local, value)

    for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
        value = _palette_dmx(channel, intent.color, active=lit)
        _write_local(frame, fixture, channel.local, value)
    for channel in roles.get(ChannelRole.COLOR, []):
        value = _palette_dmx(channel, intent.color, active=lit)
        _write_local(frame, fixture, channel.local, value)

    _apply_service_channels(
        frame,
        fixture,
        profile,
        roles,
        lit=lit,
        shutter_open=lit,
        strobe_level=intent.strobe,
    )
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
    lit = intent.dimmer > 0.02
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
        value = _palette_dmx(channel, intent.color, active=lit)
        _write_local(frame, fixture, channel.local, value)
    for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
        value = _palette_dmx(channel, intent.color, active=lit)
        _write_local(frame, fixture, channel.local, value)
    if intent.strobe > 0.02:
        _write_role(frame, fixture, roles, ChannelRole.STROBE, intent.strobe)
    _apply_service_channels(
        frame,
        fixture,
        profile,
        roles,
        lit=lit,
        shutter_open=intent.shutter_open and lit,
        strobe_level=intent.strobe,
    )
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
            profile = show.profile_for(fixture)
            _apply_fixed_and_unused(frame, fixture, profile)
            continue
        if fixture.kind is FixtureKind.FACE_PAR and not isinstance(intent, ParIntent):
            continue
        profile = show.profile_for(fixture)
        render_fixture(frame, fixture, profile, intent, beam_motion)
    return frame


def analyze_live_effect_coverage(
    show: ShowConfig,
    stage: StageIntent,
    effect_id: str,
    target_ids: list[str],
) -> dict[str, object]:
    """Compare semantic targets vs fixtures that actually receive writable DMX roles."""
    from orng_led.engine.layers import LIVE_EFFECT_META

    meta = LIVE_EFFECT_META.get(effect_id, {"label": effect_id, "target_groups": []})
    applied: list[str] = []
    skipped: list[dict[str, object]] = []
    for fixture_id in target_ids:
        fixture = next((fx for fx in show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            continue
        intent = stage.fixtures.get(fixture_id)
        profile = show.profile_for(fixture)
        if intent is None:
            skipped.append(
                {
                    "fixture_id": fixture_id,
                    "label": fixture.label,
                    "missing": ["немає semantic intent"],
                }
            )
            continue
        missing = missing_roles_for_intent(profile, intent)
        if missing and not fixture_emits_dmx(profile, intent):
            skipped.append(
                {
                    "fixture_id": fixture_id,
                    "label": fixture.label,
                    "missing": missing,
                }
            )
        elif missing:
            # Partial mapping — still applied, but reported.
            applied.append(fixture_id)
            skipped.append(
                {
                    "fixture_id": fixture_id,
                    "label": fixture.label,
                    "missing": missing,
                    "partial": True,
                }
            )
        else:
            applied.append(fixture_id)

    return {
        "id": effect_id,
        "label": meta.get("label", effect_id),
        "target_groups": list(meta.get("target_groups", [])),  # type: ignore[arg-type]
        "target_fixture_ids": list(target_ids),
        "applied_fixture_ids": applied,
        "skipped": skipped,
    }
