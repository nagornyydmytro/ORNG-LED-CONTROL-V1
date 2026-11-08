"""Rhythm estimator: spectral flux, hold, worker. Lab material is not a venue feed.

The repository has no licensed musical wav/mp3 fragments. Cases below are
synthetic. Do not treat them as the club USB line.
"""

from __future__ import annotations

import statistics
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from orng_led.audio.bpm import (
    CONFIDENCE_CAP,
    STALE_ANALYSIS_S,
    TEMPO_HOLD_S,
    TEMPO_RELEASE_S,
    TempoReading,
    _finite_block,
    _flux_is_sparse,
    _fold_period,
    _local_onsets,
    _onset_flux,
    _refine_period,
    _tempo_detail,
    click_track,
    inspect_tempo,
)
from orng_led.audio.service import BpmService

RATE = 44100


def _hit(samples: np.ndarray, start: int, level: float, length: int) -> None:
    end = min(samples.size, start + length)
    if start >= end or start < 0:
        return
    ramp = np.linspace(level, 0.0, end - start, endpoint=False)
    samples[start:end] += ramp


def am_bed(
    bpm: float,
    seconds: float,
    *,
    bed: float,
    pulse: float,
    attack_s: float,
    decay_s: float,
    freq: float,
    rate: int = RATE,
) -> np.ndarray:
    """Amplitude-modulated tone on a constant bed. The 124 BPM regression."""
    count = int(seconds * rate)
    times = np.arange(count, dtype=np.float64) / rate
    envelope = np.full(count, bed, dtype=np.float64)
    period = 60.0 / bpm
    attack_n = max(1, int(round(attack_s * rate)))
    decay_n = max(1, int(round(decay_s * rate)))
    for beat in np.arange(0.0, seconds, period):
        start = int(round(beat * rate))
        if start >= count:
            break
        peak = min(count, start + attack_n)
        envelope[start:peak] = bed + pulse * np.linspace(0.0, 1.0, peak - start, endpoint=False)
        fall = min(count, peak + decay_n)
        if fall > peak:
            envelope[peak:fall] = bed + pulse * np.linspace(1.0, 0.0, fall - peak, endpoint=False)
    return envelope * np.cos(2.0 * np.pi * freq * times)


def layered_rhythm(bpm: float, seconds: float, rate: int = RATE) -> np.ndarray:
    count = int(seconds * rate)
    samples = np.zeros(count, dtype=np.float64)
    times = np.arange(count, dtype=np.float64) / rate
    samples += 0.10 * np.sin(2.0 * np.pi * 55.0 * times)
    period = 60.0 / bpm
    step = int(round(period * rate))
    hat = max(1, int(round(step / 2)))
    for index, start in enumerate(range(0, count, step)):
        _hit(samples, start, 0.42, int(0.07 * rate))
        if index % 2 == 1:
            _hit(samples, start, 0.28, int(0.05 * rate))
        if index % 4 == 2:
            _hit(samples, start + hat // 2, 0.16, 40)
    for start in range(0, count, hat):
        _hit(samples, start, 0.10, int(0.018 * rate))
    return np.clip(samples, -1.0, 1.0)


def _legacy_energy_flux(block: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray, int]:
    hop = 256
    count = (int(block.size) - 1) // hop
    if count < 1:
        empty = np.zeros(max(count, 0), dtype=np.float64)
        return empty, empty.copy(), hop
    framed = block[: count * hop].reshape(count, hop)
    squared = framed * framed
    energies = squared.mean(axis=1)
    previous = np.empty_like(energies)
    previous[0] = float(np.median(energies))
    if energies.size > 1:
        previous[1:] = energies[:-1]
    flux = np.maximum(0.0, energies - previous)
    peak_ref = np.maximum(energies, previous)
    attack = peak_ref > 1e-9
    flux = np.where(attack & (flux < 0.30 * peak_ref), 0.0, flux)
    return flux, framed.argmax(axis=1).astype(np.float64), hop


def legacy_inspect_tempo(samples: object, sample_rate: int):
    """Onset front before this change. Grid fit is the same helper path."""
    empty = _tempo_detail(0)
    block = _finite_block(samples)
    if sample_rate < 8000 or int(block.size) < sample_rate:
        return None, "few_onsets", empty
    centered = block - float(block.mean())
    flux, peaks, hop = _legacy_energy_flux(centered, sample_rate)
    if flux.size < 2 or float(np.max(flux)) < 1e-6:
        return None, "few_onsets", empty
    onsets = _local_onsets(flux, peaks, hop, sample_rate)
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
        and fit[4] >= 0.55
        and not (fit[5] >= 6.0 and fit[3] < 5)
    ]
    if not passing:
        tight = min(fits, key=lambda item: (-item[3], item[1], -item[4]))
        return None, "weak_period", _tempo_detail(
            len(onsets), tight[3], tight[1], tight[4], 60.0 / tight[0]
        )
    passing.sort(key=lambda item: (-item[3], -item[4], item[1]))
    best_period, best_error, last_onset, inliers, coverage, _expected = passing[0]
    periodicity = max(0.0, 1.0 - best_error / 0.04)
    confidence = max(0.0, min(1.0, periodicity * min(1.0, coverage)))
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


def _score(result: tuple, expect_bpm: float | None) -> dict[str, float | str | None]:
    reading, reason, _detail = result
    if expect_bpm is None:
        locked = reason == "ok" and reading is not None and reading.confidence >= 0.55
        return {
            "reason": reason,
            "bpm": None if reading is None else round(float(reading.bpm), 1),
            "conf": None if reading is None else round(float(reading.confidence), 3),
            "false_lock": locked,
            "bpm_err": None,
            "phase_err": None,
        }
    if reading is None or reason != "ok":
        return {
            "reason": reason,
            "bpm": None,
            "conf": None,
            "false_lock": False,
            "bpm_err": None,
            "phase_err": None,
        }
    phase = float(reading.beat_phase) % 1.0
    return {
        "reason": reason,
        "bpm": round(float(reading.bpm), 1),
        "conf": round(float(reading.confidence), 3),
        "false_lock": False,
        "bpm_err": abs(float(reading.bpm) - expect_bpm),
        "phase_err": min(phase, 1.0 - phase),
    }


def _corpus() -> list[tuple[str, np.ndarray, float | None]]:
    rng = np.random.default_rng(3)
    quiet_clicks = np.asarray(click_track(120, 4.0, RATE), dtype=np.float64) * 0.08
    loud_clicks = quiet_clicks * 4.0
    accent = np.asarray(click_track(120, 4.0, RATE), dtype=np.float64) * 0.3
    accent[RATE : RATE + 90] *= 3.0
    sparse = np.asarray(click_track(120, 6.0, RATE), dtype=np.float64)
    step = int(RATE * 0.5)
    sparse[3 * step : 3 * step + 90] = 0.0
    sparse[7 * step : 7 * step + 90] = 0.0
    return [
        ("metronome_120", np.asarray(click_track(120, 4.0, RATE), dtype=np.float64), 120.0),
        ("layered_124", layered_rhythm(124, 4.0), 124.0),
        ("am_bed_124", am_bed(124, 4.0, bed=0.25, pulse=0.20, attack_s=0.08, decay_s=0.07, freq=90.0), 124.0),
        ("quiet_gain", quiet_clicks, 120.0),
        ("loud_gain", loud_clicks, 120.0),
        ("single_accent", accent, 120.0),
        ("sparse_section", sparse, 120.0),
        ("tone_50", 0.25 * np.sin(2.0 * np.pi * 50.0 * np.arange(RATE * 4) / RATE), None),
        ("tone_220", 0.25 * np.sin(2.0 * np.pi * 220.0 * np.arange(RATE * 4) / RATE), None),
        ("noise", rng.uniform(-0.2, 0.2, RATE * 4), None),
        ("silence", np.zeros(RATE * 4, dtype=np.float64), None),
    ]


def test_repo_has_no_musical_recordings() -> None:
    root = Path(__file__).resolve().parents[2]
    found = [
        path
        for path in root.rglob("*")
        if path.suffix.lower() in {".wav", ".mp3", ".flac", ".ogg"}
        and "node_modules" not in path.parts
        and "show-bench" not in path.parts
    ]
    assert found == []


def test_broadband_gate_drops_the_am_bed_spectral_flux_keeps_it() -> None:
    """Same 4 s block: relative hop energy vs lagged spectral flux."""
    block = am_bed(124, 4.0, bed=0.25, pulse=0.20, attack_s=0.08, decay_s=0.07, freq=90.0)
    centered = block - float(block.mean())
    energy, energy_peaks, hop = _legacy_energy_flux(centered, RATE)
    spectral, spec_peaks, _hop = _onset_flux(centered, RATE)
    energy_onsets = _local_onsets(energy, energy_peaks, hop, RATE)
    spec_onsets = _local_onsets(spectral, spec_peaks, hop, RATE)
    assert float(np.max(energy)) == pytest.approx(0.0, abs=1e-12) or len(energy_onsets) < 4
    assert _flux_is_sparse(spectral)
    assert len(spec_onsets) >= 6
    reading, reason, _detail = inspect_tempo(block.tolist(), RATE)
    assert reason == "ok" and reading is not None
    assert reading.bpm == pytest.approx(124, abs=3)
    assert reading.confidence >= 0.55
    assert reading.confidence <= CONFIDENCE_CAP


def test_tone_and_noise_do_not_false_lock() -> None:
    tone = 0.3 * np.sin(2.0 * np.pi * 220.0 * np.arange(RATE * 4) / RATE)
    noise = np.random.default_rng(2).uniform(-0.25, 0.25, RATE * 4)
    for block in (tone, noise):
        reading, reason, _detail = inspect_tempo(block.tolist(), RATE)
        assert reading is None or reading.confidence < 0.55
        assert reason != "ok" or (reading is not None and reading.confidence < 0.55)


def test_estimator_corpus_before_after() -> None:
    rows = []
    for name, block, expect in _corpus():
        before = legacy_inspect_tempo(block.tolist(), RATE)
        after = inspect_tempo(block.tolist(), RATE)
        rows.append((name, expect, _score(before, expect), _score(after, expect)))

    am = next(row for row in rows if row[0] == "am_bed_124")
    assert am[2]["bpm"] is None
    assert am[3]["bpm_err"] is not None and am[3]["bpm_err"] <= 3.0
    assert am[3]["conf"] is not None and am[3]["conf"] >= 0.55

    for name, expect, _before, after in rows:
        if expect is None:
            assert after["false_lock"] is False, name
            continue
        assert after["bpm_err"] is not None, name
        assert after["bpm_err"] <= 3.0, (name, after)
        assert after["conf"] is not None and after["conf"] <= CONFIDENCE_CAP
        assert after["conf"] >= 0.55

    metronome = next(row for row in rows if row[0] == "metronome_120")
    assert metronome[3]["phase_err"] is not None
    assert metronome[3]["phase_err"] < 0.15


def _feed_windows(service: BpmService, samples: np.ndarray, *, start: float, hop: float = 0.5) -> list[float]:
    locked_at: list[float] = []
    seconds = samples.size / RATE
    moment = start
    cursor = 0.0
    while cursor + 1.0 <= seconds + 1e-9:
        end = min(samples.size, int((cursor + 4.0) * RATE))
        begin = max(0, end - 4 * RATE)
        service.feed(samples[begin:end].tolist(), RATE, now=moment, dt_s=hop, latency_s=0.02)
        if service.follower.locked:
            locked_at.append(moment)
        cursor += hop
        moment += hop
    return locked_at


def test_capture_and_recapture_times(tmp_path: Path) -> None:
    clicks = np.asarray(click_track(124, 8.0, RATE), dtype=np.float64)
    service = BpmService(tmp_path / "a", "room")
    first = _feed_windows(service, clicks, start=0.0)
    assert first, "did not lock the metronome"
    assert first[0] <= 1.5

    service.poll(TEMPO_HOLD_S + TEMPO_RELEASE_S + 0.2)
    assert service.follower.bpm is None

    silence = np.zeros(RATE * 4, dtype=np.float64)
    _feed_windows(service, silence, start=20.0)
    assert service.follower.locked is False

    recapture = _feed_windows(service, clicks, start=30.0)
    assert recapture, "did not recapture"
    assert recapture[0] - 30.0 <= 1.5


def test_tempo_change_is_not_pinned(tmp_path: Path) -> None:
    slow = np.asarray(click_track(96, 6.0, RATE), dtype=np.float64)
    fast = np.asarray(click_track(160, 6.0, RATE), dtype=np.float64)
    service = BpmService(tmp_path, "room")
    _feed_windows(service, slow, start=0.0)
    assert service.follower.bpm == pytest.approx(96, abs=3)
    later = _feed_windows(service, fast, start=10.0)
    assert later
    assert service.follower.bpm == pytest.approx(160, abs=4)


def test_stale_window_does_not_rewind_phase(tmp_path: Path) -> None:
    clicks = np.asarray(click_track(120, 4.0, RATE), dtype=np.float64)
    service = BpmService(tmp_path, "room")
    service.feed(clicks.tolist(), RATE, now=1.0, dt_s=0.5, latency_s=0.02)
    service.feed(clicks.tolist(), RATE, now=1.6, dt_s=0.5, latency_s=0.02)
    assert service.follower.locked
    before = service.follower.action_beats_at(1.6)
    service.poll(0.5)
    at = service.clock_s
    extrapolated = service.follower.action_beats_at(at)
    delayed = TempoReading(bpm=80.0, beat_phase=0.9, confidence=0.9)
    service._accept_analysis(
        delayed,
        "ok",
        {"onsets": 8, "inliers": 8, "error": 0.01, "coverage": 1.0, "bpm": 80.0},
        clicks,
        dt_s=0.5,
        now=at,
        latency_s=STALE_ANALYSIS_S + 0.2,
    )
    after = service.follower.action_beats_at(at)
    assert after == pytest.approx(extrapolated, abs=1e-6)
    assert after >= before
    assert service.diagnostics()["lock_reason"] in {"hold", "locked", "pending"}


def test_lock_reasons_name_silence_and_no_period(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.feed([0.0] * RATE * 3, RATE, now=1.0, dt_s=1.0)
    assert service.diagnostics()["lock_reason"] == "quiet"
    tone = (0.25 * np.sin(2.0 * np.pi * 220.0 * np.arange(RATE * 4) / RATE)).tolist()
    service.feed(tone, RATE, now=2.0, dt_s=1.0)
    assert service.diagnostics()["lock_reason"] in {"no_tempo", "pending"}
    assert service.diagnostics()["tempo_reject"] in {"weak_period", "few_onsets", "low_confidence"}


def test_confidence_never_saturates_from_weak_alternatives() -> None:
    reading, reason, _detail = inspect_tempo(click_track(120, 4.0, RATE), RATE)
    assert reason == "ok" and reading is not None
    assert reading.confidence <= CONFIDENCE_CAP
    assert reading.confidence < 1.0


def test_dead_worker_does_not_stop_frames(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.configure(enabled=True)
    service._start_worker()
    try:
        assert service._worker_alive()
        service._worker.terminate()
        service._worker.join(timeout=1.0)
        started = time.perf_counter()
        for _ in range(10):
            service.poll(1.0 / 30.0)
        assert time.perf_counter() - started < 0.5
        service.submit_block([0.2] * RATE, RATE, captured_at=time.monotonic())
        assert service.clock_s >= 0.0
    finally:
        service._stop_worker()


def _pace_polls(service: BpmService, frames: int, work) -> list[float]:
    intervals: list[float] = []
    origin = time.perf_counter()
    last = origin
    for index in range(frames):
        work(index)
        service.poll(1.0 / 30.0)
        now = time.perf_counter()
        intervals.append(now - last)
        last = now
        remain = origin + (index + 1) / 30.0 - now
        if remain > 0.0:
            time.sleep(remain)
    return intervals


def test_worker_keeps_30hz_while_analysis_runs(tmp_path: Path) -> None:
    clicks = np.asarray(click_track(120, 4.0, RATE), dtype=np.float64)
    service = BpmService(tmp_path, "room")
    service.configure(enabled=True)

    stop = threading.Event()

    def hog() -> None:
        while not stop.is_set():
            inspect_tempo(clicks.tolist(), RATE)

    hogger = threading.Thread(target=hog, name="orng-gil-hog", daemon=True)
    hogger.start()
    try:
        inline = _pace_polls(service, 24, lambda _index: None)
    finally:
        stop.set()
        hogger.join(timeout=2.0)

    service._start_worker()
    try:
        assert service._worker_alive()

        def enqueue(index: int) -> None:
            if index % 2:
                return
            service._enqueue_analysis(
                clicks,
                RATE,
                captured_at=time.monotonic(),
                now=None,
                latency_s=0.03,
                novel=clicks[-RATE:],
                novel_s=0.5,
            )

        isolated = _pace_polls(service, 24, enqueue)
    finally:
        service._stop_worker()

    inline_p95 = float(np.percentile(inline, 95))
    worker_p95 = float(np.percentile(isolated, 95))
    # A same-process inspect_tempo holds the GIL around the renderer.
    # The worker process must not.
    assert worker_p95 < 0.050
    assert worker_p95 <= inline_p95 + 0.005
    assert float(np.median(isolated)) < 0.040


def test_bpm_off_does_not_enqueue_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = BpmService(tmp_path, "room")
    service.enabled = False
    service._use_worker = True

    class _Alive:
        def is_alive(self) -> bool:
            return True

    service._worker = _Alive()
    called = {"n": 0}

    def boom(*_args: object, **_kwargs: object) -> None:
        called["n"] += 1
        raise AssertionError("analysis ran while BPM was off")

    monkeypatch.setattr(service, "_enqueue_analysis", boom)
    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", boom)
    service.submit_block([0.2] * RATE, RATE, captured_at=1.0, now=1.0)
    assert called["n"] == 0


def _scale_rms(samples: np.ndarray, target: float) -> np.ndarray:
    rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < 1e-12:
        return samples
    return samples * (target / rms)


def _tempo_bucket(got: float | None, published: float) -> str:
    if got is None:
        return "no_lock"
    ratio = float(got) / float(published)
    if abs(ratio - 1.0) <= 0.04:
        return "exact"
    if any(abs(ratio - harmonic) <= 0.08 for harmonic in (0.5, 2.0)):
        return "half_double"
    return "other"


def test_layered_tempos_score_octave_separately() -> None:
    """Independently marked layered rhythms, not two 120 loops.

    Hats at 90 BPM sit at 180 in-range. The slower covering grid is the beat.
    124 and 150 hats fold out of range, so they already landed on the beat.
    """
    counts = {"exact": 0, "half_double": 0, "other": 0, "no_lock": 0}
    for bpm in (90.0, 124.0, 150.0):
        for rms in (0.01, 0.02):
            audio = _scale_rms(layered_rhythm(bpm, 8.0), rms)
            reading, reason, _detail = inspect_tempo(audio.tolist(), RATE)
            got = None if reading is None or reason != "ok" else float(reading.bpm)
            bucket = _tempo_bucket(got, bpm)
            counts[bucket] += 1
            if bpm == 90.0:
                assert bucket == "exact", f"90 BPM rms={rms} got={got} reason={reason}"
            elif bpm in {124.0, 150.0}:
                assert bucket == "exact", f"{bpm} BPM rms={rms} got={got} reason={reason}"
    assert counts["exact"] == 6
    assert counts["half_double"] == 0
    assert counts["other"] == 0
    assert counts["no_lock"] == 0
    fast = inspect_tempo(click_track(174, 4.0, RATE), RATE)[0]
    assert fast is not None
    assert _tempo_bucket(float(fast.bpm), 174.0) == "exact"
