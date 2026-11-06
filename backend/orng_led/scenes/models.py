"""Pydantic models for scene manifests, controls, and runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, field_validator

from orng_led.config.models import (
    ConfigError,
    ShowConfig,
    StrictModel,
    require_supported_schema_version,
)
from orng_led.config.schema import SCHEMA_VERSION
from orng_led.input.contract import InputAction


class SceneManifest(StrictModel):
    schema_version: int = SCHEMA_VERSION
    scene_id: Annotated[str, Field(min_length=1, max_length=64)]
    name: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)

    @field_validator("scene_id")
    @classmethod
    def _check_scene_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
            raise ConfigError(f"Invalid scene_id: {value!r}")
        return cleaned


class KeyboardBinding(StrictModel):
    code: Annotated[str, Field(min_length=1, max_length=64)]
    action: InputAction
    edge: Literal["press", "release", "pulse"]
    pad_slot: Annotated[int, Field(ge=0, le=8)] | None = None
    preset_id: Annotated[str, Field(min_length=1, max_length=32)] | None = None


class InputDeviceMatch(StrictModel):
    """USB identity for a scene input profile. ``vid``/``pid`` are hex or decimal."""

    vid: Annotated[str, Field(min_length=1, max_length=8)]
    pid: Annotated[str, Field(min_length=1, max_length=8)]


class InputProfile(StrictModel):
    """Named keyboard map. Bindings live in the scene; a source picks a profile."""

    label: Annotated[str, Field(min_length=1, max_length=80)]
    match: list[InputDeviceMatch] = Field(default_factory=list)
    keyboard: list[KeyboardBinding] = Field(default_factory=list)


class SceneControls(StrictModel):
    """Scene keyboard/input bindings.

    ``keyboard`` is the legacy flat map (CH57x observed codes) so an older
    runtime keeps the physical pad. ``profiles`` holds QWERTY and CH57x side
    by side. Pad preset slot assignments live only in ``app.yaml``.
    """

    schema_version: int = SCHEMA_VERSION
    keyboard: list[KeyboardBinding] = Field(default_factory=list)
    default_profile: Annotated[str, Field(min_length=1, max_length=32)] = "qwerty"
    profiles: dict[str, InputProfile] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)


class RuntimeState(StrictModel):
    schema_version: int = SCHEMA_VERSION
    active_scene_id: Annotated[str, Field(min_length=1, max_length=64)] = "orng-bar"

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)

    @field_validator("active_scene_id")
    @classmethod
    def _check_active(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
            raise ConfigError(f"Invalid active_scene_id: {value!r}")
        return cleaned


@dataclass(frozen=True)
class SceneSummary:
    scene_id: str
    name: str
    path: str
    error: str | None = None


@dataclass
class SceneBundle:
    """Fully loaded scene: show config + controls + manifest."""

    manifest: SceneManifest
    show: ShowConfig
    controls: SceneControls
    scene_dir: str
