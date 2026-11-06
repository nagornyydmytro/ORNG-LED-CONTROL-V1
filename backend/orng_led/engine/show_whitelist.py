"""Show-mode DMX role whitelist.

Only these roles may be non-zero on the engine/Live-FX output path.
Raw tester, channel mapping and Beam calibration remain unrestricted.
"""

from __future__ import annotations

from orng_led.config.models import ChannelRole, ShowConfig
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.config.validation import global_channel
from orng_led.engine.frame import empty_frame

# Roles permitted to carry non-zero values in normal show / Live FX output.
SHOW_ALLOWED_ROLES: frozenset[ChannelRole] = frozenset(
    {
        ChannelRole.DIMMER,
        ChannelRole.RED,
        ChannelRole.GREEN,
        ChannelRole.BLUE,
        ChannelRole.WHITE,
        ChannelRole.AMBER,
        ChannelRole.STROBE_SPEED,
        ChannelRole.PAN_COARSE,
        ChannelRole.PAN_FINE,
        ChannelRole.TILT_COARSE,
        ChannelRole.TILT_FINE,
        ChannelRole.SEGMENT,
        ChannelRole.SEGMENT_COLOR,
        ChannelRole.WHOLE_COLOR,
        ChannelRole.FIXED,
    }
)

SHOW_FORBIDDEN_ROLES: frozenset[ChannelRole] = frozenset(
    role for role in ChannelRole if role not in SHOW_ALLOWED_ROLES
)


def scrub_show_frame(show: ShowConfig, frame: list[int]) -> list[int]:
    """Zero every channel whose mapped role is outside the show whitelist.

    Includes ``unused`` and fixture-internal roles (program, gobo, …). Preset
    effects/transitions may only drive roles present in the saved channel mapping
    *and* listed in ``SHOW_ALLOWED_ROLES``.
    """
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            if channel.role in SHOW_ALLOWED_ROLES:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            if 0 <= index < len(frame):
                frame[index] = 0
    return frame


def unmapped_or_unused_nonzero(
    show: ShowConfig,
    frame: list[int],
) -> list[dict[str, object]]:
    """Non-zero cells that are unused / forbidden — must stay empty in show mode."""
    return forbidden_nonzero_channels(show, frame)


def assert_only_mapped_show_channels(show: ShowConfig, frame: list[int]) -> None:
    """Hard contract: preset/Live FX output never lights unmapped or forbidden roles."""
    assert_show_frame_clean(show, frame)


def forbidden_nonzero_channels(
    show: ShowConfig,
    frame: list[int],
) -> list[dict[str, object]]:
    """Return diagnostics for whitelist violations (fixture, local, role, value)."""
    hits: list[dict[str, object]] = []
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            if channel.role in SHOW_ALLOWED_ROLES:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            if 0 <= index < len(frame) and frame[index]:
                hits.append(
                    {
                        "fixture_id": fixture.id,
                        "local": channel.local,
                        "role": channel.role.value,
                        "value": int(frame[index]),
                    }
                )
    return hits


def assert_show_frame_clean(show: ShowConfig, frame: list[int]) -> None:
    hits = forbidden_nonzero_channels(show, frame)
    if hits:
        detail = ", ".join(
            f"{h['fixture_id']} ch{h['local']} {h['role']}={h['value']}" for h in hits[:12]
        )
        raise AssertionError(f"Show whitelist violated: {detail}")


AXIS_AND_SERVICE_ROLES: frozenset[ChannelRole] = frozenset(
    {
        ChannelRole.PAN_COARSE,
        ChannelRole.PAN_FINE,
        ChannelRole.TILT_COARSE,
        ChannelRole.TILT_FINE,
        ChannelRole.FIXED,
    }
)


def light_roles() -> frozenset[ChannelRole]:
    return SHOW_ALLOWED_ROLES - AXIS_AND_SERVICE_ROLES


_AXIS_ROLES: frozenset[ChannelRole] = frozenset(
    {
        ChannelRole.PAN_COARSE,
        ChannelRole.PAN_FINE,
        ChannelRole.TILT_COARSE,
        ChannelRole.TILT_FINE,
    }
)


def apply_blackout_constraint(show: ShowConfig, frame: list[int]) -> list[int]:
    """Last-wins blackout from a fully dark universe.

    Only mapped PAN/TILT already present on the source frame and documented
    FIXED of the current scene may be non-zero. Unmapped slots (including
    leftover Raw bytes) stay dark. Independent of who produced the frame.
    """
    outbound = empty_frame()
    limit = min(len(frame), len(outbound))
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            index = global_channel(fixture.start_address, channel.local) - 1
            if not (0 <= index < len(outbound)):
                continue
            if channel.role in _AXIS_ROLES:
                if index < limit:
                    outbound[index] = int(frame[index])
                continue
            if channel.role is ChannelRole.FIXED and channel.fixed_value is not None:
                outbound[index] = int(channel.fixed_value)
    return outbound


def blackout_allowed_exceptions(show: ShowConfig) -> dict[int, ChannelRole]:
    """0-based indices that may be non-zero under blackout (axes + FIXED)."""
    allowed: dict[int, ChannelRole] = {}
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            if channel.role not in AXIS_AND_SERVICE_ROLES:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            if 0 <= index < DMX_UNIVERSE_SIZE:
                allowed[index] = channel.role
    return allowed


def documented_fixed_values(show: ShowConfig) -> dict[int, int]:
    """0-based index → documented FIXED value for the current scene."""
    values: dict[int, int] = {}
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            if channel.role is not ChannelRole.FIXED or channel.fixed_value is None:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            if 0 <= index < DMX_UNIVERSE_SIZE:
                values[index] = int(channel.fixed_value)
    return values


def assert_blackout_universe(show: ShowConfig, frame: list[int] | bytes) -> None:
    """Every non-zero byte must be an allowed axis or documented FIXED.

    Unmapped / unused / light channels must be 0. This is not a skipped check.
    """
    payload = list(frame)
    if len(payload) != DMX_UNIVERSE_SIZE:
        raise AssertionError(f"blackout universe length {len(payload)} != {DMX_UNIVERSE_SIZE}")
    allowed = blackout_allowed_exceptions(show)
    fixed = documented_fixed_values(show)
    stray: list[str] = []
    for index, value in enumerate(payload):
        if not value:
            continue
        if index not in allowed:
            stray.append(f"ch{index + 1}={value}")
            continue
        expected = fixed.get(index)
        if expected is not None and int(value) != expected:
            stray.append(f"ch{index + 1} FIXED={value} != {expected}")
    if stray:
        raise AssertionError(
            "Blackout universe has values outside allowed axes/FIXED: " + ", ".join(stray[:16])
        )
    assert_lights_dark(show, payload)


def light_nonzero_channels(
    show: ShowConfig,
    frame: list[int],
) -> list[dict[str, object]]:
    """Non-zero light-emitting channels (excludes Beam axes and FIXED service)."""
    hold_ok = {
        ChannelRole.PAN_COARSE,
        ChannelRole.PAN_FINE,
        ChannelRole.TILT_COARSE,
        ChannelRole.TILT_FINE,
        ChannelRole.FIXED,
    }
    hits: list[dict[str, object]] = []
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            if channel.role in hold_ok:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            if 0 <= index < len(frame) and frame[index]:
                hits.append(
                    {
                        "fixture_id": fixture.id,
                        "local": channel.local,
                        "role": channel.role.value,
                        "value": int(frame[index]),
                    }
                )
    return hits


def assert_lights_dark(show: ShowConfig, frame: list[int]) -> None:
    hits = light_nonzero_channels(show, frame)
    if hits:
        detail = ", ".join(
            f"{h['fixture_id']} ch{h['local']} {h['role']}={h['value']}" for h in hits[:12]
        )
        raise AssertionError(f"Light channels not dark: {detail}")
