"""Automatic light for BPM sync. Colour and intensity only.

Heads keep the position they already have. This look never sends pan or tilt,
and it never writes a channel the fixture profile does not map.

The section ear is not a second tempo clock. Beat, bar and phrase boundaries
come from the existing follower. This module compares level, rhythmic density,
low/high bands and a few bars of context so a fader is not a drop, a quiet
copy of the same groove is not a break, and a pad swell is not a riser.

Acceptance bounds. These are the contract; do not widen them to fit a take.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np

from orng_led.audio.bpm import ANALYSIS_HOP, FLUX_LAG_S
from orng_led.audio.canvas import build_canvas
from orng_led.audio.compositions import LOOKS, look_name
from orng_led.audio.library import paint, resolve_composition
from orng_led.audio.palettes import COLOR_IDS as PALETTE_COLOR_IDS
from orng_led.audio.palettes import rgbw_of
from orng_led.audio.settings import cast_of, colour_of, groups_of
from orng_led.config.models import FixtureKind
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw, StageIntent

PALETTE_IDS = PALETTE_COLOR_IDS + ("bars", "heads")
SECTIONS = ("groove", "break", "riser", "drop")
# Auto may use a half beat. Preset documents keep 1/2/4/8.
AUTO_CYCLE_BEATS = (0.5, 1.0, 2.0, 4.0, 8.0)


@dataclass(frozen=True)
class StructuralEvent:
    """One confirmed musical change. Holding the section does not emit another."""

    kind: str
    ident: int
    at_s: float


# Real time, not the length of an overlapping analysis window.
NOISE_RMS = 0.004
# One pause length for the hearing, the riser, and the lamps. A missing kick
# is shorter than this. USB hiss counts; exact zero is not required.
NEAR_SILENCE_S = 1.5
SILENCE_S = NEAR_SILENCE_S
INPUT_LOSS_S = 1.2
ATTACK_TAU_S = 0.35
CONTEXT_TAU_S = 8.0
GAIN_RATIO = 1.8
GAIN_ONSET_MAX = 0.35
MIN_SECTION_S = 1.2
QUIET_ACTIVITY = 0.42
GROOVE_ACTIVITY = 0.62
RISE_SLOPE = 0.08
RISE_HOLD_S = 1.0
RISER_LIMIT_S = 6.0
DROP_ONSET = 1.2
DROP_ONSET_REL = 1.8
# Onset divided by RMS squared. A click stays large at every gain; a tone,
# noise, or a fader stays near 1. Loudness is not a section and not an accent.
GROOVE_REL = 4.0
ACCENT_REL = 4.0
DENSITY_TAU_S = 0.55
DENSITY_CTX_S = 4.0
BAND_TAU_S = 0.7
MIN_RISE_DENSITY = 4.2
RISE_DENSITY_RATIO = 1.45
RISE_DENSITY_EXTRA = 2.0
RISE_WARM_S = 2.2
BREAK_HOLD_S = 1.35
BREAK_LOW_RATIO = 0.42
DENSITY_STABLE = 0.32
TEXTURE_HOLD_S = 3.6
TEXTURE_DELTA = 0.30
TEXTURE_COOLDOWN_S = 8.0
DEFAULT_BEAT_S = 0.5
DROP_ACCENT_S = 0.5
DROP_HOLD_S = 0.7
DROP_COOLDOWN_S = 4.0
STROBE_FRAGMENT_S = 0.8
PHRASE_BARS = 4
PATTERN_GLIDE_S = 0.45
LEVEL_GLIDE_S = 0.28
# Bars are a semantic 8 in the profile model. A fixture may say otherwise.
_DEFAULT_SEGMENTS = 8

_KINDS: dict[str, frozenset[FixtureKind]] = {
    "orange": frozenset({FixtureKind.PAR, FixtureKind.BAR, FixtureKind.BEAM}),
    "red": frozenset({FixtureKind.PAR, FixtureKind.BAR, FixtureKind.BEAM}),
    "blue": frozenset({FixtureKind.PAR, FixtureKind.BAR, FixtureKind.BEAM}),
    "violet": frozenset({FixtureKind.PAR, FixtureKind.BEAM, FixtureKind.BAR}),
    "all": frozenset({FixtureKind.PAR, FixtureKind.BAR, FixtureKind.BEAM}),
    "bars": frozenset({FixtureKind.BAR}),
    "heads": frozenset({FixtureKind.PAR, FixtureKind.BEAM}),
}
_GROUP_KIND = {
    "par": FixtureKind.PAR,
    "bar": FixtureKind.BAR,
    "beam": FixtureKind.BEAM,
}
_INTENSITY_CAP: dict[str, float] = {
    "orange": 1.0,
    "red": 1.0,
    "blue": 1.0,
    "violet": 1.0,
    "bars": 1.0,
    "heads": 0.8,
}


class LitFixture(Protocol):
    id: str
    kind: FixtureKind
    spatial: object


def _activity_of(rel: float, onset_rel: float) -> float:
    """1.0 relative level is the recent context, not a drop and not a rise."""
    rel_c = min(max(rel, 0.0), 1.8)
    onset_c = min(max(onset_rel, 0.0), 2.5)
    return 0.7 * (rel_c / 1.8) + 0.3 * min(1.0, onset_c / 1.2)


def _ema(previous: float, value: float, dt_s: float, tau_s: float) -> float:
    if tau_s <= 0.0:
        return value
    alpha = 1.0 - math.exp(-max(0.0, dt_s) / tau_s)
    return previous + (value - previous) * alpha


@dataclass(frozen=True)
class SliceHearing:
    """One new slice. Attack times are hop-peak times, not a tempo grid.

    Unpacks as ``(rms, onset)`` so older callers keep working. Density is
    events per second in this slice; the tracker holds several bars of that.
    """

    rms: float
    onset: float
    density: float = 0.0
    attacks: tuple[float, ...] = ()
    low: float = 0.0
    high: float = 0.0
    low_density: float = 0.0
    high_density: float = 0.0
    low_attacks: tuple[float, ...] = ()
    high_attacks: tuple[float, ...] = ()
    origin_s: float | None = None
    origin_clock: str | None = None
    generation: int | None = None

    def __iter__(self):
        yield self.rms
        yield self.onset


def _empty_hearing(rms: float = 0.0, onset: float = 0.0) -> SliceHearing:
    return SliceHearing(rms, onset)


def _slice_peaks(
    flux: np.ndarray,
    peaks: np.ndarray,
    hop: int,
    sample_rate: int,
    refractory_s: float = 0.072,
    *,
    skip_edge: int = 0,
    impulsive: np.ndarray | None = None,
) -> list[float]:
    """Local flux peaks with original hop-peak times. Not a period estimate."""
    count = int(flux.size)
    if count < 3:
        return []
    peak = float(np.max(flux))
    if peak < 1e-6:
        return []
    med = float(np.median(flux))
    mad = float(np.median(np.abs(flux - med)))
    floor = max(med + 2.4 * mad, peak * 0.08, 1e-9)
    found: list[tuple[float, float]] = []
    last = -10.0
    for index in range(count):
        value = float(flux[index])
        if value <= floor:
            continue
        if index < skip_edge and (impulsive is None or not bool(impulsive[index])):
            continue
        left = float(flux[index - 1]) if index else 0.0
        right = float(flux[index + 1]) if index + 1 < count else 0.0
        if value <= left or value <= right:
            continue
        when = (index * hop + int(peaks[index])) / float(sample_rate)
        if when - last < refractory_s:
            if found and value > found[-1][1]:
                found[-1] = (when, value)
                last = when
            continue
        found.append((when, value))
        last = when
    return [when for when, _value in found]


def analyze_slice(samples: object, sample_rate: int = 44100) -> SliceHearing:
    """RMS, strongest energy jump, attack times, density, and two bands.

    The slice is only the audio that arrived since the previous push. Onset stays
    the raw energy jump. Hearing and accents divide it by the slice level;
    section ratios already do the same against their own context. This is not a
    second BPM reading: times stay on the original hop peaks.
    """
    block = np.asarray(samples, dtype=np.float64).reshape(-1)
    block = np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0)
    count = int(block.size)
    if count == 0:
        return _empty_hearing()
    rms = float(np.sqrt(np.mean(block * block)))
    hop = ANALYSIS_HOP
    rate = max(8000, int(sample_rate))
    if count < hop * 2:
        return _empty_hearing(rms)
    frames = block[: (count // hop) * hop].reshape(-1, hop)
    energy = np.mean(frames * frames, axis=1)
    if energy.size < 2:
        return _empty_hearing(rms)
    jump = np.diff(energy, prepend=float(energy[0]))
    jump[0] = float(energy[0])
    onset = float(max(0.0, np.max(jump)))
    # Hop FFT bins are ~172 Hz at 44.1 kHz. Kick body needs the whole slice.
    taper = np.hanning(count)
    full = np.abs(np.fft.rfft(block * taper))
    full_f = np.fft.rfftfreq(count, 1.0 / float(rate))

    def _slice_band(lo: float, hi: float) -> float:
        mask = (full_f >= lo) & (full_f < hi)
        if not np.any(mask):
            return 0.0
        return float(np.sqrt(np.mean(full[mask] ** 2)))

    low = _slice_band(40.0, 180.0)
    high = _slice_band(4000.0, 10000.0)
    window = np.hanning(hop)
    spec = np.abs(np.fft.rfft(frames * window, axis=1))
    freqs = np.fft.rfftfreq(hop, 1.0 / float(rate))
    lag = max(1, int(round(FLUX_LAG_S * float(rate) / hop)))

    def _flux(lo: float, hi: float) -> np.ndarray:
        weights = np.clip((freqs - lo) / max(20.0, lo * 0.25), 0.0, 1.0) * np.clip(
            (hi - freqs) / max(40.0, hi * 0.08), 0.0, 1.0
        )
        weighted = spec * weights
        previous = np.zeros_like(weighted)
        hops = int(weighted.shape[0])
        if hops > lag:
            previous[lag:] = weighted[:-lag]
        return np.maximum(0.0, weighted - previous).sum(axis=1)

    flux = _flux(40.0, 5000.0)
    low_flux = _flux(40.0, 400.0)
    high_flux = _flux(4000.0, 10000.0)
    rms_h = np.sqrt(np.mean(frames * frames, axis=1))
    peak = np.max(np.abs(frames), axis=1)
    peaks = np.full(int(frames.shape[0]), hop * 0.5, dtype=np.float64)
    impulsive = peak > 2.0 * (rms_h + 1e-12)
    if np.any(impulsive):
        peaks[impulsive] = frames.argmax(axis=1)[impulsive].astype(np.float64)
    attacks = tuple(
        _slice_peaks(flux, peaks, hop, rate, skip_edge=lag, impulsive=impulsive)
    )
    low_hits = _slice_peaks(
        low_flux, peaks, hop, rate, skip_edge=lag, impulsive=impulsive
    )
    high_hits = _slice_peaks(
        high_flux, peaks, hop, rate, skip_edge=lag, impulsive=impulsive
    )
    duration = max(count / float(rate), 1e-6)
    return SliceHearing(
        rms=rms,
        onset=onset,
        density=len(attacks) / duration,
        attacks=attacks,
        low=low,
        high=high,
        low_density=len(low_hits) / duration,
        high_density=len(high_hits) / duration,
        low_attacks=tuple(low_hits),
        high_attacks=tuple(high_hits),
    )


def _extra_attack_offset(
    low_attacks: tuple[float, ...] | list[float],
    high_attacks: tuple[float, ...] | list[float],
) -> float | None:
    """Newest high-band hit that is not the kick, in seconds from the slice start."""
    low = tuple(float(item) for item in low_attacks)
    high = tuple(float(item) for item in high_attacks)
    unmatched = [hit for hit in high if all(abs(hit - kick) > 0.025 for kick in low)]
    if not unmatched:
        return None
    return max(unmatched)


def _extra_attack_age(
    low_attacks: tuple[float, ...] | list[float],
    high_attacks: tuple[float, ...] | list[float],
    dt: float,
) -> float | None:
    """Seconds since the newest high-band hit that is not the kick."""
    low = tuple(float(item) for item in low_attacks)
    high = tuple(float(item) for item in high_attacks)
    unmatched = [hit for hit in high if all(abs(hit - kick) > 0.025 for kick in low)]
    if not unmatched:
        return None
    latest = max(unmatched)
    if latest < 0.0 or latest > float(dt) + 1e-6:
        return None
    return max(0.0, float(dt) - latest)


def extra_layer(low_attacks: tuple[float, ...] | list[float], high_attacks: tuple[float, ...] | list[float]) -> bool:
    """High-band hits that are not the kick.

    A second BPM, or the same pulse at half or double time, is not a second
    instrument. Hits that land on the low-band attacks are the same stroke.
    """
    low = tuple(float(item) for item in low_attacks)
    high = tuple(float(item) for item in high_attacks)
    if len(high) < 2:
        return False
    unmatched = [hit for hit in high if all(abs(hit - kick) > 0.025 for kick in low)]
    if len(unmatched) < 2:
        return False
    gaps = [right - left for left, right in zip(unmatched, unmatched[1:]) if right > left]
    return any(gap >= 0.04 for gap in gaps)


class SectionTracker:
    """Groove, break, riser, drop. Loudness alone is not a section."""

    def __init__(self) -> None:
        self.level = 0.55
        self.shown = 0.55
        self.section = "groove"
        self.drop_age = 0.0
        self.riser_age = 0.0
        self.confidence = 0.0
        self.pattern = 0
        self.mix = 1.0
        self._fast = 0.0
        self._context = 0.0
        self._onset_fast = 0.0
        self._onset_context = 0.0
        self._prev_rms = 0.0
        self._section_age = MIN_SECTION_S
        self._silent = 0.0
        self._loud_ref: float | None = None
        self._anchor = False
        self._dark_for = 0.0
        self._rise = 0.0
        self._rise_from: float | None = None
        self._break_from: float | None = None
        self._flat = 0.0
        self._drop_left = 0.0
        self._cooldown = 0.0
        self._activity = 0.55
        self._activity_slow = 0.55
        self._slope = 0.0
        self._warm = 0.0
        self._bars = 0
        self._bar: float | None = None
        self._seen: float | None = None
        self._clock = 0.0
        self._event_seq = 0
        self._events: list[StructuralEvent] = []
        self.cycle_beats = 4
        self.input_lost = False
        self._den_fast = 0.0
        self._den_ctx = 0.0
        self._low_fast = 0.0
        self._low_ctx = 0.0
        self._high_fast = 0.0
        self._high_ctx = 0.0
        self._low_d_fast = 0.0
        self._low_d_ctx = 0.0
        self._high_d_fast = 0.0
        self._high_d_ctx = 0.0
        self._tex_fast = 0.5
        self._tex_ctx = 0.5
        self._tex_hold = 0.0
        self._tex_cool = 0.0
        self._break_hold = 0.0
        self._have_hearing = False
        self._beat_period = DEFAULT_BEAT_S
        self._tempo_known = False
        self._musical_rise = False
        self._climb_for = 0.0
        self._hat_for = 0.0
        self._activity_drop = False
        self._level_change = False
        self._noted_level = False
        self.journal: list[tuple[float, str, str]] = []
        self.texture_id = 0
        self.drive_id = 0
        self.accent_amount = 0.0
        self.accent_ready: float | None = None
        self.accent_strength = 0.0
        self.extra_pulse = False
        self.extra_phase: float | None = None
        self.extra_attack_at: float | None = None
        self.extra_generation: int | None = None
        self._extra_seen: list[tuple[int | None, float]] = []
        self._wall = 0.0
        self._slice_start: float | None = None
        self._accent_at: float | None = None
        self._accent_ids: list[tuple[float, float]] = []
        self._hear = "melodic"
        self._hear_for = 0.0
        self._gap_credit = 0.0
        self._noise_floor = NOISE_RMS
        self._floor_hold = 0.0
        self._floor_sample = NOISE_RMS
        self._loud_run: list[float] = []
        self._loud_held = 0.0
        self._extra_for = 0.0
        self.reasons: list[tuple[float, float, str, str]] = []

    @property
    def clock_s(self) -> float:
        return float(self._clock)

    @property
    def hearing(self) -> str:
        """What the room is doing. Missing lock is not itself a pause or a drop."""
        if self.input_lost:
            return "absent"
        if self._silent >= NEAR_SILENCE_S:
            return "silence"
        return self._hear

    def consider_accent(
        self,
        *,
        now: float,
        onset: float,
        level: float,
        phase: float | None,
        attack_at: float | None = None,
        attack_id: tuple[float, float] | None = None,
        attack: bool = True,
    ) -> None:
        """A clear extra attack. Not a grid kick, a fader, or the tracker's own window.

        ``attack_at`` is the attack's own time. A late drain does not make it fresh.
        """
        if self.accent_amount <= 0.0 or not attack:
            return
        level_now = float(level)
        # No useful signal. This floor is not a loudness class: the same click
        # stays an accent when it is only turned down.
        if level_now <= NOISE_RMS:
            return
        unit = float(onset) / max(level_now * level_now, 1e-8)
        if unit < ACCENT_REL:
            return
        if phase is not None:
            frac = abs(float(phase) % 1.0)
            frac = min(frac, 1.0 - frac)
            if frac < 0.12:
                return
        when = float(now if attack_at is None else attack_at)
        if float(now) - when > 0.12:
            return
        if any(abs(when - previous) < 0.08 for previous, _strength in self._accent_ids):
            return
        gap = 0.9 - 0.55 * float(self.accent_amount)
        if self._accent_at is not None and when - self._accent_at < gap:
            return
        self._accent_ids.append((when, unit))
        del self._accent_ids[:-32]
        self._accent_at = when
        self.accent_strength = unit
        self.accent_ready = when

    def take_events(self) -> tuple[StructuralEvent, ...]:
        """Events since the last take. Each identity is emitted once."""
        found = tuple(self._events)
        self._events.clear()
        return found

    def lost_signal(self) -> None:
        """Input gone. End any rise, accent, or automatic strobe."""
        self.section = "break"
        self.cycle_beats = 8
        self._drop_left = 0.0
        self.drop_age = 0.0
        self.riser_age = 0.0
        self._rise = 0.0
        self._rise_from = None
        self._break_from = None
        self._flat = 0.0
        self._silent = 0.0
        self.level = 0.0
        self.shown = 0.0
        self.confidence = 0.0
        self._cooldown = 0.0
        self.input_lost = True
        self._break_hold = 0.0
        self._musical_rise = False
        self._climb_for = 0.0
        self._hat_for = 0.0
        self._activity_drop = False
        self._log("break", "signal")

    @property
    def roll_drive(self) -> bool:
        """Confirmed accelerating attacks. Not loudness, riser, or a new episode."""
        return bool(self._musical_rise) and not self.input_lost

    @property
    def roll_density(self) -> float:
        """How far the roll has packed attacks, 0..1. Used for beat subdivisions."""
        if not self._have_hearing or not self._musical_rise:
            return 0.0
        extra = max(0.0, self._den_fast - self._den_ctx)
        climb = min(1.0, self._climb_for / 3.0)
        return max(0.0, min(1.0, max(extra / 8.0, climb)))

    def push(
        self,
        energy: float,
        dt_s: float,
        onset: float | None = None,
        hearing: SliceHearing | None = None,
        beat_period_s: float | None = None,
        now: float | None = None,
        slice_start: float | None = None,
    ) -> str:
        """``dt_s`` is time that actually passed, not an overlapping window."""
        rms = max(0.0, float(energy))
        dt = max(0.0, float(dt_s))
        if dt <= 0.0:
            return self.section
        self._clock += dt
        self._wall = float(self._clock if now is None else now)
        self._slice_start = None if slice_start is None else float(slice_start)
        if beat_period_s is not None and 0.25 <= float(beat_period_s) <= 1.0:
            self._beat_period = float(beat_period_s)
            self._tempo_known = True
        else:
            self._tempo_known = False
        self._have_hearing = hearing is not None
        attack = 0.0 if onset is None else max(0.0, float(onset))
        if hearing is not None:
            rms = max(rms, float(hearing.rms))
            if onset is None:
                attack = max(0.0, float(hearing.onset))
        self._observe_floor(rms, dt)
        if self._is_near_silence(rms):
            self._silent += dt
            self._fast = _ema(self._fast, 0.0, dt, ATTACK_TAU_S)
            self._smooth_hearing(0.0, 0.0, 0.0, 0.0, 0.0, dt)
            self._prev_rms = 0.0
            self._ride_loudness(rms, dt)
            # Samples are still arriving. This is a pause, not a pulled cable.
            if self._silent >= NEAR_SILENCE_S:
                self._gap_credit = 0.0
                self._pause_for_silence()
                self._silent = NEAR_SILENCE_S
                return self.section
            # A gap between kicks is not silence yet. The rise still spends
            # this time, or one missing kick would end the build.
            self._section_age += dt
            # A gap inside a pulse is not a new hearing. Remember it; do not
            # score the decaying onset while the slice itself is empty.
            self._gap_credit = min(0.5, self._gap_credit + dt)
            self._cooldown = max(0.0, self._cooldown - dt)
            self._hold_drop(dt)
            if self._drop_left <= 0.0 and self.section == "drop":
                self._enter("groove", force=True, keep_pattern=True)
            elif self.section == "riser":
                self.riser_age += dt
                if self.riser_age >= RISER_LIMIT_S:
                    self._enter("groove", force=True)
                    self._log("groove", "riser-limit")
            return self.section
        was_silent = self._silent >= 0.4
        self._silent = 0.0
        self.input_lost = False
        if self._warm <= 0.0:
            self._adopt(rms, attack, dt, hearing=hearing)
            self._ride_loudness(rms, dt)
            return self.section
        self._fast = _ema(self._fast, rms, dt, ATTACK_TAU_S)
        self._onset_fast = _ema(self._onset_fast, attack, dt, ATTACK_TAU_S)
        if hearing is not None:
            self._smooth_hearing(
                hearing.density,
                hearing.low,
                hearing.high,
                hearing.low_density,
                hearing.high_density,
                dt,
            )
        if was_silent:
            # The pause is not a density context. The first notes are not a rise.
            self._den_ctx = self._den_fast
            self._low_d_ctx = self._low_d_fast
            self._high_d_ctx = self._high_d_fast
            self._musical_rise = False
            self._rise = 0.0
            self._rise_from = None
        onset_norm = attack / max(rms * rms, 1e-8)
        den_rel = self._den_fast / max(self._den_ctx, 0.45)
        # A rise already in progress is allowed to hit. Retuning it would
        # swallow the accent and would also freeze the rise timer.
        self._note_climb(rms, dt)
        if self.section in {"groove", "break"} and (
            not self._climbing(rms)
            and (
                self._is_gain(rms, onset_norm, den_rel)
                or self._held_new_level(rms, onset_norm, den_rel)
            )
        ):
            self._adopt(rms, attack, dt, retune=True, hearing=hearing)
            self._hold_hearing(dt)
            self._hold_extra(hearing, dt)
            self._log("gain", "level")
            self._ride_loudness(rms, dt)
            return self.section
        self._prev_rms = rms
        self._context = _ema(self._context, rms, dt, CONTEXT_TAU_S)
        self._onset_context = _ema(self._onset_context, attack, dt, CONTEXT_TAU_S)
        self._warm += dt
        rel = self._fast / max(self._context, NOISE_RMS)
        onset_rel = self._onset_ratio(attack)
        self._remember_activity(rel, onset_rel, dt)
        if rel > 0.8:
            self._loud_at = self._clock
        self.level = self._brightness(rel, onset_rel)
        self._hold_hearing(dt)
        self._hold_extra(hearing, dt)
        self._ride_loudness(rms, dt)
        self.confidence = self._confidence(rel)
        self._section_age += dt
        self._cooldown = max(0.0, self._cooldown - dt)
        self._tex_cool = max(0.0, self._tex_cool - dt)
        if self._drop_left > 0.0:
            self._hold_drop(dt)
            if self._drop_left <= 0.0:
                self._enter("groove", force=True, keep_pattern=True)
            return self.section
        self.drop_age = 0.0
        if self._musical_rise:
            if self._rise_from is None:
                self._rise_from = self._clock
            self._rise += dt
            self._noted_level = False
        else:
            self._rise = max(0.0, self._rise - dt)
            if self._rise <= 0.0:
                self._rise_from = None
            if (
                self._level_change
                and not self._noted_level
                and self.section not in {"riser", "drop"}
            ):
                self._log("reject", "level")
                self._noted_level = True
        if self._activity_drop:
            if self._break_from is None:
                self._break_from = self._clock
            self._break_hold += dt
        else:
            self._break_hold = max(0.0, self._break_hold - dt)
            if self._break_hold <= 0.0:
                self._break_from = None
        heard_hit = self._heard_attack(hearing, dt)
        if self.section == "riser":
            return self._while_riser(dt, onset_norm, onset_rel, rel, heard_hit)
        if self.section == "break":
            return self._while_break()
        return self._while_groove(rel, dt)

    def observe(self, now: float, beat_phase: float, bar_phase: float) -> None:
        """Move the drawn look. Pattern identity changes on a phrase, not a frame."""
        if self.extra_pulse and self.extra_attack_at is not None and self._beat_period > 0.0:
            age = float(now) - float(self.extra_attack_at)
            if age < -0.05:
                self.extra_phase = None
            else:
                # Age is measured from the attack's own clock, so it grows
                # between analyses. Subtracting the current beat phase here
                # would cancel that growth and hold one brightness.
                self.extra_phase = (float(beat_phase) - age / self._beat_period) % 1.0
        else:
            self.extra_phase = None
        moment = float(now)
        bar = float(bar_phase) % 1.0
        if self._seen is None:
            self._seen = moment
            self._bar = bar
            return
        dt = max(0.0, min(0.25, moment - self._seen))
        self._seen = moment
        if self._bar is not None and bar < self._bar - 0.5 and self.section == "groove":
            self._bars += 1
            if self._bars >= PHRASE_BARS:
                self._bars = 0
                self._advance_pattern()
        self._bar = bar
        accent = self.section == "drop" and self.drop_age < DROP_ACCENT_S
        if accent:
            self.shown = 1.0
            return
        self.mix = min(1.0, self.mix + dt / PATTERN_GLIDE_S)
        glide = min(1.0, dt / LEVEL_GLIDE_S)
        self.shown += (self.level - self.shown) * glide

    def _adopt(
        self,
        rms: float,
        attack: float,
        dt: float,
        *,
        retune: bool = False,
        hearing: SliceHearing | None = None,
    ) -> None:
        self._fast = rms
        self._context = rms
        self._onset_fast = attack
        self._onset_context = max(attack, 1e-6)
        self._prev_rms = rms
        self._warm = max(self._warm, dt)
        self._section_age += dt
        if hearing is not None:
            self._den_fast = hearing.density
            self._den_ctx = hearing.density
            self._low_fast = hearing.low
            self._low_ctx = hearing.low
            self._high_fast = hearing.high
            self._high_ctx = hearing.high
            self._low_d_fast = hearing.low_density
            self._low_d_ctx = hearing.low_density
            self._high_d_fast = hearing.high_density
            self._high_d_ctx = hearing.high_density
            self._tex_fast = self._texture_of(hearing.low_density, hearing.high_density)
            self._tex_ctx = self._tex_fast
        neutral = _activity_of(1.0, 1.0)
        self._activity = neutral
        self._activity_slow = neutral
        self._slope = 0.0
        self._musical_rise = False
        self._activity_drop = False
        self._level_change = False
        self._break_hold = 0.0
        self.level = 0.7 if not retune else self.level
        if retune:
            self.confidence = min(self.confidence, 0.35)
            self._rise = 0.0
        else:
            self.confidence = 0.2

    def _smooth_hearing(
        self,
        density: float,
        low: float,
        high: float,
        low_density: float,
        high_density: float,
        dt: float,
    ) -> None:
        self._den_fast = _ema(self._den_fast, density, dt, DENSITY_TAU_S)
        self._den_ctx = _ema(self._den_ctx, density, dt, DENSITY_CTX_S)
        self._low_fast = _ema(self._low_fast, low, dt, BAND_TAU_S)
        self._low_ctx = _ema(self._low_ctx, low, dt, CONTEXT_TAU_S)
        self._high_fast = _ema(self._high_fast, high, dt, BAND_TAU_S)
        self._high_ctx = _ema(self._high_ctx, high, dt, CONTEXT_TAU_S)
        self._low_d_fast = _ema(self._low_d_fast, low_density, dt, DENSITY_TAU_S)
        self._low_d_ctx = _ema(self._low_d_ctx, low_density, dt, DENSITY_CTX_S)
        self._high_d_fast = _ema(self._high_d_fast, high_density, dt, DENSITY_TAU_S)
        self._high_d_ctx = _ema(self._high_d_ctx, high_density, dt, DENSITY_CTX_S)
        tex = self._texture_of(self._low_d_fast, self._high_d_fast)
        self._tex_fast = _ema(self._tex_fast, tex, dt, BAND_TAU_S)
        self._tex_ctx = _ema(self._tex_ctx, tex, dt, CONTEXT_TAU_S)

    def _texture_of(self, low_density: float, high_density: float) -> float:
        total = max(0.15, float(low_density) + float(high_density))
        return float(low_density) / total

    def _climbing(self, rms: float) -> bool:
        """High band and level both leaving the recent context. A pad fader is not this."""
        if not self._have_hearing or self._den_fast < 3.0 or self._high_ctx <= 1e-3:
            return False
        high_up = (
            self._high_fast >= self._high_ctx * 1.35
            and self._high_fast >= self._high_ctx + 0.05
        )
        energy_up = rms >= max(self._context, NOISE_RMS) * 1.12
        return high_up and energy_up

    def _note_climb(self, rms: float, dt: float) -> None:
        if self._cooldown > 0.0:
            self._climb_for = 0.0
            self._hat_for = 0.0
            return
        if self._climbing(rms):
            self._climb_for += dt
        else:
            self._climb_for = max(0.0, self._climb_for - dt)

    def _is_gain(self, rms: float, onset_norm: float, den_rel: float) -> bool:
        loud = (
            self._prev_rms > NOISE_RMS
            and rms > self._prev_rms * GAIN_RATIO
            and onset_norm < GAIN_ONSET_MAX
        )
        if not loud:
            return False
        if not self._have_hearing:
            return True
        return abs(den_rel - 1.0) <= 0.45

    def _held_new_level(self, rms: float, onset_norm: float, den_rel: float) -> bool:
        """A fader that stays up. A build keeps climbing, so it is not this."""
        if self._prev_rms <= NOISE_RMS or self._context <= NOISE_RMS:
            return False
        ratio = rms / self._prev_rms
        settled = 0.92 <= ratio <= 1.08
        held = settled and rms > self._context * GAIN_RATIO and onset_norm < DROP_ONSET
        if not held:
            return False
        if not self._have_hearing:
            return True
        return abs(den_rel - 1.0) <= 0.45

    def _onset_ratio(self, attack: float) -> float:
        floor = 1e-4
        if max(self._onset_context, attack, self._onset_fast) < floor:
            return 1.0
        if self._onset_context < floor:
            return 3.0
        return self._onset_fast / self._onset_context

    def _remember_activity(self, rel: float, onset_rel: float, dt: float) -> None:
        activity = _activity_of(rel, onset_rel)
        self._activity_slow = _ema(self._activity_slow, activity, dt, 0.8)
        self._activity = activity
        if not self._have_hearing:
            # Loudness slope is not a musical rise. Without attacks it is a fader.
            self._slope = (activity - self._activity_slow) / 0.8
            self._musical_rise = False
            self._activity_drop = activity < QUIET_ACTIVITY and rel < 0.7 and self._warm > 1.5
            self._level_change = self._slope > RISE_SLOPE and rel > 0.75
            return
        den_ctx = max(self._den_ctx, 0.45)
        low_ctx = max(self._low_d_ctx, 0.25)
        den_rel = self._den_fast / den_ctx
        low_rel = self._low_d_fast / low_ctx
        extra = self._den_fast - self._den_ctx
        high_up = self._high_d_fast >= self._high_d_ctx * 1.18 + 0.35
        low_ok = self._low_d_ctx < 0.5 or low_rel >= 0.55
        self._slope = extra / 0.8
        spike = (
            self._hear == "rhythmic"
            and self._den_fast >= MIN_RISE_DENSITY
            and den_rel >= RISE_DENSITY_RATIO
            and extra >= RISE_DENSITY_EXTRA
            and high_up
            and (low_ok or self._climb_for >= 1.2)
        )
        # A noise bed hides hats from the rhythmic label. A high band and the
        # level that keep climbing for more than a second are still a roll.
        # A plain groove can sit above its high-band average; only a strong,
        # unbroken acceleration opens a rise on attacks alone.
        sustained = self._climb_for >= 2.0 and self._den_fast >= 3.0
        rising = (
            self._cooldown <= 0.0
            and self._warm >= RISE_WARM_S
            and (spike or sustained)
        )
        if rising and not self._musical_rise and self._rise < 0.2:
            self.drive_id += 1
        self._musical_rise = rising
        had_events = self._den_ctx >= 1.0
        collapsed = den_rel < 0.45
        kick_gone = (
            self._low_d_ctx >= 0.6
            and low_rel < BREAK_LOW_RATIO
            and not sustained
        )
        self._activity_drop = (not self._musical_rise) and (
            (had_events and collapsed) or kick_gone
        )
        self._level_change = (
            rel > 1.18
            and abs(den_rel - 1.0) <= DENSITY_STABLE
            and extra < RISE_DENSITY_EXTRA
        )
        self._activity = min(1.0, 0.5 * min(1.4, den_rel) + 0.5 * min(1.4, low_rel))

    def begin_loudness_anchor(self) -> None:
        """The ceiling is this input's normal level, not the previous EMA."""
        self._anchor = True
        self._loud_ref = None
        self._loud_run = []
        self._loud_held = 0.0
        self._fast = 0.0
        self._dark_for = 0.0
        self.level = 1.0
        self.shown = 1.0

    def _ride_loudness(self, rms: float, dt: float) -> None:
        if not self._anchor:
            return
        if self.input_lost or self._silent >= NEAR_SILENCE_S:
            self.level = 0.0
            self.shown = 0.0
            self._dark_for = 0.0
            return
        if self._loud_ref is None:
            # Stay at the operator ceiling until the passage is more than a spike.
            if rms >= max(NOISE_RMS * 3.0, self._noise_floor * 3.0):
                self._loud_run.append(float(rms))
                self._loud_held += dt
                if self._loud_held >= 0.5 and len(self._loud_run) >= 3:
                    ordered = sorted(self._loud_run)
                    body = ordered[:-1]
                    self._loud_ref = sum(body) / len(body)
            self.level = 1.0
            self.shown = 1.0
            return
        self.level = max(0.0, min(1.0, self._fast / self._loud_ref))
        # Audible but very quiet. The lift is only this rider. Blackout,
        # intensity 0, disabled groups, an empty catalogue, and no input
        # are applied by the caller and are not restored here.
        if self.level <= 0.05 and not self._is_near_silence(rms):
            self._dark_for += dt
            if self._dark_for >= 5.0:
                self.level = 0.4
        else:
            self._dark_for = 0.0
        glide = 1.0 if dt <= 0.0 else min(1.0, dt / LEVEL_GLIDE_S)
        self.shown += (self.level - self.shown) * glide

    def _observe_floor(self, rms: float, dt: float) -> None:
        """The quiet baseline of this input. It does not climb with the music."""
        if rms > NOISE_RMS * 3.0:
            self._floor_hold = 0.0
            return
        if abs(rms - self._floor_sample) <= max(0.002, self._floor_sample * 0.35):
            self._floor_hold += dt
        else:
            self._floor_hold = 0.0
            self._floor_sample = rms
        if rms < self._noise_floor:
            self._noise_floor = max(NOISE_RMS * 0.5, rms) if rms > 0.0 else NOISE_RMS * 0.5
        elif (
            self._loud_ref is None
            and self._floor_hold >= 0.4
            and rms <= NOISE_RMS * 3.0
        ):
            self._noise_floor = max(NOISE_RMS, min(NOISE_RMS * 3.0, rms))

    def _is_near_silence(self, rms: float) -> bool:
        ceiling = max(NOISE_RMS, self._noise_floor * 1.2)
        return rms <= ceiling and rms < NOISE_RMS * 3.0

    def _pause_for_silence(self) -> None:
        """The music went quiet. The cable is still there."""
        started = self._clock - self._silent
        self.input_lost = False
        self.level = 0.0
        self.shown = 0.0
        self._rise = 0.0
        self._rise_from = None
        self._musical_rise = False
        self._climb_for = 0.0
        self._hat_for = 0.0
        self._flat = 0.0
        if self.section != "break":
            self._enter("break", force=True)
        self._log("break", "silence", sound_s=started)

    def _hold_hearing(self, dt: float) -> None:
        """One slice does not flip rhythmic and melodic. A fader does not either."""
        unit = self._onset_fast / max(self._fast * self._fast, 1e-8)
        percussive = unit >= GROOVE_REL or (self._den_fast >= 4.0 and unit >= 3.0)
        credit = self._gap_credit
        self._gap_credit = 0.0
        raw = "rhythmic" if percussive else "melodic"
        if raw == self._hear:
            self._hear_for = 0.0
            return
        # The empty hop between two clicks belongs to the same pulse.
        self._hear_for += dt + (min(credit, 0.5) if percussive else 0.0)
        if self._hear_for >= 1.25:
            self._hear = raw
            self._hear_for = 0.0

    def _hold_extra(self, hearing: SliceHearing | None, dt: float) -> None:
        """A repeating high-band layer. Not a second tempo reading."""
        found = False
        fresh: float | None = None
        if hearing is not None and self._tempo_known:
            if (
                hearing.generation is not None
                and self.extra_generation is not None
                and int(hearing.generation) < int(self.extra_generation)
            ):
                found = False
            else:
                found = extra_layer(hearing.low_attacks, hearing.high_attacks)
                fresh = self._fresh_extra_attack(hearing, dt)
        if found:
            self._extra_for += dt
            if fresh is not None:
                self.extra_attack_at = fresh
                if hearing is not None and hearing.generation is not None:
                    self.extra_generation = int(hearing.generation)
            if self._extra_for >= 0.5 and self.extra_attack_at is not None:
                self.extra_pulse = True
        else:
            self._extra_for = max(0.0, self._extra_for - dt)
            if self._extra_for <= 0.0:
                self.extra_pulse = False
                self.extra_phase = None

    def _fresh_extra_attack(self, hearing: SliceHearing, dt: float) -> float | None:
        """Absolute time of a new high-band hit. Same generation, new time: accept.

        ``generation`` is the audio session, not the event. A second delivery of
        the same attack time is ignored. A late packet does not become "now".
        """
        offset = _extra_attack_offset(hearing.low_attacks, hearing.high_attacks)
        if offset is None:
            return None
        if hearing.generation is not None and (
            self.extra_generation is None or int(hearing.generation) > int(self.extra_generation)
        ):
            self._extra_seen.clear()
            self.extra_generation = int(hearing.generation)
        if self._slice_start is not None:
            start = float(self._slice_start)
        elif hearing.origin_s is not None and hearing.origin_clock != "monotonic":
            start = float(hearing.origin_s)
        else:
            start = self._wall - float(dt)
        when = start + float(offset)
        age = self._wall - when
        if age < -0.02 or age > max(0.35, float(dt) + 0.02):
            return None
        generation = None if hearing.generation is None else int(hearing.generation)
        ident = (generation, round(when, 4))
        if ident in self._extra_seen:
            return None
        self._extra_seen.append(ident)
        del self._extra_seen[:-32]
        return when

    def _brightness(self, rel: float, onset_rel: float) -> float:
        bright = min(1.0, 0.45 + 0.55 * min(1.0, rel))
        if onset_rel < 0.35 and self._warm > 1.5:
            bright *= 0.75
        return max(0.0, min(1.0, bright))

    def _confidence(self, rel: float) -> float:
        stable = math.exp(-abs(math.log(max(min(rel, 4.0), 0.05))))
        return max(0.0, min(1.0, stable * min(1.0, self._warm / 2.0)))

    def _hold_drop(self, dt: float) -> None:
        if self._drop_left <= 0.0:
            return
        self._drop_left = max(0.0, self._drop_left - dt)
        self.drop_age += dt
        self.riser_age = 0.0
        self.section = "drop"

    def _heard_attack(self, hearing: SliceHearing | None, dt: float) -> float | None:
        """Latest attack already inside this window. Not a time we have not heard."""
        if hearing is None or not hearing.attacks:
            return None
        start = self._clock - dt
        offsets = [
            float(item)
            for item in hearing.attacks
            if 0.0 <= float(item) <= dt + 1e-3
        ]
        if not offsets:
            return None
        return start + max(offsets)

    def _while_riser(
        self,
        dt: float,
        onset_norm: float,
        onset_rel: float,
        rel: float,
        heard: float | None,
    ) -> str:
        if self._musical_rise:
            self.riser_age = 0.0
        else:
            self.riser_age += dt
        if self.riser_age >= RISER_LIMIT_S:
            self._enter("groove", force=True)
            self._log("groove", "riser-limit")
            return self.section
        if self._is_hit(onset_norm, onset_rel, rel):
            self._begin_drop(heard if heard is not None else self._clock - dt)
            return self.section
        if self._musical_rise or self._slope > 0.0:
            self._flat = 0.0
        else:
            self._flat += dt
        # The build stopped and nothing hit. That is not a drop, and it is
        # not a rise that keeps going.
        if self._flat >= 0.6 and self._can_leave():
            self._enter("groove", force=True)
            self._log("groove", "rise-stopped", sound_s=self._clock - self._flat)
        return self.section

    def _while_break(self) -> str:
        self.riser_age = 0.0
        hold = self._rise_hold_s()
        if self._rise >= hold and self._can_leave():
            self._enter("riser")
            self._log("riser", "density", sound_s=self._rise_from)
        elif self._activity > GROOVE_ACTIVITY and self._can_leave() and self._rise <= 0.0:
            self._enter("groove")
            self._log("groove", "return")
        return self.section

    def _while_groove(self, rel: float, dt: float) -> str:
        self.riser_age = 0.0
        if self._is_hit_without_rise(rel):
            self._log("reject", "hit")
        if self._rise >= self._rise_hold_s() and self._can_leave():
            self._enter("riser")
            self._log("riser", "density", sound_s=self._rise_from)
            return self.section
        if self._activity_drop and self._break_hold >= self._break_hold_s() and self._can_leave():
            self._enter("break")
            # A slow fader is not a cut. The last loud moment must still be close.
            loud_at = getattr(self, "_loud_at", None)
            if loud_at is not None and self._clock - float(loud_at) <= self._break_hold_s() + 0.45:
                self._emit_event("pause")
            self._log("break", "activity", sound_s=self._break_from)
            return self.section
        if not self._have_hearing:
            quiet = self._activity < QUIET_ACTIVITY and rel < 0.7 and self._warm > 1.5
            if quiet and self._can_leave():
                self._enter("break")
                self._log("break", "quiet")
            return self.section
        self._maybe_glide_texture(dt)
        return self.section

    def _is_hit_without_rise(self, rel: float) -> bool:
        if self._rise > 0.15 or self._musical_rise:
            return False
        return rel >= 1.25 and self._onset_fast > max(self._onset_context * 1.6, 1e-4)

    def _rise_hold_s(self) -> float:
        hold = max(RISE_HOLD_S, 0.45 * self._bar_s())
        # Three quarters of a second of a real high-band acceleration is a roll.
        # A half-second density bump in a groove is not.
        fast = self._high_d_fast >= self._high_d_ctx * 1.18 + 0.35
        if self._rise >= 0.75 and fast:
            return min(hold, 0.75)
        return hold

    def _break_hold_s(self) -> float:
        return max(BREAK_HOLD_S, 0.55 * self._bar_s())

    def _bar_s(self) -> float:
        """Follower beat, or 2 s. This is not a second tempo reading."""
        return 4.0 * max(0.25, float(self._beat_period))

    def _maybe_glide_texture(self, dt: float) -> None:
        if self._tex_cool > 0.0 or self._musical_rise or self._activity_drop:
            self._tex_hold = 0.0
            return
        den_rel = self._den_fast / max(self._den_ctx, 0.45)
        if abs(den_rel - 1.0) > DENSITY_STABLE:
            self._tex_hold = 0.0
            return
        shift = abs(self._tex_fast - self._tex_ctx)
        if shift < TEXTURE_DELTA:
            self._tex_hold = max(0.0, self._tex_hold - dt)
            return
        self._tex_hold += dt
        if self._tex_hold >= max(TEXTURE_HOLD_S, self._bar_s()) and self._can_leave():
            self._advance_pattern()
            self._tex_hold = 0.0
            self._tex_cool = TEXTURE_COOLDOWN_S
            self.texture_id += 1
            self._emit_event("texture")
            self._log("texture", "theme")

    def _is_hit(self, onset_norm: float, onset_rel: float, rel: float) -> bool:
        return (
            self._cooldown <= 0.0
            and onset_norm >= DROP_ONSET
            and onset_rel >= DROP_ONSET_REL
            and rel >= 1.05
        )

    def _begin_drop(self, heard: float | None = None) -> None:
        self.section = "drop"
        self._section_age = 0.0
        self._drop_left = DROP_HOLD_S
        self._climb_for = 0.0
        self._hat_for = 0.0
        self.drop_age = 0.0
        self.riser_age = 0.0
        self._rise = 0.0
        self._rise_from = None
        self._cooldown = DROP_COOLDOWN_S
        self._advance_pattern()
        self._emit_event("drop")
        self._log("drop", "hit", sound_s=heard)

    def _emit_event(self, kind: str) -> None:
        self._event_seq += 1
        self._events.append(StructuralEvent(kind, self._event_seq, self._clock))
        if len(self._events) > 16:
            del self._events[:-16]

    def _log(self, kind: str, detail: str, *, sound_s: float | None = None) -> None:
        decision = round(self._clock, 4)
        sound = decision if sound_s is None else min(decision, round(float(sound_s), 4))
        last = self.journal[-1] if self.journal else None
        if last is not None and last[1] == kind and last[2] == detail:
            return
        self.journal.append((decision, kind, detail))
        self.reasons.append((sound, decision, kind, detail))
        if len(self.journal) > 48:
            del self.journal[:-48]
        if len(self.reasons) > 48:
            del self.reasons[:-48]

    def _enter(self, name: str, *, force: bool = False, keep_pattern: bool = False) -> None:
        if name == self.section:
            return
        if not force and not self._can_leave():
            return
        previous = self.section
        self.section = name
        self._section_age = 0.0
        self.riser_age = 0.0
        if name == "riser":
            self._flat = 0.0
        if name != "drop":
            self._drop_left = 0.0
            self.drop_age = 0.0
        if previous == "break" and name == "groove":
            self._emit_event("restore")
        if not keep_pattern:
            self._advance_pattern()
        else:
            self._choose_cycle()

    def _can_leave(self) -> bool:
        return self._section_age >= MIN_SECTION_S

    def _advance_pattern(self) -> None:
        self.pattern = (self.pattern + 1) % len(LOOKS)
        self.mix = 0.0
        self._choose_cycle()

    def _choose_cycle(self) -> None:
        """One period until the next phrase or structural step. Not every frame."""
        if self.section == "break":
            self.cycle_beats = 8
        elif self.section == "riser":
            self.cycle_beats = 2
        elif self.section == "drop":
            self.cycle_beats = 1
        else:
            self.cycle_beats = (4, 2, 8, 1)[self.pattern % 4]


def window_energy(samples: list[float]) -> float:
    block = np.asarray(samples, dtype=np.float64).reshape(-1)
    block = block[np.isfinite(block)]
    if block.size == 0:
        return 0.0
    mean = float(np.mean(block * block))
    return max(0.0, min(1.0, (mean**0.5) / 0.12))


def _blend_level(old: float, new: float, mix: float) -> float:
    return (1.0 - mix) * float(old) + mix * float(new)


def _crossfade_stages(old: StageIntent, new: StageIntent, mix: float) -> StageIntent:
    """Frame blend. Heads stay on or off. Axes stay where the winning side left them."""
    weight = max(0.0, min(1.0, float(mix)))
    painted: dict[str, object] = {}
    for ident in list(dict.fromkeys([*old.fixtures, *new.fixtures])):
        left = old.fixtures.get(ident)
        right = new.fixtures.get(ident)
        blended = _blend_fixture(left, right, weight)
        if blended is not None:
            painted[ident] = blended
    return StageIntent(fixtures=painted)  # type: ignore[arg-type]


def _blend_fixture(old: object, new: object, mix: float) -> object | None:
    if isinstance(old, ParIntent) or isinstance(new, ParIntent):
        left = old if isinstance(old, ParIntent) else None
        right = new if isinstance(new, ParIntent) else None
        color = (right or left).color if (right or left) is not None else None
        if color is None:
            return None
        return ParIntent(
            color=color,
            intensity=_blend_level(0.0 if left is None else left.intensity, 0.0 if right is None else right.intensity, mix),
            strobe=0.0,
        )
    if isinstance(old, BarIntent) or isinstance(new, BarIntent):
        left = old if isinstance(old, BarIntent) else None
        right = new if isinstance(new, BarIntent) else None
        source = right or left
        if source is None:
            return None
        left_segments = (0.0,) * 8 if left is None else tuple(left.segments)
        right_segments = (0.0,) * 8 if right is None else tuple(right.segments)
        width = max(len(left_segments), len(right_segments), 1)
        segments = tuple(
            _blend_level(
                left_segments[index] if index < len(left_segments) else 0.0,
                right_segments[index] if index < len(right_segments) else 0.0,
                mix,
            )
            for index in range(width)
        )
        return BarIntent(
            segments=segments,
            dimmer=_blend_level(0.0 if left is None else left.dimmer, 0.0 if right is None else right.dimmer, mix),
            color=source.color,
            strobe=0.0,
            whole=bool(getattr(source, "whole", False)),
        )
    if isinstance(old, BeamIntent) or isinstance(new, BeamIntent):
        left = old if isinstance(old, BeamIntent) else None
        right = new if isinstance(new, BeamIntent) else None
        source = right if mix >= 0.5 and right is not None else (left or right)
        if source is None:
            return None
        weighted = _blend_level(0.0 if left is None else left.dimmer, 0.0 if right is None else right.dimmer, mix)
        winner = right if mix >= 0.5 else left
        other = left if winner is right else right
        pan = None if winner is None else winner.pan
        tilt = None if winner is None else winner.tilt
        if pan is None and other is not None:
            pan = other.pan
        if tilt is None and other is not None:
            tilt = other.tilt
        return BeamIntent(
            pan=pan,
            tilt=tilt,
            dimmer=1.0 if weighted >= 0.5 else 0.0,
            color=source.color,
            strobe=0.0,
        )
    return new if mix >= 0.5 else old


def build_auto_stage(
    fixtures: list[LitFixture],
    palette: str,
    section: str,
    beat_phase: float,
    level: float,
    drop_age: float = 0.0,
    *,
    riser_age: float = 0.0,
    pattern: int = 0,
    mix: float = 1.0,
    beats: float | None = None,
    cycle_beats: float = 1,
    placements: object = None,
    look: str | None = None,
    motion_cycle: float | None = None,
    strobe_hold: float | None = None,
    groups: tuple[str, ...] | None = None,
    actions: tuple[object, ...] | None = None,
    flash_hold: float | None = None,
    colour: str | None = None,
    cast: str | None = None,
    mix_from: str | None = None,
) -> StageIntent:
    """One frame. Face lights are left out. Heads do not move or strobe.

    ``beats`` is the action coordinate (planning beats plus the heard-beat
    offset). A cycle of 2 or 4 does not restart on every beat, and a 0.08
    pull does not fold that cycle onto the fractional beat. Beam brightness
    is on or off: the renderer has no smooth beam dimmer. Bars share one
    canvas; left/right mirroring is a composition choice, not a law.
    ``mix`` is the weight of ``look``. ``mix_from`` is the picture leaving.
    """
    origin = None if mix_from is None else str(mix_from)
    weight = max(0.0, min(1.0, float(mix)))
    if origin and origin != (look or "") and weight < 0.999:
        departed = build_auto_stage(
            fixtures,
            palette,
            section,
            beat_phase,
            level,
            drop_age,
            riser_age=riser_age,
            pattern=pattern,
            mix=1.0,
            beats=beats,
            cycle_beats=cycle_beats,
            placements=placements,
            look=origin,
            motion_cycle=motion_cycle,
            strobe_hold=strobe_hold,
            groups=groups,
            actions=None,
            flash_hold=None,
            colour=colour,
            cast=cast,
            mix_from=None,
        )
        arrived = build_auto_stage(
            fixtures,
            palette,
            section,
            beat_phase,
            level,
            drop_age,
            riser_age=riser_age,
            pattern=pattern,
            mix=1.0,
            beats=beats,
            cycle_beats=cycle_beats,
            placements=placements,
            look=look,
            motion_cycle=motion_cycle,
            strobe_hold=strobe_hold,
            groups=groups,
            actions=actions,
            flash_hold=flash_hold,
            colour=colour,
            cast=cast,
            mix_from=None,
        )
        return _crossfade_stages(departed, arrived, weight)
    cast_name = cast_of(palette, cast)
    colour_name = colour_of(palette, colour)
    name = colour_name
    moment = section if section in SECTIONS else "groove"
    color = rgbw_of(name)
    accent = moment == "drop" and drop_age < DROP_ACCENT_S
    planned = look in LOOKS
    look_accent = accent and (not planned or look == "accent")
    active_pattern = LOOKS.index(look) if look in LOOKS else int(pattern)
    composition = resolve_composition(
        look if planned else ("accent" if look_accent else look_name(active_pattern)),
        active_pattern,
        name,
        list(fixtures),
    )
    if not planned and (moment == "break" or look_accent) and "wash" in composition.palettes:
        if moment == "break" and not look_accent:
            composition = resolve_composition("wash", 0, name, list(fixtures))
    cast_kinds = frozenset(
        _GROUP_KIND[item] for item in groups_of(cast_name) if item in _GROUP_KIND
    )
    if groups is not None:
        wanted = {_GROUP_KIND[item] for item in groups if item in _GROUP_KIND}
        allowed = frozenset(kind for kind in cast_kinds if kind in wanted)
    else:
        allowed = _active_kinds(cast_name, composition, moment, fixtures)
    cap = _INTENSITY_CAP.get(name, 1.0)
    if cast_name == "heads":
        cap = min(cap, 0.8)
    amount = max(0.0, min(cap, float(level)))
    cycle = _known_cycle(cycle_beats) or 1.0
    if beats is None:
        phase = float(beat_phase) % 1.0
    else:
        phase = _action_phase(float(beats), cycle, 0.0)
    if look_accent:
        phase = 0.0
    canvas = build_canvas(list(fixtures), placements)
    layers = list(actions or ())
    if not layers:
        layers = [
            type(
                "A",
                (),
                {
                    "role": "picture",
                    "look": composition.id,
                    "cycle_beats": cycle,
                    "groups": None,
                    "origin_beats": 0.0,
                },
            )()
        ]
    motion_gain = 1.0
    motion_span = _known_cycle(motion_cycle)
    if motion_span is not None and beats is not None and planned and not accent:
        motion_origin = 0.0
        for action in layers:
            if str(getattr(action, "role", "")) == "motion":
                motion_origin = float(getattr(action, "origin_beats", 0.0) or 0.0)
                break
        motion_phase = _action_phase(float(beats), motion_span, motion_origin)
        motion_gain = 0.82 + 0.18 * (1.0 - motion_phase)
    stage = StageIntent()
    for action in layers:
        action_look = str(getattr(action, "look", composition.id))
        action_groups = getattr(action, "groups", None)
        action_cycle = _known_cycle(getattr(action, "cycle_beats", cycle)) or cycle
        part = resolve_composition(action_look, 0, name, list(fixtures))
        if action_groups is not None:
            wanted = {_GROUP_KIND[item] for item in action_groups if item in _GROUP_KIND}
            layer_allowed = frozenset(kind for kind in allowed if kind in wanted)
        else:
            layer_allowed = allowed
        if beats is None:
            layer_phase = phase
        else:
            origin = float(getattr(action, "origin_beats", 0.0) or 0.0)
            layer_phase = _action_phase(float(beats), action_cycle, origin)
        painted = paint(
            part,
            canvas,
            phase=layer_phase,
            amount=amount * motion_gain,
            cap=cap,
            color=color,
            strobe=0.0,
            palette=colour_name,
        )
        for fixture_id, intent in painted.items():
            if isinstance(intent, ParIntent) and FixtureKind.PAR not in layer_allowed:
                continue
            if isinstance(intent, BarIntent) and FixtureKind.BAR not in layer_allowed:
                continue
            if isinstance(intent, BeamIntent):
                if FixtureKind.BEAM not in layer_allowed:
                    continue
                if moment == "break":
                    continue
            previous = stage.fixtures.get(fixture_id)
            if previous is None:
                stage.fixtures[fixture_id] = intent
                continue
            stage.fixtures[fixture_id] = _merge_intent(previous, intent)
    flash = flash_hold
    if flash is None and strobe_hold is not None and float(strobe_hold) > 0.2:
        flash = 1.0
    head_only = set(composition.groups) == {"beam"}
    if flash is not None and not head_only:
        flash_kinds = frozenset(
            kind for kind in (FixtureKind.PAR, FixtureKind.BAR) if kind in cast_kinds
        )
        _apply_auto_flash(
            stage,
            fixtures,
            flash_kinds or allowed,
            color,
            amount,
            on=float(flash) >= 0.5 and amount > 1e-9,
        )
    if composition.roles == "head_pulse" and flash is not None:
        lit = float(flash) >= 0.5 and amount > 1e-9
        for fixture_id, intent in list(stage.fixtures.items()):
            if isinstance(intent, BeamIntent):
                stage.fixtures[fixture_id] = replace(
                    intent, dimmer=1.0 if lit else 0.0, strobe=0.0
                )
    return stage


def _atmosphere_pars(
    fixtures: list[object] | None,
    groups: tuple[str, ...] | list[str] | None,
) -> list[object]:
    """PARs the operator left on. Face and a bars-only cast stay out."""
    allowed = {"par"} if groups is None else set(groups)
    if "par" not in allowed or not fixtures:
        return []
    found = []
    for fixture in fixtures:
        kind = getattr(fixture, "kind", None)
        if kind is FixtureKind.FACE_PAR or kind is not FixtureKind.PAR:
            continue
        spatial = getattr(fixture, "spatial", None)
        if bool(getattr(spatial, "face", False)):
            continue
        found.append(fixture)
    return found


def _paint_par_layer(
    stage: StageIntent,
    fixtures: list[object] | None,
    level: float,
    color: Rgbw | None,
    groups: tuple[str, ...] | list[str] | None,
    *,
    own_par: bool,
) -> StageIntent:
    """Fill allowed PARs. A PAR the picture already painted is left alone."""
    painted = dict(stage.fixtures)
    tint = color or Rgbw(r=1.0, g=0.35, b=0.05, w=0.0)
    targets = _atmosphere_pars(fixtures, groups)
    if targets:
        for fixture in targets:
            current = painted.get(fixture.id)
            if own_par and isinstance(current, ParIntent):
                continue
            if isinstance(current, ParIntent):
                tint = current.color
            painted[fixture.id] = ParIntent(color=tint, intensity=level, strobe=0.0)
        return StageIntent(fixtures=painted)
    for fixture_id, intent in list(painted.items()):
        if own_par or not isinstance(intent, ParIntent):
            continue
        painted[fixture_id] = replace(intent, intensity=level, strobe=0.0)
    return StageIntent(fixtures=painted)


def lay_par_pulse(
    stage: StageIntent,
    beat_phase: float,
    amount: float,
    fixtures: list[object] | None = None,
    *,
    color: Rgbw | None = None,
    groups: tuple[str, ...] | list[str] | None = None,
    own_par: bool = False,
    attack_phase: float | None = None,
) -> StageIntent:
    """PAR answer on the confirmed extra attack. Not a fixed off-beat and not a second BPM."""
    if float(amount) <= 0.0 or attack_phase is None:
        return stage
    local = (float(beat_phase) - float(attack_phase)) % 1.0
    level = max(0.0, min(float(amount), 0.12 + 0.7 * math.exp(-local * 6.0)))
    return _paint_par_layer(stage, fixtures, level, color, groups, own_par=own_par)


def lay_par_bed(
    stage: StageIntent,
    wall_s: float,
    amount: float,
    fixtures: list[object] | None = None,
    *,
    color: Rgbw | None = None,
    groups: tuple[str, ...] | list[str] | None = None,
    own_par: bool = False,
) -> StageIntent:
    """Slow PAR air. Honest bed: not a recognised second tempo."""
    if float(amount) <= 0.0:
        return stage
    phase = (float(wall_s) / 8.0) % 1.0
    breathe = 0.5 + 0.5 * math.sin(phase * math.tau)
    level = max(0.0, min(float(amount) * 0.55, 0.32 + 0.16 * breathe))
    return _paint_par_layer(stage, fixtures, level, color, groups, own_par=own_par)


def consume_accent(
    stage: StageIntent,
    fixtures: list[object],
    groups: tuple[str, ...] | list[str],
    amount: float,
    *,
    now: float,
    ready_at: float | None,
    color: Rgbw | None = None,
    head_dark: frozenset[str] | set[str] | None = None,
) -> tuple[StageIntent, bool]:
    """One short mark on a fresh extra attack. Zero amount paints nothing.

    A free PAR is preferred, then any other free fixture, then the quietest
    allowed one. Face stays out. A head the look does not own stays out.
    """
    if ready_at is None:
        return stage, False
    if float(now) - float(ready_at) > 0.12:
        return stage, True
    if float(amount) <= 0.0:
        return stage, True
    allowed = set(groups)
    forbidden = set(head_dark or ())
    kind_name = {"par": FixtureKind.PAR, "bar": FixtureKind.BAR, "beam": FixtureKind.BEAM}
    wanted = {kind_name[name] for name in allowed if name in kind_name}
    brightness = max(0.0, min(float(amount), 1.0))
    tint = color or Rgbw(r=1.0, g=0.35, b=0.05, w=0.0)
    dark: list[object] = []
    ranked: list[tuple[float, str, object]] = []
    for fixture in fixtures:
        spatial = getattr(fixture, "spatial", None)
        if bool(getattr(spatial, "face", False)):
            continue
        kind = getattr(fixture, "kind", None)
        if kind not in wanted or str(getattr(fixture, "id", "")) in forbidden:
            continue
        intent = stage.fixtures.get(fixture.id)
        if intent is None and kind is not FixtureKind.PAR:
            continue
        level = _intent_level(intent)
        ranked.append((level, str(getattr(fixture, "id", "")), fixture))
        if level < 0.08:
            dark.append(fixture)
    if not dark:
        ranked.sort()
        if not ranked or ranked[0][0] >= brightness - 1e-3:
            return stage, True
        floor = ranked[0][0]
        dark = [fixture for level, _ident, fixture in ranked if level <= floor + 1e-6]
    chosen = _prefer_free(dark)
    targets = [chosen]
    if getattr(chosen, "kind", None) is FixtureKind.BEAM:
        targets = [item for item in dark if getattr(item, "kind", None) is FixtureKind.BEAM][:2]
        if len(targets) < 2:
            return stage, True
    painted = dict(stage.fixtures)
    updates: dict[str, ParIntent | BarIntent | BeamIntent] = {}
    for fixture in targets:
        intent = painted.get(fixture.id)
        kind = getattr(fixture, "kind", None)
        if kind is FixtureKind.PAR and (isinstance(intent, ParIntent) or intent is None):
            held = intent.color if isinstance(intent, ParIntent) else tint
            updates[fixture.id] = ParIntent(color=held, intensity=brightness, strobe=0.0)
        elif kind is FixtureKind.BAR and isinstance(intent, BarIntent):
            updates[fixture.id] = BarIntent(
                segments=intent.segments,
                dimmer=brightness,
                color=intent.color,
                strobe=0.0,
                whole=True,
            )
        elif kind is FixtureKind.BEAM and isinstance(intent, BeamIntent):
            updates[fixture.id] = BeamIntent(
                pan=intent.pan,
                tilt=intent.tilt,
                dimmer=1.0,
                color=intent.color,
                strobe=0.0,
            )
    if getattr(chosen, "kind", None) is FixtureKind.BEAM and len(updates) < 2:
        return stage, True
    if not updates:
        return stage, True
    painted.update(updates)
    return StageIntent(fixtures=painted), True


def _prefer_free(dark: list[object]) -> object:
    """A free PAR, then a free bar, then whatever is quietest in patch order."""
    for kind in (FixtureKind.PAR, FixtureKind.BAR):
        found = [item for item in dark if getattr(item, "kind", None) is kind]
        if found:
            return found[0]
    return dark[0]


def mask_auto_groups(
    stage: StageIntent,
    groups: tuple[str, ...] | list[str],
    *,
    keep: set[str] | None = None,
) -> StageIntent:
    """Drop light the operator turned off. A dark head keeps its last aim."""
    allowed = set(groups)
    held = keep or set()
    painted: dict[str, object] = {}
    for fixture_id, intent in stage.fixtures.items():
        if fixture_id in held:
            painted[fixture_id] = intent
            continue
        if isinstance(intent, ParIntent):
            if "par" in allowed:
                painted[fixture_id] = intent
        elif isinstance(intent, BarIntent):
            if "bar" in allowed:
                painted[fixture_id] = intent
        elif isinstance(intent, BeamIntent):
            if "beam" in allowed:
                painted[fixture_id] = intent
            else:
                painted[fixture_id] = replace(intent, dimmer=0.0, strobe=0.0)
        else:
            painted[fixture_id] = intent
    return StageIntent(fixtures=painted)


def _keep_head_dark(
    before: StageIntent,
    after: StageIntent,
    head_dark: frozenset[str] | set[str] | None,
) -> StageIntent:
    """A dark return is a required phase. A later automatic layer must not relight it."""
    if not head_dark:
        return after
    painted = dict(after.fixtures)
    changed = False
    for ident in head_dark:
        current = painted.get(ident)
        previous = before.fixtures.get(ident)
        if not isinstance(current, BeamIntent):
            continue
        cap = float(previous.dimmer) if isinstance(previous, BeamIntent) else 0.0
        if float(current.dimmer) > cap + 1e-6:
            painted[ident] = replace(current, dimmer=cap, strobe=0.0)
            changed = True
    if not changed:
        return after
    return StageIntent(fixtures=painted)


def stack_auto_layers(
    stage: StageIntent,
    fixtures: list[LitFixture],
    *,
    amount: float,
    color: Rgbw,
    cast_groups: tuple[str, ...] | list[str],
    own_par: bool,
    par_bed: bool,
    pulse: bool,
    wall_s: float,
    beat_phase: float,
    attack_phase: float | None = None,
    accent: float,
    accent_ready: float | None,
    now: float,
    flash: float | None,
    head_dark: frozenset[str] | set[str] | None = None,
) -> tuple[StageIntent, bool]:
    """Bed, then one extra accent, then the series. The base is already painted.

    Amount 0 adds no light. The series is the last automatic layer and can
    darken a frame; operator blackout is applied later, outside this stack.
    """
    if float(amount) <= 0.0:
        return stage, False
    painted = stage
    if par_bed and "par" in set(cast_groups):
        if pulse:
            painted = lay_par_pulse(
                painted,
                beat_phase,
                amount,
                fixtures,
                color=color,
                groups=cast_groups,
                own_par=own_par,
                attack_phase=attack_phase,
            )
        else:
            painted = lay_par_bed(
                painted,
                wall_s,
                amount,
                fixtures,
                color=color,
                groups=cast_groups,
                own_par=own_par,
            )
    spent = False
    if float(accent) > 0.0:
        painted, spent = consume_accent(
            painted,
            fixtures,
            cast_groups,
            float(amount) * (0.55 + 0.45 * float(accent)),
            now=now,
            ready_at=accent_ready,
            color=color,
            head_dark=head_dark,
        )
    elif accent_ready is not None and float(now) - float(accent_ready) > 0.12:
        spent = True
    if flash is not None:
        kinds = frozenset(
            kind
            for name, kind in (
                ("par", FixtureKind.PAR),
                ("bar", FixtureKind.BAR),
            )
            if name in set(cast_groups)
        )
        _apply_auto_flash(painted, fixtures, kinds, color, float(amount), on=float(flash) >= 0.5)
    return _keep_head_dark(stage, painted, head_dark), spent


def _intent_level(intent: object) -> float:
    if isinstance(intent, ParIntent):
        return float(intent.intensity)
    if isinstance(intent, BarIntent):
        return float(intent.dimmer)
    if isinstance(intent, BeamIntent):
        return float(intent.dimmer)
    return 0.0


def _apply_auto_flash(
    stage: StageIntent,
    fixtures: list[LitFixture],
    allowed: frozenset[FixtureKind],
    color: Rgbw,
    cap: float,
    *,
    on: bool,
) -> None:
    """On/off Auto flash of the selected PAR and bars. Face and heads stay out."""
    for fixture in fixtures:
        kind = fixture.kind
        if kind is FixtureKind.FACE_PAR or kind is FixtureKind.BEAM:
            continue
        if kind not in allowed:
            continue
        ident = fixture.id
        if kind is FixtureKind.PAR:
            stage.fixtures[ident] = ParIntent(
                color=color,
                intensity=cap if on else 0.0,
                strobe=0.0,
            )
            continue
        if kind is FixtureKind.BAR:
            lit = 1.0 if on else 0.0
            stage.fixtures[ident] = BarIntent(
                segments=(lit,) * 8,
                dimmer=cap if on else 0.0,
                color=color,
                strobe=0.0,
                whole=True,
            )


def _merge_intent(
    previous: ParIntent | BarIntent | BeamIntent,
    incoming: ParIntent | BarIntent | BeamIntent,
) -> ParIntent | BarIntent | BeamIntent:
    if isinstance(previous, BarIntent) and isinstance(incoming, BarIntent):
        count = max(len(previous.segments), len(incoming.segments))
        bits = []
        for index in range(count):
            left = previous.segments[index] if index < len(previous.segments) else 0.0
            right = incoming.segments[index] if index < len(incoming.segments) else 0.0
            bits.append(max(left, right))
        return BarIntent(
            segments=tuple(bits),
            dimmer=max(previous.dimmer, incoming.dimmer),
            color=incoming.color,
            strobe=0.0,
            whole=previous.whole and incoming.whole,
        )
    if isinstance(previous, ParIntent) and isinstance(incoming, ParIntent):
        return ParIntent(
            color=incoming.color,
            intensity=max(previous.intensity, incoming.intensity),
            strobe=0.0,
        )
    if isinstance(previous, BeamIntent) and isinstance(incoming, BeamIntent):
        return BeamIntent(
            pan=None,
            tilt=None,
            dimmer=max(previous.dimmer, incoming.dimmer),
            color=incoming.color,
            strobe=0.0,
        )
    return incoming


def _known_cycle(value: float | int | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    for choice in AUTO_CYCLE_BEATS:
        if abs(number - choice) <= 1e-9:
            return choice
    return None


def _action_phase(beats: float, cycle: float, origin: float) -> float:
    span = max(1e-9, float(cycle))
    return ((float(beats) - float(origin)) / span) % 1.0


def _active_kinds(
    palette: str,
    composition: object,
    section: str,
    fixtures: list[LitFixture],
) -> frozenset[FixtureKind]:
    """Cast first, then the composition's groups. Face never enters."""
    allowed = {_GROUP_KIND[item] for item in groups_of(cast_of(palette)) if item in _GROUP_KIND}
    present = {fixture.kind for fixture in fixtures}
    wanted = getattr(composition, "groups", None)
    if wanted:
        named = {_GROUP_KIND[name] for name in wanted if name in _GROUP_KIND}
        allowed = {kind for kind in allowed if kind in named}
    if section == "break":
        for kind in (FixtureKind.BAR, FixtureKind.PAR, FixtureKind.BEAM):
            if kind in allowed and kind in present:
                return frozenset({kind})
        return frozenset(allowed)
    return frozenset(allowed)


def _segment_count(fixture: LitFixture) -> int:
    spatial = getattr(fixture, "spatial", None)
    raw = getattr(spatial, "segment_count", None)
    if raw is None:
        raw = getattr(fixture, "segment_count", None)
    try:
        count = int(raw) if raw is not None else _DEFAULT_SEGMENTS
    except (TypeError, ValueError):
        count = _DEFAULT_SEGMENTS
    return max(1, min(8, count))
