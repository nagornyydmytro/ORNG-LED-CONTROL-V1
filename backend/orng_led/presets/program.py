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
    beat_hit,
    effect_rate_hz,
    movement_rate_hz,
    saw,
    unipolar_sine,
)
from orng_led.presets.models import EpisodeCard, LookAction, PresetDocument, episode_actions
from orng_led.presets.musical import action_cycle, musical_turns
from orng_led.scenes.capabilities import look_supported, matches_groups

PALETTE_RGBW: dict[str, Rgbw] = {
    "warm_orange": Rgbw(r=1.0, g=0.35, b=0.05),
    "deep_red": Rgbw(r=1.0, g=0.05, b=0.02),
    "amber": Rgbw(r=1.0, g=0.55, b=0.08),
    "white_warm": Rgbw(r=1.0, g=0.85, b=0.6, w=0.7),
    "violet_orange": Rgbw(r=0.85, g=0.2, b=0.75),
    "cool_blue": Rgbw(r=0.15, g=0.35, b=1.0),
    "mint": Rgbw(r=0.2, g=0.95, b=0.65),
    "magenta": Rgbw(r=1.0, g=0.1, b=0.55),
    "rose": Rgbw(r=1.0, g=0.14, b=0.1),
    "deep_blue": Rgbw(r=0.05, g=0.18, b=1.0),
    "violet": Rgbw(r=0.55, g=0.08, b=1.0),
    # Bar wheel cyan. Not green: green channel is paired with full blue.
    "cyan": Rgbw(r=0.0, g=1.0, b=1.0),
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


def _matches_groups(fixture_groups: list[str], episode_groups: list[str]) -> bool:
    return matches_groups(fixture_groups, episode_groups)


def _selected_ids(episode: EpisodeCard, show: ShowConfig) -> set[str]:
    """Fixtures whose groups match. An empty match stays empty."""
    rear = [f for f in show.patch.fixtures if f.kind is not FixtureKind.FACE_PAR]
    return {f.id for f in rear if _matches_groups(f.groups, episode.groups)}


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

    def effect_turns(self, time_s: float, *, rate_scale: float = 1.0) -> float:
        scale = max(0.0, float(rate_scale))
        return self._accumulated_turns(time_s, lambda ep: _free_run_hz(ep) * scale)

    def movement_turns(self, time_s: float, *, rate_scale: float = 1.0) -> float:
        scale = max(0.0, float(rate_scale))
        return self._accumulated_turns(
            time_s, lambda ep: movement_rate_hz(ep.speed) * scale
        )

    def primary_cycle_beats(self, time_s: float) -> int | None:
        """Period of the first action in the episode at ``time_s``."""
        pos = self.cycle_position(time_s)
        actions = episode_actions(self.document.episodes[pos.episode_index])
        if not actions:
            return None
        return action_cycle(actions[0])

    def evaluate(
        self,
        cycle_time_s: float,
        show: ShowConfig,
        *,
        effect_rate_scale: float = 1.0,
        effect_turns: float | None = None,
        movement_turns: float | None = None,
        musical_beats: float | None = None,
    ) -> StageIntent:
        pos = self.cycle_position(cycle_time_s)
        episode = self.document.episodes[pos.episode_index]
        # Prefer externally integrated turns (engine) so live × changes do not
        # rewrite past phase — only future deltas speed up / slow down.
        clock = EffectPhase(
            effect_turns=(
                float(effect_turns)
                if effect_turns is not None
                else self.effect_turns(cycle_time_s, rate_scale=effect_rate_scale)
            ),
            movement_turns=(
                float(movement_turns)
                if movement_turns is not None
                else self.movement_turns(cycle_time_s, rate_scale=effect_rate_scale)
            ),
        )
        current_style = _saved_mix_style(episode)
        current = _evaluate_episode(
            episode, pos, clock, show, musical_beats=musical_beats, mix_style=current_style
        )

        blend = _transition_blend(episode.transition, pos.episode_time_s, episode.duration_s)
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
        prev_style = _saved_mix_style(prev_episode)
        previous = _evaluate_episode(
            prev_episode, prev_pos, clock, show, musical_beats=musical_beats, mix_style=prev_style
        )
        arrived = 1.0 - blend
        if current_style is not None or prev_style is not None:
            return _mix_join(
                previous,
                current,
                arrived,
                same_color=prev_episode.palette == episode.palette,
                transition=episode.transition,
            )
        return _blend_stage(previous, current, arrived)


def _saved_mix_style(episode: EpisodeCard) -> str | None:
    """Behaviour stored on the card. The document id is not the switch.

    A single-episode test and a duplicated preset keep another id, so the
    picture/classic choice has to travel with the episode itself.
    """
    style = getattr(episode, "mix_style", None)
    if style in {"picture", "classic"}:
        return str(style)
    return None


def _mix_join(
    previous: StageIntent,
    current: StageIntent,
    arrived: float,
    *,
    same_color: bool,
    transition: str,
) -> StageIntent:
    """Mix-only join. Colours are never averaged.

    A straight mix of warm orange into cyan lands on amber and yellow-green
    after the bar palette picks a slot. Same-colour episodes hand the picture
    across or dip the brightness. A new colour goes dark, then opens in that
    colour alone.
    """
    t = max(0.0, min(1.0, arrived))
    if same_color and transition == "soft":
        # Picture handoff. Both sides already share a palette slot.
        prev_level, cur_level = 1.0 - t, t
    elif same_color:
        # Brightness falls through the middle, then the same colour returns.
        prev_level = max(0.0, 1.0 - t * 1.6)
        cur_level = max(0.0, t * 1.6 - 0.45)
    elif transition == "soft":
        # Reveal: the old picture leaves, the new colour opens on its own.
        prev_level = max(0.0, 1.0 - t * 2.8)
        cur_level = max(0.0, (t - 0.34) / 0.66)
    else:
        # Drop: both sides meet in black, then the new colour comes up.
        prev_level = max(0.0, 1.0 - t * 2.4)
        cur_level = max(0.0, t * 2.4 - 1.35)
    out = StageIntent()
    keys = set(previous.fixtures) | set(current.fixtures)
    motion_t = cur_level / max(prev_level + cur_level, 1e-6)
    for key in keys:
        older = previous.fixtures.get(key)
        newer = current.fixtures.get(key)
        # A fixture the new picture does not use must not keep playing.
        # Same-colour fades re-sample the previous pulse on the moving clock,
        # so bars from the episode before a beam picture kept flashing.
        if newer is None:
            continue
        use_new = cur_level >= prev_level
        source = newer if use_new else older
        level = cur_level if use_new else prev_level
        if source is None:
            source = newer or older
            level = cur_level if newer is not None else prev_level
        if source is None or level <= 0.02:
            continue
        scaled = _one_sided(source, level)
        if (
            isinstance(older, BeamIntent)
            and isinstance(newer, BeamIntent)
            and isinstance(scaled, BeamIntent)
        ):
            scaled = BeamIntent(
                pan=_blend_axis(older.pan, newer.pan, motion_t),
                tilt=_blend_axis(older.tilt, newer.tilt, motion_t),
                dimmer=scaled.dimmer,
                color=scaled.color,
                wheel=scaled.wheel,
                shutter_open=scaled.shutter_open,
                strobe=0.0,
            )
        out.fixtures[key] = scaled
    return out


def _transition_window(transition: str, episode_duration_s: float) -> float:
    """Show-time window. A shorter episode finishes the same curve inside itself."""
    if transition == "fade":
        canonical = FADE_DURATION_S
    elif transition == "soft":
        canonical = SOFT_DURATION_S
    else:
        return 0.0
    return min(canonical, max(float(episode_duration_s), 1e-3))


def _transition_blend(transition: str, episode_time_s: float, episode_duration_s: float) -> float:
    """Return blend weight of *previous* episode (1 → fully previous, 0 → current)."""
    duration = _transition_window(transition, episode_duration_s)
    if duration <= 0.0 or episode_time_s >= duration:
        return 0.0
    if transition == "fade":
        return 1.0 - (episode_time_s / duration)
    # Smoothstep ease for a softer ramp than linear fade.
    t = episode_time_s / duration
    eased = t * t * (3.0 - 2.0 * t)
    return 1.0 - eased


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _blend_rgbw(a: Rgbw, b: Rgbw, t: float) -> Rgbw:
    return Rgbw(
        r=_lerp(a.r, b.r, t),
        g=_lerp(a.g, b.g, t),
        b=_lerp(a.b, b.b, t),
        w=_lerp(a.w, b.w, t),
    )


def _one_sided(intent: FixtureIntent, level: float) -> FixtureIntent:
    """A fixture that exists on only one side of the cut.

    PAR and bar dimmers follow the blend. A head is on or off: it does not
    pretend to dim, and a missing side does not pull pan or tilt toward zero.
    """
    level = max(0.0, min(1.0, level))
    if isinstance(intent, ParIntent):
        return ParIntent(
            color=intent.color,
            intensity=intent.intensity * level,
            strobe=intent.strobe * level,
        )
    if isinstance(intent, BarIntent):
        return BarIntent(
            segments=intent.segments,
            dimmer=intent.dimmer * level,
            color=intent.color,
            strobe=intent.strobe * level,
            whole=intent.whole,
        )
    return BeamIntent(
        pan=intent.pan,
        tilt=intent.tilt,
        dimmer=max(0.0, min(1.0, intent.dimmer * level)),
        color=intent.color,
        wheel=intent.wheel,
        shutter_open=intent.shutter_open and level > 0.02 and intent.dimmer > 0.02,
        strobe=intent.strobe * level,
    )


def _blend_intent(
    a: FixtureIntent | None, b: FixtureIntent | None, t: float
) -> FixtureIntent | None:
    # t=0 is the previous episode, t=1 is the current one.
    if a is None and b is None:
        return None
    if a is None and b is not None:
        return _one_sided(b, t)
    if b is None and a is not None:
        return _one_sided(a, 1.0 - t)
    if a is None or b is None:
        return None
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
        # RGB is the head dimmer. Fade it with the episode instead of snapping full-on.
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


def _effect_level(effect: str, turns: float, speed: float, *, musical: bool = False) -> float:
    """0..1 modulation of one fixture at the given accumulated phase."""
    if effect == "static":
        return 1.0
    if effect == "glow":
        # Near-static wash: always mostly on, slow shimmer only.
        return 0.62 + 0.38 * unipolar_sine(turns)
    if effect in {"glow_wave", "glow_rise", "glow_line", "glow_pinch", "glow_eq"}:
        return 0.72 + 0.28 * unipolar_sine(turns)
    if effect == "breathe":
        # Deep troughs so hardware dimmer travel is obvious on LED Bars.
        return 0.06 + 0.94 * unipolar_sine(turns)
    if effect == "pulse":
        # Sharper the faster it runs, so P08–P10 read as accents, not a hum.
        shape = beat_hit if musical else accent
        return 0.02 + 0.98 * shape(turns, 1.6 + 2.6 * speed)
    # Wave / chase / mirror: full 0..1 so the dark part of the run is truly off.
    if effect == "wave":
        return unipolar_sine(turns)
    if effect == "chase":
        shape = beat_hit if musical else accent
        return shape(turns, 1.4 + 2.0 * speed)
    if effect == "mirror_sweep":
        return unipolar_sine(turns)
    return 0.7


def _bar_dimmer(episode: EpisodeCard, level: float) -> float:
    """High-contrast bar master: peak near full; trough may be exactly 0."""
    peak = max(0.95, min(1.0, float(episode.intensity)))
    # Gamma pulls mid/low levels down so the swing reads on LED Bars.
    shaped = max(0.0, min(1.0, float(level))) ** 1.45
    value = peak * shaped
    if episode.effect in {
        "glow",
        "glow_wave",
        "glow_rise",
        "glow_line",
        "glow_pinch",
        "glow_eq",
    }:
        # Keep vertical bars visibly lit — no blackouts between shimmer peaks.
        return max(0.55 * peak, value)
    return value


# Patterned bar looks: kill soft falloff glow so the wave/chase trough is black.
_BAR_SEGMENT_BLACK_FLOOR = 0.05
# Venue LED bars only have per-segment palette on/off (no analog segment dimmer).
# Levels at/under this write palette ``off``; above write the look colour.
_ATMOSPHERE_BAR_MOTION = frozenset({"glow_line", "glow_pinch", "glow_eq"})
_GLOW_BEAM_EFFECTS = frozenset(
    {"glow", "glow_wave", "glow_rise", "glow_line", "glow_pinch", "glow_eq"}
)


def _spatial_offset(fixture: FixtureInstance, show: ShowConfig) -> float:
    """Phase offset in turns derived from the stage layout (left → right)."""
    placement = show.layout.placement_for(fixture.id)
    if placement is not None:
        return placement.x * 0.45
    order = fixture.spatial.order or 1
    return (order - 1) * 0.1


def _bar_x(fixture: FixtureInstance, show: ShowConfig) -> float:
    """Normalised left→right stage position for equalizer-style looks."""
    placement = show.layout.placement_for(fixture.id)
    if placement is not None:
        return max(0.0, min(1.0, float(placement.x)))
    order = fixture.spatial.order or 1
    return max(0.0, min(1.0, (order - 1) / 7.0))


def _action_card(episode: EpisodeCard, action: LookAction) -> EpisodeCard:
    return episode.model_copy(
        update={
            "id": action.id or episode.id,
            "groups": list(action.groups),
            "palette": action.palette or episode.palette,
            "effect": action.effect,
            "speed": action.speed,
            "intensity": action.intensity,
            "cycle_beats": action.cycle_beats,
            "actions": [],
        }
    )


def _free_run_hz(episode: EpisodeCard) -> float:
    """Hz represented by the integrated clock.

    A card with no action list keeps its own speed. A list uses the first
    action that actually moves, so that action's phase is the clock itself
    and every later speed is a ratio of it. The ratio is applied once.
    """
    if episode.actions:
        for action in episode.actions:
            rate = effect_rate_hz(action.speed, action.effect)
            if rate > 1e-9:
                return rate
        return 0.0
    return effect_rate_hz(episode.speed, episode.effect)


def _action_turns(
    episode: EpisodeCard,
    action: LookAction,
    clock: EffectPhase,
    *,
    musical_beats: float | None,
) -> tuple[float, bool]:
    """Phase for one action, and whether a musical period owns it.

    A locked beat clock replaces the free-running phase. It is not multiplied
    by it. Static has no period.
    """
    if musical_beats is not None:
        cycle = action_cycle(action)
        if cycle:
            return musical_turns(musical_beats, cycle), True
        return 0.0, False
    action_hz = effect_rate_hz(action.speed, action.effect)
    if action_hz <= 1e-9:
        return 0.0, False
    reference = _free_run_hz(episode)
    if reference <= 1e-9:
        return 0.0, False
    return clock.effect_turns * (action_hz / reference), False


def _evaluate_look(episode: EpisodeCard, clock: EffectPhase, show: ShowConfig) -> StageIntent:
    """Paint one auto composition in show time. Face stays out. Heads are overlaid later."""
    from orng_led.audio.canvas import build_canvas
    from orng_led.audio.library import BY_ID, paint

    item = BY_ID.get(str(episode.look or ""))
    if item is None:
        return StageIntent()
    canvas = build_canvas(list(show.patch.fixtures), show.layout.placements)
    color = PALETTE_RGBW.get(episode.palette, PALETTE_RGBW["deep_red"])
    amount = max(0.0, min(1.0, float(episode.intensity)))
    painted = paint(
        item,
        canvas,
        phase=float(clock.effect_turns) % 1.0,
        amount=amount,
        cap=1.0,
        color=color,
        strobe=0.0,
        palette=None,
    )
    intent = StageIntent()
    by_id = {fixture.id: fixture for fixture in show.patch.fixtures}
    for fixture_id, fixture_intent in painted.items():
        fixture = by_id.get(fixture_id)
        if fixture is not None and fixture.kind is FixtureKind.FACE_PAR:
            continue
        intent.fixtures[fixture_id] = fixture_intent
    return intent


def _head_motion_turns(episode_time_s: float) -> float:
    """Head clock, separate from the slow look.

    One target lasts about 3.6 s. That is long enough for the saved pan/tilt
    limiter to finish a side-to-side or up/down move, then hold the end.
    """
    return max(0.0, float(episode_time_s)) * 0.28


# Ordinary preset beams use tilt 0.44 ± 0.20. Joystick-up is the high end.
_MIX_TILT_LO = 0.24
_MIX_TILT_HI = 0.64


def _head_target(style: str, turns: float, *, side: str, index: int, mix: bool = False) -> tuple[float, float]:
    """Endpoints. The motion limiter runs the head at its saved max speed."""
    fast = turns
    flip = int(fast) % 2 == 0
    left = side == "left"
    if style == "nod":
        tilt = (_MIX_TILT_LO + 0.04) if flip else _MIX_TILT_HI
        if not mix:
            tilt = 0.16 if flip else 0.84
        return (0.42 if left else 0.58, tilt)
    if style == "cross":
        go_left = (index + int(fast)) % 2 == 0
        return (0.14 if go_left else 0.86, 0.46 if mix else 0.46)
    if style == "rise":
        climb = _MIX_TILT_LO + (_MIX_TILT_HI - _MIX_TILT_LO) * saw(fast)
        if not mix:
            climb = saw(fast)
        return (0.36 if left else 0.64, climb)
    if style == "pulse":
        tilt = 0.40 if flip else 0.60
        if not mix:
            tilt = 0.62 if flip else 0.38
        return (0.22 if left else 0.78, tilt)
    # sweep: full side-to-side, opposite heads so the pair reads as a fan
    pan = 0.1 if flip else 0.9
    if left:
        pan = 1.0 - pan
    return (pan, 0.48 if mix else 0.5)


def _mix_head_lamp(episode: EpisodeCard, episode_time_s: float, index: int) -> float:
    """Head brightness follows the picture. It may go out. Not one shared blink."""
    base = max(0.0, min(1.0, float(episode.intensity)))
    look = str(episode.look or "")
    effect = str(episode.effect or "")
    t = max(0.0, float(episode_time_s))
    if look in {"breathe_bars", "breathe_pars"} or effect == "breathe":
        return base * (0.04 + 0.96 * unipolar_sine(t * 0.29))
    if look in {"hit_bars", "hit_pars"} or effect == "pulse":
        return base * beat_hit(t * 0.85, 9.0)
    if look in {"band_left", "band_right", "bands_2_left", "bands_2_diag"}:
        on_left = saw(t * 0.22) < 0.5
        mine = (index % 2 == 0) == on_left
        return base * (1.0 if mine else 0.12)
    if look in {"meet_diag", "band_diag", "band_diag_flip"} or effect == "mirror_sweep":
        return base * (0.4 + 0.6 * unipolar_sine(t * 0.2 + index * 0.15))
    if look == "iris":
        opened = abs(math.sin(math.pi * min(1.0, t / max(episode.duration_s, 1.0))))
        return base * opened
    if look == "negative_pulse_hole":
        return base * (1.0 - beat_hit(t * 0.7, 7.0))
    if look == "pulse_by_bar":
        step = int(t * 0.75) % 2
        return base if step == (index % 2) else base * 0.18
    if effect in {"glow_line", "glow_eq", "glow_rise"}:
        return base * (0.62 + 0.38 * unipolar_sine(t * 0.16))
    return base * (0.5 + 0.5 * unipolar_sine(t * 0.22 + index))


def _overlay_heads(
    intent: StageIntent,
    episode: EpisodeCard,
    show: ShowConfig,
    *,
    episode_time_s: float,
    mix_style: str | None = None,
) -> None:
    """Replace head pan/tilt/brightness. RGB carries the flash; strobe stays off.

    The beam dimmer channel is a parked fixed value. Brightness is the RGB
    level. Unmapped locals are not written here. A saved Mix card keeps its
    own head curve and stays inside the ordinary-preset tilt limit.
    """
    style = str(episode.head or "")
    if not style:
        return
    picture = mix_style == "picture"
    rules = mix_style in {"picture", "classic"}
    fallback = PALETTE_RGBW.get(episode.palette, PALETTE_RGBW["deep_red"])
    if rules and episode.palette == "magenta":
        fallback = PALETTE_RGBW["magenta"]
    if picture:
        lamp_for = lambda index: _mix_head_lamp(episode, episode_time_s, index)
    else:
        base = max(0.0, min(1.0, float(episode.intensity)))
        shared = base * (0.28 + 0.72 * beat_hit(float(episode_time_s) * 1.15, 5.5))
        lamp_for = lambda index: shared
    turns = _head_motion_turns(episode_time_s)
    beams = [fixture for fixture in show.patch.fixtures if fixture.kind is FixtureKind.BEAM]
    for index, fixture in enumerate(beams):
        side = str(getattr(fixture.spatial.side, "value", fixture.spatial.side))
        pan, tilt = _head_target(style, turns, side=side, index=index, mix=picture)
        if rules:
            tilt = min(_MIX_TILT_HI, max(_MIX_TILT_LO, tilt))
        lamp = max(0.0, min(1.0, lamp_for(index)))
        previous = intent.fixtures.get(fixture.id)
        tint = previous.color if isinstance(previous, BeamIntent) else fallback
        intent.fixtures[fixture.id] = BeamIntent(
            pan=max(0.0, min(1.0, pan)),
            tilt=max(0.0, min(1.0, tilt)),
            dimmer=lamp,
            color=tint,
            shutter_open=lamp > 0.04,
            strobe=0.0,
        )


def _mix_bar_color(intent: StageIntent, episode: EpisodeCard) -> None:
    """Pink stays on RGB fixtures. Bars only get a saved red slot."""
    if episode.palette != "magenta":
        return
    red = PALETTE_RGBW["deep_red"]
    for fixture_id, item in list(intent.fixtures.items()):
        if isinstance(item, BarIntent):
            intent.fixtures[fixture_id] = BarIntent(
                segments=item.segments,
                dimmer=item.dimmer,
                color=red,
                strobe=item.strobe,
                whole=item.whole,
            )


def _evaluate_episode(
    episode: EpisodeCard,
    pos: CyclePosition,
    clock: EffectPhase,
    show: ShowConfig,
    *,
    musical_beats: float | None = None,
    mix_style: str | None = None,
) -> StageIntent:
    rules = mix_style in {"picture", "classic"}
    if episode.look:
        intent = _evaluate_look(episode, clock, show)
        if rules:
            _mix_bar_color(intent, episode)
        _overlay_heads(intent, episode, show, episode_time_s=pos.episode_time_s, mix_style=mix_style)
        return intent
    intent = StageIntent()
    # Later actions replace the whole intent of a shared fixture. This is
    # ordered replacement, not a mix of the two looks.
    for action in episode_actions(episode):
        turns, musical = _action_turns(
            episode, action, clock, musical_beats=musical_beats
        )
        part = _evaluate_card(
            _action_card(episode, action),
            pos,
            EffectPhase(effect_turns=turns, movement_turns=clock.movement_turns),
            show,
            musical=musical,
        )
        intent.fixtures.update(part.fixtures)
    if rules:
        _mix_bar_color(intent, episode)
    _overlay_heads(intent, episode, show, episode_time_s=pos.episode_time_s, mix_style=mix_style)
    return intent


def _evaluate_card(
    episode: EpisodeCard,
    pos: CyclePosition,
    clock: EffectPhase,
    show: ShowConfig,
    *,
    musical: bool = False,
) -> StageIntent:
    intent = StageIntent()
    color = PALETTE_RGBW.get(episode.palette, PALETTE_RGBW["deep_red"])
    turns = clock.effect_turns
    mirrored = episode.effect == "mirror_sweep"
    selection = _selected_ids(episode, show)

    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.FACE_PAR:
            continue
        if fixture.id not in selection:
            continue
        profile = show.profile_for(fixture)
        if not look_supported(profile, fixture.kind.value, episode.effect):
            continue

        offset = _spatial_offset(fixture, show)
        if mirrored:
            offset = abs(offset - 0.225)
        # Sync atmosphere looks share one phase; glow_eq keeps shared time and
        # uses bar_x inside the segment pattern instead of a time stagger.
        if episode.effect in _ATMOSPHERE_BAR_MOTION:
            local_turns = turns
        else:
            local_turns = turns - offset
        envelope_turns = (
            turns if musical and episode.effect in {"pulse", "chase"} else local_turns
        )
        level = _effect_level(
            episode.effect, envelope_turns, episode.speed, musical=musical
        )
        intensity = max(0.0, min(1.0, episode.intensity * level))

        if fixture.kind is FixtureKind.PAR:
            intent.fixtures[fixture.id] = ParIntent(color=color, intensity=intensity)
        elif fixture.kind is FixtureKind.BAR:
            segments = _bar_segments(
                episode,
                local_turns,
                level,
                bar_x=_bar_x(fixture, show),
                episode_progress=pos.episode_progress,
                pinch_cycles=1.0 if musical else 5.0,
                musical=musical,
            )
            # Chase/wave/mirror use segments; solid washes prefer whole color.
            whole = episode.effect in {"static", "breathe", "pulse", "glow"}
            # Never drive fixture strobe on bars — CH2 blink fights dimmer pulse.
            # Solid looks modulate master dimmer; patterned looks follow the lit
            # segment peak so dark parts of the wave/chase go fully off (dimmer 0).
            strobe = 0.0
            if whole:
                dimmer = _bar_dimmer(episode, level)
            else:
                peak = max(segments) if segments else 0.0
                dimmer = _bar_dimmer(episode, peak)
            intent.fixtures[fixture.id] = BarIntent(
                segments=(0.0,) * 8 if whole else segments,
                dimmer=dimmer,
                color=color,
                strobe=strobe,
                whole=whole,
            )
        elif fixture.kind is FixtureKind.BEAM:
            # Beams are binary full-bright when the effect is "on" — no master dimmer.
            if intensity <= 0.02:
                continue
            # Near-static washes: small slow drift only — wide sine + cut-ins read as jerks.
            if episode.effect in _GLOW_BEAM_EFFECTS:
                sweep = 0.5 + 0.16 * math.sin(2.0 * math.pi * clock.movement_turns)
                tilt = 0.50 + 0.08 * math.sin(2.0 * math.pi * clock.movement_turns * 0.41 + 0.5)
            else:
                sweep = 0.5 + 0.42 * math.sin(2.0 * math.pi * clock.movement_turns)
                tilt = 0.44 + 0.20 * math.sin(2.0 * math.pi * clock.movement_turns * 0.63 + 0.7)
            pan = 1.0 - sweep if fixture.spatial.side.value == "left" else sweep
            # Never drive fixture strobe_speed — hardware blink is much faster than
            # the episode pulse the stage preview shows (software intensity only).
            intent.fixtures[fixture.id] = BeamIntent(
                pan=max(0.0, min(1.0, pan)),
                tilt=max(0.0, min(1.0, tilt)),
                dimmer=1.0,
                color=color,
                shutter_open=True,
                strobe=0.0,
            )
    return intent


def _bar_segments(
    episode: EpisodeCard,
    turns: float,
    level: float,
    *,
    bar_x: float = 0.0,
    episode_progress: float = 0.0,
    pinch_cycles: float = 5.0,
    musical: bool = False,
) -> tuple[float, ...]:
    """Per-segment levels; segment 0 is the bottom of a vertically mounted Bar."""
    effect = episode.effect
    segments: list[float] = []
    if effect == "chase":
        center = saw(turns) * 8.0
        for index in range(8):
            distance = min(abs(index - center), 8.0 - abs(index - center))
            segments.append(max(0.0, 1.0 - distance / 1.8))
    elif effect == "glow_rise":
        # Legacy climb (saw); prefer glow_line for sync ping-pong.
        center = saw(turns) * 7.0
        for index in range(8):
            distance = abs(index - center)
            segments.append(1.0 if distance <= 1.35 else 0.0)
    elif effect == "glow_line":
        # Sync lit band bouncing bottom↔top — identical on every bar.
        center = (0.5 + 0.5 * math.sin(2.0 * math.pi * turns)) * 7.0
        for index in range(8):
            segments.append(1.0 if abs(index - center) <= 1.2 else 0.0)
    elif effect == "glow_pinch":
        # 8 segments (even): grow 1..4 from EACH end so the two fronts meet
        # as a pair (indices 3+4) — there is no single centre cell to leave dark.
        # Integer counts keep top/bottom in lockstep on on/off palette bars.
        if pinch_cycles == 1.0:
            phase = turns % 1.0
        else:
            cycles = pinch_cycles
            phase = (max(0.0, min(1.0, episode_progress)) * cycles) % 1.0
        if musical:
            grow = abs(2.0 * phase - 1.0)  # 1 on the grid hit, then close
        else:
            grow = 1.0 - abs(2.0 * phase - 1.0)  # 0 → 1 → 0
        count = min(4, max(1, 1 + int(grow * 3.999999)))
        for index in range(8):
            from_bottom = (index + 1) <= count
            from_top = (8 - index) <= count
            segments.append(1.0 if from_bottom or from_top else 0.0)
    elif effect == "glow_eq":
        # Standing-wave / equalizer: bottom-up column height varies across the row.
        wave = 0.5 + 0.5 * math.sin(2.0 * math.pi * (turns - bar_x * 1.35))
        height = 1.2 + 6.3 * wave
        for index in range(8):
            segments.append(1.0 if index < height else 0.0)
    elif effect == "wave":
        for index in range(8):
            segments.append(unipolar_sine(turns - index / 8.0))
    elif effect == "glow_wave":
        # Soft shimmer with a floor (legacy); not used for hard vertical looks.
        for index in range(8):
            segments.append(
                0.42 + 0.58 * unipolar_sine(turns - index / 8.0)
            )
    elif effect == "mirror_sweep":
        center = 3.5 + 3.5 * math.sin(2.0 * math.pi * turns)
        for index in range(8):
            segments.append(max(0.0, 1.0 - abs(index - center) / 2.4))
    elif effect == "static":
        segments = [0.85] * 8
    else:
        segments = [max(0.08, level)] * 8
    clipped = [max(0.0, min(1.0, value)) for value in segments]
    if effect in {"chase", "wave", "mirror_sweep"}:
        # Soft falloff tails → hard black so the wave trough is truly off.
        clipped = [
            0.0 if value < _BAR_SEGMENT_BLACK_FLOOR else value for value in clipped
        ]
    return tuple(clipped)
