"""Pydantic models for versioned YAML configuration."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orng_led.config.schema import (
    DMX_CHANNEL_MAX,
    DMX_CHANNEL_MIN,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
)


class ConfigError(ValueError):
    """Raised when configuration is invalid or incompatible."""


class StrictModel(BaseModel):
    """Reject unknown fields so schema drift cannot be ignored silently."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def require_supported_schema_version(version: int) -> int:
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(str(v) for v in sorted(SUPPORTED_SCHEMA_VERSIONS))
        raise ConfigError(f"Unsupported schema_version={version}. Supported: {supported}.")
    return version


class ChannelRole(StrEnum):
    """Semantic DMX channel functions used by the mapping UI and renderer."""

    UNUSED = "unused"
    DIMMER = "dimmer"
    RED = "red"
    GREEN = "green"
    BLUE = "blue"
    WHITE = "white"
    AMBER = "amber"
    UV = "uv"
    STROBE = "strobe"
    STROBE_SPEED = "strobe_speed"
    SHUTTER = "shutter"
    PROGRAM = "program"
    EFFECT_SPEED = "effect_speed"
    DIRECTION_MODE = "direction_mode"
    WHOLE_COLOR = "whole_color"
    SEGMENT = "segment"
    SEGMENT_COLOR = "segment_color"
    PAN_COARSE = "pan_coarse"
    PAN_FINE = "pan_fine"
    TILT_COARSE = "tilt_coarse"
    TILT_FINE = "tilt_fine"
    MOVEMENT_SPEED = "movement_speed"
    COLOR = "color"
    GOBO = "gobo"
    GOBO_ROTATION = "gobo_rotation"
    PRISM = "prism"
    PRISM_ROTATION = "prism_rotation"
    FOCUS = "focus"
    ZOOM = "zoom"
    RESET = "reset"
    FIXED = "fixed"
    # Legacy aliases kept for older YAML / tests.
    MACRO = "macro"
    SPEED = "speed"
    UNKNOWN = "unknown"


DEFAULT_COLOR_PALETTE: dict[str, int] = {
    "off": 0,
    "red": 16,
    "green": 32,
    "blue": 48,
    "white": 64,
    "amber": 80,
    "cyan": 96,
    "purple": 112,
}


class ChannelDefinition(StrictModel):
    local: Annotated[int, Field(ge=1, le=DMX_CHANNEL_MAX)]
    role: ChannelRole
    label: str | None = None
    segment_index: Annotated[int, Field(ge=1, le=8)] | None = None
    notes: str | None = None
    fixed_value: Annotated[int, Field(ge=0, le=255)] | None = None
    palette: dict[str, int] | None = None

    @field_validator("palette")
    @classmethod
    def _palette_bounds(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        cleaned: dict[str, int] = {}
        for key, raw in value.items():
            number = int(raw)
            if number < 0 or number > 255:
                raise ConfigError(f"Palette value for {key!r} must be 0..255")
            cleaned[str(key)] = number
        return cleaned


class Capability(StrEnum):
    DIMMER = "dimmer"
    RGB = "rgb"
    WHITE = "white"
    STROBE = "strobe"
    SEGMENTS = "segments"
    PAN_TILT = "pan_tilt"
    COLOR_WHEEL = "color_wheel"
    GOBO = "gobo"
    FACE_ONLY = "face_only"


class FixtureKind(StrEnum):
    PAR = "par"
    BAR = "bar"
    BEAM = "beam"
    FACE_PAR = "face_par"


class Side(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    CENTER = "center"


class Ring(StrEnum):
    INNER = "inner"
    OUTER = "outer"
    NONE = "none"


class Orientation(StrEnum):
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"
    POINT = "point"


class MountPosition(StrEnum):
    CEILING = "ceiling"
    TRUSS = "truss"
    WALL = "wall"
    STAGE = "stage"
    FLOOR = "floor"


class TransportMode(StrEnum):
    MOCK = "mock"
    ARTNET = "artnet"


class FixtureProfile(StrictModel):
    schema_version: int = SCHEMA_VERSION
    id: Annotated[str, Field(min_length=1)]
    label: str
    kind: FixtureKind
    footprint: Annotated[int, Field(ge=1, le=DMX_CHANNEL_MAX)]
    hardware_verified: bool = False
    capabilities: list[Capability] = Field(default_factory=list)
    channels: list[ChannelDefinition] = Field(default_factory=list)
    segment_count: Annotated[int, Field(ge=0, le=8)] = 0
    notes: str | None = None

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)

    @model_validator(mode="after")
    def _check_channels(self) -> FixtureProfile:
        locals_seen: set[int] = set()
        for channel in self.channels:
            if channel.local in locals_seen:
                raise ConfigError(f"Profile {self.id!r}: duplicate local channel {channel.local}.")
            locals_seen.add(channel.local)
            if channel.local > self.footprint:
                raise ConfigError(
                    f"Profile {self.id!r}: local channel {channel.local} exceeds "
                    f"footprint {self.footprint}."
                )
            if channel.role in (ChannelRole.SEGMENT, ChannelRole.SEGMENT_COLOR) and (
                channel.segment_index is None
            ):
                raise ConfigError(
                    f"Profile {self.id!r}: {channel.role.value} channel {channel.local} "
                    "needs segment_index."
                )
            if channel.role is ChannelRole.FIXED and channel.fixed_value is None:
                raise ConfigError(
                    f"Profile {self.id!r}: fixed channel {channel.local} needs fixed_value."
                )

        if Capability.SEGMENTS in self.capabilities and self.segment_count != 8:
            raise ConfigError(
                f"Profile {self.id!r}: Bars use semantic 8 segments "
                f"(got segment_count={self.segment_count})."
            )
        return self


class SpatialPlacement(StrictModel):
    side: Side
    ring: Ring = Ring.NONE
    face: bool = False
    order: Annotated[int, Field(ge=1)] | None = None
    invert_segments: bool = False
    pan_invert: bool = False
    tilt_invert: bool = False
    notes: str | None = None


class FixtureInstance(StrictModel):
    id: Annotated[str, Field(min_length=1)]
    profile_id: Annotated[str, Field(min_length=1)]
    label: str
    start_address: Annotated[int, Field(ge=DMX_CHANNEL_MIN, le=DMX_CHANNEL_MAX)]
    kind: FixtureKind
    groups: list[str] = Field(default_factory=list)
    spatial: SpatialPlacement
    enabled: bool = True
    notes: str | None = None


Normalized = Annotated[float, Field(ge=-1.0, le=2.0)]


class StagePlacement(StrictModel):
    """Normalized viewer-facing stage coordinates for one fixture.

    ``x`` grows to the audience right, ``y`` grows downwards in the stage
    picture, ``z`` grows upstage (0 = downstage / closest to the audience).
    Values describe the drawn rig plan and stay PENDING HARDWARE until the
    venue day confirms real trim heights and distances.
    """

    fixture_id: Annotated[str, Field(min_length=1)]
    kind: FixtureKind
    x: Normalized
    y: Normalized
    z: Normalized = 0.8
    width: Annotated[float, Field(gt=0.0, le=1.0)] = 0.05
    height: Annotated[float, Field(gt=0.0, le=1.0)] = 0.05
    rotation_deg: Annotated[float, Field(ge=-180.0, le=180.0)] = 0.0
    orientation: Orientation = Orientation.POINT
    mount: MountPosition = MountPosition.TRUSS
    aim_x: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.0
    aim_y: Annotated[float, Field(ge=-1.0, le=1.0)] = -1.0
    aim_z: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.0
    zone: str | None = None
    notes: str | None = None


class SpatialLayout(StrictModel):
    """Viewer-facing stage layout metadata."""

    schema_version: int = SCHEMA_VERSION
    viewer_facing: Literal[True] = True
    description: str = "Left/right are from the audience looking at the stage (canon §3.3)."
    fixtures: list[str] = Field(default_factory=list)
    placements: list[StagePlacement] = Field(default_factory=list)
    cable_chain: list[str] = Field(default_factory=list)
    artnet_node: StagePlacement | None = None

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)

    @model_validator(mode="after")
    def _check_placements(self) -> SpatialLayout:
        seen: set[str] = set()
        for placement in self.placements:
            if placement.fixture_id in seen:
                raise ConfigError(f"Layout: duplicate placement for {placement.fixture_id!r}.")
            seen.add(placement.fixture_id)
        listed = set(self.fixtures)
        if self.placements and listed and seen != listed:
            raise ConfigError(
                "Layout placements must cover exactly layout.fixtures. "
                f"missing={sorted(listed - seen)}, extra={sorted(seen - listed)}."
            )
        for fixture_id in self.cable_chain:
            if listed and fixture_id not in listed:
                raise ConfigError(f"Layout cable_chain references unknown fixture {fixture_id!r}.")
        return self

    def placement_for(self, fixture_id: str) -> StagePlacement | None:
        for placement in self.placements:
            if placement.fixture_id == fixture_id:
                return placement
        return None


class PatchDocument(StrictModel):
    schema_version: int = SCHEMA_VERSION
    universe: Annotated[int, Field(ge=0, le=15)] = 0
    fixtures: list[FixtureInstance]

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)


class ArtNetSettings(StrictModel):
    target_ip: str | None = None
    universe: Annotated[int, Field(ge=0, le=15)] = 0
    fps: Annotated[int, Field(ge=1, le=60)] = 30
    udp_port: Annotated[int, Field(ge=1, le=65535)] = 6454
    hardware_verified: bool = False


class AppConfig(StrictModel):
    schema_version: int = SCHEMA_VERSION
    transport: TransportMode = TransportMode.MOCK
    output_armed: bool = False
    master_brightness: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    artnet: ArtNetSettings = Field(default_factory=ArtNetSettings)
    config_dir: str | None = None

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)

    @model_validator(mode="after")
    def _safe_defaults(self) -> AppConfig:
        if self.transport is TransportMode.ARTNET and self.output_armed:
            # Allowed as data, but HOME runtime must still refuse network send.
            pass
        if self.artnet.hardware_verified and self.artnet.target_ip is None:
            raise ConfigError("artnet.hardware_verified requires artnet.target_ip to be set.")
        return self


class ShowConfig(StrictModel):
    """Loaded show: profiles + patch + layout + app settings."""

    app: AppConfig
    profiles: dict[str, FixtureProfile]
    patch: PatchDocument
    layout: SpatialLayout

    def profile_for(self, fixture: FixtureInstance) -> FixtureProfile:
        try:
            return self.profiles[fixture.profile_id]
        except KeyError as exc:
            raise ConfigError(
                f"Fixture {fixture.id!r} references missing profile {fixture.profile_id!r}."
            ) from exc


def model_to_plain(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="python")
