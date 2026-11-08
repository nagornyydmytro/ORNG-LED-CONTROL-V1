"""Tempo from a speaker feed, and three ways to follow it inside one preset.

Operator canon:
- Preset follows the preset the operator already chose. Tempo moves the cycles
  inside the current episode and the step into the next episode. It does not
  pick another preset.
- Manual live FX stay on their own press, drag, release and speed. They are
  not locked to this clock.
- Auto chooses patterns inside the selected palette. On the executor, keyboard
  actions other than blackout are refused.
- Blackout darkens every mode.

``smooth`` glides the episode clock with the tempo. 120 BPM is the authored ×1.
``bar`` ends the episode on a counted 4-beat lattice. That lattice is not a
detected downbeat of the song.
``beat`` keeps that episode clock. It does not replace an action's musical
cycle with the fractional beat, and it does not zero the episode on every kick.
A periodic action completes in its own musical period:
``turns = action_beats / cycle``. Planning beats only move forward with
elapsed time. The heard-beat correction is a separate, limited offset on the
action coordinate, so a deadline, a drop, or a 2/4/8-beat cycle does not
rewind. Auto may use a half-beat cycle when the look and a 30 Hz frame allow
it. Preset documents stay on 1, 2, 4 or 8. Program ×0.5–×5 is not this clock.
A confirmed half, double, 2/3 or 3/2 replaces the tempo and does not scale
the beat counter. When the release ends, the beats keep moving at the last
tempo so Auto does not freeze. That motion is not a recognised BPM, does not
keep the lock, and does not drive Preset: the manual speed owns time again.
Phase is kept when the fallback starts and when a later lock returns.
None of them leaves the running preset, syncs a held live FX, or clears blackout.

Acceptance bounds (do not loosen to match a run):
- tempo outside 70..180 is unsupported
- a reading below lock confidence is not a lock
- phase correction per analysis is at most 0.08 beat
- beat-effect slew is at most 0.12 turns per second
- holdover is 2.5 s, then a 1 s release back to the manual speed
- a bar episode lasts at least 2 beats and at most 1.35× its authored beats
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass

import numpy as np

MODES = ("smooth", "bar", "beat")
REFERENCE_BPM = 120.0
BPM_MIN = 70.0
BPM_MAX = 180.0
# Last good tempo survives a short gap. It does not survive the song ending.
TEMPO_HOLD_S = 2.5
TEMPO_RELEASE_S = 1.0
LOCK_CONFIDENCE = 0.55
# One lucky window is not a lock. The next agreeing analysis is.
LOCK_AGREE = 2
PHASE_PULL = 0.08
TEMPO_GLIDE = 0.35
OCTAVE_AGREE = 3
BAR_BEATS = 4.0
MIN_EPISODE_BEATS = 2.0
MAX_EPISODE_STRETCH = 1.35
PHASE_SLEW_PER_S = 0.12
BEAT_EFFECTS = frozenset({"pulse", "chase", "wave", "mirror_sweep", "breathe"})
_SUBDIVISIONS = (0.5, 1.0, 2.0, 4.0)
ANALYSIS_HOP = 256
# Compare the spectrum to ~45 ms ago. Adjacent-hop relative energy (0.30 of
# the current hop) zeroed 50–80 ms attacks on a musical bed.
FLUX_LAG_S = 0.045
MIN_FLUX_PEAK_MEDIAN = 6.0
MAX_FLUX_DUTY = 0.28
CONFIDENCE_CAP = 0.92
# How long a finished result may sit unread in the result slot.
# Compute of the latest USB window is not this gate.
STALE_ANALYSIS_S = 0.40
# Full capture → queue → compute → drain age. Same meaning on USB and in tests.
# Staff Pi inspect is 150–430 ms; two 0.5 s hops are 1 s; leftover-slot wait is
# 0.40 s. 1.5 s covers a slow inspect plus one hop. A 10 s-old capture is not
# a lock. Phase compensation still uses this age on accepted results.
MAX_ANALYSIS_AGE_S = 1.50
# Phrase hold on a locked groove / Preset-BPM episode without a new event.
PHRASE_CAP_BEATS = 64.0
# Irregular hits can sit on a lucky grid (~0.59 coverage). A beat train
# covers more of the expected lattice. Extra off-grid hats are not this gate:
# those raise onset count without erasing a covering bar.
MIN_COVERAGE = 0.62


@dataclass(frozen=True)
class TempoReading:
    bpm: float
    beat_phase: float
    confidence: float = 1.0


def tempo_ratio(bpm: float) -> float:
    return max(0.5, min(5.0, float(bpm) / REFERENCE_BPM))


def _in_range(value: float) -> bool:
    return BPM_MIN <= value <= BPM_MAX


def _wrap_error(target: float, current: float) -> float:
    return (target - current + 0.5) % 1.0 - 0.5


def _harmonic_ratio(ratio: float) -> bool:
    """Half, double, 2/3 and 3/2. These jump whole or stay. They do not glide."""
    return (
        (0.4 <= ratio <= 0.6)
        or (0.62 <= ratio <= 0.72)
        or (1.45 <= ratio <= 1.60)
        or (1.8 <= ratio <= 2.2)
    )


class TempoFollower:
    """One beat clock. Phase keeps moving between analyses.

    A new reading corrects phase by at most ``PHASE_PULL`` and never replaces
    the clock with the average of two octaves. ``grid_phase`` counts groups of
    four beats from this clock. It is not a detected downbeat.
    """

    def __init__(self) -> None:
        self.bpm: float | None = None
        self.confidence: float = 0.0
        self.locked: bool = False
        self._beats = 0.0
        self._at = 0.0
        self._fresh_until = 0.0
        self._release_until: float | None = None
        self._release_from = 1.0
        self._octave_votes = 0
        self._octave_candidate: float | None = None
        self._phase = 0.0
        self._action_offset = 0.0
        self._pending: float | None = None
        self._hits = 0
        self._started = False
        self._coast_bpm: float | None = None

    def _motion_bpm(self) -> float | None:
        """Rate of the action clock. Recognised tempo is ``bpm`` only."""
        if self.bpm is not None:
            return float(self.bpm)
        if self._coast_bpm is not None:
            return float(self._coast_bpm)
        return None

    def beats_at(self, now: float) -> float:
        rate = self._motion_bpm()
        if rate is None or not self._started:
            return self._beats
        elapsed = max(0.0, float(now) - self._at)
        return self._beats + elapsed * (rate / 60.0)

    def action_beats_at(self, now: float) -> float:
        """Continuous action coordinate: planning beats plus the heard-beat offset.

        ``_phase`` stays on one beat for display. This value does not wrap, so a
        0.08 pull cannot rewind a 2/4/8-beat cycle or the planner.
        """
        return self.beats_at(now) + float(self._action_offset)

    def _commit(self, now: float) -> None:
        self._beats = self.beats_at(now)
        self._at = float(now)

    def choose_tempo(self, estimate: float) -> float | None:
        """Pick one hypothesis. A half or a double is not averaged with the lock."""
        raw = float(estimate)
        if self.bpm is None or not self.locked:
            return raw if _in_range(raw) else None
        ratio = raw / float(self.bpm)
        if _harmonic_ratio(ratio):
            if (
                self._octave_candidate is not None
                and abs(raw - self._octave_candidate) / max(raw, 1.0) < 0.04
            ):
                self._octave_votes += 1
            else:
                self._octave_candidate = raw
                self._octave_votes = 1
            if self._octave_votes >= OCTAVE_AGREE and _in_range(raw):
                self._octave_votes = 0
                self._octave_candidate = None
                return raw
            return float(self.bpm)
        self._octave_votes = 0
        self._octave_candidate = None
        return raw if _in_range(raw) else None

    def _agree(self, raw: float) -> None:
        if self._pending is None or abs(raw - self._pending) > max(1.5, abs(raw) * 0.02):
            self._pending = raw
            self._hits = 1
            return
        self._hits += 1

    def _heard_error(
        self, reading: TempoReading, chosen: float, moment: float, latency_s: float
    ) -> float:
        """Beat-fraction error at ``moment``. Planning beats are not involved."""
        heard = (float(reading.beat_phase) + max(0.0, latency_s) * (chosen / 60.0)) % 1.0
        current = (self.beats_at(moment) + self._phase) % 1.0
        return _wrap_error(heard, current)

    def _shift_phase(self, delta: float) -> None:
        self._action_offset += float(delta)
        self._phase = (self._action_offset + 0.5) % 1.0 - 0.5

    def _apply_phase(self, reading: TempoReading, chosen: float, moment: float, latency_s: float) -> None:
        """Pull the beat fraction only. The planning counter is not the phase."""
        error = self._heard_error(reading, chosen, moment, latency_s)
        # A third of a beat away with no capture delay is a mismatched
        # window (clock ran without samples), not a heard downbeat.
        # A measured capture delay may be that large; then pull.
        if abs(error) > 0.22 and float(latency_s) <= 1e-9:
            return
        pull = max(-PHASE_PULL, min(PHASE_PULL, error))
        self._shift_phase(pull)

    def _place_phase(self, reading: TempoReading, chosen: float, moment: float, latency_s: float) -> None:
        """Put the heard beat on the action coordinate once. Do not rewind ``_beats``."""
        self._shift_phase(self._heard_error(reading, chosen, moment, latency_s))

    def _take_tempo(self, chosen: float) -> None:
        """Install ``chosen``. A confirmed octave jumps; a real accel still glides."""
        current = self.bpm
        if current is None:
            self.bpm = chosen
        elif _harmonic_ratio(chosen / float(current)) and abs(chosen - float(current)) > 0.5:
            self.bpm = chosen
        elif not self.locked:
            self.bpm = chosen
        else:
            self.bpm = float(current) + (chosen - float(current)) * TEMPO_GLIDE
        if self.bpm is not None:
            self._coast_bpm = float(self.bpm)

    def push(
        self,
        reading: TempoReading,
        now: float | None = None,
        *,
        latency_s: float = 0.0,
        pull_phase: bool = True,
    ) -> float | None:
        moment = time.monotonic() if now is None else float(now)
        if float(reading.confidence) < LOCK_CONFIDENCE:
            return self.bpm
        raw = float(reading.bpm)
        if not _in_range(raw):
            return self.bpm
        if not self.locked:
            self._agree(raw)
            self._commit(moment)
            if self._hits < LOCK_AGREE:
                if self.bpm is None:
                    self.bpm = raw
                if self.bpm is not None:
                    self._coast_bpm = float(self.bpm)
                self._started = True
                if pull_phase:
                    self._apply_phase(reading, float(self.bpm), moment, latency_s)
                # One window may start the clock. It does not drive the show.
                self._fresh_until = moment - 1.0
                self.confidence = min(float(reading.confidence), 0.5)
                return self.bpm
            self._take_tempo(raw)
            self._started = True
            if pull_phase:
                # The lock is the first time the show may follow this beat.
                # Later readings only pull. Placing here does not reset _beats.
                self._place_phase(reading, float(self.bpm or raw), moment, latency_s)
            self.locked = True
            self._release_until = None
            self.confidence = max(0.0, min(1.0, float(reading.confidence)))
            self._fresh_until = moment + TEMPO_HOLD_S
            return self.bpm
        chosen = self.choose_tempo(raw)
        if chosen is None:
            return self.bpm
        previous = float(self.bpm) if self.bpm is not None else float(chosen)
        self._commit(moment)
        self._take_tempo(chosen)
        accepted = float(self.bpm or chosen)
        # A half or double that is only a vote is not this beat's phase.
        raw_matches = abs(raw - accepted) / max(accepted, 1.0) <= 0.06
        jumped = _harmonic_ratio(accepted / previous) and abs(accepted - previous) > 0.5
        if pull_phase and raw_matches:
            if jumped:
                self._place_phase(reading, accepted, moment, latency_s)
            else:
                self._apply_phase(reading, accepted, moment, latency_s)
        self.locked = True
        self._started = True
        self._release_until = None
        self.confidence = max(0.0, min(1.0, float(reading.confidence)))
        self._fresh_until = moment + TEMPO_HOLD_S
        return self.bpm

    def advance(self, now: float) -> None:
        """Hold a short gap, then release. Do not freeze the phase forever."""
        if self.bpm is None:
            self.locked = False
            return
        moment = float(now)
        if self.locked and moment > self._fresh_until:
            self.locked = False
            # The release starts when the hold ended, so one late frame that
            # jumps past both windows leaves the lock in that same step.
            self._release_until = self._fresh_until + TEMPO_RELEASE_S
            self._release_from = tempo_ratio(self.bpm)
            self.confidence = min(self.confidence, 0.2)
        if self._release_until is not None and moment >= self._release_until:
            # Count through the end of the release, including one late frame,
            # and do not step back to an older commit. The lock ends here; the
            # action clock keeps the phase and coasts at the last tempo.
            end = max(self._at, min(moment, self._release_until))
            if self.bpm is not None:
                self._coast_bpm = float(self.bpm)
            self._commit(end)
            self.bpm = None
            self.confidence = 0.0
            self.locked = False
            self._release_until = None
            self._pending = None
            self._hits = 0
            self._octave_votes = 0
            self._octave_candidate = None

    def begin_release(self, now: float) -> None:
        if self.bpm is None:
            self.locked = False
            return
        self._commit(float(now))
        self.locked = False
        self._release_from = tempo_ratio(self.bpm)
        self._release_until = float(now) + TEMPO_RELEASE_S
        self._fresh_until = float(now)
        self.confidence = min(self.confidence, 0.2)

    def show_factor(self, now: float, manual_ratio: float) -> float | None:
        """Authored seconds per wall second, or None when the manual clock owns time."""
        moment = float(now)
        if self.bpm is not None and (self.locked or moment <= self._fresh_until):
            return tempo_ratio(self.bpm)
        if self._release_until is not None and moment < self._release_until:
            started = self._release_until - TEMPO_RELEASE_S
            span = max(1e-6, TEMPO_RELEASE_S)
            mix = max(0.0, min(1.0, (moment - started) / span))
            return self._release_from * (1.0 - mix) + float(manual_ratio) * mix
        return None

    def phase_at(self, now: float) -> float:
        if self.bpm is None:
            return 0.0
        return (self.beats_at(now) + self._phase) % 1.0

    def grid_phase(self, now: float) -> float:
        """Position in the internal 4-beat lattice. Not a recognised downbeat."""
        if self.bpm is None:
            return 0.0
        return (self.beats_at(now) % BAR_BEATS) / BAR_BEATS

    @property
    def beat_phase(self) -> float:
        return self.phase_at(self._at if self._started else time.monotonic())

    @beat_phase.setter
    def beat_phase(self, value: float) -> None:
        """Place the beat fraction without moving the planning counter."""
        target = float(value) % 1.0
        if not self._started:
            self._at = time.monotonic()
            self._started = True
        base = self.beats_at(self._at) % 1.0
        self._phase = (target - base + 0.5) % 1.0 - 0.5
        self._action_offset = self._phase

    @property
    def releasing(self) -> bool:
        return self._release_until is not None and self.bpm is not None and not self.locked

    def bar_phase(self) -> float:
        return self.grid_phase(self._at if self._started else time.monotonic())


def show_scale(
    mode: str,
    bpm: float,
    episode_time_s: float,
    episode_duration_s: float,
    beat_phase: float = 0.0,
    grid_phase: float = 0.0,
) -> float:
    """How many authored seconds pass per wall second. 1.0 is the written preset.

    Bar and beat no longer chase the episode with a fresh error every frame.
    That chase pushed the ending away. The grid and the beat own their own
    clocks; this ratio is only the tempo.
    """
    del mode, episode_time_s, episode_duration_s, beat_phase, grid_phase
    return tempo_ratio(bpm)


@dataclass
class BarPlan:
    """One episode's ending, chosen once. Not recomputed every frame."""

    episode_index: int
    start_beat: float
    target_beat: float
    origin_elapsed: float
    remaining_s: float


def _ceil_bar(beat: float) -> float:
    return math.ceil((beat / BAR_BEATS) - 1e-9) * BAR_BEATS


def _floor_bar(beat: float) -> float:
    return math.floor((beat / BAR_BEATS) + 1e-9) * BAR_BEATS


def plan_bar(
    start_beat: float,
    remaining_s: float,
    episode_index: int,
    origin_elapsed: float,
) -> BarPlan:
    """Land on the internal 4-beat grid without a tiny episode or an open wait."""
    authored = max(MIN_EPISODE_BEATS, float(remaining_s) * (REFERENCE_BPM / 60.0))
    if authored > PHRASE_CAP_BEATS:
        authored = PHRASE_CAP_BEATS
    earliest = float(start_beat) + MIN_EPISODE_BEATS
    ideal = float(start_beat) + authored
    target = max(_ceil_bar(ideal), _ceil_bar(earliest))
    latest = float(start_beat) + authored * MAX_EPISODE_STRETCH
    if target > latest:
        floored = _floor_bar(latest)
        target = floored if floored >= earliest - 1e-6 else _ceil_bar(earliest)
    if target <= float(start_beat) + 1e-6:
        target = float(start_beat) + BAR_BEATS
    return BarPlan(
        episode_index=int(episode_index),
        start_beat=float(start_beat),
        target_beat=float(target),
        origin_elapsed=float(origin_elapsed),
        remaining_s=max(0.05, float(remaining_s)),
    )


def beat_phase_correction(
    effect_turns: float,
    beat_phase: float,
    rate_hz: float,
    bpm: float,
    wall_dt: float,
    effect: str,
) -> float:
    """Slew a matching periodic effect toward the beat. Movement is not touched."""
    if effect not in BEAT_EFFECTS or rate_hz <= 0.0 or bpm <= 0.0 or wall_dt <= 0.0:
        return 0.0
    beat_hz = float(bpm) / 60.0
    ratio = rate_hz / beat_hz
    subdivision = None
    for candidate in _SUBDIVISIONS:
        if abs(ratio - candidate) / candidate <= 0.12:
            subdivision = candidate
            break
    if subdivision is None:
        return 0.0
    target = (float(beat_phase) * subdivision) % 1.0
    error = _wrap_error(target, float(effect_turns) % 1.0)
    limit = PHASE_SLEW_PER_S * float(wall_dt)
    return max(-limit, min(limit, error))


def _snapped_error(onsets: list[float], period: float) -> float:
    """Mean distance from each onset to the nearest multiple of ``period``."""
    if period <= 0.0 or len(onsets) < 2:
        return 1.0
    origin = onsets[0]
    total = 0.0
    count = 0
    for onset in onsets[1:]:
        gap = onset - origin
        if gap <= 0.0:
            continue
        multiples = round(gap / period)
        if multiples < 1:
            continue
        total += abs(gap - multiples * period) / period
        count += 1
    if count == 0:
        return 1.0
    return total / count


def _finite_block(samples: object) -> np.ndarray:
    """One analysis block. Non-finite samples become silence and keep their time."""
    array = np.asarray(samples, dtype=np.float64)
    if array.ndim != 1:
        array = array.reshape(-1)
    if array.size == 0:
        return array
    return np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0)


def _onset_flux(block: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Lagged spectral flux. One system: positive bin change vs ~45 ms ago.

    Broadband hop energy with a 0.30 relative gate treated a smooth 50–80 ms
    attack on a bed as 'not an attack'. Spectral bins keep kick/hat change
    when the bed is a steady bass or tone; the lag is longer than hop-leak
    of a sine and shorter than a musical attack.
    """
    hop = ANALYSIS_HOP
    count = (int(block.size) - 1) // hop
    if count < 2:
        empty = np.zeros(max(count, 0), dtype=np.float64)
        return empty, np.zeros(empty.size, dtype=np.float64), hop
    framed = block[: count * hop].reshape(count, hop)
    window = np.hanning(hop)
    spec = np.abs(np.fft.rfft(framed * window, axis=1))
    freqs = np.fft.rfftfreq(hop, 1.0 / float(sample_rate))
    # Keep kick body and hats. Drop DC and air hiss.
    weights = np.clip((freqs - 40.0) / 20.0, 0.0, 1.0) * np.clip(
        (5000.0 - freqs) / 400.0, 0.0, 1.0
    )
    weighted = spec * weights
    lag = max(1, int(round(FLUX_LAG_S * float(sample_rate) / hop)))
    # Silence before the buffer, not a copy of hop 0: copying hid the first
    # click of a short metronome (2 s / four hits → three onsets).
    previous = np.zeros_like(weighted)
    if count > lag:
        previous[lag:] = weighted[:-lag]
    flux = np.maximum(0.0, weighted - previous).sum(axis=1)
    # Clicks are a peak inside the hop. A tonal bed is not: argmax would
    # chase the carrier, so those hops stay at the hop centre.
    rms = np.sqrt(np.mean(framed * framed, axis=1))
    peak = np.max(np.abs(framed), axis=1)
    peaks = np.full(count, hop * 0.5, dtype=np.float64)
    impulsive = peak > 2.0 * (rms + 1e-12)
    if np.any(impulsive):
        peaks[impulsive] = framed.argmax(axis=1)[impulsive].astype(np.float64)
    return flux, peaks, hop


def _flux_is_sparse(flux: np.ndarray) -> bool:
    """Tone and noise fill the hop grid. A beat train does not."""
    if flux.size == 0:
        return False
    peak = float(np.max(flux))
    if peak < 1e-6:
        return False
    median = float(np.median(flux))
    duty = float(np.mean(flux > 0.30 * peak))
    return peak >= MIN_FLUX_PEAK_MEDIAN * (median + 1e-12) and duty <= MAX_FLUX_DUTY


def _attack_weight(
    block: np.ndarray | None,
    when: float,
    sample_rate: int,
    hop: int,
    flux_value: float,
) -> float:
    """Time-domain peak around an attack.

    Spectral-flux magnitude depends on where the click sits inside the hop.
    Equal clicks then look like loud kicks and quiet hats, and the octave
    step takes half the tempo. The waveform peak does not.
    """
    if block is None or int(block.size) == 0:
        return float(flux_value)
    center = int(round(float(when) * float(sample_rate)))
    half = max(int(hop), 1)
    lo = max(0, center - half)
    hi = min(int(block.size), center + half + 1)
    if hi <= lo:
        return float(flux_value)
    return float(np.max(np.abs(block[lo:hi])))


def _local_onset_peaks(
    flux: np.ndarray,
    peaks: np.ndarray,
    hop: int,
    sample_rate: int,
    block: np.ndarray | None = None,
) -> tuple[list[float], list[float]]:
    """Attack times and flux from each neighborhood, not the loudest hit in the bar.

    The median and MAD of a short window ignore one spike. A global fraction of
    the peak does not: that spike becomes the only attack that survives.
    Strength for the hat test is the waveform peak, not the hop flux.
    """
    count = int(flux.size)
    if count < 3:
        return [], []
    radius = max(4, int(0.40 * sample_rate / hop))
    refractory_s = 0.18
    found: list[tuple[float, float, float]] = []
    last = -10.0
    for index in range(count):
        value = float(flux[index])
        if value < 1e-6:
            continue
        left = float(flux[index - 1]) if index else 0.0
        right = float(flux[index + 1]) if index + 1 < count else 0.0
        if value < left or value < right:
            continue
        start = max(0, index - radius)
        window = flux[start : min(count, index + radius + 1)]
        med = float(np.median(window))
        mad = float(np.median(np.abs(window - med)))
        floor = med + 3.0 * mad
        if floor < 1e-6:
            floor = 1e-6
        if value < floor:
            continue
        when = (index * hop + int(peaks[index])) / float(sample_rate)
        weight = _attack_weight(block, when, sample_rate, hop, value)
        if when - last < refractory_s:
            if found and value > found[-1][1]:
                found[-1] = (when, value, weight)
                last = when
            continue
        found.append((when, value, weight))
        last = when
    if len(found) >= 2:
        ordered = sorted(flux_value for _when, flux_value, _weight in found)
        typical = ordered[len(ordered) // 2]
        # The first hop has no predecessor, so mean removal looks like a tiny
        # attack. A real quieter hit in the same bar stays well above that.
        # The floor stays on flux. Waveform peak is only the hat comparison.
        floor = typical * 0.12
        found = [item for item in found if item[1] >= floor]
    return [item[0] for item in found], [item[2] for item in found]


def _local_onsets(
    flux: np.ndarray,
    peaks: np.ndarray,
    hop: int,
    sample_rate: int,
) -> list[float]:
    times, _strengths = _local_onset_peaks(flux, peaks, hop, sample_rate)
    return times


def _fold_period(period: float) -> float | None:
    """Move a gap into the supported tempo range by octaves, or refuse it."""
    if period <= 0.0:
        return None
    min_period = 60.0 / BPM_MAX
    max_period = 60.0 / BPM_MIN
    while period > max_period * 1.08 and period * 0.5 >= min_period * 0.92:
        period *= 0.5
    while period < min_period * 0.92 and period * 2.0 <= max_period * 1.08:
        period *= 2.0
    if not _in_range(60.0 / period):
        return None
    return period


def _fit_grid(
    times: np.ndarray, period: float, origin: float
) -> tuple[float, int, float, float]:
    """Inlier error, count, last inlier, and expected beats across the whole window."""
    if period <= 0.0 or int(times.size) == 0:
        return 1.0, 0, origin, 0.0
    gaps = times - origin
    errors = np.abs(gaps - np.round(gaps / period) * period) / period
    mask = errors <= 0.04
    count = int(np.count_nonzero(mask))
    if count == 0:
        return 1.0, 0, origin, 0.0
    span = float(times[-1] - times[0])
    expected = span / period if span > 0.0 else 0.0
    return float(errors[mask].mean()), count, float(times[mask][-1]), expected


def _choose_origin(times: np.ndarray, period: float) -> float:
    """Origin of the densest on-grid group, not necessarily the first attack."""
    count = int(times.size)
    if count == 0:
        return 0.0
    if count <= 12:
        origins = times
    else:
        origins = times[np.round(np.linspace(0, count - 1, 12)).astype(int)]
    gaps = times[None, :] - origins[:, None]
    errors = np.abs(gaps - np.round(gaps / period) * period) / period
    inlier = errors <= 0.04
    scores = inlier.sum(axis=1)
    mean_err = np.where(
        scores > 0,
        (errors * inlier).sum(axis=1) / np.maximum(scores, 1),
        1.0,
    )
    return float(origins[int(np.lexsort((mean_err, -scores))[0])])


def _refine_period(onsets: list[float], period: float) -> tuple[float, float, float, int, int, float]:
    """Small timing search around a consistent origin. Period, error, last inlier, counts, span beats."""
    times = np.asarray(onsets, dtype=np.float64)
    origin = _choose_origin(times, period)
    best_period = period
    best_error = 1.0
    best_last = float(times[-1])
    best_inliers = 0
    best_compared = int(times.size)
    best_expected = 0.0
    for step in range(-25, 26):
        candidate = period * (1.0 + step * 0.002)
        if candidate <= 0.0 or not _in_range(60.0 / candidate):
            continue
        error, inliers, last, expected = _fit_grid(times, candidate, origin)
        if inliers < 4:
            continue
        if inliers > best_inliers or (inliers == best_inliers and error < best_error):
            best_error = error
            best_period = candidate
            best_last = last
            best_inliers = inliers
            best_expected = expected
    if best_inliers >= 4:
        origin = _choose_origin(times, best_period)
        error, inliers, last, expected = _fit_grid(times, best_period, origin)
        return best_period, error, last, inliers, best_compared, expected
    return best_period, best_error, best_last, best_inliers, best_compared, best_expected


def _tempo_detail(
    onsets: int,
    inliers: int = 0,
    error: float | None = None,
    coverage: float | None = None,
    bpm: float | None = None,
) -> dict[str, float | int | None]:
    return {
        "onsets": int(onsets),
        "inliers": int(inliers),
        "error": None if error is None else round(float(error), 4),
        "coverage": None if coverage is None else round(float(coverage), 3),
        "bpm": None if bpm is None else round(float(bpm), 1),
    }


def _inlier_mask(times: np.ndarray, period: float, origin: float) -> np.ndarray:
    if period <= 0.0 or int(times.size) == 0:
        return np.zeros(int(times.size), dtype=bool)
    gaps = times - origin
    errors = np.abs(gaps - np.round(gaps / period) * period) / period
    return errors <= 0.04


def _weak_subdivision(
    times: list[float],
    strengths: list[float],
    slow_period: float,
    fast_period: float,
) -> bool:
    """Hats in range add extra inliers on the fast grid. Equal clicks do not.

    ``strengths`` are waveform peaks. Hop flux is not a loudness.
    """
    if not times or not strengths or slow_period <= 0.0 or fast_period <= 0.0:
        return False
    stamps = np.asarray(times, dtype=np.float64)
    weight = np.asarray(strengths, dtype=np.float64)
    slow_origin = _choose_origin(stamps, slow_period)
    fast_origin = _choose_origin(stamps, fast_period)
    slow_m = _inlier_mask(stamps, slow_period, slow_origin)
    fast_m = _inlier_mask(stamps, fast_period, fast_origin)
    extra = fast_m & ~slow_m
    shared = fast_m & slow_m
    if int(extra.sum()) < 2 or int(shared.sum()) < 2:
        return False
    return float(weight[extra].mean()) <= 0.55 * float(weight[shared].mean())


def _maybe_slower_beat(
    best: tuple[float, float, float, int, float, float],
    onsets: list[float],
    strengths: list[float],
) -> tuple[float, float, float, int, float, float]:
    """If the leading grid is a loud-kick / quiet-hat octave, take the kick period."""
    slower = best[0] * 2.0
    if not _in_range(60.0 / slower):
        return best
    period, error, last, inliers, _compared, expected = _refine_period(onsets, slower)
    if inliers < 4 or expected <= 0.0:
        return best
    coverage = inliers / expected
    if error > 0.04 or expected < 3.0 or coverage < MIN_COVERAGE:
        return best
    if expected >= 6.0 and inliers < 5:
        return best
    if not _weak_subdivision(onsets, strengths, period, best[0]):
        return best
    return (period, error, last, inliers, coverage, expected)


def inspect_tempo(
    samples: list[float], sample_rate: int
) -> tuple[TempoReading | None, str, dict[str, float | int | None]]:
    """Read a tempo, and name the gate when the window is refused.

    Attacks come from lagged spectral flux, not from a hop-to-hop relative
    energy gate. An off-grid hat or syncopation does not erase a beat that
    the other attacks already form. Reasons are ``few_onsets``,
    ``weak_period``, ``rival``, ``out_of_range`` and ``low_confidence``.
    The detail counts are for the local diagnostic, not the operator desk.
    """
    empty = _tempo_detail(0)
    block = _finite_block(samples)
    if sample_rate < 8000 or int(block.size) < sample_rate:
        return None, "few_onsets", empty
    centered = block - float(block.mean())
    if float(np.max(np.abs(centered))) <= 1e-9:
        return None, "few_onsets", empty
    flux, peaks, hop = _onset_flux(centered, sample_rate)
    if flux.size < 2 or float(np.max(flux)) < 1e-6:
        return None, "few_onsets", empty
    # The zero lag-baseline makes hop 0 look like an attack (needed so a
    # 2 s metronome keeps the first click). A tone's buffer edge is the
    # same transient. Sparsity is judged after that lag, or the startup
    # peak hides a dense bed and a 220 Hz sine false-locks.
    lag = max(1, int(round(FLUX_LAG_S * float(sample_rate) / hop)))
    sparse = flux[lag:] if flux.size > lag else flux
    if not _flux_is_sparse(sparse):
        return None, "weak_period", empty
    onsets, strengths = _local_onset_peaks(flux, peaks, hop, sample_rate, centered)
    if len(onsets) < 4:
        return None, "few_onsets", _tempo_detail(len(onsets))
    gaps = [right - left for left, right in zip(onsets, onsets[1:]) if right > left]
    if len(gaps) < 3:
        return None, "few_onsets", _tempo_detail(len(onsets))
    folded: list[float] = []
    for seed in (statistics.median(gaps), *gaps):
        period = _fold_period(float(seed))
        if period is None:
            continue
        if any(abs(period - kept) / kept <= 0.015 for kept in folded):
            continue
        folded.append(period)
    if not folded:
        return None, "out_of_range", _tempo_detail(len(onsets))
    fits: list[tuple[float, float, float, int, float, float]] = []
    for period in folded:
        refined = _refine_period(onsets, period)
        _period, error, last, inliers, _compared, expected = refined
        if inliers < 4 or expected <= 0.0:
            continue
        coverage = inliers / expected
        fits.append((_period, error, last, inliers, coverage, expected))
    if not fits:
        return None, "weak_period", _tempo_detail(len(onsets))
    passing = [
        fit
        for fit in fits
        if fit[1] <= 0.04
        and fit[5] >= 3.0
        and fit[4] >= MIN_COVERAGE
        and not (fit[5] >= 6.0 and fit[3] < 5)
    ]
    if not passing:
        tight = min(fits, key=lambda item: (-item[3], item[1], -item[4]))
        return None, "weak_period", _tempo_detail(
            len(onsets), tight[3], tight[1], tight[4], 60.0 / tight[0]
        )
    # A 4-hit accident with a tiny error is not the bar. The larger
    # consistent group is, even if its error is a little worse.
    passing.sort(key=lambda item: (-item[3], -item[4], item[1]))
    best_period, best_error, last_onset, inliers, coverage, _expected = _maybe_slower_beat(
        passing[0], onsets, strengths
    )
    for other in passing:
        ratio = other[0] / best_period
        if 0.92 <= ratio <= 1.08:
            continue
        if 1.85 <= ratio <= 2.15 or 0.46 <= ratio <= 0.54:
            continue
        for factor in (1.5, 2.0 / 3.0):
            if abs(ratio - factor) / factor > 0.08:
                continue
            if (
                other[1] <= best_error + 0.012
                and other[4] >= 0.55
                and other[3] >= inliers - 1
            ):
                return (
                    None,
                    "rival",
                    _tempo_detail(len(onsets), inliers, best_error, coverage, 60.0 / best_period),
                )
    # Inlier cutoff is 0.04 period. Smooth 50–80 ms attacks sit near that
    # floor; they are still a beat. Do not map that timing to confidence 0.5.
    periodicity = max(0.0, 1.0 - best_error / 0.08)
    # A lone weak alternative is not proof. Inliers and coverage scale the
    # score; the cap stops it reaching 1.
    support = min(1.0, float(inliers) / 8.0) * min(1.0, float(coverage))
    evidence = 0.62 + 0.38 * support
    confidence = max(0.0, min(CONFIDENCE_CAP, periodicity * evidence))
    detail = _tempo_detail(len(onsets), inliers, best_error, coverage, 60.0 / best_period)
    if confidence < 0.35:
        return None, "low_confidence", detail
    end_s = block.size / float(sample_rate)
    phase = ((end_s - last_onset) % best_period) / best_period
    return (
        TempoReading(bpm=60.0 / best_period, beat_phase=phase, confidence=confidence),
        "ok",
        detail,
    )


def estimate_tempo(samples: list[float], sample_rate: int) -> TempoReading | None:
    """Period of the local attacks. One louder hit does not set the bar."""
    reading, _reason, _detail = inspect_tempo(samples, sample_rate)
    return reading


def click_track(bpm: float, seconds: float, sample_rate: int = 44100) -> list[float]:
    """A kick-like click train. Used to prove the estimator without a sound card."""
    total = int(seconds * sample_rate)
    samples = [0.0] * total
    step = max(1, int(sample_rate * 60.0 / bpm))
    for start in range(0, total, step):
        for offset in range(min(90, total - start)):
            samples[start + offset] = 0.95 * (1.0 - offset / 90.0)
    return samples
