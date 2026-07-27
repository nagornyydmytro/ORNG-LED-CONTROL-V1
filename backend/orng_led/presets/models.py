"""Semantic preset document models (YAML-backed, not raw DMX)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orng_led.config.models import require_supported_schema_version
from orng_led.config.schema import SCHEMA_VERSION

PALETTES = (
    "warm_orange",
    "deep_red",
    "amber",
    "white_warm",
    "violet_orange",
    "cool_blue",
    "mint",
    "magenta",
)

EFFECTS = (
    "static",
    "pulse",
    "wave",
    "chase",
    "mirror_sweep",
    "breathe",
)

TRANSITIONS = ("cut", "soft", "fade")

GROUP_CHOICES = (
    "all_rear",
    "par",
    "bar",
    "beam",
    "outer",
    "inner",
    "left",
    "right",
    "rear",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EpisodeCard(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=64)]
    duration_s: Annotated[float, Field(gt=0.0, le=180.0)] = 18.0
    groups: list[str] = Field(default_factory=lambda: ["all_rear"])
    palette: str = "warm_orange"
    effect: str = "pulse"
    speed: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    intensity: Annotated[float, Field(ge=0.0, le=1.0)] = 0.7
    transition: str = "soft"
    notes: str | None = None

    @field_validator("palette")
    @classmethod
    def _palette(cls, value: str) -> str:
        if value not in PALETTES:
            raise ValueError(f"Unknown palette {value!r}. Allowed: {', '.join(PALETTES)}")
        return value

    @field_validator("effect")
    @classmethod
    def _effect(cls, value: str) -> str:
        if value not in EFFECTS:
            raise ValueError(f"Unknown effect {value!r}. Allowed: {', '.join(EFFECTS)}")
        return value

    @field_validator("transition")
    @classmethod
    def _transition(cls, value: str) -> str:
        if value not in TRANSITIONS:
            raise ValueError(f"Unknown transition {value!r}. Allowed: {', '.join(TRANSITIONS)}")
        return value

    @field_validator("groups")
    @classmethod
    def _groups(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("Episode must target at least one group")
        unknown = [g for g in value if g not in GROUP_CHOICES]
        if unknown:
            raise ValueError(f"Unknown groups: {unknown}")
        return value


class PresetDocument(StrictModel):
    schema_version: int = SCHEMA_VERSION
    id: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
    label: Annotated[str, Field(min_length=1, max_length=80)]
    hardware_tuned: bool = False
    builtin: bool = False
    episodes: list[EpisodeCard] = Field(min_length=1, max_length=20)

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)

    @model_validator(mode="after")
    def _check_doc(self) -> PresetDocument:
        if self.hardware_tuned:
            # HOME editor must not claim stage tuning.
            object.__setattr__(self, "hardware_tuned", False)
        ids = [ep.id for ep in self.episodes]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Preset {self.id!r}: duplicate episode ids")
        total = sum(ep.duration_s for ep in self.episodes)
        if total <= 0:
            raise ValueError(f"Preset {self.id!r}: total duration must be > 0")
        return self

    @property
    def total_duration_s(self) -> float:
        return sum(ep.duration_s for ep in self.episodes)


class PresetSummary(StrictModel):
    id: str
    label: str
    builtin: bool
    hardware_tuned: bool
    episode_count: int
    total_duration_s: float
    source: Literal["yaml", "scaffold"] = "yaml"
