"""Evaluate semantic preset documents into StageIntent."""

from __future__ import annotations

import math
from dataclasses import dataclass

from orng_led.config.models import FixtureKind, ShowConfig
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw, StageIntent
from orng_led.engine.presets import CyclePosition
from orng_led.presets.models import EpisodeCard, PresetDocument

PALETTE_RGBW: dict[str, Rgbw] = {
    "warm_orange": Rgbw(r=1.0, g=0.35, b=0.05),
    "deep_red": Rgbw(r=1.0, g=0.05, b=0.02),
    "amber": Rgbw(r=1.0, g=0.55, b=0.08),
    "white_warm": Rgbw(r=1.0, g=0.85, b=0.6, w=0.7),
    "violet_orange": Rgbw(r=0.85, g=0.2, b=0.75),
    "cool_blue": Rgbw(r=0.15, g=0.35, b=1.0),
    "mint": Rgbw(r=0.2, g=0.95, b=0.65),
    "magenta": Rgbw(r=1.0, g=0.1, b=0.55),
}


def _matches_groups(fixture_groups: list[str], episode_groups: list[str]) -> bool:
    fx = set(fixture_groups)
    for group in episode_groups:
        if group == "all_rear":
            if "all_rear" in fx or "rear" in fx or "par" in fx or "bar" in fx or "beam" in fx:
                if "face" not in fx:
                    return True
            continue
        if group in fx:
            return True
    return False


@dataclass
class YamlPresetProgram:
    """PresetProgram backed by a validated PresetDocument."""

    document: PresetDocument

    @property
    def id(self) -> str:
        return self.document.id

    @property
    def label(self) -> str:
        return self.document.label

    @property
    def total_duration_s(self) -> float:
        return self.document.total_duration_s

    def cycle_position(self, time_s: float) -> CyclePosition:
        total = self.total_duration_s
        cycle_time = time_s % total if total > 0 else 0.0
        elapsed = 0.0
        for index, episode in enumerate(self.document.episodes):
            end = elapsed + episode.duration_s
            if cycle_time < end or index == len(self.document.episodes) - 1:
                episode_time = max(0.0, cycle_time - elapsed)
                progress = episode_time / episode.duration_s if episode.duration_s else 0.0
                return CyclePosition(cycle_time, index, episode_time, min(1.0, progress))
            elapsed = end
        return CyclePosition(0.0, 0, 0.0, 0.0)

    def evaluate(self, cycle_time_s: float, show: ShowConfig) -> StageIntent:
        pos = self.cycle_position(cycle_time_s)
        episode = self.document.episodes[pos.episode_index]
        return _evaluate_episode(episode, pos, show)


def _modulate(progress: float, speed: float, effect: str) -> float:
    rate = 0.35 + 1.65 * speed
    phase = progress * rate
    if effect in ("static",):
        return 1.0
    if effect in ("pulse", "breathe"):
        return 0.45 + 0.55 * (0.5 + 0.5 * math.sin(2 * math.pi * phase))
    if effect == "wave":
        return 0.5 + 0.5 * math.sin(2 * math.pi * phase)
    if effect == "chase":
        return 0.35 + 0.65 * ((math.sin(2 * math.pi * phase) + 1) * 0.5)
    if effect == "mirror_sweep":
        return 0.5 + 0.5 * math.sin(2 * math.pi * phase * 0.5)
    return 0.7


def _evaluate_episode(episode: EpisodeCard, pos: CyclePosition, show: ShowConfig) -> StageIntent:
    intent = StageIntent()
    color = PALETTE_RGBW.get(episode.palette, PALETTE_RGBW["warm_orange"])
    mod = _modulate(pos.episode_progress, episode.speed, episode.effect)
    intensity = max(0.0, min(1.0, episode.intensity * mod))
    wave = pos.episode_progress

    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.FACE_PAR:
            continue
        if not _matches_groups(fixture.groups, episode.groups):
            continue

        if fixture.kind is FixtureKind.PAR:
            intent.fixtures[fixture.id] = ParIntent(color=color, intensity=intensity)
        elif fixture.kind is FixtureKind.BAR:
            segments: list[float] = []
            for index in range(8):
                if episode.effect == "chase":
                    center = (wave * 8.0 * (0.5 + episode.speed)) % 8.0
                    level = max(0.0, 1.0 - abs(index - center) / 1.8)
                elif episode.effect == "wave":
                    center = wave * 7.0
                    level = max(0.0, 1.0 - abs(index - center) / 2.2)
                elif episode.effect == "static":
                    level = 0.85
                else:
                    level = 0.35 + 0.65 * mod
                segments.append(level)
            intent.fixtures[fixture.id] = BarIntent(
                segments=tuple(segments),
                dimmer=intensity,
            )
        elif fixture.kind is FixtureKind.BEAM:
            sweep = 0.5 + 0.4 * math.sin(
                2 * math.pi * (pos.cycle_time_s / max(1.0, pos.cycle_time_s + 1))
            )
            if episode.effect == "mirror_sweep":
                sweep = 0.5 + 0.4 * math.sin(2 * math.pi * wave * (0.4 + episode.speed))
            if fixture.spatial.side.value == "left":
                pan = 1.0 - sweep
            else:
                pan = sweep
            tilt = 0.3 + 0.25 * math.sin(2 * math.pi * wave + 0.4)
            intent.fixtures[fixture.id] = BeamIntent(
                pan=pan,
                tilt=tilt,
                dimmer=intensity * 0.85,
                color=0.15 + 0.2 * intensity,
                shutter_open=True,
            )
    return intent
