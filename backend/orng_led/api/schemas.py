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
    build_id: str | None = None
    transport: str
    output_armed: bool
    artnet_network_enabled: bool
    frontend_dist_present: bool
    ready: bool = True
    frame_cycle_alive: bool = False
    scene_id: str | None = None
    scene_name: str | None = None
    blackout: bool | None = None
    install_mode: str | None = None
    platform: str | None = None
    arch: str | None = None
    scene_error: str | None = None
    degraded: bool = False
    udp_active: bool | None = None
    output_policy: str | None = None
    venue_ready_state: str | None = None
    venue_last_error: str | None = None
    ethernet_link: bool | None = None
    artnet_route_valid: bool | None = None
    keyboard_connected: bool | None = None
    startup_preset: str | None = None
    instance_id: str | None = None
    desk_role: str | None = None
    executor_online: bool | None = None
    frame_timing: dict[str, Any] | None = None


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
    source_owner: Literal["raw_tester", "engine", "none", "beam_calibration_test"] = "none"


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
    vertical_sweep_active: bool = False
    strobe_speed: float = 0.10
    sweep_speed: float = 0.10


class AppStateResponse(ApiModel):
    engine: EngineState
    output: OutputState
    presets: list[str]
    fixture_ids: list[str]
    frame: list[int]
    sequence: int
    preview_speed: float = 1.0
    pad_presets: list[str] = Field(default_factory=list)
    strobe_speed: float = 0.10
    sweep_speed: float = 0.10
    simulator: SimulatorView
    raw_tester: dict[str, Any] | None = None
    preset_editor_preview: dict[str, Any] | None = None
    live_effects: dict[str, Any] | None = None
    beam_calibration: dict[str, Any] | None = None
    bpm: dict[str, Any] | None = None
    scene_id: str | None = None
    scene_name: str | None = None
    desk_role: str | None = None
    executor_online: bool | None = None
    executor_installation_id: str | None = None
    capabilities: list[str] | None = None


class BpmSyncCommand(ApiModel):
    enabled: bool | None = None
    mode: str | None = None
    scope: str | None = None
    palette: str | None = None
    cast: str | None = None
    groups: list[str] | None = None
    excluded: list[str] | None = None
    character: str | None = None
    intensity: str | None = None
    period: float | str | None = None
    par_rhythm: bool | None = None
    accent: float | None = None
    client_command_id: str | None = None
    source_id: str | None = None


class NextLookCommand(ApiModel):
    client_command_id: str | None = None
    source_id: str | None = None


class PreviewSpeedCommand(ApiModel):
    value: Annotated[float, Field(ge=0.5, le=120.0)]
    client_command_id: str | None = None
    source_id: str | None = None


class PadPresetsCommand(ApiModel):
    preset_ids: Annotated[list[str], Field(min_length=9, max_length=9)]
    client_command_id: str | None = None
    source_id: str | None = None


class LiveFxSpeedCommand(ApiModel):
    strobe_speed: Annotated[float, Field(ge=0.05, le=1.0)] | None = None
    sweep_speed: Annotated[float, Field(ge=0.05, le=1.0)] | None = None
    persist: bool = True
    client_command_id: str | None = None
    source_id: str | None = None


class RawTesterSetCommand(ApiModel):
    channel: Annotated[int, Field(ge=1, le=512)] | None = None
    value: Annotated[int, Field(ge=0, le=255)] | None = None
    channels: dict[str, Annotated[int, Field(ge=0, le=255)]] | None = None
    client_command_id: str | None = None


class RawTesterCommand(ApiModel):
    client_command_id: str | None = None


class EditorEpisodePreviewRequest(ApiModel):
    """Temporary single-episode hardware check from the preset editor."""

    preset_id: Annotated[str, Field(min_length=1, max_length=32)]
    preset_label: Annotated[str, Field(min_length=1, max_length=80)] = "Чернетка"
    episode_index: Annotated[int, Field(ge=0, le=19)] = 0
    episode: dict[str, Any]
    client_command_id: str | None = None


class EditorEpisodePreviewStopRequest(ApiModel):
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
    base_version: int | None = None


class SavePatchRequest(ApiModel):
    patch: dict[str, Any]
    base_version: int | None = None


class SaveProfileRequest(ApiModel):
    profile: dict[str, Any]
    base_version: int | None = None


class SaveLayoutRequest(ApiModel):
    layout: dict[str, Any]
    base_version: int | None = None


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
    source_id: str | None = None


class InputKeyboardRequest(ApiModel):
    code: str
    type: Literal["keydown", "keyup"] = "keydown"
    repeat: bool = False
    client_command_id: str | None = None
    source_id: str | None = None
    profile_id: str | None = None


class ActivateSceneRequest(ApiModel):
    scene_id: Annotated[str, Field(min_length=1, max_length=64)]
    client_command_id: str | None = None


class InputActionRequest(ApiModel):
    """Direct InputAction dispatch (UI holds that are not pad buttons)."""

    action: Annotated[str, Field(min_length=1, max_length=64)]
    edge: Literal["press", "release", "pulse"] | None = None
    source: Literal["ui", "keyboard", "mock", "gpio"] = "ui"
    pad_slot: Annotated[int, Field(ge=0, le=8)] | None = None
    preset_id: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    client_command_id: str | None = None
    source_id: str | None = None


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
    source_id: str | None = None


class SeekEpisodeCommand(ApiModel):
    episode_index: Annotated[int, Field(ge=0, le=63)]
    client_command_id: str | None = None
    source_id: str | None = None


class FixtureChannelTestSetCommand(ApiModel):
    fixture_id: str
    local: Annotated[int, Field(ge=1, le=512)]
    value: Annotated[int, Field(ge=0, le=255)]
    client_command_id: str | None = None


class FixtureChannelTestCommand(ApiModel):
    fixture_id: str | None = None
    client_command_id: str | None = None


class StrobeCommand(ApiModel):
    action: Literal["press", "release"]
    client_command_id: str | None = None


class BlackoutCommand(ApiModel):
    enabled: bool | None = None
    client_command_id: str | None = None
    source_id: str | None = None


class FaceCommand(ApiModel):
    enabled: bool
    brightness: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    client_command_id: str | None = None
    source_id: str | None = None


class MasterBrightnessCommand(ApiModel):
    value: Annotated[float, Field(ge=0.0, le=1.0)]
    client_command_id: str | None = None
    source_id: str | None = None


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
    source_id: str | None = None

    def rgb(self) -> tuple[float, float, float] | None:
        if self.r is None or self.g is None or self.b is None:
            return None
        return (self.r, self.g, self.b)


class SweepHitCommand(ColorHitCommand):
    pass


class SweepCommand(ApiModel):
    action: Literal["press", "release"]
    mode: Literal["horizontal", "vertical"] = "horizontal"
    r: Unit | None = None
    g: Unit | None = None
    b: Unit | None = None
    client_command_id: str | None = None

    def rgb(self) -> tuple[float, float, float] | None:
        if self.r is None or self.g is None or self.b is None:
            return None
        return (self.r, self.g, self.b)


class BeamCalibrationBeginCommand(ApiModel):
    fixture_id: Annotated[str, Field(min_length=1)]
    confirmed: bool = False
    client_command_id: str | None = None


class BeamCalibrationPositionCommand(ApiModel):
    pan: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    tilt: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    client_command_id: str | None = None


class BeamCalibrationVisibleCommand(ApiModel):
    enabled: bool
    confirmed: bool = False
    client_command_id: str | None = None


class BeamCalibrationSaveCommand(ApiModel):
    fixture_id: Annotated[str, Field(min_length=1)]
    pan_invert: bool | None = None
    tilt_invert: bool | None = None
    pan_offset: Annotated[float, Field(ge=-0.5, le=0.5)] | None = None
    tilt_offset: Annotated[float, Field(ge=-0.5, le=0.5)] | None = None
    pan_min: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    pan_max: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    tilt_min: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    tilt_max: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    home_pan: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    home_tilt: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    max_pan_speed: Annotated[float, Field(gt=0.0, le=2.0)] | None = None
    max_tilt_speed: Annotated[float, Field(gt=0.0, le=2.0)] | None = None
    beam_calibration_confirmed: bool | None = None
    beam_calibration_notes: str | None = None
    client_command_id: str | None = None

    def spatial_update(self) -> dict[str, object]:
        data = self.model_dump(exclude_none=True, exclude={"fixture_id", "client_command_id"})
        return data


class FailsafeCommand(ApiModel):
    client_command_id: str | None = None
    source_id: str | None = None


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
