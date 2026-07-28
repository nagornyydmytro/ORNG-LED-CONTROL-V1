"""Authoritative Beam orientation calibration test session.

Motion-only by default (dimmer/shutter off). Optional visible beam requires
explicit confirmation and mapped light roles. Never activates Art-Net/Arm.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orng_led.config.models import (
    ChannelRole,
    FixtureInstance,
    FixtureKind,
    FixtureProfile,
    ShowConfig,
)
from orng_led.config.validation import global_channel
from orng_led.engine.beam_transform import (
    clamp01,
    encode_fixture_pan_tilt,
    missing_pan_tilt_roles,
)
from orng_led.engine.frame import empty_frame
from orng_led.engine.intents import Rgbw
from orng_led.engine.renderer import _control_byte, _palette_dmx, _role_map

VISIBLE_BEAM_DIMMER = 0.12


@dataclass
class BeamCalibrationTestSession:
    active: bool = False
    fixture_id: str | None = None
    semantic_pan: float = 0.5
    semantic_tilt: float = 0.5
    visible_beam_requested: bool = False
    visible_beam_confirmed: bool = False
    frame: list[int] = field(default_factory=empty_frame)
    last_error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "fixture_id": self.fixture_id,
            "semantic_pan": self.semantic_pan,
            "semantic_tilt": self.semantic_tilt,
            "visible_beam_requested": self.visible_beam_requested,
            "visible_beam_confirmed": self.visible_beam_confirmed,
            "visible_beam_on": self.visible_beam_active,
            "nonzero_channels": int(sum(1 for value in self.frame if value)),
            "last_error": self.last_error,
        }

    @property
    def visible_beam_active(self) -> bool:
        return self.active and self.visible_beam_requested and self.visible_beam_confirmed


def _zero_fixture(frame: list[int], fixture: FixtureInstance, footprint: int) -> None:
    for local in range(1, footprint + 1):
        frame[global_channel(fixture.start_address, local) - 1] = 0


def visible_beam_blockers(
    show: ShowConfig,
    fixture: FixtureInstance,
    *,
    blackout: bool,
) -> list[str]:
    blockers: list[str] = []
    if blackout:
        blockers.append("Blackout блокує видимий тестовий промінь")
    profile = show.profile_for(fixture)
    roles = {ch.role for ch in profile.channels}
    if ChannelRole.DIMMER not in roles:
        blockers.append("не призначено Master Dimmer")
    if ChannelRole.SHUTTER not in roles and ChannelRole.STROBE not in roles:
        blockers.append("не призначено Shutter Open")
    has_color = bool(
        roles
        & {
            ChannelRole.COLOR,
            ChannelRole.WHOLE_COLOR,
            ChannelRole.RED,
            ChannelRole.GREEN,
            ChannelRole.BLUE,
            ChannelRole.WHITE,
        }
    )
    if not has_color:
        blockers.append("не налаштовано White/Open для Color Wheel")
    if ChannelRole.PROGRAM in roles:
        program = next(ch for ch in profile.channels if ch.role is ChannelRole.PROGRAM)
        # Calibrated off is optional when default 0 is acceptable; still require role mapped.
        _ = program
    return blockers


def render_calibration_frame(
    show: ShowConfig,
    session: BeamCalibrationTestSession,
    *,
    blackout: bool,
) -> list[int]:
    frame = empty_frame()
    if not session.active or not session.fixture_id:
        return frame
    fixture = next((fx for fx in show.patch.fixtures if fx.id == session.fixture_id), None)
    if fixture is None or fixture.kind is not FixtureKind.BEAM:
        return frame
    profile = show.profile_for(fixture)
    _zero_fixture(frame, fixture, profile.footprint)

    encoded = encode_fixture_pan_tilt(
        fixture,
        profile,
        semantic_pan=session.semantic_pan,
        semantic_tilt=session.semantic_tilt,
    )
    roles = encoded["roles"]
    pan_enc = encoded["pan"]
    tilt_enc = encoded["tilt"]
    assert isinstance(roles, dict)
    if roles.get("pan_coarse") is not None:
        frame[global_channel(fixture.start_address, int(roles["pan_coarse"])) - 1] = pan_enc.coarse  # type: ignore[union-attr]
        if roles.get("pan_fine") is not None and pan_enc.fine is not None:  # type: ignore[union-attr]
            frame[global_channel(fixture.start_address, int(roles["pan_fine"])) - 1] = pan_enc.fine  # type: ignore[union-attr]
    if roles.get("tilt_coarse") is not None:
        frame[global_channel(fixture.start_address, int(roles["tilt_coarse"])) - 1] = (
            tilt_enc.coarse
        )  # type: ignore[union-attr]
        if roles.get("tilt_fine") is not None and tilt_enc.fine is not None:  # type: ignore[union-attr]
            frame[global_channel(fixture.start_address, int(roles["tilt_fine"])) - 1] = (
                tilt_enc.fine
            )  # type: ignore[union-attr]

    if session.visible_beam_active and not visible_beam_blockers(show, fixture, blackout=blackout):
        _apply_min_visible_beam(frame, fixture, profile)

    session.frame = list(frame)
    return frame


def _apply_min_visible_beam(
    frame: list[int],
    fixture: FixtureInstance,
    profile: FixtureProfile,
) -> None:
    roles = _role_map(profile)
    white = Rgbw(r=1.0, g=1.0, b=1.0, w=1.0)
    level = VISIBLE_BEAM_DIMMER
    for channel in roles.get(ChannelRole.DIMMER, []):
        frame[global_channel(fixture.start_address, channel.local) - 1] = int(round(level * 255))
    # Profiles that park dimmer as FIXED 255 still need a visible RGB look below.
    for channel in roles.get(ChannelRole.SHUTTER, []):
        frame[global_channel(fixture.start_address, channel.local) - 1] = _control_byte(
            channel, "open", 255
        )
    for channel in roles.get(ChannelRole.PROGRAM, []):
        frame[global_channel(fixture.start_address, channel.local) - 1] = _control_byte(
            channel, "off", 0
        )
    for channel in roles.get(ChannelRole.COLOR, []):
        frame[global_channel(fixture.start_address, channel.local) - 1] = _palette_dmx(
            channel, white, active=True
        )
    for channel in roles.get(ChannelRole.WHOLE_COLOR, []):
        frame[global_channel(fixture.start_address, channel.local) - 1] = _palette_dmx(
            channel, white, active=True
        )
    for role, component in (
        (ChannelRole.RED, white.r),
        (ChannelRole.GREEN, white.g),
        (ChannelRole.BLUE, white.b),
        (ChannelRole.WHITE, white.w),
    ):
        for channel in roles.get(role, []):
            frame[global_channel(fixture.start_address, channel.local) - 1] = int(
                round(component * level * 255)
            )


def begin_blockers(show: ShowConfig, fixture_id: str) -> list[str]:
    fixture = next((fx for fx in show.patch.fixtures if fx.id == fixture_id), None)
    if fixture is None:
        return [f"невідомий прилад {fixture_id!r}"]
    if fixture.kind is not FixtureKind.BEAM:
        return [f"{fixture.label}: не є Beam Head"]
    profile = show.profile_for(fixture)
    missing = missing_pan_tilt_roles(profile)
    return [
        f"{fixture.label}: неможливо почати калібрування — не призначено {name}" for name in missing
    ]


def home_semantics(fixture: FixtureInstance) -> tuple[float, float]:
    return clamp01(fixture.spatial.home_pan), clamp01(fixture.spatial.home_tilt)
