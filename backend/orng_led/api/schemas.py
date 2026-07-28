"""Pydantic schemas for the HTTP/WebSocket API."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orng_led.simulator.decode import SimulatorView


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    transport: str
    output_armed: bool
    artnet_network_enabled: bool
    frontend_dist_present: bool
    ready: bool = True


class ReadyResponse(ApiModel):
    ready: bool
    engine: bool
    output: bool
    detail: str | None = None


class OutputState(ApiModel):
    transport: str
    preferred_transport: str = "mock"
    armed: bool
    last_error: str | None = None
    frames_sent: int
    network_allowed: bool
    udp_active: bool = False
    target_ip: str | None = None
    universe: int = 0
    # Compat: frame_sum / nonzero_channels always mirror the wire (outbound) frame.
    frame_sum: int = 0
    nonzero_channels: int = 0
    source_frame_sum: int = 0
    source_nonzero_channels: int = 0
    wire_frame_sum: int = 0
    wire_nonzero_channels: int = 0


class ActivateArtNetCommand(ApiModel):
    """Confirmed operator action to start Art-Net with zero frames only."""

    confirmed: bool = False
    client_command_id: str | None = None


class DeactivateArtNetCommand(ApiModel):
    client_command_id: str | None = None


class ArmOutputCommand(ApiModel):
    """Confirmed operator action to arm Art-Net while Blackout stays on."""

    confirmed: bool = False
    client_command_id: str | None = None


class DisarmOutputCommand(ApiModel):
    client_command_id: str | None = None


class EngineState(ApiModel):
    preset_id: str
    preset_time_s: float
    episode_index: int
    episode_time_s: float
    episode_count: int = 10
    cycle_duration_s: float = 180.0
    blackout: bool
    face_on: bool
    face_brightness: float
    strobe_held: bool
    white_hit_active: bool
    master_brightness: float
    time_s: float
    drop_active: bool = False
    color_hit_active: bool = False
    sweep_active: bool = False


class AppStateResponse(ApiModel):
    engine: EngineState
    output: OutputState
    presets: list[str]
    fixture_ids: list[str]
    frame: list[int]
    sequence: int
    preview_speed: float = 1.0
    simulator: SimulatorView
    raw_tester: dict[str, Any] | None = None


class PreviewSpeedCommand(ApiModel):
    value: Annotated[float, Field(ge=1.0, le=120.0)]
    client_command_id: str | None = None


class RawTesterSetCommand(ApiModel):
    channel: Annotated[int, Field(ge=1, le=512)] | None = None
    value: Annotated[int, Field(ge=0, le=255)] | None = None
    channels: dict[str, Annotated[int, Field(ge=0, le=255)]] | None = None
    client_command_id: str | None = None


class RawTesterCommand(ApiModel):
    client_command_id: str | None = None


class IdentifyFixtureCommand(ApiModel):
    fixture_id: str
    level: Annotated[int, Field(ge=0, le=255)] = 200
    client_command_id: str | None = None


class IdentifyGroupCommand(ApiModel):
    group: str
    level: Annotated[int, Field(ge=0, le=255)] = 180
    client_command_id: str | None = None


class ValidatePatchRequest(ApiModel):
    patch: dict[str, Any]
    profiles: dict[str, Any] | None = None


class SaveAppConfigRequest(ApiModel):
    config: dict[str, Any]


class SavePatchRequest(ApiModel):
    patch: dict[str, Any]


class SaveProfileRequest(ApiModel):
    profile: dict[str, Any]


class SaveLayoutRequest(ApiModel):
    layout: dict[str, Any]


class PresetCreateRequest(ApiModel):
    preset: dict[str, Any] | None = None
    id: str | None = None
    label: str | None = None


class PresetUpdateRequest(ApiModel):
    preset: dict[str, Any]


class PresetRenameRequest(ApiModel):
    label: Annotated[str, Field(min_length=1, max_length=80)]


class PresetDuplicateRequest(ApiModel):
    new_id: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
    label: str | None = None


class PresetPreviewRequest(ApiModel):
    speed: Annotated[float, Field(ge=1.0, le=120.0)] = 10.0


class InputButtonRequest(ApiModel):
    button_id: Annotated[int, Field(ge=1, le=16)]
    edge: Literal["press", "release", "pulse"] = "pulse"
    source: Literal["ui", "keyboard", "mock", "gpio"] = "mock"
    client_command_id: str | None = None


class InputKeyboardRequest(ApiModel):
    code: str
    type: Literal["keydown", "keyup"] = "keydown"
    repeat: bool = False
    client_command_id: str | None = None


class InputDispatchResponse(ApiModel):
    ok: bool = True
    accepted: bool
    reason: str | None = None
    idempotent_replay: bool = False
    state: AppStateResponse


class CommandAck(ApiModel):
    ok: bool = True
    idempotent_replay: bool = False
    state: AppStateResponse


class SelectPresetCommand(ApiModel):
    preset_id: str
    reset_clock: bool = True
    client_command_id: str | None = None


class StrobeCommand(ApiModel):
    action: Literal["press", "release"]
    client_command_id: str | None = None


class BlackoutCommand(ApiModel):
    enabled: bool | None = None
    client_command_id: str | None = None


class FaceCommand(ApiModel):
    enabled: bool
    brightness: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    client_command_id: str | None = None


class MasterBrightnessCommand(ApiModel):
    value: Annotated[float, Field(ge=0.0, le=1.0)]
    client_command_id: str | None = None


class WhiteHitCommand(ApiModel):
    client_command_id: str | None = None


Unit = Annotated[float, Field(ge=0.0, le=1.0)]


class DropCommand(ApiModel):
    action: Literal["press", "release"]
    client_command_id: str | None = None


class ColorHitCommand(ApiModel):
    """Optional explicit RGB; omitted → contrasting colour of the running look."""

    r: Unit | None = None
    g: Unit | None = None
    b: Unit | None = None
    client_command_id: str | None = None

    def rgb(self) -> tuple[float, float, float] | None:
        if self.r is None or self.g is None or self.b is None:
            return None
        return (self.r, self.g, self.b)


class SweepHitCommand(ColorHitCommand):
    pass


class FailsafeCommand(ApiModel):
    client_command_id: str | None = None


class ErrorBody(ApiModel):
    detail: str
    code: str | None = None


class WsServerMessage(ApiModel):
    type: Literal["state", "hello", "error"]
    state: AppStateResponse | None = None
    detail: str | None = None


class WsClientMessage(ApiModel):
    type: Literal["ping", "request_state", "focus_loss", "visibility_hidden"]
    client_command_id: str | None = None


def dump_config_model(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    raise TypeError(f"Unsupported config object: {type(model)!r}")
