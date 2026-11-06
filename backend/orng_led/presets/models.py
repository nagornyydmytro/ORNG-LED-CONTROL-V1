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
    "rose",
    "deep_blue",
    "violet",
)

EFFECTS = (
    "static",
    "pulse",
    "wave",
    "chase",
    "mirror_sweep",
    "breathe",
    # Near-static wash looks: always lit, slow shimmer / soft segment motion.
    "glow",
    "glow_wave",
    "glow_rise",
    # One-shot atmosphere bar looks (palette on/off segments).
    "glow_line",   # sync band bouncing up/down on every bar
    "glow_pinch",  # top+bottom grow toward center, then retract
    "glow_eq",     # standing-wave / equalizer columns across the bar row
)

TRANSITIONS = ("cut", "soft", "fade")
# One periodic action completes in this many beats of the shared musical clock.
# Absent on old documents: the loader adapts an explicit value once, in memory.
CYCLE_BEATS = (1, 2, 4, 8)
MUSICAL_CYCLE_VERSION = 1

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


def _palette(value: str) -> str:
    if value not in PALETTES:
        raise ValueError(f"Unknown palette {value!r}. Allowed: {', '.join(PALETTES)}")
    return value


def _effect(value: str) -> str:
    if value not in EFFECTS:
        raise ValueError(f"Unknown effect {value!r}. Allowed: {', '.join(EFFECTS)}")
    return value


def _groups(value: list[str]) -> list[str]:
    if not value:
        raise ValueError("Episode must target at least one group")
    unknown = [g for g in value if g not in GROUP_CHOICES]
    if unknown:
        raise ValueError(f"Unknown groups: {unknown}")
    return value


def _cycle_beats(value: int | None) -> int | None:
    if value is None:
        return None
    if int(value) not in CYCLE_BEATS:
        raise ValueError(f"cycle_beats must be one of {CYCLE_BEATS}")
    return int(value)


class LookAction(StrictModel):
    """One concurrent look inside an episode. Several may run on one clock."""

    id: str = ""
    groups: list[str] = Field(default_factory=lambda: ["all_rear"])
    palette: str | None = None
    effect: str = "pulse"
    speed: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    intensity: Annotated[float, Field(ge=0.0, le=1.0)] = 0.7
    cycle_beats: Literal[1, 2, 4, 8] | None = None

    @field_validator("palette")
    @classmethod
    def _check_palette(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _palette(value)

    @field_validator("effect")
    @classmethod
    def _check_effect(cls, value: str) -> str:
        return _effect(value)

    @field_validator("groups")
    @classmethod
    def _check_groups(cls, value: list[str]) -> list[str]:
        return _groups(value)

    @field_validator("cycle_beats")
    @classmethod
    def _check_cycle(cls, value: int | None) -> int | None:
        return _cycle_beats(value)


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
    # Explicit musical period. None on an old card: adapted once at read time.
    cycle_beats: Literal[1, 2, 4, 8] | None = None
    # Empty means this card is the only action. A list is the concurrent set.
    actions: list[LookAction] = Field(default_factory=list)

    @field_validator("palette")
    @classmethod
    def _check_palette(cls, value: str) -> str:
        return _palette(value)

    @field_validator("effect")
    @classmethod
    def _check_effect(cls, value: str) -> str:
        return _effect(value)

    @field_validator("transition")
    @classmethod
    def _transition(cls, value: str) -> str:
        if value not in TRANSITIONS:
            raise ValueError(f"Unknown transition {value!r}. Allowed: {', '.join(TRANSITIONS)}")
        return value

    @field_validator("groups")
    @classmethod
    def _check_groups(cls, value: list[str]) -> list[str]:
        return _groups(value)

    @field_validator("cycle_beats")
    @classmethod
    def _check_cycle(cls, value: int | None) -> int | None:
        return _cycle_beats(value)


def episode_actions(episode: EpisodeCard) -> list[LookAction]:
    """The looks that actually run together. One card stays one action."""
    if episode.actions:
        return list(episode.actions)
    return [
        LookAction(
            id=episode.id,
            groups=list(episode.groups),
            palette=episode.palette,
            effect=episode.effect,
            speed=episode.speed,
            intensity=episode.intensity,
            cycle_beats=episode.cycle_beats,
        )
    ]


class PresetDocument(StrictModel):
    schema_version: int = SCHEMA_VERSION
    id: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
    label: Annotated[str, Field(min_length=1, max_length=80)]
    hardware_tuned: bool = False
    builtin: bool = False
    # None on every preset written before musical cycles. 1 names this canon.
    musical_version: int | None = None
    episodes: list[EpisodeCard] = Field(min_length=1, max_length=20)

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, value: int) -> int:
        return require_supported_schema_version(value)

    @field_validator("musical_version")
    @classmethod
    def _check_musical(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if int(value) != MUSICAL_CYCLE_VERSION:
            raise ValueError(
                f"Unsupported musical_version {value}. Expected {MUSICAL_CYCLE_VERSION}."
            )
        return int(value)

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
    # Shown on the preset cards so the operator sees the real look, not a guess.
    palettes: list[str] = Field(default_factory=list)
    avg_intensity: float = 0.0
    avg_speed: float = 0.0
    effects: list[str] = Field(default_factory=list)
