"""Pydantic schemas for the HTTP/WebSocket API."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    armed: bool
    last_error: str | None = None
    frames_sent: int
    network_allowed: bool
    target_ip: str | None = None
    universe: int = 0


class EngineState(ApiModel):
    preset_id: str
    preset_time_s: float
    episode_index: int
    episode_time_s: float
    blackout: bool
    face_on: bool
    face_brightness: float
    strobe_held: bool
    white_hit_active: bool
    master_brightness: float
    time_s: float


class AppStateResponse(ApiModel):
    engine: EngineState
    output: OutputState
    presets: list[str]
    fixture_ids: list[str]
    frame: list[int]
    sequence: int


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
