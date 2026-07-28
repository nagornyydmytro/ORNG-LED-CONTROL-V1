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
    """Resolve a semantic colour via the operator-saved palette only.

    Missing palette entries are skipped (off) — never invent DMX values.
    """
    import logging

    table = _palette_table(channel)
    if not active:
        return int(table.get("off", 0))
    name = _nearest_palette_name(color)
    if name not in table:
        logging.getLogger(__name__).info(
            "Show colour %r not in saved palette for ch%s (%s); skipping",
            name,
            channel.local,
            channel.label,
        )
        return int(table.get("off", 0))
    return int(table[name])


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

    if isinstance(intent, BarIntent) and level > 0.02:
        if intent.whole:
            if ChannelRole.WHOLE_COLOR not in roles and ChannelRole.COLOR not in roles:
                missing.append("Whole Fixture / Fixed Color Palette")
        else:
            if ChannelRole.SEGMENT_COLOR not in roles and ChannelRole.SEGMENT not in roles:
                missing.append("Segment Color")
            elif ChannelRole.SEGMENT_COLOR in roles:
                segs = [
                    ch
                    for ch in profile.channels
                    if ch.role is ChannelRole.SEGMENT_COLOR and ch.segment_index is not None
                ]
                if len(segs) < 8:
                    missing.append(f"Segment Color ({len(segs)}/8)")
            # FIXED / strip-select is optional. Operators may leave that channel
            # unused so show mode never drives internal strip/mode programs.

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
        elif channel.role is ChannelRole.UNUSED:
            # Explicit zero: unused must never retain a previous fixed/service value.
            _write_local(frame, fixture, channel.local, 0)


def _write_strobe_speed(
    frame: list[int],
    fixture: FixtureInstance,
    roles: dict[ChannelRole, list[ChannelDefinition]],
    strobe: float,
) -> None:
    """Show-mode strobe uses STROBE_SPEED only; 0 means no strobe."""
    level = max(0.0, min(1.0, float(strobe)))
    if level <= 0.02:
        return
    _write_role(frame, fixture, roles, ChannelRole.STROBE_SPEED, level)


def _write_rgbw_look(
    frame: list[int],
    fixture: FixtureInstance,
    roles: dict[ChannelRole, list[ChannelDefinition]],
    color: Rgbw,
    *,
    look_level: float,
) -> None:
    """Write colour channels from the look (not Master). Missing roles are skipped."""
    level = max(0.0, min(1.0, look_level))
    scaled = color.scaled(level)
    has_rgb = ChannelRole.RED in roles and ChannelRole.GREEN in roles and ChannelRole.BLUE in roles
    if has_rgb:
        _write_role(frame, fixture, roles, ChannelRole.RED, scaled.r)
        _write_role(frame, fixture, roles, ChannelRole.GREEN, scaled.g)
        _write_role(frame, fixture, roles, ChannelRole.BLUE, scaled.b)
    if ChannelRole.WHITE in roles:
        # Prefer explicit white channel for near-white looks; otherwise fold w.
        white_level = scaled.w if scaled.w > 0.02 else (min(scaled.r, scaled.g, scaled.b))
        if white_level > 0.02:
            _write_role(frame, fixture, roles, ChannelRole.WHITE, white_level)
    if ChannelRole.AMBER in roles and scaled.r > 0.3 and scaled.g > 0.15 and scaled.b < 0.25:
        _write_role(frame, fixture, roles, ChannelRole.AMBER, scaled.r * 0.5)


def render_par(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: ParIntent,
    *,
    master: float = 1.0,
) -> None:
    roles = _role_map(profile)
    look = max(0.0, min(1.0, intent.intensity))
    master = max(0.0, min(1.0, master))
    _write_role(frame, fixture, roles, ChannelRole.DIMMER, look * master)
    _write_rgbw_look(frame, fixture, roles, intent.color, look_level=look)
    _write_strobe_speed(frame, fixture, roles, intent.strobe)
    _apply_fixed_and_unused(frame, fixture, profile)


def _enforce_bar_mode_exclusivity(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    *,
    whole: bool,
) -> None:
    """Guarantee segment patterning and whole-fixture palette are never both active.

    Solid ``whole`` looks that are rendered via all eight segment colours still
    keep ``whole_color`` at 0 (pixel-mode bars ignore the whole palette channel).
    """
    roles = _role_map(profile)
    has_segment_colors = bool(
        roles.get(ChannelRole.SEGMENT_COLOR) or roles.get(ChannelRole.SEGMENT)
    )
    if whole and has_segment_colors:
        for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
            _write_local(frame, fixture, channel.local, 0)
    elif whole:
        for channel in roles.get(ChannelRole.SEGMENT, []):
            _write_local(frame, fixture, channel.local, 0)
        for channel in roles.get(ChannelRole.SEGMENT_COLOR, []):
            _write_local(frame, fixture, channel.local, 0)
    else:
        for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
            _write_local(frame, fixture, channel.local, 0)


def render_bar(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: BarIntent,
    *,
    master: float = 1.0,
) -> None:
    roles = _role_map(profile)
    look = max(0.0, min(1.0, intent.dimmer))
    master = max(0.0, min(1.0, master))
    lit = look > 0.02
    _write_role(frame, fixture, roles, ChannelRole.DIMMER, look * master)
    _write_rgbw_look(frame, fixture, roles, intent.color, look_level=look)
    _write_strobe_speed(frame, fixture, roles, intent.strobe)

    invert = fixture.spatial.invert_segments
    segments = list(intent.segments)
    if len(segments) < 8:
        segments.extend([0.0] * (8 - len(segments)))
    if invert:
        segments = list(reversed(segments[:8]))

    has_segment_colors = bool(
        roles.get(ChannelRole.SEGMENT_COLOR) or roles.get(ChannelRole.SEGMENT)
    )

    if intent.whole and has_segment_colors:
        # Solid one-colour look (static/breathe/pulse): drive all eight segment
        # palette channels. Pixel-mode LED Bars ignore whole_color while the
        # strip/mode channel selects segmented strips — writing only whole_color
        # left the bar dark while chase (segments) worked.
        for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
            _write_local(frame, fixture, channel.local, 0)
        for channel in roles.get(ChannelRole.SEGMENT, []):
            if channel.segment_index is None:
                continue
            _write_local(frame, fixture, channel.local, 1.0 if lit else 0.0)
        for channel in roles.get(ChannelRole.SEGMENT_COLOR, []):
            if channel.segment_index is None:
                continue
            value = _palette_dmx(channel, intent.color, active=lit)
            _write_local(frame, fixture, channel.local, value)
    elif intent.whole:
        # Profiles without segment roles: fall back to whole-fixture palette.
        for channel in roles.get(ChannelRole.SEGMENT, []):
            _write_local(frame, fixture, channel.local, 0)
        for channel in roles.get(ChannelRole.SEGMENT_COLOR, []):
            _write_local(frame, fixture, channel.local, 0)
        for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
            value = _palette_dmx(channel, intent.color, active=lit)
            _write_local(frame, fixture, channel.local, value)
    else:
        # Patterned segment mode: whole_color must stay 0.
        for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
            _write_local(frame, fixture, channel.local, 0)
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

    # Profile FIXED from the saved mapping only; unused stays 0.
    _apply_fixed_and_unused(frame, fixture, profile)
    # Hard exclusivity after every write path.
    _enforce_bar_mode_exclusivity(frame, fixture, profile, whole=intent.whole)


def _write_beam_axes(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    motion: BeamMotionState,
) -> None:
    """Always emit last-valid pan/tilt clamped to saved min/max — never 0/0 by default."""
    from orng_led.engine.beam_transform import (
        clamp,
        encode_axis_dmx,
        missing_pan_tilt_roles,
        pan_tilt_role_locals,
        semantic_to_physical,
    )

    if missing_pan_tilt_roles(profile):
        return
    roles = _role_map(profile)
    semantic_pan = float(motion.last_valid_pan if motion.last_valid_pan is not None else motion.pan)
    semantic_tilt = float(
        motion.last_valid_tilt if motion.last_valid_tilt is not None else motion.tilt
    )
    physical_pan, physical_tilt = semantic_to_physical(
        fixture.spatial, pan=semantic_pan, tilt=semantic_tilt
    )
    # Final hard clamp — no preset/effect/transition may bypass saved ranges.
    physical_pan = clamp(
        physical_pan, float(fixture.spatial.pan_min), float(fixture.spatial.pan_max)
    )
    physical_tilt = clamp(
        physical_tilt, float(fixture.spatial.tilt_min), float(fixture.spatial.tilt_max)
    )
    role_locals = pan_tilt_role_locals(profile)
    pan_enc = encode_axis_dmx(physical_pan, has_fine=role_locals["pan_fine"] is not None)
    tilt_enc = encode_axis_dmx(physical_tilt, has_fine=role_locals["tilt_fine"] is not None)
    for role, byte_value in (
        (ChannelRole.PAN_COARSE, pan_enc.coarse),
        (ChannelRole.PAN_FINE, pan_enc.fine),
        (ChannelRole.TILT_COARSE, tilt_enc.coarse),
        (ChannelRole.TILT_FINE, tilt_enc.fine),
    ):
        if byte_value is None:
            continue
        entries = roles.get(role)
        if not entries:
            continue
        _write_local(frame, fixture, entries[0].local, int(byte_value))


def render_beam(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: BeamIntent | None,
    motion: BeamMotionState,
    *,
    master: float = 1.0,
) -> None:
    """Write axes always; light only when confirmed + intent dimmer > 0."""
    _write_beam_axes(frame, fixture, profile, motion)
    roles = _role_map(profile)
    confirmed = bool(fixture.spatial.beam_calibration_confirmed)
    if intent is None or not confirmed:
        _apply_fixed_and_unused(frame, fixture, profile)
        return

    look = max(0.0, min(1.0, intent.dimmer))
    master = max(0.0, min(1.0, master))
    if look <= 0.02:
        _apply_fixed_and_unused(frame, fixture, profile)
        return

    _write_role(frame, fixture, roles, ChannelRole.DIMMER, look * master)
    _write_rgbw_look(frame, fixture, roles, intent.color, look_level=look)
    _write_strobe_speed(frame, fixture, roles, intent.strobe)
    _apply_fixed_and_unused(frame, fixture, profile)


def render_fixture(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
    intent: FixtureIntent,
    beam_motion: dict[str, BeamMotionState],
    *,
    master: float = 1.0,
) -> None:
    if isinstance(intent, ParIntent):
        render_par(frame, fixture, profile, intent, master=master)
    elif isinstance(intent, BarIntent):
        render_bar(frame, fixture, profile, intent, master=master)
    elif isinstance(intent, BeamIntent):
        motion = beam_motion.get(fixture.id) or BeamMotionState.from_home(
            float(fixture.spatial.home_pan),
            float(fixture.spatial.home_tilt),
        )
        render_beam(frame, fixture, profile, intent, motion, master=master)
    else:
        raise TypeError(f"Unsupported intent type: {type(intent)!r}")


def render_stage(
    show: ShowConfig,
    stage: StageIntent,
    beam_motion: dict[str, BeamMotionState],
    *,
    master: float = 1.0,
    scrub: bool = True,
) -> list[int]:
    """Render show frame. Beam axes always follow last_valid pose; light follows intents."""
    from orng_led.engine.show_whitelist import scrub_show_frame

    frame = empty_frame()
    master = max(0.0, min(1.0, master))
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        intent = stage.fixtures.get(fixture.id)

        if fixture.kind is FixtureKind.BEAM:
            motion = beam_motion.get(fixture.id) or BeamMotionState.from_home(
                float(fixture.spatial.home_pan),
                float(fixture.spatial.home_tilt),
            )
            if isinstance(intent, BeamIntent):
                render_beam(frame, fixture, profile, intent, motion, master=master)
            else:
                render_beam(frame, fixture, profile, None, motion, master=master)
            continue

        if intent is None:
            _apply_fixed_and_unused(frame, fixture, profile)
            continue
        if fixture.kind is FixtureKind.FACE_PAR and not isinstance(intent, ParIntent):
            continue
        render_fixture(frame, fixture, profile, intent, beam_motion, master=master)

    if scrub:
        scrub_show_frame(show, frame)
    return frame


def safe_dark_frame(
    show: ShowConfig,
    beam_motion: dict[str, BeamMotionState] | None = None,
) -> list[int]:
    """Lights off, Beam axes parked at last_valid/home — never Pan/Tilt 0/0."""
    motion = dict(beam_motion or {})
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM and fixture.id not in motion:
            motion[fixture.id] = BeamMotionState.from_home(
                float(fixture.spatial.home_pan),
                float(fixture.spatial.home_tilt),
            )
    return render_stage(show, StageIntent(), motion, master=0.0, scrub=True)


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
