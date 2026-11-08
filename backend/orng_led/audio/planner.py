"""Auto's own episode plan. It does not estimate tempo and it does not render.

The section tracker already decided groove, break, rise and drop. This planner
holds a recognisable motif, varies motion inside it, and uses a short accent
only when a drop is confirmed. A frame does not build a new episode. The
gallery catalogue is not a playlist. Runtime plans are not preset documents.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from orng_led.audio.auto_show import StructuralEvent
from orng_led.audio.bpm import MIN_EPISODE_BEATS
from orng_led.audio.compositions import LOOKS
from orng_led.audio.library import (
    AUTO_CYCLE_BEATS,
    BY_ID,
    allowed_compositions,
    composition_fits,
    director_looks,
)
from orng_led.audio.palettes import COLOR_IDS
from orng_led.audio.settings import (
    CHARACTER_CYCLE,
    CHARACTER_DURATION,
    CHARACTER_FAMILIES,
    CHARACTER_IDS,
    CHARACTER_VARIATION,
    cast_of,
    colour_of,
    director_home,
    groups_of,
    known_period,
    legacy_cast,
    series_period_frames,
)
from orng_led.engine.clock import FRAME_DT

_UNSET = object()

THEME_GROUPS: dict[str, tuple[str, ...]] = {
    **{name: groups_of("all") for name in COLOR_IDS},
    "all": groups_of("all"),
    "bars": groups_of("bars"),
    "heads": groups_of("heads"),
}
_GROOVE_LOOKS = ("pulse", "groups", "wash", "bloom", "density", "vertical_wave")
_RISE_LOOKS = ("bloom", "density", "pulse", "vertical_wave", "density_segments")
_PHRASE_BEATS = {"heads": 8, "bars": 16}
_CALM_BEATS = {"heads": 16, "bars": 32}
_RISE_BEATS = {"heads": 4, "bars": 8}
_ACCENT_GAP = 2.0
_JOURNAL_LIMIT = 96
_SEEN_LIMIT = 64
# A confirmed return does not wait out a 16- or 32-beat emergency phrase.
_RECOVER_BEATS = 4.0
_RECOVER_WALL_S = 2.0
# One step may enter the next episode. A larger gap is not a backlog.
_JUMP_BEATS = 8.0
_ACCENT_WALL_S = 2.0
# A lost signal is not a 32-beat phrase. Recovery does not wait that phrase out.
_SIGNAL_BEATS = 4
# Two beats at the slowest accepted tempo is under two seconds. This stops a
# frozen clock from holding a flash open.
_EFFECT_WALL_S = 2.5
_BLEND_S = 1.5
# One confirmed roll, not one strobe per lighting episode.
_SERIES_LIMIT_BEATS = 8.0
_SERIES_COOL_BEATS = 8.0
_SERIES_MIN_DENSITY = 0.2
# Minimum hold, then the longest a flat groove may keep one motif.
# A confirmed drop and the operator's next look do not wait for the long side.
MOTIF_HOLD: dict[str, tuple[int, int]] = {
    "smooth": (32, 128),
    "rhythmic": (32, 96),
    "impact": (16, 64),
}
# One related change inside the motif. Not a new composition, and not every bar.
VARIATION_GAP: dict[str, int] = {
    "smooth": 32,
    "rhythmic": 16,
    "impact": 16,
}
# A section must stay this long before it cuts the motif. One hop is chatter.
_SECTION_CONFIRM_BEATS = 4.0
# Spatial waves stay waves. A 0.5-beat cycle is a blink, not a variation.
_WAVE_ENVELOPES = frozenset(
    {
        "sine",
        "sine2d",
        "sine_travel",
        "triangle",
        "saw",
        "fall",
        "travel",
        "cascade",
        "breathe",
        "window",
        "iris",
        "meet",
        "oppose",
    }
)


@dataclass(frozen=True)
class AutoAction:
    """One musical part of an episode. Periods may differ inside one episode."""

    role: str
    look: str
    cycle_beats: float
    groups: tuple[str, ...] | None = None
    origin_beats: float = 0.0


@dataclass(frozen=True)
class AutoEffect:
    kind: str
    owner: str
    start_beat: float
    duration_beats: float
    role: str
    wall_origin: float | None = None
    wall_limit_s: float = _ACCENT_WALL_S


@dataclass(frozen=True)
class AutoEpisode:
    ident: int
    look: str
    groups: tuple[str, ...]
    theme: str
    intensity: float
    start_beat: float
    duration_beats: int
    actions: tuple[AutoAction, ...]
    transition: str
    effects: tuple[AutoEffect, ...]
    end_early: tuple[str, ...]
    reason: str
    section: str
    close_at: float | None = None
    character: str = "rhythmic"
    cast: str = "all"
    palette: str = "orange"
    clock: str = "musical"
    motif: str = ""
    role: str = "motif"

    @property
    def picture_cycle(self) -> float:
        for action in self.actions:
            if action.role == "picture":
                return float(action.cycle_beats)
        return 4.0

    @property
    def motion_cycle(self) -> float | None:
        for action in self.actions:
            if action.role == "motion":
                return float(action.cycle_beats)
        return None


class EpisodePlanner:
    """Current episode plus the next one. Choice uses a fixed seed."""

    def __init__(self, seed: int = 0x4F524E47) -> None:
        self._rng = random.Random(seed)
        self._ident = 1
        self._theme = "orange"
        self._seen: set[int] = set()
        self._seen_order: list[int] = []
        self._mark = 0.0
        self._effect_watch: tuple[object, ...] | None = None
        self._effect_wall0: float | None = None
        self._last_accent_at: float | None = None
        self._blackout = False
        self.character = "rhythmic"
        self._hearing = "rhythmic"
        self.period: float | None = None
        self.intensity_cap = 1.0
        self.palette = "orange"
        self._cast = "all"
        self.current: AutoEpisode | None = None
        self.upcoming: AutoEpisode | None = None
        self.played: list[AutoEpisode] = []
        self._journal: list[dict[str, object]] = []
        self._recent_families: tuple[str, ...] = ()
        self._flash_origin: float | None = None
        self._clock = "musical"
        self._home_look: str | None = None
        self._variation_at = -1.0
        self._motif_holds = 0
        self._motif_at = 0.0
        self._excluded: frozenset[str] = frozenset()
        self._groups: tuple[str, ...] | None = None
        self._pending_section: str | None = None
        self._pending_at = 0.0
        self._fixtures: list[object] | None = None
        self._texture_seen = 0
        self._heard_section: str | None = None
        self._beat_period = 0.5
        self._series_on = False
        self._series_drive_id = 0
        self._series_origin_beats: float | None = None
        self._series_wall0: float | None = None
        self._series_until: float | None = None
        self._series_cool_until: float | None = None
        self._series_density = 0.0
        self._dim_until: float | None = None
        self._dim_cool_wall = -1.0
        self._dim_cool_beat = -1.0
        self._pause_seen: set[int] = set()
        self._operator_pending = False
        self._freewheel_wall: float | None = None
        self._smooth_since: float | None = None
        self._lock_since: float | None = None
        self._before_smooth: str | None = None
        self._blend_from: str | None = None
        self._blend_to: str | None = None
        self._blend_wall0: float | None = None

    def bind_scene(self, fixtures: list[object] | None) -> None:
        """Director paints only instruments that are on this scene."""
        self._fixtures = None if fixtures is None else list(fixtures)

    def set_excluded(self, names: object) -> None:
        """Compositions the operator removed from Auto. Every choice path uses this."""
        self._excluded = frozenset(str(item) for item in (names or ()))
        if self.current is not None:
            self.current = replace(
                self.current,
                actions=tuple(action for action in self.current.actions if action.look not in self._excluded),
            )
        if self.upcoming is None:
            return
        if self.upcoming.look in self._excluded:
            self.upcoming = None
            return
        self.upcoming = replace(
            self.upcoming,
            actions=tuple(action for action in self.upcoming.actions if action.look not in self._excluded),
        )

    def set_groups(self, groups: tuple[str, ...] | list[str] | None) -> None:
        """Enabled classes. None keeps the theme cast. () is an empty Auto."""
        self._groups = None if groups is None else tuple(groups)

    def drop_excluded_current(self, beats: float, *, locked: bool) -> None:
        """Leave a forbidden picture on this frame. No wash and no first library row."""
        current = self.current
        if current is None or current.look not in self._excluded:
            return
        try:
            self.request_next_look(beats, locked=locked, immediate=True)
        except ValueError:
            self.current = replace(current, groups=(), actions=())
            self.upcoming = None

    @property
    def excluded(self) -> frozenset[str]:
        return self._excluded

    def request_next_look(self, beats: float, *, locked: bool, immediate: bool = False) -> str:
        """Operator asked for a different picture. Palette, period and cast stay."""
        current = self.current
        if current is None:
            raise ValueError("Ефект ще не почався")
        if (
            self.upcoming is not None
            and self.upcoming.reason == "operator"
            and float(self.upcoming.start_beat) > float(beats) + 1e-6
        ):
            raise ValueError("Зачекайте перемикання")
        look = self._different_look(current)
        if look is None:
            raise ValueError("Немає іншого сумісного рисунка")
        cycle = float(current.picture_cycle)
        floor = _cycle_floor(look, self.character)
        if cycle + 1e-9 < floor:
            cycle = floor
        moment = max(0.0, float(beats))
        smooth_mode = self._clock == "melodic"
        if locked and not smooth_mode and not immediate:
            start = math.ceil(moment - 1e-9)
            if start < moment:
                start = moment
        else:
            start = moment
        episode = replace(
            current,
            ident=self._ident,
            look=look,
            start_beat=float(start),
            duration_beats=_duration_for("phrase", self.character, current.intensity),
            actions=(AutoAction("picture", look, cycle),),
            effects=(),
            reason="operator",
            role="motif",
            motif=look,
            clock="melodic" if smooth_mode else ("musical" if locked else "freewheel"),
        )
        self._ident += 1
        self._remember(look)
        self._note(moment, "operator", look=look, at=float(start))
        if start <= moment + 1e-6:
            self._begin(episode, moment, queue=False)
        else:
            self.upcoming = episode
            self._operator_pending = True
        return look

    def cancel_dim(self, reason: str) -> None:
        if self._dim_until is None:
            return
        self._dim_until = None
        self._note(self._mark, "dim_end", reason=reason)

    def dimming(self, wall_s: float | None) -> bool:
        if self._dim_until is None or wall_s is None:
            return False
        if float(wall_s) + 1e-9 >= self._dim_until:
            self._dim_until = None
            return False
        return True

    def blend(self, wall_s: float | None) -> tuple[str | None, float]:
        """Weight of the picture just chosen. The outgoing look is the first item."""
        if self._blend_from is None or self._blend_wall0 is None or wall_s is None:
            return None, 1.0
        done = (float(wall_s) - float(self._blend_wall0)) / _BLEND_S
        if done >= 1.0 - 1e-6:
            self._blend_from = None
            self._blend_to = None
            self._blend_wall0 = None
            return None, 1.0
        return self._blend_from, max(0.0, done)

    def _notice_blend(self, before: str | None, wall_s: float | None) -> None:
        current = self.current
        after = None if current is None else current.look
        # A confirmed hit lands on its musical boundary. A 1.5 s crossfade
        # would hold the new picture at weight 0 and darken the accent.
        if current is not None and (current.reason == "drop_next" or current.role == "accent"):
            self._blend_from = None
            self._blend_to = None
            self._blend_wall0 = None
            return
        if not before or not after or before == after or wall_s is None:
            return
        if self._blend_from == before and self._blend_to == after and self._blend_wall0 is not None:
            return
        self._blend_from = before
        self._blend_to = after
        self._blend_wall0 = float(wall_s)

    def _accept_pause(
        self,
        beats: float,
        events: tuple[StructuralEvent, ...],
        *,
        locked: bool,
        wall_s: float | None,
        signal_lost: bool,
    ) -> None:
        # Quiet is the volume rider, not a scheduled blackout between looks.
        del beats, events, locked, wall_s, signal_lost

    def _soften_melody(self, beats: float) -> None:
        """Audible music without attacks. Keep the cast and slow the picture."""
        current = self.current
        if current is None:
            return
        item = BY_ID.get(current.look)
        jumpy = item is not None and item.envelope in {
            "hit",
            "hit_wide",
            "double",
            "double_offbeat",
            "pulse",
            "pulse_scan",
        }
        if self._before_smooth is None:
            self._before_smooth = current.look
        if not jumpy and float(current.picture_cycle) + 1e-9 >= 4.0 and _program_of(current.look) == "smooth":
            self._clock = "melodic"
            self.current = replace(current, clock="melodic", effects=())
            return
        look = self._calm_look(current)
        cycle = max(8.0, float(current.picture_cycle))
        action = _reanchor_action(AutoAction("picture", look, cycle), beats, cycle)
        self.current = replace(
            current,
            look=look,
            actions=(action,),
            effects=(),
            clock="melodic",
            reason="melodic",
        )
        self._clock = "melodic"
        self._stop_series(beats, "melodic")
        self._note(beats, "melodic", look=look, cycle=cycle)

    def _quiet_picture(self, beats: float) -> None:
        """Silence is not a moving show. A dim wash, not a beat program."""
        current = self.current
        if current is None or current.reason == "silence":
            return
        self.current = replace(
            current,
            look="wash",
            actions=(AutoAction("picture", "wash", max(8.0, float(current.picture_cycle))),),
            effects=(),
            clock="freewheel",
            reason="silence",
            intensity=min(float(current.intensity), 0.12),
        )
        self._clock = "freewheel"
        self._smooth_since = None
        self._stop_series(beats, "silence")
        self._note(beats, "silence", look="wash")

    def _schedule_restore(self, beats: float, wall_s: float | None, locked: bool) -> None:
        """Stay smooth at least 6s, then only after 2s of fresh lock, on a beat."""
        if self._clock != "melodic" or self._smooth_since is None or wall_s is None:
            return
        if float(wall_s) - float(self._smooth_since) < 6.0:
            return
        if not locked or self._lock_since is None or float(wall_s) - float(self._lock_since) < 2.0:
            return
        if self.upcoming is not None and self.upcoming.reason in {
            "restore",
            "drop_next",
            "develop",
            "operator",
            "flow",
        }:
            return
        current = self.current
        if current is None:
            return
        start = math.ceil(float(beats) - 1e-9)
        if start < float(beats):
            start = float(beats)
        look = self._beat_return_look(current)
        cycle = max(2.0, _cycle_floor(look, self.character), float(current.picture_cycle))
        origin = 0.0
        for action in current.actions:
            if action.role == "picture":
                origin = float(action.origin_beats)
                break
        resumed = _reanchor_action(
            AutoAction("picture", look, cycle, origin_beats=origin),
            float(beats),
            cycle,
        )
        episode = replace(
            current,
            ident=self._ident,
            look=look,
            start_beat=float(start),
            duration_beats=_duration_for("phrase", self.character, current.intensity),
            actions=(resumed,),
            effects=(),
            reason="restore",
            role="motif",
            motif=look,
            clock="musical",
        )
        self._ident += 1
        self._note(beats, "restore", look=look, at=float(start))
        if start <= float(beats) + 1e-6:
            self._begin(episode, beats, queue=False)
            self._clock = "musical"
            self._smooth_since = None
            self._before_smooth = None
        else:
            self.upcoming = episode

    def _beat_return_look(self, current: AutoEpisode) -> str:
        saved = BY_ID.get(str(self._before_smooth or ""))
        if saved is not None and saved.program == "beat" and saved.id not in self._excluded:
            return saved.id
        names = director_looks(
            "phrase",
            current.theme,
            character=self.character,
            current=current.look,
            fixtures=self._fixtures,
            program="beat",
            excluded=self._excluded,
            groups=self._groups,
        )
        for name in names:
            item = BY_ID.get(name)
            if item is not None and item.program == "beat" and name != current.look:
                return name
        return current.look

    def _different_look(self, current: AutoEpisode) -> str | None:
        live = BY_ID.get(current.look)
        want = "smooth" if self._clock == "melodic" else ("" if live is None else str(live.program))
        names = director_looks(
            "phrase",
            current.theme,
            character=self.character,
            current=current.look,
            fixtures=self._fixtures,
            program=want or None,
            excluded=self._excluded,
            groups=self._groups,
        )
        ranked: list[str] = []
        for name in names:
            if name == current.look or name in self._recent:
                continue
            item = BY_ID.get(name)
            if item is None or live is None:
                continue
            if item.family == live.family and item.envelope == live.envelope:
                continue
            if item.program != want:
                continue
            ranked.append(name)
        family = [name for name in ranked if BY_ID[name].family != live.family]
        if family:
            ranked = family
        if not ranked:
            ranked = [
                name
                for name in names
                if name != current.look and BY_ID.get(name) is not None and BY_ID[name].program == want
            ]
        if not ranked:
            return None
        return self._rng.choice(ranked)

    def _calm_look(self, current: AutoEpisode) -> str:
        groups = set(current.groups)
        for item in allowed_compositions(
            current.theme,
            program="smooth",
            fixtures=self._fixtures,
            excluded=self._excluded,
            groups=self._groups,
        ):
            if groups and not set(item.groups) <= set(groups):
                continue
            if item.pool == "accent":
                continue
            return item.id
        if current.look not in self._excluded and self._look_fits(current.look, current.theme):
            return current.look
        return ""

    def advance(
        self,
        beats: float,
        *,
        section: str,
        level: float,
        theme: str,
        events: tuple[StructuralEvent, ...] = (),
        signal_lost: bool = False,
        blackout: bool = False,
        locked: bool = True,
        tempo_held: bool = False,
        character: str | None = None,
        period: object = _UNSET,
        palette: str | None = None,
        wall_s: float | None = None,
        phase_beats: float | None = None,
        fixtures: list[object] | None = None,
        texture_id: int = 0,
        drive: bool = False,
        drive_id: int = 0,
        drive_density: float = 0.0,
        beat_period_s: float | None = None,
        hearing: str = "rhythmic",
        manual: bool = False,
    ) -> AutoEpisode:
        moment = max(0.0, float(beats))
        before_look = None if self.current is None else self.current.look
        # A late or corrected sample must not reopen a phrase that already played.
        if moment < self._mark:
            moment = self._mark
        else:
            self._mark = moment
        # Period retune follows the painted coordinate, not a second time scale.
        anchor = moment if phase_beats is None else float(phase_beats)
        if character in CHARACTER_IDS and str(character) != self.character:
            self.character = str(character)
            self._retarget_character(anchor)
        elif character in CHARACTER_IDS:
            self.character = str(character)
        if period is not _UNSET:
            next_period = known_period(period, self.character)
            if next_period != self.period:
                self.period = next_period
                self._retune_period(anchor)
        if palette is not None:
            self.palette = colour_of(theme, palette)
            if self.current is not None:
                self.current = replace(self.current, palette=self.palette)
        if fixtures is not None:
            self.bind_scene(fixtures)
        if beat_period_s is not None and 0.25 <= float(beat_period_s) <= 1.0:
            self._beat_period = float(beat_period_s)
        self._hearing = hearing if hearing in {"rhythmic", "melodic", "silence"} else "rhythmic"
        name = theme if theme in THEME_GROUPS else "orange"
        amount = max(0.0, min(1.0, float(level), float(self.intensity_cap)))
        if self.current is not None and abs(float(self.current.intensity) - amount) > 1e-6:
            self.current = replace(self.current, intensity=amount)
        if blackout:
            self._enter_blackout(moment)
        else:
            self._blackout = False
        self._apply_operator_theme(moment, name)
        if signal_lost:
            self._stop_series(moment, "signal")
            self.cancel_dim("signal")
            self._freewheel_wall = None
            entered = self._enter_signal(moment, name, amount)
            self._notice_blend(before_look, wall_s)
            return entered
        if not locked:
            self._stop_series(moment, "grid")
            self.cancel_dim("grid")
        if manual:
            self.cancel_dim("manual")
        if locked and wall_s is not None:
            if self._lock_since is None:
                self._lock_since = float(wall_s)
        else:
            self._lock_since = None
        if not locked and not tempo_held:
            entered = self._clock != "freewheel" and self._clock != "melodic"
            self._enter_freewheel(moment)
            if entered or self._freewheel_wall is None:
                self._freewheel_wall = None if wall_s is None else float(wall_s)
            held = 0.0 if self._freewheel_wall is None or wall_s is None else float(wall_s) - self._freewheel_wall
            # A brief unlock keeps the beat picture. Smooth only after a real loss.
            if hearing in {"melodic", "rhythmic"} and held >= 4.0:
                if self._smooth_since is None and wall_s is not None:
                    self._smooth_since = float(wall_s)
                self._soften_melody(moment)
        elif not locked:
            # Release still holds the last tempo. Do not open a new phrase yet.
            pass
        elif self._clock == "melodic" and self.current is not None:
            self._freewheel_wall = None
            self._schedule_restore(moment, wall_s, bool(locked))
        elif self._clock == "freewheel" and self.current is not None:
            self._freewheel_wall = None
            self._clock = "musical"
            self.current = replace(self.current, clock="musical", effects=())
        if self.current is not None and self.current.reason == "signal_loss":
            recovered = self._recover(moment, section, amount, name)
            self._notice_blend(before_look, wall_s)
            return recovered
        self._accept_drops(moment, events, name, amount, section, locked=locked)
        self._accept_pause(moment, events, locked=locked, wall_s=wall_s, signal_lost=signal_lost)
        if any(event.kind != "pause" for event in events):
            self.cancel_dim("event")
        dimmed = self.dimming(wall_s)
        if dimmed:
            self._stop_series(moment, "dim")
        if amount <= 0.05:
            self._stop_series(moment, "silence")
        self._sync_series(
            moment,
            drive=bool(drive) and bool(locked) and not self._blackout and not dimmed and amount > 0.05,
            drive_id=int(drive_id),
            density=float(drive_density),
            wall_s=wall_s,
        )
        if texture_id > self._texture_seen:
            self._texture_seen = int(texture_id)
            if locked:
                self._queue_develop(moment, section, amount, name)
        if self.current is None:
            self._begin(self._compose(moment, section, amount, name, "start"), moment)
        else:
            self._follow_section(moment, section, amount, name)
            due = self._due(moment)
            if not due:
                self._maybe_vary(moment, section, amount, name)
            self._hold_next(moment, section, amount, name)
            commit = self._ready_commit(moment)
            if (due or commit) and (self._clock != "melodic" or commit) and (locked or not tempo_held):
                self._cross(moment, section, amount, name)
        self._notice_blend(before_look, wall_s)
        return self.current

    def _hold_next(self, beats: float, section: str, level: float, theme: str) -> None:
        """Keep one future episode. The episode already playing stays as it is."""
        if self.current is None or self.current.reason == "signal_loss":
            return
        if self.upcoming is not None and self.upcoming.reason in {"drop_next", "develop", "operator", "restore", "flow"}:
            return
        if self.upcoming is not None and self.upcoming.section == section:
            return
        if self.upcoming is not None:
            self._note(beats, "context", section=section)
        end = self.current.start_beat + self.current.duration_beats
        self.upcoming = self._compose(end, section, level, theme, "queued")

    def strobe_at(self, beats: float, *, wall_s: float | None = None) -> float:
        """Frame flash gate: 1 on, 0 off. Never a hardware strobe speed."""
        return 1.0 if self.flash_on(beats, wall_s=wall_s) else 0.0

    def flash_gate(self, beats: float, *, wall_s: float | None = None) -> float | None:
        """None when no Auto flash is armed. 1 on, 0 off. Off extinguishes the base."""
        if self._blackout or self.current is None:
            return None
        if self.current.reason in {"signal_loss", "melodic"} or self._clock in {"freewheel", "melodic"}:
            return None
        if self.dimming(wall_s):
            return None
        active = self._armed_flash(beats)
        # A lone riser / rise-role flash is not a strobe. Only accent or an
        # armed series may gate the base look off.
        if not self._series_on and (active is None or active.role != "accent"):
            return None
        return 1.0 if self.flash_on(beats, wall_s=wall_s) else 0.0

    def _armed_flash(self, beats: float) -> AutoEffect | None:
        if self.current is None:
            return None
        moment = max(self._mark, max(0.0, float(beats)))
        for effect in self.current.effects:
            if effect.kind not in {"flash", "strobe"} or effect.owner != "auto":
                continue
            end = effect.start_beat + effect.duration_beats
            if effect.start_beat <= moment < end:
                return effect
        return None

    def _apply_operator_theme(self, beats: float, name: str) -> None:
        if self._groups is not None and name not in {"bars", "heads"}:
            legacy = legacy_cast(self._groups)
            if legacy in {"bars", "heads"}:
                self._cast = legacy
                if self.current is not None and self.current.cast != legacy:
                    self.current = replace(
                        self.current,
                        cast=legacy,
                        groups=tuple(self._groups),
                    )
                return
        if cast_of(name) != self._cast or (
            name != self._theme and cast_of(name) != cast_of(self._theme)
        ):
            self._retarget_theme(beats, name)
            return
        if name == self._theme:
            return
        self._theme = name
        self.palette = colour_of(name, self.palette)
        if self.current is not None:
            self.current = replace(self.current, theme=name, palette=self.palette)
        if self.upcoming is not None:
            self.upcoming = replace(self.upcoming, theme=name, palette=self.palette)

    def flash_on(self, beats: float, *, wall_s: float | None = None) -> bool:
        """Musical PAR/bar flash. Heads and Face stay out."""
        if self._blackout or self.current is None:
            return False
        if self.current.reason == "signal_loss" or self._clock == "freewheel":
            self._effect_watch = None
            self._stop_series(max(self._mark, max(0.0, float(beats))), "clock")
            return False
        moment = max(self._mark, max(0.0, float(beats)))
        active = self._armed_flash(moment)
        if active is not None and active.role == "accent":
            if wall_s is not None:
                key = (self.current.ident, active.start_beat, active.role)
                if self._effect_watch != key:
                    self._effect_watch = key
                    self._effect_wall0 = float(wall_s)
                    self._flash_origin = float(wall_s)
                elif (
                    self._effect_wall0 is not None
                    and float(wall_s) - self._effect_wall0 > _EFFECT_WALL_S
                ):
                    return False
            origin = self._flash_origin
            elapsed_s = 0.0 if wall_s is None or origin is None else max(0.0, float(wall_s) - origin)
            return (
                elapsed_s < 2.0 * FRAME_DT
                if wall_s is not None
                else (moment - active.start_beat) < 0.12
            )
        self._effect_watch = None
        if not self._series_on or self.character == "smooth":
            return False
        if wall_s is not None and self._series_wall0 is None:
            self._series_wall0 = float(wall_s)
        if wall_s is not None and self._series_wall0 is not None:
            if float(wall_s) - self._series_wall0 > _EFFECT_WALL_S:
                self._stop_series(moment, "wall")
                return False
        if self._series_until is not None and moment >= self._series_until - 1e-9:
            self._stop_series(moment, "limit")
            return False
        period = series_period_frames(self._series_density, self._beat_period, self.character)
        origin = 0.0 if self._series_origin_beats is None else float(self._series_origin_beats)
        elapsed_beats = max(0.0, moment - origin)
        frame = int(round((elapsed_beats * self._beat_period) / FRAME_DT))
        return (frame % period) == 0

    def journal(self) -> tuple[dict[str, object], ...]:
        return tuple(self._journal)

    def reset(self) -> None:
        """Drop the plan. Leaving Auto must not keep a flash or a queued episode."""
        self.current = None
        self.upcoming = None
        self._blackout = False
        self._last_accent_at = None
        self._seen.clear()
        self._seen_order.clear()
        self._mark = 0.0
        self._effect_watch = None
        self._effect_wall0 = None
        self._flash_origin = None
        self._clock = "musical"
        self._recent_families = ()
        self._recent = ()
        self._home_look = None
        self._variation_at = -1.0
        self._motif_holds = 0
        self._motif_at = 0.0
        self._pending_section = None
        self._pending_at = 0.0
        self._texture_seen = 0
        self._heard_section = None
        self._smooth_since = None
        self._lock_since = None
        self._before_smooth = None
        self._stop_series(0.0, "reset")
        self._series_drive_id = 0
        self._series_cool_until = None
        self._blend_from = None
        self._blend_to = None
        self._blend_wall0 = None

    def _natural_end(self) -> float:
        assert self.current is not None
        end = self.current.start_beat + self.current.duration_beats
        if self.current.transition == "bar":
            end = math.ceil((end - 1e-9) / 4.0) * 4.0
        return end

    def _effective_end(self) -> float:
        """Where this episode actually yields. A drop or develop does not wait out the phrase."""
        end = self._natural_end()
        nxt = self.upcoming
        if (
            nxt is not None
            and nxt.reason in {"drop_next", "develop", "operator", "restore", "flow"}
            and nxt.start_beat > (self.current.start_beat if self.current else 0.0) + 1e-6
        ):
            end = min(end, float(nxt.start_beat))
        return end

    def _due(self, beats: float) -> bool:
        return beats + 1e-9 >= self._effective_end()

    def _ready_commit(self, beats: float) -> bool:
        """A restore or a fresh drop is its own state. Melody must not hold it forever."""
        nxt = self.upcoming
        if nxt is None or nxt.reason not in {"restore", "drop_next"}:
            return False
        return float(beats) + 1e-9 >= float(nxt.start_beat)

    def _missed(self, beats: float, end: float) -> bool:
        """A jump past the whole next window is not a queue of expired episodes."""
        assert self.current is not None
        if beats > end + max(4.0, float(self.current.duration_beats)):
            return True
        nxt = self.upcoming
        if nxt is not None and beats > float(nxt.start_beat) + float(nxt.duration_beats) + 1e-6:
            return True
        return False

    def _land(
        self,
        beats: float,
        section: str,
        level: float,
        theme: str,
        reason: str,
    ) -> None:
        self.upcoming = None
        fresh = self._compose(float(beats), section, level, theme, reason)
        live = tuple(
            effect
            for effect in fresh.effects
            if effect.start_beat + effect.duration_beats > float(beats) + 1e-6
        )
        fresh = replace(fresh, effects=live)
        self._begin(fresh, beats, queue=False)
        self.upcoming = self._compose(
            fresh.start_beat + fresh.duration_beats,
            fresh.section,
            fresh.intensity,
            fresh.theme,
            "queued",
        )

    def _cross(self, beats: float, section: str, level: float, theme: str) -> None:
        assert self.current is not None
        end = self._effective_end()
        if self._missed(beats, end):
            self._land(beats, section, level, theme, "boundary")
            return
        nxt = self.upcoming
        self.upcoming = None
        current = self.current
        if nxt is not None and nxt.reason == "operator":
            nxt = replace(nxt, start_beat=end, effects=())
            self._motif_holds = 0
            self._operator_pending = False
        elif nxt is not None and nxt.reason == "restore":
            nxt = replace(nxt, start_beat=end, effects=(), clock="musical")
            self._clock = "musical"
            self._smooth_since = None
            self._before_smooth = None
        elif current.role == "accent":
            nxt = self._compose(end, "groove" if section == "drop" else section, level, theme, "return")
        elif nxt is not None and nxt.reason == "drop_next":
            nxt = replace(nxt, start_beat=end, clock="musical")
            self._clock = "musical"
            self._smooth_since = None
            self._before_smooth = None
            nxt = self._with_effects(nxt)
        elif nxt is not None and nxt.reason == "flow":
            nxt = replace(nxt, start_beat=end)
            nxt = self._with_effects(nxt)
        elif nxt is None or nxt.theme != theme:
            nxt = self._compose(end, section, level, theme, "boundary")
        elif current.look in self._excluded:
            nxt = self._compose(end, section, level, theme, "related")
            self._motif_holds = 0
        elif (
            current.role != "accent"
            and section == "groove"
            and nxt.reason != "develop"
            and self._should_step_motif(section, end)
        ):
            nxt = self._compose(end, section, level, theme, "related")
            self._motif_holds = 0
        else:
            kept = nxt.reason if nxt.reason in {"drop_next", "develop"} else "hold"
            nxt = replace(
                nxt,
                start_beat=end,
                reason=kept,
                look=current.look if kept == "hold" else nxt.look,
                motif=current.motif or current.look,
                role="motif" if kept != "drop_next" else "accent",
            )
            if kept == "hold":
                nxt = self._carry_motif(nxt, current)
                self._motif_holds += 1
            nxt = self._with_effects(nxt)
        self._begin(nxt, end, queue=False)
        self.upcoming = self._compose(
            nxt.start_beat + nxt.duration_beats,
            nxt.section if nxt.role != "accent" else section,
            level,
            theme,
            "queued",
        )

    def _begin(self, episode: AutoEpisode, at: float, *, queue: bool = True) -> None:
        if self._clock == "freewheel":
            episode = replace(episode, effects=(), clock="freewheel", role="motif")
        if episode.role != "accent" and self._home_look is None:
            self._home_look = episode.look
            episode = replace(episode, motif=episode.look)
        elif episode.motif == "":
            episode = replace(episode, motif=self._home_look or episode.look)
        self.current = episode
        self._heard_section = episode.section
        self._effect_watch = None
        self._effect_wall0 = None
        self._variation_at = float(episode.start_beat)
        previous = self.played[-1] if self.played else None
        self.played.append(episode)
        if len(self.played) > 32:
            del self.played[: len(self.played) - 32]
        self._remember(episode.look)
        self._cast = episode.cast
        self._clock = episode.clock
        chosen = BY_ID.get(episode.look)
        if episode.role == "accent":
            kind = "accent"
        elif previous is not None and previous.look == episode.look:
            kind = "hold"
        else:
            kind = "motif"
            self._motif_holds = 0
            self._motif_at = float(episode.start_beat)
        self._note(
            at,
            kind,
            look=episode.look,
            family=None if chosen is None else chosen.family,
            duration=episode.duration_beats,
            reason=episode.reason,
            section=episode.section,
            theme=episode.theme,
            character=episode.character,
            role=episode.role,
            motif=episode.motif,
            cycles=[action.cycle_beats for action in episode.actions],
            effects=[effect.role for effect in episode.effects],
        )
        if queue and self.upcoming is None and episode.reason != "signal_loss":
            self.upcoming = self._compose(
                episode.start_beat + episode.duration_beats,
                episode.section,
                episode.intensity,
                episode.theme,
                "queued",
            )

    def _accept_drops(
        self,
        beats: float,
        events: tuple[StructuralEvent, ...],
        theme: str,
        level: float,
        section: str,
        *,
        locked: bool = True,
    ) -> None:
        for event in events:
            if event.kind != "drop" or not self._remember_drop(event.ident):
                continue
            if not locked:
                continue
            if self._last_accent_at is not None and beats - self._last_accent_at < _ACCENT_GAP:
                self._note(beats, "drop_ignored", reason="accent_density", ident=event.ident)
                continue
            self._last_accent_at = beats
            self._stop_series(beats, "drop")
            start = self._drop_boundary(beats)
            self.upcoming = self._compose(float(start), section, level, theme, "drop_next")
            self._note(beats, "drop_next", ident=event.ident, accent_beats=2, at=start)

    def _enter_freewheel(self, beats: float) -> None:
        """Lock gone. Keep the phase and keep developing; do not flash."""
        if self._clock == "freewheel":
            if self.current is not None and self.current.effects:
                self.current = replace(self.current, effects=(), clock="freewheel")
            return
        self._stop_series(beats, "freewheel")
        self._clock = "freewheel"
        if self.upcoming is not None and self.upcoming.reason in {"drop_next", "restore"}:
            self.upcoming = None
        if self.current is None:
            return
        if self.current.effects:
            self.current = replace(self.current, effects=(), clock="freewheel")
        else:
            self.current = replace(self.current, clock="freewheel")
        self._note(beats, "freewheel", look=self.current.look)

    def _freewheel(self, beats: float, section: str, level: float, theme: str) -> AutoEpisode:
        """Lock gone. Keep the picture; do not pretend a beat grid."""
        self._enter_freewheel(beats)
        if self.current is None:
            calm = self._compose(beats, "groove", min(level, 0.35), theme, "freewheel")
            calm = replace(calm, effects=(), clock="freewheel", reason="freewheel")
            self._begin(calm, beats, queue=False)
        return self.current

    def _enter_signal(self, beats: float, theme: str, level: float) -> AutoEpisode:
        if self.current is not None and self.current.reason == "signal_loss":
            return self.current
        self.upcoming = None
        calm = self._compose(beats, "break", min(level, 0.2), theme, "signal_loss")
        calm = replace(
            calm,
            duration_beats=_SIGNAL_BEATS,
            transition="beat",
            effects=(),
            end_early=("signal_loss", "blackout"),
        )
        self._begin(calm, beats)
        self._note(beats, "signal_loss", look="wash")
        return self.current

    def _recover(self, beats: float, section: str, level: float, theme: str) -> AutoEpisode:
        """Music is back. Leave the emergency episode now, without its old flash."""
        self._land(beats, section, level, theme, "recover")
        if self.current is not None and self.current.effects:
            self.current = replace(
                self.current,
                effects=tuple(effect for effect in self.current.effects if effect.role != "accent"),
            )
        self._note(beats, "recover", section=section)
        assert self.current is not None
        return self.current

    def _retarget_character(self, beats: float) -> None:
        """Character changes cycles now. The playing look stays until a boundary."""
        chosen = None if self.current is None else BY_ID.get(self.current.look)
        families = CHARACTER_FAMILIES.get(self.character, CHARACTER_FAMILIES["rhythmic"])
        if chosen is None or chosen.family not in families:
            self._home_look = None
            self._motif_holds = 0
        if self.current is not None:
            self.current = self._rewrite_character(self.current, beats)
        if self.upcoming is not None:
            chosen = BY_ID.get(self.upcoming.look)
            families = CHARACTER_FAMILIES.get(self.character, CHARACTER_FAMILIES["rhythmic"])
            if (
                chosen is not None
                and chosen.family not in families
                and chosen.id not in {"wash", "accent"}
            ):
                self.upcoming = None
            else:
                self.upcoming = self._rewrite_character(self.upcoming, beats)

    def _rewrite_character(self, episode: AutoEpisode, beats: float) -> AutoEpisode:
        kind = _kind_for(episode.section, episode.intensity, episode.reason)
        picture = _picture_cycle(
            kind,
            episode.duration_beats,
            episode.look,
            self.character,
            self.period,
        )
        actions = [
            _reanchor_action(action, beats, picture) if action.role == "picture" else action
            for action in episode.actions
        ]
        if self.character == "impact":
            actions = [action for action in actions if action.role != "bed"]
        elif not any(action.role == "bed" for action in actions):
            bed = self._bed_action(kind, episode.theme, episode.groups, picture)
            if bed is not None:
                actions.append(bed)
        else:
            actions = [
                _reanchor_action(action, beats, self._bed_cycle(picture))
                if action.role == "bed"
                else action
                for action in actions
            ]
        effects = episode.effects
        if self.character == "smooth":
            effects = tuple(effect for effect in effects if effect.role != "rise")
        return replace(
            episode,
            character=self.character,
            actions=tuple(actions),
            effects=effects,
        )

    def _bed_cycle(self, picture: float) -> float:
        if self.period is not None:
            return float(self.period)
        return 8.0 if self.character == "smooth" else max(4.0, picture * 2.0)

    def _retune_period(self, beats: float) -> None:
        """Apply Auto or a forced cycle to the live episode and the queued one."""
        if self.current is not None:
            self.current = self._rewrite_period(self.current, beats)
        if self.upcoming is not None:
            self.upcoming = self._rewrite_period(self.upcoming, beats)

    def _rewrite_period(self, episode: AutoEpisode, beats: float) -> AutoEpisode:
        kind = _kind_for(episode.section, episode.intensity, episode.reason)
        picture = _picture_cycle(
            kind,
            episode.duration_beats,
            episode.look,
            self.character,
            self.period,
        )
        actions = []
        for action in episode.actions:
            # Head travel limits are a later task. Every light layer follows
            # the operator period; motion is the only layer left on its cycle.
            if action.role == "motion":
                actions.append(action)
            elif action.role == "bed":
                actions.append(_reanchor_action(action, beats, self._bed_cycle(picture)))
            else:
                actions.append(_reanchor_action(action, beats, picture))
        return replace(episode, actions=tuple(actions))

    def _retarget_theme(self, beats: float, name: str) -> None:
        """Cast change retargets who is lit. Colour change does not."""
        new_cast = cast_of(name)
        self._cancel_upcoming(beats, "theme")
        self._theme = name
        self._cast = new_cast
        self.palette = colour_of(name, self.palette)
        if self.current is None:
            return
        groups = groups_of(new_cast)
        chosen = BY_ID.get(self.current.look)
        look = self.current.look
        actions = self.current.actions
        if chosen is not None:
            owned = tuple(item for item in chosen.groups if item in groups)
            if owned:
                groups = owned
            else:
                look = "wash"
                actions = tuple(
                    replace(action, look="wash") if action.role == "picture" else action
                    for action in actions
                )
        effects = self.current.effects
        if "par" not in groups:
            effects = tuple(effect for effect in effects if effect.kind not in {"strobe", "flash"})
            actions = tuple(
                action
                for action in actions
                if action.role != "bed" and "par" not in (action.groups or ())
            )
        self.current = replace(
            self.current,
            look=look,
            theme=name,
            groups=groups,
            actions=actions,
            effects=effects,
            cast=new_cast,
            palette=self.palette,
        )

    def _drop_boundary(self, beats: float) -> float:
        """Nearest bar or beat that this episode is allowed to yield."""
        if self.current is None:
            return float(math.ceil(beats - 1e-9))
        earliest = self.current.start_beat + MIN_EPISODE_BEATS
        # A drop yields on the next allowed beat. A 32-beat bar phrase must
        # not postpone the accent and the new look until that phrase ends.
        target = max(float(beats), earliest)
        boundary = math.ceil(target - 1e-9)
        natural = self._natural_end()
        if boundary > natural:
            boundary = natural
        if boundary <= self.current.start_beat + 1e-6:
            boundary = natural
        return float(boundary)

    def _enter_blackout(self, beats: float) -> None:
        if self._blackout:
            return
        self._blackout = True
        self._stop_series(beats, "blackout")
        if self.current is not None and self.current.effects:
            self.current = replace(self.current, effects=())
        self._note(beats, "blackout", reason="immediate")

    def _cancel_upcoming(self, beats: float, reason: str) -> None:
        if self.upcoming is None:
            self._note(beats, reason, cancelled=False)
            return
        self._note(beats, reason, cancelled=self.upcoming.look, theme=self.upcoming.theme)
        self.upcoming = None

    def _follow_section(self, beats: float, section: str, level: float, theme: str) -> None:
        """A section that stays puts a new picture on the next beat. One hop does not."""
        if self.current is None or self.current.reason == "signal_loss":
            return
        if self.current.role == "accent":
            self._heard_section = section
            self._pending_section = None
            return
        if self.upcoming is not None and self.upcoming.reason == "drop_next":
            self._heard_section = section
            self._pending_section = None
            return
        if section == "drop" or self._heard_section == section:
            self._pending_section = None
            return
        previous = self._heard_section
        wanted = section == "break" or (
            section == "riser" and previous in {"groove", "break", None}
        ) or (section == "groove" and previous in {"break", "riser"})
        if not wanted:
            self._heard_section = section
            self._pending_section = None
            return
        if self._pending_section != section:
            self._pending_section = section
            self._pending_at = float(beats)
            return
        if float(beats) - float(self._pending_at) < _SECTION_CONFIRM_BEATS:
            return
        self._pending_section = None
        self._heard_section = section
        start = self._drop_boundary(beats)
        self.upcoming = self._compose(float(start), section, level, theme, "flow")
        self._note(beats, "flow", look=self.upcoming.look, at=start)

    def _maybe_vary(self, beats: float, section: str, level: float, theme: str) -> None:
        """Same motif. One slower or denser step. Not a new composition and not 8→½."""
        del theme
        if self.current is None or section != "groove" or self._clock != "musical":
            return
        if self.current.role == "accent" or self.period is not None:
            return
        if self.current.reason in {"signal_loss", "silence", "melodic"}:
            return
        gap = float(VARIATION_GAP.get(self.character, VARIATION_GAP["rhythmic"]))
        if float(beats) - float(self._variation_at) < gap - 1e-9:
            return
        varied = self._vary_actions(self.current, float(beats), section, level)
        if self.character != "impact" and not any(action.role == "bed" for action in varied.actions):
            bed = self._bed_action("phrase", varied.theme, varied.groups, varied.picture_cycle)
            if bed is not None:
                extra = tuple(name for name in bed.groups or () if name not in varied.groups)
                varied = replace(varied, actions=varied.actions + (bed,), groups=varied.groups + extra)
        if varied.actions == self.current.actions:
            return
        self._variation_at = float(beats)
        self.current = varied
        self._note(
            beats,
            "variation",
            look=varied.look,
            cycles=[action.cycle_beats for action in varied.actions],
        )

    def _queue_develop(self, beats: float, section: str, level: float, theme: str) -> None:
        """Confirmed texture change: a related step, not a drop and not a playlist skip."""
        if self.current is None or self.current.role == "accent":
            return
        if self.current.reason in {"signal_loss", "freewheel"}:
            return
        if self.upcoming is not None and self.upcoming.reason in {"drop_next", "operator", "restore", "flow"}:
            return
        start = math.ceil((float(beats) + 1e-9) / 4.0) * 4.0
        natural = self._natural_end()
        start = min(start, natural) if start > self.current.start_beat + 1e-6 else natural
        self.upcoming = self._compose(float(start), section, level, theme, "develop")
        self._note(beats, "develop", look=self.upcoming.look, at=start)

    def _should_step_motif(self, section: str, at: float) -> bool:
        """A flat groove yields on a phrase boundary once the long hold is up."""
        if section != "groove" or self._clock != "musical":
            return False
        _minimum, limit = MOTIF_HOLD.get(self.character, MOTIF_HOLD["rhythmic"])
        return float(at) - float(self._motif_at) + 1e-9 >= float(limit)

    def _carry_motif(self, episode: AutoEpisode, current: AutoEpisode) -> AutoEpisode:
        actions = tuple(
            replace(action, look=current.look) if action.role == "picture" else action
            for action in current.actions
            if action.role == "picture" or action.look not in self._excluded
        )
        return replace(
            episode,
            look=current.look,
            groups=current.groups,
            actions=actions,
            motif=current.motif or current.look,
            role="motif",
            character=self.character,
        )

    def _simplify(self, episode: AutoEpisode, beats: float, level: float) -> AutoEpisode:
        kind = "calm"
        picture = _picture_cycle(kind, episode.duration_beats, episode.look, self.character, self.period)
        actions = []
        for action in episode.actions:
            if action.role == "picture":
                slower = max(float(action.cycle_beats), picture, 4.0)
                actions.append(_reanchor_action(action, beats, slower))
            elif action.role in {"response", "motion"}:
                continue
            elif action.role == "bed" and action.look not in self._excluded:
                actions.append(_reanchor_action(action, beats, max(8.0, self._bed_cycle(picture))))
            else:
                actions.append(action)
        return replace(episode, actions=tuple(actions), intensity=min(float(episode.intensity), level))

    def _intensify(self, episode: AutoEpisode, beats: float, level: float) -> AutoEpisode:
        kind = "rise"
        picture = _picture_cycle(kind, episode.duration_beats, episode.look, self.character, self.period)
        floor = _cycle_floor(episode.look, self.character)
        actions = []
        for action in episode.actions:
            if action.role == "picture":
                faster = min(float(action.cycle_beats), picture)
                if self.character == "impact":
                    faster = min(faster, 1.0)
                actions.append(_reanchor_action(action, beats, max(floor, faster)))
            elif action.role == "response" and self.character == "smooth":
                continue
            else:
                actions.append(action)
        if self.character != "smooth" and not any(action.role == "response" for action in actions):
            reply = self._response_action(episode, picture)
            if reply is not None:
                actions.append(reply)
        return replace(episode, actions=tuple(actions), intensity=max(float(episode.intensity), level))

    def _restore(self, episode: AutoEpisode, beats: float, level: float) -> AutoEpisode:
        kind = "phrase"
        picture = _picture_cycle(kind, episode.duration_beats, episode.look, self.character, self.period)
        actions = []
        for action in episode.actions:
            if action.role == "picture":
                actions.append(_reanchor_action(action, beats, picture))
            elif action.role == "response" and self.character == "smooth":
                continue
            else:
                actions.append(action)
        return replace(
            episode,
            actions=tuple(actions),
            intensity=level,
            effects=tuple(effect for effect in episode.effects if effect.role != "rise"),
        )

    def _vary_actions(
        self,
        episode: AutoEpisode,
        beats: float,
        section: str,
        level: float,
    ) -> AutoEpisode:
        """Step the picture one musical subdivision. Do not blink a wave."""
        del level
        cycles = _variation_cycles(self.character)
        actions: list[AutoAction] = []
        picture_cycle = episode.picture_cycle
        floor = _cycle_floor(episode.look, self.character)
        for action in episode.actions:
            if action.role == "picture" and self.period is None:
                chosen = _step_cycle(float(action.cycle_beats), cycles, self._rng, floor)
                picture_cycle = chosen
                actions.append(_reanchor_action(action, beats, chosen))
            elif action.role == "response":
                if self.character == "smooth" or section != "riser":
                    continue
                reply_cycle = max(floor, min(2.0, max(1.0, float(picture_cycle))))
                actions.append(_reanchor_action(action, beats, reply_cycle))
            else:
                actions.append(action)
        has_response = any(action.role == "response" for action in actions)
        if (
            section == "riser"
            and self.character != "smooth"
            and not has_response
        ):
            reply = self._response_action(episode, picture_cycle)
            if reply is not None:
                actions.append(reply)
                has_response = True
        groups = episode.groups
        if has_response and "par" not in groups:
            groups = groups + ("par",)
        return replace(episode, actions=tuple(actions), groups=groups)

    def _response_action(self, episode: AutoEpisode, picture: float) -> AutoAction | None:
        theme_groups = self._active_groups(episode.theme)
        if "par" not in theme_groups:
            return None
        names = director_looks(
            "phrase",
            episode.theme,
            character=self.character,
            current=episode.look,
            fixtures=self._fixtures,
            program=_program_of(episode.look),
            excluded=self._excluded,
            groups=self._groups,
        )
        look = None
        for name in names:
            item = BY_ID.get(name)
            if item is None or name == episode.look:
                continue
            if "par" not in item.groups:
                continue
            if item.roles in {"call_lr", "layer_left_right"}:
                continue
            if item.stagger in {"x", "opposite", "alternate"}:
                continue
            look = name
            break
        if look is None and "breathe_pars" in LOOKS and "breathe_pars" not in self._excluded:
            if self._look_fits("breathe_pars", episode.theme):
                look = "breathe_pars"
        if look is None or look in self._excluded:
            return None
        floor = _cycle_floor(look, self.character)
        cycle = max(floor, min(2.0, max(1.0, float(picture))))
        return AutoAction("response", look, cycle, groups=("par",))

    def _compose(
        self,
        start: float,
        section: str,
        level: float,
        theme: str,
        reason: str,
    ) -> AutoEpisode:
        role = "motif"
        live = self.current
        if reason == "drop_next":
            section = "drop"
            kind = "accent"
            role = "accent"
            look = self._pick(kind, theme, prefer=None if live is None else live.look)
        elif reason == "signal_loss":
            kind = "signal"
            look = self._pick(kind, theme)
        elif reason in {"related", "develop"}:
            kind = _kind_for(section, level, reason, self._hearing)
            look = self._pick(
                kind,
                theme,
                prefer=None if live is None else live.look,
                related=True,
                program=self._kept_program(live),
            )
        elif reason == "flow":
            kind = _kind_for(section, level, reason, self._hearing)
            look = self._pick(
                kind,
                theme,
                prefer=None if live is None else live.look,
                related=True,
                program=self._kept_program(live),
            )
            if look == "wash":
                look = live.look if live is not None else look
        elif reason == "return":
            kind = "phrase"
            section = "groove"
            look = self._pick(kind, theme, prefer=self._home_look)
        elif live is not None and live.role != "accent" and reason in {"queued", "hold", "boundary"}:
            kind = _kind_for(section, level, reason, self._hearing)
            if section == "break":
                look = live.look
            else:
                look = live.look
        else:
            kind = _kind_for(section, level, reason, self._hearing)
            look = self._pick(kind, theme, prefer=self._home_look or (None if live is None else live.look))
        duration = _duration_for(kind, self.character, level)
        picture = _picture_cycle(kind, duration, look, self.character, self.period)
        if live is not None and self.period is None and reason != "signal_loss":
            picture = _approach_cycle(float(live.picture_cycle), picture, self.character)
        actions = [AutoAction("picture", look, picture)]
        if not look or not self._look_fits(look, theme):
            look = ""
        theme_groups = () if not look else self._active_groups(theme)
        chosen = BY_ID.get(look)
        groups = theme_groups
        if chosen is not None:
            owned = tuple(name for name in chosen.groups if name in theme_groups)
            if owned:
                groups = owned
        if live is not None and look == live.look and reason in {"queued", "hold", "boundary"}:
            carried = self._carry_motif(
                AutoEpisode(
                    ident=self._ident,
                    look=look,
                    groups=groups,
                    theme=theme,
                    intensity=level,
                    start_beat=float(start),
                    duration_beats=duration,
                    actions=tuple(actions),
                    transition="beat" if duration <= 4 or self.character == "impact" else "bar",
                    effects=(),
                    end_early=("signal_loss", "blackout"),
                    reason=reason,
                    section=section if section in {"groove", "break", "riser", "drop"} else "groove",
                    character=self.character,
                    cast=self._cast_name(theme),
                    palette=colour_of(theme, self.palette),
                    clock=self._clock,
                    motif=live.motif or look,
                    role="motif",
                ),
                live,
            )
            carried = replace(carried, start_beat=float(start), duration_beats=duration, intensity=level, section=carried.section if reason == "queued" else section)
            self._ident += 1
            return self._with_effects(replace(carried, ident=self._ident - 1, reason=reason, theme=theme, palette=colour_of(theme, self.palette)))
        bed = self._bed_action(kind, theme, theme_groups, picture)
        if bed is not None:
            actions.append(bed)
            extra = tuple(name for name in bed.groups or () if name not in groups)
            groups = groups + extra
        if live is not None and look == live.look:
            floor = _cycle_floor(look, self.character)
            for action in live.actions:
                if action.role == "response" and not any(item.role == "response" for item in actions):
                    if self.character == "smooth" or float(action.cycle_beats) + 1e-9 < floor:
                        continue
                    actions.append(action)
                    extra = tuple(name for name in action.groups or () if name not in groups)
                    groups = groups + extra
        transition = "beat" if duration <= 4 or self.character == "impact" else "bar"
        if role == "accent":
            transition = "beat"
        episode = AutoEpisode(
            ident=self._ident,
            look=look,
            groups=groups,
            theme=theme,
            intensity=level,
            start_beat=float(start),
            duration_beats=duration,
            actions=tuple(actions),
            transition=transition,
            effects=(),
            end_early=("signal_loss", "blackout"),
            reason=reason,
            section=section if section in {"groove", "break", "riser", "drop"} else "groove",
            character=self.character,
            cast=self._cast_name(theme),
            palette=colour_of(theme, self.palette),
            clock=self._clock,
            motif=self._home_look or look,
            role=role,
        )
        self._ident += 1
        return self._with_effects(episode)

    def _bed_action(
        self,
        kind: str,
        theme: str,
        groups: tuple[str, ...],
        picture: float,
    ) -> AutoAction | None:
        if kind in {"accent", "signal"} or "par" not in groups:
            return None
        if self.character == "impact":
            return None
        look = "breathe_pars" if "breathe_pars" in LOOKS else "wash"
        if look in self._excluded or not self._look_fits(look, theme):
            return None
        return AutoAction("bed", look, self._bed_cycle(picture), groups=("par",))

    def _with_effects(self, episode: AutoEpisode) -> AutoEpisode:
        if self._blackout or episode.reason == "signal_loss":
            return replace(episode, effects=())
        if episode.look == "wash" and episode.role != "accent":
            return replace(episode, effects=())
        if episode.reason == "drop_next" or episode.role == "accent":
            span = min(2.0, float(episode.duration_beats))
            effect = AutoEffect("flash", "auto", episode.start_beat, span, "accent")
            return replace(episode, effects=(effect,))
        return replace(episode, effects=())

    def _sync_series(
        self,
        beats: float,
        *,
        drive: bool,
        drive_id: int,
        density: float,
        wall_s: float | None,
    ) -> None:
        """Arm or kill the flash series from the musical confirmation, not the episode."""
        if self._series_on:
            self._series_density = max(0.0, min(1.0, density))
            if not drive:
                self._stop_series(beats, "drive")
                return
            if self._series_until is not None and beats >= self._series_until - 1e-9:
                self._stop_series(beats, "limit")
            return
        if not drive or self.character == "smooth":
            return
        if density < _SERIES_MIN_DENSITY:
            return
        if drive_id <= 0 or drive_id == self._series_drive_id:
            return
        if self._series_cool_until is not None and beats < self._series_cool_until - 1e-9:
            return
        self._series_on = True
        self._series_drive_id = int(drive_id)
        self._series_origin_beats = float(beats)
        self._series_wall0 = None if wall_s is None else float(wall_s)
        self._series_until = float(beats) + _SERIES_LIMIT_BEATS
        self._series_density = max(0.0, min(1.0, density))
        self._note(beats, "series", drive_id=drive_id, density=round(self._series_density, 3))

    def _stop_series(self, beats: float, reason: str) -> None:
        if not self._series_on:
            return
        self._series_on = False
        self._series_origin_beats = None
        self._series_wall0 = None
        self._series_until = None
        self._series_cool_until = float(beats) + _SERIES_COOL_BEATS
        self._note(beats, "series_stop", reason=reason)

    def _kept_program(self, live: AutoEpisode | None) -> str | None:
        """A step stays in the program already playing. Melody stays smooth."""
        if self._clock == "melodic":
            return "smooth"
        if live is None:
            return None
        return _program_of(live.look)

    def _allowed_ids(
        self,
        theme: str,
        *,
        program: str | None = None,
        pool: str | None = None,
    ) -> tuple[str, ...]:
        return tuple(
            item.id
            for item in allowed_compositions(
                theme,
                program=program,
                pool=pool,
                fixtures=self._fixtures,
                excluded=self._excluded,
                groups=self._groups,
            )
        )

    def _cast_name(self, theme: str) -> str:
        if self._groups is not None:
            legacy = legacy_cast(self._groups)
            if legacy in {"bars", "heads"}:
                return legacy
        return cast_of(theme)

    def _active_groups(self, theme: str) -> tuple[str, ...]:
        if self._groups is not None:
            return self._groups
        return groups_of(cast_of(theme))

    def _look_fits(self, name: str | None, theme: str) -> bool:
        if not name or str(name) in self._excluded:
            return False
        item = BY_ID.get(str(name))
        if item is None:
            return False
        groups = set(self._active_groups(theme))
        if item.needs_segments and "bar" not in groups:
            return False
        owned = set(item.groups)
        if self._groups is not None:
            if not owned or not owned <= groups:
                return False
        elif not owned.intersection(groups):
            return False
        if self._fixtures is not None and not composition_fits(item, self._fixtures, theme):
            return False
        return True

    def _pick(
        self,
        kind: str,
        theme: str,
        *,
        prefer: str | None = None,
        related: bool = False,
        program: str | None = None,
    ) -> str:
        if kind == "signal":
            if self._look_fits("wash", theme):
                return "wash"
            calm = [name for name in self._allowed_ids(theme, program="smooth") if self._look_fits(name, theme)]
            return calm[0] if calm else ""
        bucket = {"calm": "calm", "rise": "rise", "accent": "accent"}.get(kind, "phrase")
        current = prefer or (None if self.current is None else self.current.look)
        home = self._home_look or director_home(self.character, theme, groups=self._groups)
        if home in self._excluded:
            home = ""
        neighborhood = director_looks(
            bucket,
            theme,
            character=self.character,
            current=current or home or None,
            fixtures=self._fixtures,
            program=program,
            excluded=self._excluded,
            groups=self._groups,
        )
        if not neighborhood:
            for candidate in (current, home, "wash"):
                if candidate and self._look_fits(candidate, theme):
                    item = BY_ID.get(str(candidate))
                    if program is not None and (item is None or item.program != program):
                        continue
                    return str(candidate)
            wider = [name for name in self._allowed_ids(theme, program=program, pool=bucket) if self._look_fits(name, theme)]
            if wider:
                return wider[0]
            return "" if not self._look_fits("wash", theme) else "wash"
        if self._home_look is None:
            if home in neighborhood:
                return home
            return neighborhood[0]
        if related:
            recent = set(self._recent)
            families = CHARACTER_FAMILIES.get(self.character, CHARACTER_FAMILIES["rhythmic"])
            def _open(name: str, *, family_only: bool) -> bool:
                if name == current or name in recent:
                    return False
                item = BY_ID.get(name)
                if item is None:
                    return False
                return (not family_only) or item.family in families
            preferred = [name for name in neighborhood if _open(name, family_only=True)]
            wider = [name for name in neighborhood if _open(name, family_only=False)]
            if preferred and (not wider or self._rng.random() < 0.7):
                return self._rng.choice(preferred)
            others = wider or preferred or [name for name in neighborhood if name != current]
            if not others:
                others = [
                    name
                    for name in self._allowed_ids(theme, program=program, pool=bucket)
                    if name != current
                ]
            if others:
                return self._rng.choice(others)
            return current or neighborhood[0]
        if kind == "accent":
            return self._rng.choice(list(neighborhood))
        if current in neighborhood:
            return current
        if home in neighborhood:
            return home
        return neighborhood[0]

    def _remember_drop(self, ident: int) -> bool:
        """True the first time this drop is seen. The memory stays bounded."""
        number = int(ident)
        if number in self._seen:
            return False
        self._seen.add(number)
        self._seen_order.append(number)
        while len(self._seen_order) > _SEEN_LIMIT:
            self._seen.discard(self._seen_order.pop(0))
        return True

    def _remember(self, look: str) -> None:
        self._recent = (self._recent + (look,))[-8:]
        item = BY_ID.get(look)
        if item is not None:
            self._recent_families = (self._recent_families + (item.family,))[-2:]

    _recent: tuple[str, ...] = ()

    def _note(self, beats: float, kind: str, **fields: object) -> None:
        row: dict[str, object] = {"beat": round(float(beats), 3), "kind": kind}
        row.update(fields)
        self._journal.append(row)
        if len(self._journal) > _JOURNAL_LIMIT:
            del self._journal[: len(self._journal) - _JOURNAL_LIMIT]


def _reanchor_action(action: AutoAction, beats: float, cycle: float) -> AutoAction:
    """Keep the painted phase. Only later speed follows the new period."""
    new_cycle = float(cycle)
    old_cycle = max(1e-9, float(action.cycle_beats))
    if abs(new_cycle - float(action.cycle_beats)) < 1e-12:
        return action
    origin = float(action.origin_beats)
    phase = ((float(beats) - origin) / old_cycle) % 1.0
    return replace(action, cycle_beats=new_cycle, origin_beats=float(beats) - phase * new_cycle)


def _kind_for(section: str, level: float, reason: str, hearing: str = "rhythmic") -> str:
    del level
    if reason == "signal_loss":
        return "signal"
    if reason == "drop_next":
        return "accent"
    if section == "break":
        return "calm"
    if section == "riser":
        return "rise"
    if hearing == "melodic" and reason not in {"drop_next", "signal_loss"}:
        return "calm"
    return "phrase"


def _duration_for(kind: str, character: str, level: float) -> int:
    """Musical length of the motif window. Loudness does not collapse it."""
    del level
    table = CHARACTER_DURATION.get(character, CHARACTER_DURATION["rhythmic"])
    if kind == "accent":
        return table["accent"]
    if kind in {"calm", "signal"}:
        return table["calm"] if kind == "calm" else table["signal"]
    if kind == "rise":
        return table["rise"]
    return table["phrase"]


def _variation_cycles(character: str) -> tuple[float, ...]:
    if character == "smooth":
        return (2.0, 4.0, 8.0)
    if character == "impact":
        return (0.5, 1.0, 2.0, 4.0)
    return tuple(AUTO_CYCLE_BEATS)


def _program_of(look: str | None) -> str:
    item = BY_ID.get(str(look or ""))
    return "beat" if item is None else str(item.program)


def _cycle_floor(look: str | None, character: str) -> float:
    """Ordinary variation does not turn a picture into a blink."""
    item = BY_ID.get(str(look or ""))
    if item is not None and "beam" in item.groups and item.program == "beat":
        return 2.0
    if character == "smooth":
        return 2.0
    envelope = "" if item is None else str(item.envelope)
    if envelope in _WAVE_ENVELOPES:
        return 2.0 if character == "smooth" else 1.0
    return 1.0


def _approach_cycle(previous: float, target: float, character: str) -> float:
    """A new picture may change speed by one subdivision, not from 8 straight to 0.5."""
    choices = sorted(set(_variation_cycles(character)) | set(AUTO_CYCLE_BEATS))
    if not choices:
        return float(target)
    prev_i = min(range(len(choices)), key=lambda i: abs(choices[i] - float(previous)))
    tgt_i = min(range(len(choices)), key=lambda i: abs(choices[i] - float(target)))
    if abs(tgt_i - prev_i) <= 1:
        return float(choices[tgt_i])
    return float(choices[prev_i + (1 if tgt_i > prev_i else -1)])


def _step_cycle(
    current: float,
    allowed: tuple[float, ...],
    rng: random.Random,
    floor: float,
) -> float:
    """Move one musical subdivision. Never jump 8 → 0.5."""
    choices = sorted({cycle for cycle in allowed if cycle + 1e-9 >= float(floor)})
    if not choices:
        return max(float(floor), float(current))
    if float(current) + 1e-9 < float(floor):
        return choices[0]
    index = min(range(len(choices)), key=lambda i: abs(choices[i] - float(current)))
    neighbors = []
    if index > 0:
        neighbors.append(choices[index - 1])
    if index < len(choices) - 1:
        neighbors.append(choices[index + 1])
    if not neighbors:
        return choices[index]
    return float(rng.choice(neighbors))


def _picture_cycle(
    kind: str,
    duration: int,
    look: str,
    character: str,
    period: float | None,
) -> float:
    forced = known_period(period, character)
    if forced is not None:
        return forced
    if look == "pulse" and kind == "phrase" and character == "rhythmic":
        return 0.5
    table = CHARACTER_CYCLE.get(character, CHARACTER_CYCLE["rhythmic"])
    if kind == "accent":
        return table["accent"]
    if kind == "rise":
        return table["rise"]
    if kind in {"calm", "signal"}:
        return table["calm"]
    if duration >= 16 and character == "smooth":
        return 8.0
    return table["phrase"]


def _motion_cycle(picture: float, kind: str) -> float | None:
    if kind in {"accent", "signal"} or picture < 4:
        return None
    return 2.0
