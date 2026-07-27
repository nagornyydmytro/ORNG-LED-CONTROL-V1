"""Derive stage-simulator views strictly from the current DMX frame + patch."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from orng_led.config.models import ChannelRole, FixtureKind, ShowConfig
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.config.validation import global_channel


class StrictView(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParFixtureView(StrictView):
    id: str
    label: str
    kind: str
    side: str
    ring: str
    face: bool
    order: int | None = None
    r: float
    g: float
    b: float
    w: float
    intensity: float


class BarFixtureView(StrictView):
    id: str
    label: str
    kind: str
    side: str
    ring: str
    order: int | None = None
    dimmer: float
    segments: list[float] = Field(min_length=8, max_length=8)


class BeamFixtureView(StrictView):
    id: str
    label: str
    kind: str
    side: str
    order: int | None = None
    pan: float
    tilt: float
    dimmer: float
    shutter_open: bool


class SimulatorView(StrictView):
    pars: list[ParFixtureView]
    bars: list[BarFixtureView]
    beams: list[BeamFixtureView]
    faces: list[ParFixtureView]
    nonzero_channels: int
    blackout_visual: bool


def _read(frame: list[int], start: int, local: int) -> int:
    channel = global_channel(start, local)
    return frame[channel - 1]


def _role_locals(profile_channels: list, role: ChannelRole) -> list[tuple[int, int | None]]:
    return [(ch.local, ch.segment_index) for ch in profile_channels if ch.role is role]


def _u8(frame: list[int], start: int, local: int | None) -> float:
    if local is None:
        return 0.0
    return _read(frame, start, local) / 255.0


def decode_simulator_view(show: ShowConfig, frame: list[int]) -> SimulatorView:
    if len(frame) != DMX_UNIVERSE_SIZE:
        raise ValueError(f"Frame must have {DMX_UNIVERSE_SIZE} channels")

    pars: list[ParFixtureView] = []
    bars: list[BarFixtureView] = []
    beams: list[BeamFixtureView] = []
    faces: list[ParFixtureView] = []

    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        start = fixture.start_address
        channels = profile.channels

        if fixture.kind in (FixtureKind.PAR, FixtureKind.FACE_PAR):
            dim_local = _role_locals(channels, ChannelRole.DIMMER)
            red = _role_locals(channels, ChannelRole.RED)
            green = _role_locals(channels, ChannelRole.GREEN)
            blue = _role_locals(channels, ChannelRole.BLUE)
            white = _role_locals(channels, ChannelRole.WHITE)
            intensity = _u8(frame, start, dim_local[0][0] if dim_local else None)
            view = ParFixtureView(
                id=fixture.id,
                label=fixture.label,
                kind=fixture.kind.value,
                side=fixture.spatial.side.value,
                ring=fixture.spatial.ring.value,
                face=fixture.spatial.face or fixture.kind is FixtureKind.FACE_PAR,
                order=fixture.spatial.order,
                r=_u8(frame, start, red[0][0] if red else None),
                g=_u8(frame, start, green[0][0] if green else None),
                b=_u8(frame, start, blue[0][0] if blue else None),
                w=_u8(frame, start, white[0][0] if white else None),
                intensity=intensity,
            )
            if fixture.kind is FixtureKind.FACE_PAR:
                faces.append(view)
            else:
                pars.append(view)

        elif fixture.kind is FixtureKind.BAR:
            dim_local = _role_locals(channels, ChannelRole.DIMMER)
            segments = [0.0] * 8
            for local, segment_index in _role_locals(channels, ChannelRole.SEGMENT):
                if segment_index is None:
                    continue
                level = _u8(frame, start, local)
                idx = segment_index - 1
                if fixture.spatial.invert_segments:
                    idx = 7 - idx
                segments[idx] = level
            bars.append(
                BarFixtureView(
                    id=fixture.id,
                    label=fixture.label,
                    kind=fixture.kind.value,
                    side=fixture.spatial.side.value,
                    ring=fixture.spatial.ring.value,
                    order=fixture.spatial.order,
                    dimmer=_u8(frame, start, dim_local[0][0] if dim_local else None),
                    segments=segments,
                )
            )

        elif fixture.kind is FixtureKind.BEAM:
            pan_c = _role_locals(channels, ChannelRole.PAN_COARSE)
            pan_f = _role_locals(channels, ChannelRole.PAN_FINE)
            tilt_c = _role_locals(channels, ChannelRole.TILT_COARSE)
            tilt_f = _role_locals(channels, ChannelRole.TILT_FINE)
            dim_local = _role_locals(channels, ChannelRole.DIMMER)
            shutter = _role_locals(channels, ChannelRole.SHUTTER)

            pan_hi = _read(frame, start, pan_c[0][0]) if pan_c else 0
            pan_lo = _read(frame, start, pan_f[0][0]) if pan_f else 0
            tilt_hi = _read(frame, start, tilt_c[0][0]) if tilt_c else 0
            tilt_lo = _read(frame, start, tilt_f[0][0]) if tilt_f else 0
            pan = ((pan_hi << 8) | pan_lo) / 65535.0
            tilt = ((tilt_hi << 8) | tilt_lo) / 65535.0
            if fixture.spatial.pan_invert:
                pan = 1.0 - pan
            if fixture.spatial.tilt_invert:
                tilt = 1.0 - tilt
            shutter_level = _u8(frame, start, shutter[0][0] if shutter else None)
            beams.append(
                BeamFixtureView(
                    id=fixture.id,
                    label=fixture.label,
                    kind=fixture.kind.value,
                    side=fixture.spatial.side.value,
                    order=fixture.spatial.order,
                    pan=pan,
                    tilt=tilt,
                    dimmer=_u8(frame, start, dim_local[0][0] if dim_local else None),
                    shutter_open=shutter_level > 0.05,
                )
            )

    nonzero = sum(1 for value in frame if value > 0)
    return SimulatorView(
        pars=pars,
        bars=bars,
        beams=beams,
        faces=faces,
        nonzero_channels=nonzero,
        blackout_visual=nonzero == 0,
    )
