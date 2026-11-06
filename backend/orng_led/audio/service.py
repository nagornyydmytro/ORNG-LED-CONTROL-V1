"""Per-location BPM sync. Audio capture is optional; the clock math is not."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import logging
import os
import threading
import time
from multiprocessing import get_context
from pathlib import Path
from queue import Empty, Full

import numpy as np

from orng_led.audio.auto_show import (
    INPUT_LOSS_S,
    LitFixture,
    SectionTracker,
    SliceHearing,
    analyze_slice,
    build_auto_stage,
    consume_accent,
    lay_par_bed,
    window_energy,
)
from orng_led.audio.library import BY_ID
from orng_led.audio.planner import EpisodePlanner
from orng_led.audio.palettes import known_palette, migrate_palette
from orng_led.audio.settings import (
    CAST_IDS,
    CHARACTER_IDS,
    COLOR_IDS,
    INTENSITY_IDS,
    colour_of,
    intensity_of,
    known_period,
)
from orng_led.audio.bpm import (
    MAX_ANALYSIS_AGE_S,
    MODES,
    STALE_ANALYSIS_S,
    BarPlan,
    TempoFollower,
    TempoReading,
    estimate_tempo,
    inspect_tempo,
    plan_bar,
    tempo_ratio,
)
from orng_led.audio.devices import InputDevice, choose_input, stable_identity
from orng_led.engine.intents import StageIntent

logger = logging.getLogger(__name__)

MAX_BUFFER_S = 4.0
CALLBACK_STALE_S = 2.5
# New audio this quiet does not refresh the held tempo.
_HEARD = 0.04
# A repeated look at the same samples is not a second lock vote.
MIN_NOVEL_S = 0.5
DIAG_INTERVAL_S = 0.2
_CLIP = 0.98
WORKER_RESTART_DELAY_S = 0.8
WORKER_RESTART_MAX = 6
WINDOW_TRACE = 8


class BpmService:
    def __init__(self, config_root: Path, scene_id: str) -> None:
        self.config_root = Path(config_root)
        self.scene_id = scene_id or "default"
        self.enabled = False
        self.mode = "smooth"
        self.scope = "preset"
        self.palette = "orange"
        self.cast = "all"
        self.character = "rhythmic"
        self.intensity = "full"
        self.period: float | None = None
        self.par_rhythm = False
        self.accent = 0.0
        self.section = SectionTracker()
        self.follower = TempoFollower()
        self.planner = EpisodePlanner()
        self.device_name: str | None = None
        self.device_id: str | None = None
        self.capture = "idle"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_feed_at: float | None = None
        self._heard_at: float | None = None
        self._now = time.monotonic()
        self._bar_plan: BarPlan | None = None
        # Float arrays, not Python lists. A 4 s list copy holds the GIL long
        # enough to stall a frame.
        self._history = np.empty(0, dtype=np.float64)
        self._novel = np.empty(0, dtype=np.float64)
        self._unvoted_s = 0.0
        self._sample_rate: int | None = None
        self._channels: int | None = None
        self._captured_at: float | None = None
        self._callback_mono: float | None = None
        self._analysis_mono: float | None = None
        self._analysis_errors = 0
        self._analysis_error: str | None = None
        self._logged_analysis_error: str | None = None
        self._trimmed = 0
        self._last_proof_at: float | None = None
        self._raw_bpm: float | None = None
        self._raw_confidence = 0.0
        self._lock_reason = "idle"
        self._tempo_reject: str | None = None
        self._tempo_fit: dict[str, float | int | None] | None = None
        self._signal = False
        self._bar_behind_s = 0.0
        self._rms = 0.0
        self._peak = 0.0
        self._clipping = 0.0
        self._reconnects = 0
        self._overflows = 0
        self._diag_at: float | None = None
        self._device_latency = 0.0
        self._worker = None
        self._jobs = None
        self._results = None
        self._worker_stop = None
        self._use_worker = False
        self._generation = 0
        self._worker_died_at: float | None = None
        self._worker_restarts = 0
        self._recovering = False
        self._window_id = 0
        self._windows: list[dict[str, object]] = []
        self._last_delivery_s: float | None = None
        self._load()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._start_worker()
        self._thread = threading.Thread(target=self._loop, name="orng-bpm", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.5)
        self._stop_worker()

    def _start_worker(self) -> None:
        if self._worker is not None:
            return
        from orng_led.audio.tempo_worker import start_analysis_process

        try:
            self._worker, self._jobs, self._results, self._worker_stop = start_analysis_process(
                get_context("spawn")
            )
            self._use_worker = True
            self._retire_session("worker_start")
            self._worker_died_at = None
        except Exception:  # noqa: BLE001
            logger.warning("tempo worker failed to start", exc_info=True)
            self._worker = None
            self._jobs = None
            self._results = None
            self._worker_stop = None
            # Capture stays on the worker path. Sync inspect_tempo is tests only.
            self._use_worker = True
            if self._worker_died_at is None:
                self._worker_died_at = time.monotonic()

    def _stop_worker(self) -> None:
        self._use_worker = False
        self._worker_died_at = None
        deadline = time.monotonic() + 1.5
        while self._recovering and time.monotonic() < deadline:
            time.sleep(0.02)
        stop = self._worker_stop
        jobs = self._jobs
        worker = self._worker
        if stop is not None:
            try:
                stop.set()
            except Exception:  # noqa: BLE001
                pass
        if jobs is not None:
            try:
                jobs.put_nowait(None)
            except (Full, OSError, ValueError):
                pass
        if worker is not None:
            worker.join(timeout=1.5)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=0.5)
        self._worker = None
        self._jobs = None
        self._results = None
        self._worker_stop = None
        self._recovering = False

    def _worker_alive(self) -> bool:
        worker = self._worker
        return worker is not None and bool(worker.is_alive())

    def _retire_session(self, reason: str) -> None:
        """Drop in-flight analysis. Old captured_at must not lock the new session."""
        del reason
        with self._lock:
            self._generation += 1
        self._flush_queues_nowait()

    def _flush_queues_nowait(self) -> None:
        for queue in (self._jobs, self._results):
            if queue is None:
                continue
            for _ in range(8):
                try:
                    queue.get_nowait()
                except (Empty, OSError, ValueError):
                    break

    def _reap_dead_worker(self) -> None:
        worker = self._worker
        if worker is None or worker.is_alive():
            return
        self._note_analysis_failure(RuntimeError("tempo worker died"))
        self._retire_session("worker_died")
        if self._worker_died_at is None:
            self._worker_died_at = time.monotonic()
        self._jobs = None
        self._results = None
        self._worker_stop = None
        self._worker = None
        threading.Thread(
            target=_join_dead_process,
            args=(worker,),
            name="orng-tempo-reap",
            daemon=True,
        ).start()

    def _schedule_worker_recovery(self) -> None:
        if self._worker_alive() or not self._use_worker or not self.enabled:
            return
        died = self._worker_died_at
        if died is None:
            return
        if time.monotonic() - died < WORKER_RESTART_DELAY_S:
            return
        if self._worker_restarts >= WORKER_RESTART_MAX or self._recovering:
            return
        self._recovering = True
        threading.Thread(
            target=self._restart_worker_bg,
            name="orng-tempo-recover",
            daemon=True,
        ).start()

    def _restart_worker_bg(self) -> None:
        try:
            if self._worker_restarts >= WORKER_RESTART_MAX:
                return
            if self._worker_alive() or not self.enabled or not self._use_worker:
                return
            self._worker_restarts += 1
            self._start_worker()
            if not self._worker_alive():
                self._use_worker = True
                self._worker_died_at = time.monotonic()
        finally:
            self._recovering = False

    def _enqueue_analysis(
        self,
        window: np.ndarray,
        rate: int,
        *,
        captured_at: float,
        now: float | None,
        latency_s: float,
        novel: np.ndarray,
        novel_s: float,
    ) -> None:
        del latency_s
        from orng_led.audio.tempo_worker import put_latest

        jobs = self._jobs
        if jobs is None:
            return
        self._window_id += 1
        job = (
            np.asarray(window, dtype=np.float32),
            int(rate),
            float(captured_at),
            None if now is None else float(now),
            np.asarray(novel, dtype=np.float32),
            float(novel_s),
            int(self._generation),
            time.monotonic(),
            int(self._window_id),
        )
        put_latest(jobs, job)

    def _result_age(
        self,
        captured_at: float,
        sample_now: float | None,
        enqueue_mono: float | None,
    ) -> float:
        """Capture → queue → drain age. USB uses wall time; tests use the show clock."""
        wall = time.monotonic()
        queued = 0.0 if enqueue_mono is None else max(0.0, wall - float(enqueue_mono))
        if sample_now is None:
            return max(0.0, wall - float(captured_at)) + float(self._device_latency)
        logical = max(0.0, float(self._now) - float(captured_at))
        return logical + queued + float(self._device_latency)

    def _note_window(self, record: dict[str, object]) -> None:
        with self._lock:
            self._windows = [*self._windows, record][-WINDOW_TRACE:]
            delay = record.get("delivery_s")
            if isinstance(delay, (int, float)):
                self._last_delivery_s = float(delay)

    def _drain_results(self) -> None:
        """Apply at most one compact worker snapshot. Never PCM, never wait."""
        self._reap_dead_worker()
        results = self._results
        if results is None:
            return
        latest = None
        for _ in range(4):
            try:
                latest = results.get_nowait()
            except (Empty, OSError, ValueError):
                break
        if latest is None or not isinstance(latest, tuple):
            return
        wall = time.monotonic()
        kind = latest[0]
        generation = latest[6] if len(latest) > 6 else None
        captured_at = latest[4] if len(latest) > 4 else None
        sample_now = latest[5] if len(latest) > 5 else None
        enqueue_mono = latest[7] if len(latest) > 7 else None
        computed_at = latest[14] if len(latest) > 14 else None
        window_id = latest[15] if len(latest) > 15 else None
        compute_start = latest[16] if len(latest) > 16 else None
        capture_age = (
            None
            if captured_at is None
            else max(0.0, wall - float(captured_at)) + float(self._device_latency)
        )
        delivery = None if computed_at is None else max(0.0, wall - float(computed_at))
        compute_s = None
        if computed_at is not None and compute_start is not None:
            compute_s = max(0.0, float(computed_at) - float(compute_start))
        raw = latest[1]
        raw_bpm = None if not isinstance(raw, TempoReading) else round(float(raw.bpm), 1)
        reject = None if kind != "ok" else str(latest[2] or "")
        analysed = None
        with self._lock:
            analysed = self._analysis_mono
        analysis_age = None if analysed is None else max(0.0, wall - analysed)

        def _trace(reason: str) -> None:
            self._note_window(
                {
                    "window": window_id,
                    "captured_at": None if captured_at is None else round(float(captured_at), 3),
                    "enqueue_mono": None if enqueue_mono is None else round(float(enqueue_mono), 3),
                    "compute_start": None if compute_start is None else round(float(compute_start), 3),
                    "compute_end": None if computed_at is None else round(float(computed_at), 3),
                    "drained_at": round(wall, 3),
                    "compute_s": None if compute_s is None else round(float(compute_s), 3),
                    "delivery_s": None if delivery is None else round(float(delivery), 3),
                    "capture_age_s": None if capture_age is None else round(float(capture_age), 3),
                    "analysis_age_s": None if analysis_age is None else round(float(analysis_age), 3),
                    "raw_bpm": raw_bpm,
                    "reject": reject,
                    "reason": reason,
                }
            )

        if generation is not None and int(generation) != int(self._generation):
            _trace("generation")
            return
        if not self.enabled:
            _trace("disabled")
            return
        live_usb = sample_now is None
        if captured_at is not None:
            age = self._result_age(float(captured_at), sample_now, enqueue_mono)
        else:
            age = 0.0 if capture_age is None else float(capture_age)
        if live_usb and delivery is not None and delivery > STALE_ANALYSIS_S:
            _trace("stale_result")
            return
        if captured_at is not None and age > MAX_ANALYSIS_AGE_S:
            _trace("stale_capture")
            return
        if kind != "ok":
            _trace("error")
            self._note_analysis_failure(RuntimeError(str(latest[1] if len(latest) > 1 else "analysis")))
            return
        hearing = latest[8] if len(latest) > 8 else None
        if not isinstance(hearing, SliceHearing):
            _trace("no_hearing")
            return
        novel_s = float(latest[9] or 0.0) if len(latest) > 9 else 0.0
        energy = latest[10] if len(latest) > 10 else float(hearing.rms)
        peak = latest[11] if len(latest) > 11 else None
        clipping = latest[12] if len(latest) > 12 else None
        extras = latest[13] if len(latest) > 13 else ()
        if extras:
            self._apply_pending_hearings(extras, dt_s=novel_s)
        applied = self._accept_analysis(
            latest[1],
            str(latest[2] or ""),
            latest[3] or {},
            None,
            dt_s=novel_s,
            now=None,
            latency_s=age,
            sample_rate=self._sample_rate,
            hearing=hearing,
            energy=None if energy is None else float(energy),
            peak=None if peak is None else float(peak),
            clipping=None if clipping is None else float(clipping),
            refuse_stale=False,
        )
        if applied is not None:
            _trace("applied")
        elif reject in {"weak_period", "few_onsets", "rival", "out_of_range", "low_confidence"}:
            _trace(reject or "refused")
        else:
            _trace("refused")

    def set_scene(self, scene_id: str) -> None:
        next_id = scene_id or "default"
        if next_id != self.scene_id:
            self._retire_session("scene")
        self.scene_id = next_id
        self.follower = TempoFollower()
        self.section = SectionTracker()
        self.planner = EpisodePlanner()
        self._heard_at = None
        self._signal = False
        self._bar_plan = None
        self._load()

    def configure(
        self,
        *,
        enabled: bool | None = None,
        mode: str | None = None,
        scope: str | None = None,
        palette: str | None = None,
        cast: str | None = None,
        character: str | None = None,
        intensity: str | float | None = None,
        period: float | int | str | None = None,
        par_rhythm: bool | None = None,
        accent: float | None = None,
    ) -> None:
        was_auto = self.enabled and self.scope == "auto"
        if mode is not None:
            if mode not in MODES:
                raise ValueError("mode must be smooth, bar, or beat")
            if mode != self.mode:
                self._bar_plan = None
                self._bar_behind_s = 0.0
            self.mode = mode
        if scope is not None:
            if scope not in {"preset", "auto"}:
                raise ValueError("scope must be preset or auto")
            self.scope = scope
        if palette is not None:
            if not known_palette(palette):
                raise ValueError("unknown bpm palette")
            ident, extra = migrate_palette(palette)
            self.palette = ident
            if extra:
                self.cast = extra
        if cast is not None:
            if cast not in CAST_IDS:
                raise ValueError("cast must be all, bars, or heads")
            self.cast = cast
        if character is not None:
            if character not in CHARACTER_IDS:
                raise ValueError("character must be smooth, rhythmic, or impact")
            self.character = character
        if intensity is not None:
            if isinstance(intensity, str) and intensity not in INTENSITY_IDS:
                raise ValueError("intensity must be soft, normal, or full")
            self.intensity = intensity if isinstance(intensity, str) else "full"
            self.planner.intensity_cap = intensity_of(intensity)
        if period is not None:
            self.period = known_period(period, self.character)
        elif character is not None and self.period is not None:
            if known_period(self.period, self.character) is None:
                self.period = None
        if par_rhythm is not None:
            self.par_rhythm = bool(par_rhythm)
        if accent is not None:
            self.accent = max(0.0, min(1.0, float(accent)))
            self.section.accent_amount = self.accent
        # Do not write planner.character / planner.period here: look() passes
        # them into advance() so a change is detected and retunes the live
        # episode. Pre-writing made configure(period=8) a no-op.
        self.planner.palette = colour_of(self.palette, self.palette)
        self.planner.intensity_cap = intensity_of(self.intensity)
        if enabled is not None:
            turning_off = self.enabled and not bool(enabled)
            turning_on = (not self.enabled) and bool(enabled)
            self.enabled = bool(enabled)
            if turning_off:
                self._retire_session("bpm_off")
                self.follower.begin_release(self._now)
                self._bar_plan = None
                self._heard_at = None
                self._signal = False
            elif turning_on:
                self._schedule_worker_recovery()
        changed = any(
            item is not None
            for item in (mode, scope, palette, cast, character, intensity, period, par_rhythm, accent)
        )
        if changed:
            self._save_prefs()
        if was_auto and not (self.enabled and self.scope == "auto"):
            self.planner.reset()

    def feed(
        self,
        samples: list[float],
        sample_rate: int,
        *,
        dt_s: float | None = None,
        now: float | None = None,
        energy_samples: list[float] | None = None,
        latency_s: float = 0.0,
    ) -> TempoReading | None:
        """Push one analysis window.

        The show clock moves in :meth:`poll` (and when a caller passes ``now``).
        A capture thread must not write that clock: it would race the renderer
        and rewind it. ``latency_s`` is the age of the newest sample, so phase
        correction aims at the render instant rather than the end of the buffer.
        """
        try:
            reading, reject, detail = inspect_tempo(samples, sample_rate)
        except Exception as exc:  # noqa: BLE001 — analysis must not escape into frames
            self._note_analysis_failure(exc)
            return None
        return self._accept_analysis(
            reading,
            reject,
            detail,
            samples if energy_samples is None else energy_samples,
            dt_s=dt_s,
            now=now,
            latency_s=latency_s,
            sample_rate=sample_rate,
        )

    def _apply_pending_hearings(self, extras: object, *, dt_s: float) -> None:
        """Section events from replaced snapshots. Not a second tempo vote."""
        if not extras:
            return
        step = max(0.0, float(dt_s or 0.0))
        with self._lock:
            beat_period = None if self.follower.bpm is None else 60.0 / float(self.follower.bpm)
            for hearing in extras:
                if not isinstance(hearing, SliceHearing):
                    continue
                phase = self.follower.phase_at(self._now) if self.follower.locked else None
                self.section.push(
                    hearing.rms,
                    step,
                    onset=hearing.onset,
                    hearing=hearing,
                    beat_period_s=beat_period,
                )
                self.section.consider_accent(
                    now=self._now,
                    onset=float(hearing.onset),
                    level=float(hearing.rms),
                    phase=phase,
                )

    def _accept_analysis(
        self,
        reading: TempoReading | None,
        reject: str,
        detail: dict[str, float | int | None],
        heard: object,
        *,
        dt_s: float | None,
        now: float | None,
        latency_s: float,
        sample_rate: int | None = None,
        hearing: SliceHearing | None = None,
        energy: float | None = None,
        peak: float | None = None,
        clipping: float | None = None,
        refuse_stale: bool = True,
    ) -> TempoReading | None:
        rate = int(sample_rate or self._sample_rate or 44100)
        if hearing is None:
            # Sync feed / tests. The frame path must pass a worker SliceHearing.
            energy = window_energy(heard)
            hearing = analyze_slice(heard, rate)
        elif energy is None:
            energy = float(hearing.rms)
        stale = bool(refuse_stale) and float(latency_s) > STALE_ANALYSIS_S
        with self._lock:
            if now is not None:
                self._now = float(now)
            moment = self._now
            self._sample_rate = rate
            if stale:
                self.follower.advance(moment)
                self._raw_bpm = None if reading is None else float(reading.bpm)
                self._raw_confidence = 0.0 if reading is None else float(reading.confidence)
                self._tempo_reject = None if reading is not None else reject
                self._tempo_fit = detail
                self._sync_lock_reason()
                return None
            if dt_s is None:
                dt_s = 0.0 if self._last_feed_at is None else max(0.0, moment - self._last_feed_at)
            self._last_feed_at = moment
            self._heard_at = moment
            self._raw_bpm = None if reading is None else float(reading.bpm)
            self._raw_confidence = 0.0 if reading is None else float(reading.confidence)
            self._tempo_reject = None if reading is not None else reject
            self._tempo_fit = detail
            beat_period = None if self.follower.bpm is None else 60.0 / float(self.follower.bpm)
            self.section.push(
                hearing.rms,
                dt_s,
                onset=hearing.onset,
                hearing=hearing,
                beat_period_s=beat_period,
            )
            self._signal = energy >= _HEARD
            fresh = reading is not None and self._signal
            self._analysis_mono = time.monotonic()
            self._analysis_error = None
            if self.capture in {"idle", "unavailable"}:
                # Samples were analysed. A previous analysis fault is not a dead input.
                self.capture = "listening"
            if fresh and reading is not None:
                self.follower.push(reading, now=moment, latency_s=latency_s)
                self._last_proof_at = moment
                if self.capture in {"idle", "no_device", "listening", "unavailable"}:
                    self.capture = "listening"
                self._lock_reason = "locked" if self.follower.locked else "pending"
            else:
                self.follower.advance(moment)
                if energy < _HEARD:
                    self._lock_reason = "quiet"
                elif reject == "weak_period":
                    self._lock_reason = "no_tempo"
                elif reject in {"few_onsets", "low_confidence", "rival"}:
                    self._lock_reason = "pending"
                else:
                    self._lock_reason = "no_tempo"
            self._sync_lock_reason()
            if peak is not None and clipping is not None:
                if self._diag_at is None or moment - self._diag_at >= DIAG_INTERVAL_S:
                    self._diag_at = moment
                    self._rms = float(hearing.rms)
                    self._peak = float(peak)
                    self._clipping = float(clipping)
            elif heard is not None:
                self._note_levels(heard, moment)
        return reading if fresh else None

    def poll(self, dt_s: float = 0.0) -> None:
        """Advance the beat clock by render time. Safe if capture has died."""
        self._drain_results()
        self._schedule_worker_recovery()
        with self._lock:
            self._now += max(0.0, float(dt_s))
            self.follower.advance(self._now)
            heard = self._heard_at
            if heard is not None and self._now - heard >= INPUT_LOSS_S:
                self.section.lost_signal()
            self._sync_lock_reason()

    def submit_block(
        self,
        fresh: list[float],
        sample_rate: int,
        *,
        captured_at: float,
        channels: int = 1,
        now: float | None = None,
    ) -> None:
        """Ingest one callback.

        Only new samples count. The analysis window overlaps the previous one,
        and that overlap is not another lock vote. A failure stays here: it
        does not escape into DMX, input, or an effect release.
        """
        try:
            self._submit_block(
                fresh,
                sample_rate,
                captured_at=captured_at,
                channels=channels,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            self._note_analysis_failure(exc)

    def diagnostics(self) -> dict[str, object]:
        """Levels and tempo from the open stream. Not a recording, and not the desk."""
        wall = time.monotonic()
        with self._lock:
            proof = self._last_proof_at
            moment = self._now
            accepted = self.follower.bpm if self.follower.locked else None
            callback_at = self._callback_mono
            analysed_at = self._analysis_mono
            return {
                "capture": self.capture,
                "sample_rate": self._sample_rate,
                "channels": self._channels,
                "rms": round(self._rms, 5),
                "peak": round(self._peak, 5),
                "clipping": round(self._clipping, 5),
                "callback_age_s": (
                    None if callback_at is None else round(max(0.0, wall - callback_at), 3)
                ),
                "analysis_age_s": (
                    None if analysed_at is None else round(max(0.0, wall - analysed_at), 3)
                ),
                "tempo_age_s": None if proof is None else round(max(0.0, moment - proof), 3),
                "raw_bpm": None if self._raw_bpm is None else round(float(self._raw_bpm), 1),
                "accepted_bpm": None if accepted is None else round(float(accepted), 1),
                "confidence": round(
                    float(self.follower.confidence if accepted is not None else 0.0),
                    3,
                ),
                "lock_reason": self._lock_reason,
                "tempo_reject": self._tempo_reject,
                "tempo_fit": self._tempo_fit,
                "analysis_errors": self._analysis_errors,
                "analysis_error": self._analysis_error,
                "reconnects": self._reconnects,
                "overflows": self._overflows,
                "history_trimmed": self._trimmed,
                "listening_means_music": False,
                "device_latency_s": round(float(self._device_latency), 3),
                "delivery_s": (
                    None
                    if self._last_delivery_s is None
                    else round(float(self._last_delivery_s), 3)
                ),
                "windows": list(self._windows),
                "planner": list(self.planner.journal()),
            }

    def _submit_block(
        self,
        fresh: list[float],
        sample_rate: int,
        *,
        captured_at: float,
        channels: int,
        now: float | None,
    ) -> None:
        rate = int(sample_rate)
        if rate <= 0 or not fresh:
            return
        if now is None:
            age = max(0.0, time.monotonic() - float(captured_at))
        else:
            age = max(0.0, float(now) - float(captured_at))
        age = min(1.0, age + self._device_latency)
        block = _as_block(fresh)
        if block.size == 0:
            with self._lock:
                self._callback_mono = _callback_moment(captured_at, from_callback=now is None)
            return
        vote = False
        window = block
        novel = block
        novel_s = 0.0
        with self._lock:
            self._sample_rate = rate
            self._channels = int(channels)
            self._captured_at = float(captured_at)
            # The callback clock, not the moment analysis of that buffer finishes.
            self._callback_mono = _callback_moment(captured_at, from_callback=now is None)
            self._history = np.concatenate((self._history, block))
            cap = max(rate, int(rate * MAX_BUFFER_S))
            if self._history.size > cap:
                self._history = np.ascontiguousarray(self._history[-cap:])
                self._trimmed += 1
            self._novel = np.concatenate((self._novel, block))
            self._unvoted_s += block.size / rate
            vote = self._unvoted_s >= MIN_NOVEL_S
            if vote:
                # Detach snapshots; pickle copies them on the capture thread.
                # Do not copy 4 s of PCM under this lock — poll waits on it.
                window = self._history
                novel = self._novel
                novel_s = self._unvoted_s
                self._novel = np.empty(0, dtype=np.float64)
                self._unvoted_s = 0.0
        if vote:
            energy_now = float(window_energy(novel))
            rms, peak, clipping = _block_levels(novel)
            with self._lock:
                if now is not None:
                    self._now = float(now)
                moment = self._now
                self._heard_at = moment
                self._signal = energy_now >= _HEARD
                if self._diag_at is None or moment - self._diag_at >= DIAG_INTERVAL_S:
                    self._diag_at = moment
                    self._rms = rms
                    self._peak = peak
                    self._clipping = clipping
                if self.capture in {"idle", "unavailable", "no_device"}:
                    self.capture = "listening"
            if self._use_worker:
                if self.enabled and self._worker_alive():
                    self._enqueue_analysis(
                        window,
                        rate,
                        captured_at=captured_at,
                        now=now,
                        latency_s=age,
                        novel=novel,
                        novel_s=novel_s,
                    )
                elif self.enabled:
                    self._schedule_worker_recovery()
                return
            self.feed(
                window,
                rate,
                energy_samples=novel,
                dt_s=novel_s,
                now=now,
                latency_s=age,
            )
            return
        with self._lock:
            if now is not None:
                self._now = float(now)
            moment = self._now
            # The slice is not a new lock vote. Section time waits for that vote
            # so the same samples are not counted twice.
            self._heard_at = moment
            self._signal = window_energy(novel) >= _HEARD
            if not self.follower.locked:
                self._lock_reason = "overlap"
            self._note_levels(novel, moment)
            self._sync_lock_reason()

    def _sync_lock_reason(self) -> None:
        if self.follower.releasing:
            self._lock_reason = "release"
        elif self.follower.locked:
            proof = self._last_proof_at
            holding = proof is not None and self._now - proof >= MIN_NOVEL_S
            self._lock_reason = "hold" if holding else "locked"
        elif self.follower.bpm is None and self._lock_reason in {"locked", "hold", "release"}:
            self._lock_reason = "idle"

    def note_live_input(
        self,
        fresh: list[float],
        sample_rate: int,
        *,
        channels: int = 1,
        callback_at: float | None = None,
    ) -> None:
        """A live block while BPM is off. Levels only: no tempo window and no 4 s history."""
        samples = _finite_samples(fresh)
        with self._lock:
            self._callback_mono = (
                float(callback_at) if callback_at is not None else time.monotonic()
            )
            self._sample_rate = int(sample_rate)
            self._channels = int(channels)
            if self.capture in {"unavailable", "idle"}:
                self.capture = "listening"
            if not samples:
                return
            self._signal = window_energy(samples) >= _HEARD
            self._note_levels(samples, self._now)

    def _note_analysis_failure(self, exc: BaseException) -> None:
        message = f"{type(exc).__name__}: {exc}"[:180]
        with self._lock:
            self._analysis_errors += 1
            self._analysis_error = message
            if self._lock_reason not in {"locked", "hold", "release"}:
                self._lock_reason = "analysis_error"
        if message != self._logged_analysis_error:
            self._logged_analysis_error = message
            logger.warning("audio analysis failed: %s", message)

    def _note_levels(self, samples: list[float], moment: float) -> None:
        if self._diag_at is not None and moment - self._diag_at < DIAG_INTERVAL_S:
            return
        self._diag_at = moment
        self._rms, self._peak, self._clipping = _block_levels(samples)

    def drop_bar_plan(self) -> None:
        self._bar_plan = None
        self._bar_behind_s = 0.0

    @property
    def clock_s(self) -> float:
        return self._now

    def show_factor(self, manual_ratio: float) -> float | None:
        with self._lock:
            if not self.enabled and not self.follower.releasing:
                return None
            if self.scope == "auto":
                return None
            return self.follower.show_factor(self._now, manual_ratio)

    def bar_delta(
        self,
        *,
        episode_index: int,
        episode_time: float,
        duration: float,
        preset_elapsed: float,
        wall: float,
    ) -> float | None:
        """Seconds of episode time to apply this frame, or None when bar is not in charge."""
        with self._lock:
            if self.mode != "bar" or self.scope == "auto" or self.follower.bpm is None:
                return None
            if not self.follower.locked:
                return None
            plan = self._bar_plan
            if plan is None or plan.episode_index != int(episode_index):
                remaining = max(0.05, float(duration) - float(episode_time))
                plan = plan_bar(
                    self.follower.beats_at(self._now),
                    remaining,
                    int(episode_index),
                    float(preset_elapsed),
                )
                self._bar_plan = plan
            beats = self.follower.beats_at(self._now)
            if beats >= plan.target_beat - 1e-6:
                self._bar_plan = None
                return max(0.0, float(duration) - float(episode_time)) + 1e-3
            span = max(1e-6, plan.target_beat - plan.start_beat)
            progress = max(0.0, min(1.0, (beats - plan.start_beat) / span))
            desired = plan.origin_elapsed + progress * plan.remaining_s
            delta = desired - float(preset_elapsed)
            if delta <= 0.0:
                # A short wait absorbs one late beat. A long wait freezes the
                # episode, so the plan is dropped and this frame keeps moving.
                self._bar_behind_s += float(wall)
                if self._bar_behind_s < 0.2:
                    return 0.0
                self._bar_behind_s = 0.0
                self._bar_plan = None
                return float(wall)
            self._bar_behind_s = 0.0
            cap = max(float(wall) * 1.5, float(wall) * tempo_ratio(self.follower.bpm) * 1.5)
            return min(delta, cap)

    def scale_for(self, episode_time_s: float, episode_duration_s: float) -> float | None:
        del episode_time_s, episode_duration_s
        with self._lock:
            if not self.enabled or self.scope == "auto" or self.follower.bpm is None:
                return None
            if not self.follower.locked and not self.follower.releasing:
                return None
            return tempo_ratio(self.follower.bpm)

    def look(
        self,
        fixtures: list[LitFixture],
        placements: object = None,
        *,
        blackout: bool = False,
        manual: bool = False,
        axes_busy: bool = False,
    ) -> StageIntent:
        with self._lock:
            self.follower.advance(self._now)
            phase = self.follower.phase_at(self._now)
            grid = self.follower.grid_phase(self._now)
            self.section.observe(self._now, phase, grid)
            section = self.section.section
            level = self.section.shown
            palette = self.palette
            drop_age = self.section.drop_age
            riser_age = self.section.riser_age
            mix = self.section.mix
            plan_beats = self.follower.beats_at(self._now)
            action_beats = self.follower.action_beats_at(self._now)
            events = self.section.take_events()
            locked = bool(self.follower.locked)
            tempo_held = bool(self.follower.releasing)
            theme = self.cast if self.cast in {"bars", "heads"} else palette
            if self.cast in {"bars", "heads"}:
                theme = self.cast
            elif palette in COLOR_IDS:
                theme = palette
            beat_period = None if self.follower.bpm is None else 60.0 / float(self.follower.bpm)
            episode = self.planner.advance(
                plan_beats,
                section=section,
                level=min(float(level), intensity_of(self.intensity)),
                theme=theme,
                events=events,
                signal_lost=bool(self.section.input_lost),
                blackout=blackout,
                locked=locked,
                tempo_held=tempo_held,
                character=self.character,
                period=self.period,
                palette=colour_of(palette, self.palette),
                wall_s=self._now,
                phase_beats=action_beats,
                fixtures=fixtures,
                texture_id=int(self.section.texture_id),
                drive=bool(self.section.roll_drive) and locked,
                drive_id=int(self.section.drive_id),
                drive_density=float(self.section.roll_density),
                beat_period_s=beat_period,
                hearing=self.section.hearing,
                manual=bool(manual),
            )
            placed = placements
            if (
                blackout
                or self.section.input_lost
                or episode.reason in {"signal_loss", "freewheel"}
            ):
                flash = None
            else:
                flash = self.planner.flash_gate(plan_beats, wall_s=self._now)
            picture = episode.section
            owned = episode.groups
            amount = max(0.0, min(1.0, float(episode.intensity), intensity_of(self.intensity)))
            if self.planner.dimming(self._now):
                amount = 0.0
            chosen = episode.look
            picture_cycle = episode.picture_cycle
            motion = episode.motion_cycle
            colour = colour_of(episode.palette, self.palette)
        stage = build_auto_stage(
            fixtures,
            colour,
            picture,
            phase,
            amount,
            drop_age if picture == "drop" else 0.0,
            riser_age=riser_age if picture == "riser" else 0.0,
            pattern=0,
            mix=mix,
            beats=action_beats,
            cycle_beats=picture_cycle,
            placements=placed,
            look=chosen,
            motion_cycle=motion,
            flash_hold=flash,
            groups=owned,
            actions=episode.actions,
            colour=colour,
            cast=episode.cast,
        )
        if not blackout and not axes_busy:
            from types import SimpleNamespace

            from orng_led.audio.head_motion import HEAD_PATHS_LIVE, preview_heads

            if HEAD_PATHS_LIVE:
                stage = preview_heads(
                    SimpleNamespace(patch=SimpleNamespace(fixtures=fixtures)),
                    stage,
                    beats=float(action_beats),
                    section=str(picture),
                    look=str(chosen),
                    beat_period_s=float(beat_period or 0.5),
                )
        if self.par_rhythm and not blackout:
            stage = lay_par_bed(stage, self._now, amount)
        if not blackout and not self.planner.dimming(self._now):
            stage, spent = consume_accent(
                stage,
                list(fixtures),
                tuple(owned),
                amount,
                now=self._now,
                ready_at=self.section.accent_ready,
            )
            if spent:
                self.section.accent_ready = None
        elif self.section.accent_ready is not None and self._now - float(self.section.accent_ready) > 0.12:
            self.section.accent_ready = None
        return stage

    def operator_next_look(self) -> str:
        """Desk asked for another picture. Does not open USB and does not reset the clock."""
        with self._lock:
            if not (self.enabled and self.scope == "auto"):
                raise ValueError("Кнопка лише в Auto")
            if self.planner.current is None:
                raise ValueError("Ефект ще не почався")
            return self.planner.request_next_look(
                self.follower.beats_at(self._now),
                locked=bool(self.follower.locked),
            )

    def status(self) -> dict[str, object]:
        with self._lock:
            self.follower.advance(self._now)
            bpm = self.follower.bpm if self.follower.locked else None
            phase = self.follower.phase_at(self._now)
            section = self.section.section
            confidence = self.follower.confidence if bpm is not None else 0.0
            locked = self.follower.locked and bpm is not None
            clock = None if self.planner.current is None else self.planner.current.clock
            # No device is not the same as a failed analysis. A named input
            # keeps its signal even if an earlier fault left capture unavailable.
            device_missing = self.capture == "no_device" or (
                self.capture == "unavailable" and not self.device_name
            )
            signal = bool(self._signal) and not device_missing and self.capture != "idle"
            if device_missing:
                sync = "no_input"
            elif not signal:
                sync = "waiting"
            elif self.follower.releasing:
                sync = "hold"
            elif locked:
                sync = "locked"
            elif clock == "freewheel":
                sync = "fallback"
            else:
                sync = "searching"
        return {
            "enabled": self.enabled,
            "scope": self.scope,
            "mode": self.mode,
            "palette": colour_of(self.palette, self.palette),
            "cast": self.cast,
            "character": self.character,
            "intensity": self.intensity,
            "period": self.period if self.period is not None else "auto",
            "par_rhythm": self.par_rhythm,
            "accent": round(float(self.accent), 3),
            "par_note": "PAR — плавний фон" if self.par_rhythm else None,
            "look": None if self.planner.current is None else self.planner.current.look,
            "look_title": (
                None
                if self.planner.current is None
                else (
                    BY_ID[self.planner.current.look].title
                    if self.planner.current.look in BY_ID
                    else self.planner.current.look
                )
            ),
            "section": section,
            "bpm": None if bpm is None else round(float(bpm), 1),
            "beat_phase": round(float(phase), 3),
            "confidence": round(float(confidence), 3),
            "locked": locked,
            "signal": signal,
            "sync": sync,
            "grid": "internal_4" if self.mode == "bar" else None,
            "device": self.device_name,
            "capture": self.capture,
        }

    def _prefs_path(self) -> Path:
        return self.config_root / "location-show" / f"{self.scene_id}.json"

    def _hardware_path(self) -> Path:
        return self.config_root / "audio-input.json"

    def _load(self) -> None:
        self.enabled = False
        self.mode = "smooth"
        self.scope = "preset"
        self.palette = "orange"
        self.cast = "all"
        self.character = "rhythmic"
        self.intensity = "full"
        self.period = None
        path = self._prefs_path()
        data: dict = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}
        elif (self.config_root / "bpm-sync.json").is_file():
            try:
                raw = (self.config_root / "bpm-sync.json").read_text(encoding="utf-8")
                legacy = json.loads(raw)
                scenes = legacy.get("scenes") if isinstance(legacy, dict) else None
                entry = scenes.get(self.scene_id) if isinstance(scenes, dict) else None
                if isinstance(entry, dict):
                    data = {
                        "mode": entry.get("mode"),
                        "scope": entry.get("scope"),
                        "palette": entry.get("palette"),
                    }
            except (OSError, json.JSONDecodeError):
                data = {}
        if data:
            mode = str(data.get("mode") or "smooth")
            self.mode = mode if mode in MODES else "smooth"
            scope = str(data.get("scope") or "preset")
            self.scope = scope if scope in {"preset", "auto"} else "preset"
            palette = str(data.get("palette") or "orange")
            ident, extra = migrate_palette(palette if known_palette(palette) else "orange")
            self.palette = ident
            if extra:
                self.cast = extra
            stored_cast = str(data.get("cast") or self.cast)
            self.cast = stored_cast if stored_cast in CAST_IDS else "all"
            stored_character = str(data.get("character") or "rhythmic")
            self.character = stored_character if stored_character in CHARACTER_IDS else "rhythmic"
            stored_intensity = str(data.get("intensity") or "full")
            self.intensity = stored_intensity if stored_intensity in INTENSITY_IDS else "full"
            self.period = known_period(data.get("period"), self.character)
            if "par_rhythm" in data:
                self.par_rhythm = bool(data.get("par_rhythm"))
            if "accent" in data:
                try:
                    self.accent = max(0.0, min(1.0, float(data.get("accent"))))
                except (TypeError, ValueError):
                    self.accent = 0.0
            self.section.accent_amount = self.accent
        self.planner.character = self.character
        self.planner.period = self.period
        self.planner.palette = colour_of(self.palette, self.palette)
        self.planner.intensity_cap = intensity_of(self.intensity)
        hardware = self._hardware_path()
        if not hardware.is_file():
            return
        try:
            data = json.loads(hardware.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        remembered = data.get("device_id") if isinstance(data, dict) else None
        if isinstance(remembered, str) and remembered:
            self.device_id = remembered

    def _save_prefs(self) -> None:
        path = self._prefs_path()
        payload = {
            "mode": self.mode,
            "scope": self.scope,
            "palette": colour_of(self.palette, self.palette),
            "cast": self.cast,
            "character": self.character,
            "intensity": self.intensity,
            "period": "auto" if self.period is None else self.period,
            "par_rhythm": self.par_rhythm,
            "accent": self.accent,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.warning("location show prefs were not saved", exc_info=True)

    def _save_hardware(self) -> None:
        path = self._hardware_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"device_id": self.device_id}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("audio input was not saved", exc_info=True)

    def _wants_capture(self) -> bool:
        """BPM off does not keep PortAudio open or poll devices."""
        return bool(self.enabled or self.follower.releasing)

    def _loop(self) -> None:
        """Capture thread. Failures here never touch Art-Net, the keyboard, or the desk."""
        stream = None
        chunks: list[float] = []
        history: list[float] = []
        audio_lock = threading.Lock()
        meta: dict[str, float] = {"last": 0.0, "opened": 0.0, "cap": 48000.0}
        rate = 48000
        open_id: str | None = None
        sd: object | None = None
        while not self._stop.is_set():
            self._reap_dead_worker()
            self._schedule_worker_recovery()
            if not self._wants_capture():
                if stream is not None:
                    stream = _close_stream(stream)
                    open_id = None
                    chunks.clear()
                self.capture = "idle"
                self._stop.wait(0.2)
                continue
            if sd is None:
                try:
                    sd = _import_sounddevice()
                except Exception:  # noqa: BLE001 — PortAudio is optional until BPM is on
                    logger.warning("audio capture unavailable", exc_info=True)
                    self.capture = "unavailable"
                    return
            try:
                stream, rate, open_id, history = self._capture_turn(
                    sd,
                    stream,
                    chunks,
                    history,
                    audio_lock,
                    meta,
                    rate,
                    open_id,
                )
            except Exception as exc:  # noqa: BLE001
                message = f"{type(exc).__name__}: {exc}"[:180]
                if message != self._logged_analysis_error:
                    self._logged_analysis_error = message
                    logger.warning("audio capture iteration failed: %s", message)
                self.capture = "unavailable"
                stream = _close_stream(stream)
                open_id = None
            self._stop.wait(0.4)
        _close_stream(stream)

    def _capture_turn(
        self,
        sd: object,
        stream: object | None,
        chunks: list[float],
        history: list[float],
        audio_lock: threading.Lock,
        meta: dict[str, float],
        rate: int,
        open_id: str | None,
    ) -> tuple[object | None, int, str | None, list[float]]:
        devices = _query(sd)
        chosen = choose_input(devices, current_id=self.device_id)
        if chosen is None:
            self.device_name = None
            self.capture = "no_device"
            return _close_stream(stream), rate, None, history
        if chosen.id != self.device_id or chosen.name != self.device_name:
            changed = self.device_id is not None and chosen.id != self.device_id
            self.device_id = chosen.id
            self.device_name = chosen.name
            self._save_hardware()
            if changed:
                self._retire_session("device")
        stale = _stream_stale(stream, meta)
        if stream is None or open_id != chosen.id or stale:
            reopened = stream is not None or stale
            stream = _close_stream(stream)
            chunks.clear()
            opened = _open_line(sd, chosen, chunks, audio_lock, meta)
            if opened is None:
                logger.warning("audio open failed for %s", chosen.name)
                self.capture = "unavailable"
                return None, rate, None, history
            stream, rate = opened
            open_id = chosen.id
            self.capture = "listening"
            history = []
            with self._lock:
                self._device_latency = float(meta.get("device_latency") or 0.0)
                self._history = np.empty(0, dtype=np.float64)
                self._novel = np.empty(0, dtype=np.float64)
                self._unvoted_s = 0.0
                if reopened:
                    self._reconnects += 1
        with audio_lock:
            fresh = list(chunks)
            chunks.clear()
            seen = int(meta.get("overflows") or 0.0)
            counted = int(meta.get("counted_overflows") or 0.0)
        if seen > counted:
            with self._lock:
                self._overflows += seen - counted
            meta["counted_overflows"] = float(seen)
        if fresh:
            captured_at = float(meta.get("last") or time.monotonic())
            channel_count = int(meta.get("channels") or 1)
            if self.enabled or self.follower.releasing:
                self.submit_block(
                    fresh,
                    rate,
                    captured_at=captured_at,
                    channels=channel_count,
                )
            else:
                self.note_live_input(
                    fresh,
                    rate,
                    channels=channel_count,
                    callback_at=captured_at,
                )
        elif _stream_stale(stream, meta):
            self.capture = "unavailable"
        return stream, rate, open_id, history


def _join_dead_process(worker: object) -> None:
    """Reap a dead worker off the renderer thread. Never inspect_tempo here."""
    join = getattr(worker, "join", None)
    alive = getattr(worker, "is_alive", None)
    terminate = getattr(worker, "terminate", None)
    try:
        if join is not None:
            join(timeout=0.2)
        if alive is not None and alive() and terminate is not None:
            terminate()
            if join is not None:
                join(timeout=0.2)
    except Exception:  # noqa: BLE001
        return


def _callback_moment(captured_at: float, *, from_callback: bool) -> float:
    """When the callback fired.

    ``captured_at`` is that monotonic time on the capture thread. A logical
    clock (tests pass ``now=``) is not one: stamp the ingest moment once,
    before analysis. A later failure must not call this again.
    """
    wall = time.monotonic()
    if not from_callback:
        return wall
    candidate = float(captured_at)
    if candidate > wall + 1.0:
        return wall
    return candidate


def _as_block(samples: object) -> np.ndarray:
    """One finite block. Keeps samples in a float array so the frame loop is not copied out."""
    block = np.asarray(samples, dtype=np.float64).reshape(-1)
    if block.size == 0:
        return block
    if not np.isfinite(block).all():
        block = np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0)
    return block


def _finite_samples(samples: list[float]) -> list[float]:
    block = np.asarray(samples, dtype=np.float64).reshape(-1)
    if block.size == 0:
        return []
    if not np.isfinite(block).all():
        block = np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0)
    return block.tolist()


def _block_levels(samples: list[float]) -> tuple[float, float, float]:
    block = np.asarray(samples, dtype=np.float64).reshape(-1)
    block = block[np.isfinite(block)]
    if block.size == 0:
        return 0.0, 0.0, 0.0
    magnitude = np.abs(block)
    return (
        float(np.sqrt(np.mean(block * block))),
        float(np.max(magnitude)),
        float(np.mean(magnitude >= _CLIP)),
    )


def local_diagnostics_allowed(host: str | None) -> bool:
    """The capture snapshot is for the machine that already holds the stream."""
    return host in {"127.0.0.1", "::1"}


def mix_down(indata: object) -> list[float]:
    """Collapse channels without cancelling an inverted pair.

    The mean of left and right is silence when one channel is the negative of
    the other. Rhythm uses the root-mean-square of the channels, which keeps
    that energy. Two identical channels stay at the level of either one.
    Music on only the right input is not silence.
    """
    try:
        block = np.asarray(indata, dtype=np.float64)
    except (TypeError, ValueError):
        return []
    if block.size == 0:
        return []
    block = np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0)
    if block.ndim == 1:
        return block.tolist()
    if block.ndim != 2 or block.shape[0] == 0:
        return []
    if block.shape[1] <= 1:
        return block[:, 0].tolist()
    mixed = np.sqrt(np.mean(block * block, axis=1))
    return mixed.tolist()


def append_capped(
    chunks: list[float], samples: list[float], rate: int, lock: threading.Lock
) -> bool:
    """Append and drop the oldest samples past the cap. True when samples were dropped."""
    cap = max(1, int(rate * MAX_BUFFER_S))
    with lock:
        chunks.extend(samples)
        if len(chunks) > cap:
            del chunks[: len(chunks) - cap]
            return True
    return False


def _stream_stale(stream: object | None, meta: dict[str, float]) -> bool:
    if stream is None:
        return False
    active = getattr(stream, "active", True)
    if active is False:
        return True
    now = time.monotonic()
    opened = float(meta.get("opened") or 0.0)
    last = float(meta.get("last") or 0.0)
    if opened <= 0.0 or now - opened < CALLBACK_STALE_S:
        return False
    return now - last > CALLBACK_STALE_S


def _close_stream(stream: object | None) -> None:
    if stream is None:
        return None
    try:
        stream.stop()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        logger.debug("audio stream stop failed", exc_info=True)
    try:
        stream.close()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        logger.debug("audio stream close failed", exc_info=True)
    return None


def _native_dir() -> Path | None:
    """``native/`` next to the managed release, when this file lives inside one."""
    root = Path(__file__).resolve().parents[2]
    native = root / "native"
    if native.is_dir():
        return native
    return None


_NATIVE_LOAD_ORDER = (
    "libdb-5.3.so",
    "libsamplerate.so.0",
    "libopus.so.0",
    "libasound.so.2",
    "libjack.so.0",
    "libportaudio.so.2",
)


def _arm_portaudio() -> None:
    """Load the bundled arm64 PortAudio before sounddevice asks the system linker."""
    native = _native_dir()
    if native is None:
        return
    libport = _soname(native, "libportaudio.so.2")
    if libport is None:
        return
    previous = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = (
        str(native) if not previous else str(native) + os.pathsep + previous
    )
    mode = getattr(ctypes, "RTLD_GLOBAL", 0)
    for name in _NATIVE_LOAD_ORDER:
        path = _soname(native, name)
        if path is not None:
            ctypes.CDLL(str(path), mode=mode)
    original = ctypes.util.find_library

    def _find(name: str) -> str | None:
        if name in {"portaudio", "libportaudio.so.2", "libportaudio.so"}:
            return str(libport)
        return original(name)

    ctypes.util.find_library = _find  # type: ignore[assignment]


def _soname(directory: Path, name: str) -> Path | None:
    direct = directory / name
    if direct.is_file():
        return direct
    matches = sorted(directory.glob(name + "*"))
    for match in matches:
        if match.is_file():
            return match
    return None


def _import_sounddevice() -> object:
    _arm_portaudio()
    import sounddevice as sd

    return sd


def _open_line(
    sd: object,
    device: InputDevice,
    chunks: list[float],
    lock: threading.Lock,
    meta: dict[str, float],
) -> tuple[object, int] | None:
    """Open the device's own channel count and sample rate. Mix happens in the callback."""
    index = device.index if device.index >= 0 else _maybe_int(device.id)
    rate = 48000
    channels = 2
    if index is not None:
        try:
            info = sd.query_devices(index)  # type: ignore[attr-defined]
            rate = int(float(info.get("default_samplerate") or 48000))
            channels = int(info.get("max_input_channels") or 1)
        except Exception:  # noqa: BLE001
            logger.debug("audio device query failed", exc_info=True)
    if rate < 8000 or rate > 192000:
        rate = 48000
    channels = 1 if channels < 2 else min(channels, 2)
    meta["cap"] = float(int(rate * MAX_BUFFER_S))
    meta["opened"] = time.monotonic()
    meta["last"] = meta["opened"]

    def _on_audio(indata, frames, time_info, status, target=chunks, guard=lock) -> None:  # noqa: ARG001
        try:
            meta["last"] = time.monotonic()
            if status:
                meta["overflows"] = float(meta.get("overflows") or 0.0) + 1.0
            if append_capped(target, mix_down(indata), rate, guard):
                meta["overflows"] = float(meta.get("overflows") or 0.0) + 1.0
        except Exception:  # noqa: BLE001 — a bad buffer must not abort the stream
            return

    order = (channels, 1 if channels != 1 else 2)
    for count in order:
        try:
            stream = sd.InputStream(  # type: ignore[attr-defined]
                device=index,
                channels=count,
                samplerate=rate,
                dtype="float32",
                callback=_on_audio,
            )
            stream.start()
            raw_latency = getattr(stream, "latency", 0.0)
            if isinstance(raw_latency, (tuple, list)) and raw_latency:
                device_latency = float(raw_latency[0] or 0.0)
            else:
                try:
                    device_latency = float(raw_latency or 0.0)
                except (TypeError, ValueError):
                    device_latency = 0.0
            meta["device_latency"] = max(0.0, min(0.25, device_latency))
            meta["channels"] = float(count)
            return stream, rate
        except Exception:  # noqa: BLE001
            logger.debug("audio open channels=%s failed", count, exc_info=True)
    return None


def _maybe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _query(sd: object) -> list[InputDevice]:
    hostapis: list[dict[str, object]] = []
    try:
        hostapis = list(sd.query_hostapis())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        hostapis = []
    found: list[InputDevice] = []
    for index, info in enumerate(sd.query_devices()):  # type: ignore[attr-defined]
        inputs = int(info.get("max_input_channels") or 0)
        if inputs < 1:
            continue
        name = str(info.get("name") or "")
        host_name = ""
        try:
            host_index = int(info.get("hostapi") or 0)
        except (TypeError, ValueError):
            host_index = 0
        if 0 <= host_index < len(hostapis):
            host_name = str(hostapis[host_index].get("name") or "")
        found.append(
            InputDevice(
                id=stable_identity(name, host_name),
                name=name,
                inputs=inputs,
                index=index,
            )
        )
    return found
