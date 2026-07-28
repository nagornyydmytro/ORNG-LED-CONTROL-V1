"""Evaluate semantic preset documents into StageIntent."""

from __future__ import annotations

import math
from dataclasses import dataclass

from orng_led.config.models import FixtureKind, ShowConfig
from orng_led.engine.intents import (
    BarIntent,
    BeamIntent,
    FixtureIntent,
    ParIntent,
    Rgbw,
    StageIntent,
)
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

# Transition window into the current episode (seconds of show time).
FADE_DURATION_S = 2.0
SOFT_DURATION_S = 3.0


TYPE_TAGS = frozenset({"par", "bar", "beam", "face"})
SIDE_TAGS = frozenset({"left", "right"})
RING_TAGS = frozenset({"inner", "outer"})


def _matches_groups(fixture_groups: list[str], episode_groups: list[str]) -> bool:
    """Match episode group selectors with dimensional AND / within-dimension OR.

    ``["outer", "par"]`` → only outer PARs (not all PARs, not outer Bars).
    ``["par", "beam"]`` → PARs or Beams.
    ``["left", "right"]`` → left or right fixtures.
    ``all_rear`` → any non-face rear fixture.
    """
    if not episode_groups:
        return False
    fx = set(fixture_groups)

    if "all_rear" in episode_groups and len(episode_groups) == 1:
        is_rear = bool(fx & {"all_rear", "rear", "par", "bar", "beam"})
        return is_rear and "face" not in fx

    wanted_types = set(episode_groups) & TYPE_TAGS
    wanted_sides = set(episode_groups) & SIDE_TAGS
    wanted_rings = set(episode_groups) & RING_TAGS
    # Ignore all_rear when combined with more specific tags.
    if not wanted_types and not wanted_sides and not wanted_rings:
        if "all_rear" in episode_groups:
            is_rear = bool(fx & {"all_rear", "rear", "par", "bar", "beam"})
            return is_rear and "face" not in fx
        return False

    if "face" in fx and "face" not in wanted_types:
        return False
    if wanted_types and not (fx & wanted_types):
        return False
    if wanted_sides and not (fx & wanted_sides):
        return False
    if wanted_rings and not (fx & wanted_rings):
        return False
    return True


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

    @property
    def episode_count(self) -> int:
        return len(self.document.episodes)

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
        current = _evaluate_episode(episode, pos, show)

        blend = _transition_blend(episode.transition, pos.episode_time_s)
        if blend <= 0.0:
            return current

        prev_index = (pos.episode_index - 1) % len(self.document.episodes)
        prev_episode = self.document.episodes[prev_index]
        # Sample previous episode at its end so the look continues into the blend.
        prev_pos = CyclePosition(
            cycle_time_s=pos.cycle_time_s,
            episode_index=prev_index,
            episode_time_s=max(0.0, prev_episode.duration_s - 1e-6),
            episode_progress=1.0,
        )
        previous = _evaluate_episode(prev_episode, prev_pos, show)
        return _blend_stage(previous, current, 1.0 - blend)


def _transition_blend(transition: str, episode_time_s: float) -> float:
    """Return blend weight of *previous* episode (1 → fully previous, 0 → current)."""
    if transition == "cut":
        return 0.0
    if transition == "fade":
        duration = FADE_DURATION_S
        if episode_time_s >= duration:
            return 0.0
        return 1.0 - (episode_time_s / duration)
    if transition == "soft":
        duration = SOFT_DURATION_S
        if episode_time_s >= duration:
            return 0.0
        # Smoothstep ease for a softer ramp than linear fade.
        t = episode_time_s / duration
        eased = t * t * (3.0 - 2.0 * t)
        return 1.0 - eased
    return 0.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _blend_rgbw(a: Rgbw, b: Rgbw, t: float) -> Rgbw:
    return Rgbw(
        r=_lerp(a.r, b.r, t),
        g=_lerp(a.g, b.g, t),
        b=_lerp(a.b, b.b, t),
        w=_lerp(a.w, b.w, t),
    )


def _blend_intent(
    a: FixtureIntent | None, b: FixtureIntent | None, t: float
) -> FixtureIntent | None:
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, ParIntent) and isinstance(b, ParIntent):
        return ParIntent(
            color=_blend_rgbw(a.color, b.color, t),
            intensity=_lerp(a.intensity, b.intensity, t),
            strobe=_lerp(a.strobe, b.strobe, t),
        )
    if isinstance(a, BarIntent) and isinstance(b, BarIntent):
        segs = tuple(_lerp(x, y, t) for x, y in zip(a.segments, b.segments, strict=False))
        # Pad if segment counts differ (should not for staff profiles).
        if len(segs) < 8:
            segs = segs + (0.0,) * (8 - len(segs))
        return BarIntent(
            segments=segs[:8],
            dimmer=_lerp(a.dimmer, b.dimmer, t),
            strobe=_lerp(a.strobe, b.strobe, t),
        )
    if isinstance(a, BeamIntent) and isinstance(b, BeamIntent):
        return BeamIntent(
            pan=_lerp(a.pan, b.pan, t),
            tilt=_lerp(a.tilt, b.tilt, t),
            dimmer=_lerp(a.dimmer, b.dimmer, t),
            color=_lerp(a.color, b.color, t),
            shutter_open=b.shutter_open if t >= 0.5 else a.shutter_open,
            strobe=_lerp(a.strobe, b.strobe, t),
        )
    return b


def _blend_stage(previous: StageIntent, current: StageIntent, t: float) -> StageIntent:
    """t=0 → previous, t=1 → current."""
    keys = set(previous.fixtures) | set(current.fixtures)
    out = StageIntent()
    for key in keys:
        blended = _blend_intent(previous.fixtures.get(key), current.fixtures.get(key), t)
        if blended is not None:
            out.fixtures[key] = blended
    return out


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
