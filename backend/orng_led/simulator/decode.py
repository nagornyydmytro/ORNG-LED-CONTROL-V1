"""Derive stage-simulator views strictly from the current DMX frame + patch."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from orng_led.config.models import ChannelDefinition, ChannelRole, FixtureKind, ShowConfig
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.config.validation import global_channel
from orng_led.engine.renderer import _PALETTE_RGB, _palette_table
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
    physical_pan: float = 0.5
    physical_tilt: float = 0.5
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
    mount: str = "truss"
    calibration_confirmed: bool = False
    calibration_blocker: str | None = None


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


def _rgb_from_palette_dmx(
    channel: ChannelDefinition | None,
    dmx_value: int,
) -> tuple[float, float, float] | None:
    """Map a fixture palette byte back to approximate linear RGB for the stage sim."""
    if channel is None or dmx_value <= 0:
        return None
    table = _palette_table(channel)
    best_name: str | None = None
    best_dist = 1_000
    for name, value in table.items():
        dist = abs(int(value) - int(dmx_value))
        if dist < best_dist:
            best_dist = dist
            best_name = str(name).lower()
    # Palette slots on LED Bars are ~30 DMX apart; reject far mismatches.
    if best_name is None or best_name == "off" or best_dist > 15:
        return None
    rgb = _PALETTE_RGB.get(best_name)
    if rgb is None:
        return None
    return float(rgb[0]), float(rgb[1]), float(rgb[2])


def _first_channel(channels: list[ChannelDefinition], role: ChannelRole) -> ChannelDefinition | None:
    for channel in channels:
        if channel.role is role:
            return channel
    return None


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
            segment_rgbs: list[tuple[float, float, float] | None] = [None] * 8
            for local, segment_index in _role_locals(channels, ChannelRole.SEGMENT):
                if segment_index is None:
                    continue
                level = _u8(frame, start, local)
                idx = segment_index - 1
                if fixture.spatial.invert_segments:
                    idx = 7 - idx
                segments[idx] = level
            for channel in channels:
                if channel.role is not ChannelRole.SEGMENT_COLOR or channel.segment_index is None:
                    continue
                dmx = _read(frame, start, channel.local)
                idx = channel.segment_index - 1
                if fixture.spatial.invert_segments:
                    idx = 7 - idx
                if dmx > 0:
                    # Encoded palette values are not linear RGB; any non-zero
                    # means the segment is lit for stage visualization.
                    segments[idx] = max(segments[idx], 1.0)
                    segment_rgbs[idx] = _rgb_from_palette_dmx(channel, dmx)
            dimmer = _u8(frame, start, _first(_role_locals(channels, ChannelRole.DIMMER)))
            r = _u8(frame, start, _first(_role_locals(channels, ChannelRole.RED)))
            g = _u8(frame, start, _first(_role_locals(channels, ChannelRole.GREEN)))
            b = _u8(frame, start, _first(_role_locals(channels, ChannelRole.BLUE)))
            whole_channel = _first_channel(channels, ChannelRole.WHOLE_COLOR)
            whole_local = whole_channel.local if whole_channel is not None else None
            whole_dmx = _read(frame, start, whole_local) if whole_local is not None else 0
            whole_level = whole_dmx / 255.0
            # Post-whitelist equivalence: solid whole-bar when CH whole is lit and
            # every segment colour/intensity channel is dark.
            if whole_level > 0.02 and sum(segments) < 0.05 and dimmer > 0.02:
                segments = [1.0] * 8
                whole_rgb = _rgb_from_palette_dmx(whole_channel, whole_dmx)
                if whole_rgb is not None:
                    segment_rgbs = [whole_rgb] * 8
            # Palette-only bars: decode saved palette slots → real RGB for the пульт.
            if dimmer > 0.02 and r + g + b < 0.05:
                picked: tuple[float, float, float] | None = None
                for level, rgb in zip(segments, segment_rgbs, strict=True):
                    if level > 0.02 and rgb is not None and sum(rgb) > 0.05:
                        picked = rgb
                        break
                if picked is None and whole_dmx > 0:
                    picked = _rgb_from_palette_dmx(whole_channel, whole_dmx)
                if picked is not None:
                    r, g, b = picked
                elif sum(segments) > 0.05:
                    # Lit but unknown palette slot — keep a neutral fallback.
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
            from orng_led.engine.beam_transform import (
                decode_axis_dmx,
                physical_to_semantic,
            )

            pan_c = _role_locals(channels, ChannelRole.PAN_COARSE)
            pan_f = _role_locals(channels, ChannelRole.PAN_FINE)
            tilt_c = _role_locals(channels, ChannelRole.TILT_COARSE)
            tilt_f = _role_locals(channels, ChannelRole.TILT_FINE)
            shutter = _role_locals(channels, ChannelRole.SHUTTER)

            pan_coarse = _read(frame, start, pan_c[0][0]) if pan_c else 0
            pan_fine = _read(frame, start, pan_f[0][0]) if pan_f else None
            tilt_coarse = _read(frame, start, tilt_c[0][0]) if tilt_c else 0
            tilt_fine = _read(frame, start, tilt_f[0][0]) if tilt_f else None
            physical_pan = decode_axis_dmx(pan_coarse, pan_fine if pan_f else None)
            physical_tilt = decode_axis_dmx(tilt_coarse, tilt_fine if tilt_f else None)
            pan, tilt = physical_to_semantic(fixture.spatial, pan=physical_pan, tilt=physical_tilt)
            shutter_level = _u8(frame, start, shutter[0][0] if shutter else None)

            placement = show.layout.placement_for(fixture.id)
            origin_x = placement.x if placement else 0.5
            origin_y = (1.0 - placement.y) if placement else 0.75
            origin_z = placement.z if placement else 0.85
            mount = placement.mount.value if placement is not None else "truss"
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
            # Beams with FIXED full dimmer and no DIMMER role: infer "on" from RGB.
            if dimmer < 0.02 and (r + g + b) > 0.05:
                dimmer = 1.0
            if dimmer > 0.02 and r + g + b < 0.05 and color_wheel:
                r = g = b = dimmer
            if not shutter and dimmer > 0.02:
                shutter_level = 1.0

            confirmed = bool(fixture.spatial.beam_calibration_confirmed)
            blocker = None if confirmed else "Не відкалібровано — фізичний світ Beam заблоковано"

            beams.append(
                BeamFixtureView(
                    id=fixture.id,
                    label=fixture.label,
                    kind=fixture.kind.value,
                    side=fixture.spatial.side.value,
                    order=fixture.spatial.order,
                    pan=pan,
                    tilt=tilt,
                    physical_pan=physical_pan,
                    physical_tilt=physical_tilt,
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
                    mount=mount,
                    calibration_confirmed=confirmed,
                    calibration_blocker=blocker,
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
