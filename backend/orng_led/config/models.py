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
    DIMMER = "dimmer"
    RED = "red"
    GREEN = "green"
    BLUE = "blue"
    WHITE = "white"
    STROBE = "strobe"
    SHUTTER = "shutter"
    MACRO = "macro"
    SPEED = "speed"
    RESET = "reset"
    SEGMENT = "segment"
    PAN_COARSE = "pan_coarse"
    PAN_FINE = "pan_fine"
    TILT_COARSE = "tilt_coarse"
    TILT_FINE = "tilt_fine"
    COLOR = "color"
    GOBO = "gobo"
    FOCUS = "focus"
    PRISM = "prism"
    MOVEMENT_SPEED = "movement_speed"
    UNKNOWN = "unknown"


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


class TransportMode(StrEnum):
    MOCK = "mock"
    ARTNET = "artnet"


class ChannelDefinition(StrictModel):
    local: Annotated[int, Field(ge=1, le=DMX_CHANNEL_MAX)]
    role: ChannelRole
    label: str | None = None
    segment_index: Annotated[int, Field(ge=1, le=8)] | None = None
    notes: str | None = None


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
            if channel.role is ChannelRole.SEGMENT and channel.segment_index is None:
                raise ConfigError(
                    f"Profile {self.id!r}: segment channel {channel.local} needs segment_index."
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


class SpatialLayout(StrictModel):
    """Viewer-facing stage layout metadata."""

    schema_version: int = SCHEMA_VERSION
    viewer_facing: Literal[True] = True
    description: str = "Left/right are from the audience looking at the stage (canon §3.3)."
    fixtures: list[str] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)


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
