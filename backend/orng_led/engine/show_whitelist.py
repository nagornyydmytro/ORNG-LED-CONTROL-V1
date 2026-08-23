"""Show-mode DMX role whitelist.

Only these roles may be non-zero on the engine/Live-FX output path.
Raw tester, channel mapping and Beam calibration remain unrestricted.
"""

from __future__ import annotations

from orng_led.config.models import ChannelRole, ShowConfig
from orng_led.config.validation import global_channel

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


def light_roles() -> frozenset[ChannelRole]:
    return SHOW_ALLOWED_ROLES - {
        ChannelRole.PAN_COARSE,
        ChannelRole.PAN_FINE,
        ChannelRole.TILT_COARSE,
        ChannelRole.TILT_FINE,
        ChannelRole.FIXED,
    }


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
