"""Evaluate semantic preset documents into StageIntent."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from orng_led.config.models import FixtureInstance, FixtureKind, ShowConfig
from orng_led.engine.intents import (
    BarIntent,
    BeamIntent,
    FixtureIntent,
    ParIntent,
    Rgbw,
    StageIntent,
)
from orng_led.engine.presets import CyclePosition
from orng_led.engine.timing import (
    accent,
    effect_rate_hz,
    movement_rate_hz,
    saw,
    unipolar_sine,
)
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


RateFn = Callable[[EpisodeCard], float]


@dataclass(frozen=True)
class EffectPhase:
    """Continuous accumulated phase, in turns, at one evaluation instant."""

    effect_turns: float = 0.0
    movement_turns: float = 0.0


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


def _selected_ids(episode: EpisodeCard, show: ShowConfig) -> set[str]:
    """Fixtures lit by an episode, never an empty stage.

    A selector such as ``["outer", "beam"]`` intersects to nothing because beams
    carry no ring tag. An episode is a look, not a pause, so an impossible
    combination falls back to every rear fixture instead of 18 dark seconds.
    """
    rear = [f for f in show.patch.fixtures if f.kind is not FixtureKind.FACE_PAR]
    selected = {f.id for f in rear if _matches_groups(f.groups, episode.groups)}
    return selected or {f.id for f in rear}


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

    def _accumulated_turns(self, time_s: float, rate_of: RateFn) -> float:
        """Effect turns accumulated since t=0 — continuous across episode/loop edges.

        Every episode may run at its own Hz; integrating the rate instead of
        multiplying absolute time keeps the waveform phase continuous, so an
        episode change never produces a random visual jump.
        """
        total = self.total_duration_s
        episodes = self.document.episodes
        if total <= 0.0 or not episodes:
            return 0.0
        per_cycle = sum(ep.duration_s * rate_of(ep) for ep in episodes)
        cycles_done = math.floor(time_s / total) if time_s >= 0 else 0
        pos = self.cycle_position(time_s)
        turns = cycles_done * per_cycle
        for index, episode in enumerate(episodes):
            if index >= pos.episode_index:
                break
            turns += episode.duration_s * rate_of(episode)
        turns += pos.episode_time_s * rate_of(episodes[pos.episode_index])
        return turns

    def effect_turns(self, time_s: float) -> float:
        return self._accumulated_turns(time_s, lambda ep: effect_rate_hz(ep.speed, ep.effect))

    def movement_turns(self, time_s: float) -> float:
        return self._accumulated_turns(time_s, lambda ep: movement_rate_hz(ep.speed))

    def evaluate(self, cycle_time_s: float, show: ShowConfig) -> StageIntent:
        pos = self.cycle_position(cycle_time_s)
        episode = self.document.episodes[pos.episode_index]
        clock = EffectPhase(
            effect_turns=self.effect_turns(cycle_time_s),
            movement_turns=self.movement_turns(cycle_time_s),
        )
        current = _evaluate_episode(episode, pos, clock, show)

        blend = _transition_blend(episode.transition, pos.episode_time_s)
        if blend <= 0.0:
            return current

        prev_index = (pos.episode_index - 1) % len(self.document.episodes)
        prev_episode = self.document.episodes[prev_index]
        # The previous look is sampled on the *same* continuous phase, so only
        # the look parameters cross-fade — the waveform itself never restarts.
        prev_pos = CyclePosition(
            cycle_time_s=pos.cycle_time_s,
            episode_index=prev_index,
            episode_time_s=max(0.0, prev_episode.duration_s - 1e-6),
            episode_progress=1.0,
        )
        previous = _evaluate_episode(prev_episode, prev_pos, clock, show)
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
        # Exclusive mode follows the *current* episode immediately. Keeping the
        # previous whole=True flag during soft/fade left whole_color active and
        # forced segments to 0 — the wire drove a solid whole-bar look while a
        # later sample of the same episode already looked like a chase.
        if b.whole:
            # Solid look is rendered via all eight segments — keep them fully on
            # during the blend so dimmer breathe is visible immediately.
            segs = (1.0,) * 8
        else:
            # Take the current segment pattern immediately. Lerping from a whole
            # episode's zero segments kept every segment below the renderer's
            # on-threshold for the first beat, leaving only Master Dimmer on the
            # wire (fixture appears as one static colour / dark).
            segs = tuple(b.segments[:8])
            if len(segs) < 8:
                segs = segs + (0.0,) * (8 - len(segs))
        return BarIntent(
            segments=segs[:8],
            dimmer=_lerp(a.dimmer, b.dimmer, t),
            color=_blend_rgbw(a.color, b.color, t),
            strobe=_lerp(a.strobe, b.strobe, t),
            whole=b.whole,
        )
    if isinstance(a, BeamIntent) and isinstance(b, BeamIntent):
        return BeamIntent(
            pan=_blend_axis(a.pan, b.pan, t),
            tilt=_blend_axis(a.tilt, b.tilt, t),
            dimmer=_lerp(a.dimmer, b.dimmer, t),
            color=_blend_rgbw(a.color, b.color, t),
            wheel=0.0,
            shutter_open=b.shutter_open if t >= 0.5 else a.shutter_open,
            strobe=_lerp(a.strobe, b.strobe, t),
        )
    return b


def _blend_axis(a: float | None, b: float | None, t: float) -> float | None:
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return _lerp(a, b, t)


def _blend_stage(previous: StageIntent, current: StageIntent, t: float) -> StageIntent:
    """t=0 → previous, t=1 → current."""
    keys = set(previous.fixtures) | set(current.fixtures)
    out = StageIntent()
    for key in keys:
        blended = _blend_intent(previous.fixtures.get(key), current.fixtures.get(key), t)
        if blended is not None:
            out.fixtures[key] = blended
    return out


def _effect_level(effect: str, turns: float, speed: float) -> float:
    """0..1 modulation of one fixture at the given accumulated phase."""
    if effect == "static":
        return 1.0
    if effect == "breathe":
        return 0.30 + 0.70 * unipolar_sine(turns)
    if effect == "pulse":
        # Sharper the faster it runs, so P08–P10 read as accents, not a hum.
        return 0.12 + 0.88 * accent(turns, 1.6 + 2.6 * speed)
    if effect == "wave":
        return 0.25 + 0.75 * unipolar_sine(turns)
    if effect == "chase":
        return 0.20 + 0.80 * accent(turns, 1.4 + 2.0 * speed)
    if effect == "mirror_sweep":
        return 0.35 + 0.65 * unipolar_sine(turns)
    return 0.7


def _spatial_offset(fixture: FixtureInstance, show: ShowConfig) -> float:
    """Phase offset in turns derived from the stage layout (left → right)."""
    placement = show.layout.placement_for(fixture.id)
    if placement is not None:
        return placement.x * 0.45
    order = fixture.spatial.order or 1
    return (order - 1) * 0.1


def _evaluate_episode(
    episode: EpisodeCard,
    pos: CyclePosition,
    clock: EffectPhase,
    show: ShowConfig,
) -> StageIntent:
    intent = StageIntent()
    color = PALETTE_RGBW.get(episode.palette, PALETTE_RGBW["warm_orange"])
    turns = clock.effect_turns
    mirrored = episode.effect == "mirror_sweep"
    selection = _selected_ids(episode, show)

    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.FACE_PAR:
            continue
        if fixture.id not in selection:
            continue

        offset = _spatial_offset(fixture, show)
        if mirrored:
            offset = abs(offset - 0.225)
        local_turns = turns - offset
        level = _effect_level(episode.effect, local_turns, episode.speed)
        intensity = max(0.0, min(1.0, episode.intensity * level))

        if fixture.kind is FixtureKind.PAR:
            intent.fixtures[fixture.id] = ParIntent(color=color, intensity=intensity)
        elif fixture.kind is FixtureKind.BAR:
            segments = _bar_segments(episode, local_turns, level)
            # Chase/wave/mirror use segments; static/breathe/pulse prefer whole palette.
            whole = episode.effect in {"static", "breathe", "pulse"}
            intent.fixtures[fixture.id] = BarIntent(
                segments=(0.0,) * 8 if whole else segments,
                dimmer=max(intensity, 0.15 * episode.intensity),
                color=color,
                whole=whole,
            )
        elif fixture.kind is FixtureKind.BEAM:
            sweep = 0.5 + 0.42 * math.sin(2.0 * math.pi * clock.movement_turns)
            pan = 1.0 - sweep if fixture.spatial.side.value == "left" else sweep
            tilt = 0.44 + 0.20 * math.sin(2.0 * math.pi * clock.movement_turns * 0.63 + 0.7)
            intent.fixtures[fixture.id] = BeamIntent(
                pan=max(0.0, min(1.0, pan)),
                tilt=max(0.0, min(1.0, tilt)),
                dimmer=intensity * 0.9,
                color=color,
                shutter_open=True,
            )
    return intent


def _bar_segments(episode: EpisodeCard, turns: float, level: float) -> tuple[float, ...]:
    """Per-segment levels; segment 1 is the bottom of a vertically mounted Bar."""
    effect = episode.effect
    segments: list[float] = []
    if effect == "chase":
        center = saw(turns) * 8.0
        for index in range(8):
            distance = min(abs(index - center), 8.0 - abs(index - center))
            segments.append(max(0.0, 1.0 - distance / 1.8))
    elif effect == "wave":
        for index in range(8):
            segments.append(unipolar_sine(turns - index / 8.0))
    elif effect == "mirror_sweep":
        center = 3.5 + 3.5 * math.sin(2.0 * math.pi * turns)
        for index in range(8):
            segments.append(max(0.0, 1.0 - abs(index - center) / 2.4))
    elif effect == "static":
        segments = [0.85] * 8
    else:
        segments = [max(0.08, level)] * 8
    return tuple(max(0.0, min(1.0, value)) for value in segments)
