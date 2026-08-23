"""Minimal programmatic preset model for the engine (not L011 art content)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from orng_led.config.models import FixtureKind, ShowConfig
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw, StageIntent

EPISODE_COUNT = 10
EPISODE_DURATION_S = 18.0
CYCLE_DURATION_S = EPISODE_COUNT * EPISODE_DURATION_S  # 180
NONE_PRESET_ID = "NONE"


class PresetProgram(Protocol):
    id: str

    def evaluate(
        self,
        cycle_time_s: float,
        show: ShowConfig,
        *,
        effect_rate_scale: float = 1.0,
        effect_turns: float | None = None,
        movement_turns: float | None = None,
    ) -> StageIntent: ...


@dataclass(frozen=True)
class CyclePosition:
    cycle_time_s: float
    episode_index: int  # 0..9
    episode_time_s: float
    episode_progress: float  # 0..1 within episode


def cycle_position(time_s: float) -> CyclePosition:
    cycle_time = time_s % CYCLE_DURATION_S
    episode_index = min(EPISODE_COUNT - 1, int(cycle_time // EPISODE_DURATION_S))
    episode_time = cycle_time - episode_index * EPISODE_DURATION_S
    progress = episode_time / EPISODE_DURATION_S
    return CyclePosition(cycle_time, episode_index, episode_time, progress)


@dataclass
class NonePresetProgram:
    """Explicit «Без пресету»: zero base look, no episode clock output."""

    id: str = NONE_PRESET_ID
    label: str = "Без пресету"
    episode_count: int = 0
    total_duration_s: float = 0.0

    def evaluate(
        self,
        cycle_time_s: float,
        show: ShowConfig,
        *,
        effect_rate_scale: float = 1.0,
        effect_turns: float | None = None,
        movement_turns: float | None = None,
    ) -> StageIntent:
        del cycle_time_s, show, effect_rate_scale, effect_turns, movement_turns
        return StageIntent()

    def cycle_position(self, time_s: float) -> CyclePosition:
        del time_s
        return CyclePosition(0.0, 0, 0.0, 0.0)


@dataclass
class BasePulsePreset:
    """Deterministic scaffold preset used until L011 artistic packs arrive."""

    id: str = "P05"
    label: str = "Універсальний (engine scaffold)"

    def evaluate(
        self,
        cycle_time_s: float,
        show: ShowConfig,
        *,
        effect_rate_scale: float = 1.0,
        effect_turns: float | None = None,
        movement_turns: float | None = None,
    ) -> StageIntent:
        del effect_rate_scale, effect_turns, movement_turns
        pos = cycle_position(cycle_time_s)
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * pos.episode_progress)
        wave = pos.episode_progress
        intent = StageIntent()

        for fixture in show.patch.fixtures:
            if fixture.kind is FixtureKind.FACE_PAR:
                continue
            if fixture.kind is FixtureKind.PAR:
                intensity = 0.35 + 0.55 * pulse
                if "outer" in fixture.groups:
                    color = Rgbw(r=1.0, g=0.12, b=0.0)
                else:
                    color = Rgbw(r=1.0, g=0.35, b=0.05)
                intent.fixtures[fixture.id] = ParIntent(color=color, intensity=intensity)
            elif fixture.kind is FixtureKind.BAR:
                segments = []
                for index in range(8):
                    center = wave * 7.0
                    level = max(0.0, 1.0 - abs(index - center) / 2.2)
                    segments.append(level)
                intent.fixtures[fixture.id] = BarIntent(
                    segments=tuple(segments),
                    dimmer=0.55 + 0.35 * pulse,
                    color=Rgbw(r=0.95, g=0.4, b=0.1),
                )
            elif fixture.kind is FixtureKind.BEAM:
                # Slow mirrored sweep across the cycle; interpolation happens in engine.
                sweep = 0.5 + 0.4 * math.sin(2 * math.pi * (cycle_time_s / CYCLE_DURATION_S))
                if fixture.spatial.side.value == "left":
                    pan = 1.0 - sweep
                else:
                    pan = sweep
                tilt = 0.35 + 0.2 * math.sin(2 * math.pi * pos.episode_progress + 0.5)
                intent.fixtures[fixture.id] = BeamIntent(
                    pan=pan,
                    tilt=tilt,
                    dimmer=1.0,
                    color=Rgbw(r=0.3, g=0.55, b=1.0),
                    wheel=0.2 + 0.1 * pos.episode_index / 9.0,
                    shutter_open=True,
                )
        return intent
