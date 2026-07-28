"""Derive stage-simulator views strictly from the current DMX frame + patch."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from orng_led.config.models import ChannelRole, FixtureKind, ShowConfig
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.config.validation import global_channel
from orng_led.simulator.geometry import beam_vector


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
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
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
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
    strobe: float = 0.0
    dir_x: float = 0.0
    dir_y: float = -1.0
    dir_z: float = 0.0
    hit_x: float = 0.5
    hit_z: float = 0.5
    throw: float = 0.0


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


def _first(entries: list[tuple[int, int | None]]) -> int | None:
    return entries[0][0] if entries else None


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
            intensity = _u8(frame, start, _first(_role_locals(channels, ChannelRole.DIMMER)))
            view = ParFixtureView(
                id=fixture.id,
                label=fixture.label,
                kind=fixture.kind.value,
                side=fixture.spatial.side.value,
                ring=fixture.spatial.ring.value,
                face=fixture.spatial.face or fixture.kind is FixtureKind.FACE_PAR,
                order=fixture.spatial.order,
                r=_u8(frame, start, _first(_role_locals(channels, ChannelRole.RED))),
                g=_u8(frame, start, _first(_role_locals(channels, ChannelRole.GREEN))),
                b=_u8(frame, start, _first(_role_locals(channels, ChannelRole.BLUE))),
                w=_u8(frame, start, _first(_role_locals(channels, ChannelRole.WHITE))),
                intensity=intensity,
            )
            if fixture.kind is FixtureKind.FACE_PAR:
                faces.append(view)
            else:
                pars.append(view)

        elif fixture.kind is FixtureKind.BAR:
            segments = [0.0] * 8
            for local, segment_index in _role_locals(channels, ChannelRole.SEGMENT):
                if segment_index is None:
                    continue
                level = _u8(frame, start, local)
                idx = segment_index - 1
                if fixture.spatial.invert_segments:
                    idx = 7 - idx
                segments[idx] = level
            for local, segment_index in _role_locals(channels, ChannelRole.SEGMENT_COLOR):
                if segment_index is None:
                    continue
                level = _u8(frame, start, local)
                idx = segment_index - 1
                if fixture.spatial.invert_segments:
                    idx = 7 - idx
                if level > 0:
                    # Encoded palette values are not linear RGB; treat any
                    # non-zero as a fully-lit segment for stage visualization.
                    segments[idx] = max(segments[idx], 1.0)
            dimmer = _u8(frame, start, _first(_role_locals(channels, ChannelRole.DIMMER)))
            r = _u8(frame, start, _first(_role_locals(channels, ChannelRole.RED)))
            g = _u8(frame, start, _first(_role_locals(channels, ChannelRole.GREEN)))
            b = _u8(frame, start, _first(_role_locals(channels, ChannelRole.BLUE)))
            # Palette-only bars: when dimmer/segments are active but RGB roles are
            # absent, mirror the semantic look as near-white so sim matches Live FX.
            if dimmer > 0.02 and r + g + b < 0.05:
                has_palette = bool(
                    _role_locals(channels, ChannelRole.SEGMENT_COLOR)
                    or _role_locals(channels, ChannelRole.WHOLE_COLOR)
                    or _role_locals(channels, ChannelRole.COLOR)
                )
                if has_palette and (sum(segments) > 0 or dimmer > 0.02):
                    r = g = b = dimmer
            bars.append(
                BarFixtureView(
                    id=fixture.id,
                    label=fixture.label,
                    kind=fixture.kind.value,
                    side=fixture.spatial.side.value,
                    ring=fixture.spatial.ring.value,
                    order=fixture.spatial.order,
                    dimmer=dimmer,
                    r=r,
                    g=g,
                    b=b,
                    segments=segments,
                )
            )

        elif fixture.kind is FixtureKind.BEAM:
            pan_c = _role_locals(channels, ChannelRole.PAN_COARSE)
            pan_f = _role_locals(channels, ChannelRole.PAN_FINE)
            tilt_c = _role_locals(channels, ChannelRole.TILT_COARSE)
            tilt_f = _role_locals(channels, ChannelRole.TILT_FINE)
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

            placement = show.layout.placement_for(fixture.id)
            origin_x = placement.x if placement else 0.5
            origin_y = (1.0 - placement.y) if placement else 0.75
            origin_z = placement.z if placement else 0.85
            vector = beam_vector(
                pan=pan,
                tilt=tilt,
                origin_x=origin_x,
                origin_y=origin_y,
                origin_z=origin_z,
            )

            dimmer = _u8(frame, start, _first(_role_locals(channels, ChannelRole.DIMMER)))
            r = _u8(frame, start, _first(_role_locals(channels, ChannelRole.RED)))
            g = _u8(frame, start, _first(_role_locals(channels, ChannelRole.GREEN)))
            b = _u8(frame, start, _first(_role_locals(channels, ChannelRole.BLUE)))
            color_wheel = _role_locals(channels, ChannelRole.COLOR) or _role_locals(
                channels, ChannelRole.WHOLE_COLOR
            )
            if dimmer > 0.02 and r + g + b < 0.05 and color_wheel:
                # Palette/wheel heads: show semantic white when dimmer is up.
                r = g = b = dimmer
            # If shutter is unmapped but dimmer is active, treat as open for visualization.
            if not shutter and dimmer > 0.02:
                shutter_level = 1.0

            beams.append(
                BeamFixtureView(
                    id=fixture.id,
                    label=fixture.label,
                    kind=fixture.kind.value,
                    side=fixture.spatial.side.value,
                    order=fixture.spatial.order,
                    pan=pan,
                    tilt=tilt,
                    dimmer=dimmer,
                    shutter_open=shutter_level > 0.05 or dimmer > 0.02,
                    r=r,
                    g=g,
                    b=b,
                    strobe=_u8(frame, start, _first(_role_locals(channels, ChannelRole.STROBE))),
                    dir_x=vector.dir_x,
                    dir_y=vector.dir_y,
                    dir_z=vector.dir_z,
                    hit_x=vector.hit_x,
                    hit_z=vector.hit_z,
                    throw=vector.throw,
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
