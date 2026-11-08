"""PCM scale: loudness does not retune a groove, and an extra attack reaches a frame.

Synthetic clicks, tones, noise and fades are built here. Musical files are read
only when a local cache already exists; this test does not download them.
PAR rhythm stays off: that bed is not a second BPM. An extra attack lights one
dark fixture of the cast. It does not isolate an instrument.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from orng_led.audio.auto_show import SectionTracker, analyze_slice
from orng_led.audio.bpm import click_track, inspect_tempo
from orng_led.audio.service import BpmService
from orng_led.audio import tempo_worker
from orng_led.audio.tempo_worker import analysis_worker
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

RATE = 44100
HOP_S = 0.25


def _scale(samples: np.ndarray, rms: float) -> tuple[np.ndarray, float]:
    block = np.asarray(samples, dtype=np.float64).reshape(-1)
    current = float(np.sqrt(np.mean(block * block)))
    gain = float(rms) / max(current, 1e-12)
    out = block * gain
    peak = float(np.max(np.abs(out)))
    assert peak < 0.98, peak
    return out, peak


def _stream(samples: np.ndarray, *, bpm: float | None = None) -> SectionTracker:
    ear = SectionTracker()
    ear.accent_amount = 1.0
    total = int(samples.size)
    hop = int(HOP_S * RATE)
    cursor = 0
    while cursor < total:
        end = min(total, cursor + hop)
        piece = samples[cursor:end]
        if piece.size < hop:
            break
        heard = analyze_slice(piece, RATE)
        period = None if bpm is None else 60.0 / float(bpm)
        ear.push(heard.rms, HOP_S, onset=heard.onset, hearing=heard, beat_period_s=period)
        cursor = end
    return ear


def _service(tmp_path: Path) -> BpmService:
    service = BpmService(tmp_path, "orng-bar")
    service.configure(
        enabled=True,
        scope="auto",
        accent=1.0,
        palette="orange",
        cast="all",
        par_rhythm=False,
    )
    return service


def _pose(show: object) -> dict[str, BeamMotionState]:
    return {
        fixture.id: BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
        for fixture in show.patch.fixtures
        if fixture.kind is FixtureKind.BEAM
    }


def _levels(show: object, stage) -> dict[tuple[str, str], float]:
    view = decode_simulator_view(show, render_stage(show, stage, _pose(show)))
    found: dict[tuple[str, str], float] = {}
    for item in view.pars:
        found[("par", item.id)] = float(item.intensity)
    for item in view.bars:
        found[("bar", item.id)] = float(item.dimmer)
    for item in view.beams:
        found[("beam", item.id)] = float(item.dimmer)
    for item in view.faces:
        found[("face", item.id)] = float(item.intensity)
    return found


def _clicks(length: float, rms: float, *, extra_at: float | None = None) -> tuple[np.ndarray, float]:
    step = 60.0 / 120.0
    total = int(round(length * RATE))
    buf = np.zeros(total, dtype=np.float64)
    starts = [at for at in np.arange(0.0, length - 0.02, step)]
    if extra_at is not None:
        starts.append(float(extra_at))
    for at in starts:
        start = int(round(at * RATE))
        if start < 0 or start >= total:
            continue
        width = min(90, total - start)
        ramp = np.linspace(1.0, 0.0, width, endpoint=False)
        buf[start : start + width] += ramp
    return _scale(buf, rms)


class _Worker:
    def __init__(self, service: BpmService) -> None:
        self.service = service
        self.jobs: queue.Queue = queue.Queue(maxsize=1)
        self.results: queue.Queue = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.slow = False
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.settled = threading.Event()
        self._real_put = tempo_worker.put_latest
        self.thread = threading.Thread(
            target=analysis_worker,
            args=(self.jobs, self.results, self.stop),
            name="orng-tempo-test",
            daemon=True,
        )
        service._results = self.results

    def __enter__(self) -> _Worker:
        def _watch(queue_out, item):
            placed = self._real_put(queue_out, item)
            if isinstance(item, tuple) and item and item[0] in {"ok", "error"}:
                self.settled.set()
            return placed

        tempo_worker.put_latest = _watch
        self.thread.start()
        return self

    def __exit__(self, *_) -> None:
        tempo_worker.put_latest = self._real_put
        self.stop.set()
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            pass
        self.thread.join(timeout=2.0)

    def submit(self, samples: np.ndarray, *, show_end: float, novel_s: float = 0.5) -> float:
        novel_n = int(round(novel_s * RATE))
        novel = np.asarray(samples[-novel_n:], dtype=np.float64)
        mono = time.monotonic()
        job = (
            np.asarray(samples, dtype=np.float32),
            RATE,
            mono,
            mono,
            np.asarray(novel, dtype=np.float32),
            float(novel.size / RATE),
            int(self.service._generation),
            mono,
            7,
        )
        self.entered.clear()
        self.finished.clear()
        self.settled.clear()
        self.jobs.put(job)
        self.service._now = float(show_end)
        return mono

    def _apply(self, item: tuple[object, ...]) -> None:
        holder: queue.Queue = queue.Queue()
        holder.put(item)
        self.service._results = holder
        self.service._drain_results()
        self.service._results = self.results

    def drain_hearing(self, timeout: float = 1.0) -> tuple[object, ...]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                item = self.results.get_nowait()
            except queue.Empty:
                time.sleep(0.005)
                continue
            assert item[0] == "hearing", item[0]
            assert not self.finished.is_set()
            assert self.entered.wait(timeout=0.2)
            assert not self.finished.is_set()
            self._apply(item)
            return item
        raise AssertionError("hearing did not arrive before tempo")

    def drain_until_tempo(self, timeout: float = 4.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                item = self.results.get_nowait()
            except queue.Empty:
                time.sleep(0.01)
                continue
            kind = item[0]
            self._apply(item)
            if kind in {"ok", "error"}:
                return
        raise AssertionError("tempo snapshot did not settle")


def test_level_does_not_retune_a_click_track() -> None:
    """Synthetic click_track. One groove at three gains, including the reported trio."""
    raw = np.asarray(click_track(120.0, 2.0, RATE), dtype=np.float64)
    heard: list[str] = []
    sections: list[str] = []
    for rms in (0.009, 0.018, 0.036):
        block, peak = _scale(raw, rms)
        ear = _stream(block, bpm=120.0)
        slice_h = analyze_slice(block[: int(0.5 * RATE)], RATE)
        unit = slice_h.onset / max(slice_h.rms * slice_h.rms, 1e-8)
        print(
            f"PCM synthetic click_track rms={rms:.3f} peak={peak:.3f} "
            f"onset={slice_h.onset:.5f} unit={unit:.2f} "
            f"hearing={ear.hearing} section={ear.section}"
        )
        heard.append(ear.hearing)
        sections.append(ear.section)
        assert peak < 0.98
    assert heard == ["rhythmic", "rhythmic", "rhythmic"]
    assert sections == ["groove", "groove", "groove"]


def test_smooth_signals_do_not_become_groove_or_accents(tmp_path: Path) -> None:
    """Synthetic. A fade, a tone and noise stay smooth. Quiet noise is no signal."""
    tone = np.sin(2.0 * np.pi * 440.0 * np.arange(int(2.0 * RATE)) / RATE)
    ramp = np.linspace(0.15, 1.0, tone.size)
    noise = np.random.default_rng(7).standard_normal(tone.size)
    cases = {
        "tone": _scale(tone, 0.05)[0],
        "fader": _scale(tone * ramp, 0.05)[0],
        "noise": _scale(noise, 0.02)[0],
    }
    service = _service(tmp_path)
    with _Worker(service) as worker:
        for name, block in cases.items():
            ear = _stream(block)
            slice_h = analyze_slice(block[: int(0.5 * RATE)], RATE)
            unit = slice_h.onset / max(slice_h.rms * slice_h.rms, 1e-8)
            print(
                f"PCM synthetic {name} rms={slice_h.rms:.4f} onset={slice_h.onset:.5f} "
                f"unit={unit:.2f} hearing={ear.hearing} section={ear.section}"
            )
            assert ear.hearing == "melodic"
            assert ear.section != "drop"
            assert ear.section != "riser"
            worker.slow = False
            worker.submit(block, show_end=2.0)
            worker.drain_until_tempo()
            assert service.section.accent_ready is None
    quiet = _stream(_scale(noise, 0.002)[0])
    assert quiet.hearing in {"silence", "absent"}
    print(f"PCM synthetic noise-floor hearing={quiet.hearing}")


def _end_for_phase(service: BpmService, gap: float, *, offbeat: bool) -> float:
    """A show time whose attack, one ``gap`` before the end, is on or off the grid."""
    start = float(service._now)
    for step in np.linspace(0.2, 1.2, 80):
        end = start + float(step)
        phase = float(service._phase_at(end - gap)) % 1.0
        frac = min(phase, 1.0 - phase)
        if offbeat and frac >= 0.28:
            return end
        if not offbeat and frac <= 0.05:
            return end
    raise AssertionError("no grid moment inside the tempo hold")


def _offbeat_end(service: BpmService, gap: float) -> float:
    return _end_for_phase(service, gap, offbeat=True)


def _arm_clicks(service: BpmService, worker: _Worker) -> None:
    # Two agreeing windows. The first starts the clock; the second may lock.
    for end in (2.0, 4.0):
        block, _peak = _clicks(2.0, 0.03)
        worker.slow = False
        worker.submit(block, show_end=end)
        assert worker.settled.wait(timeout=4.0)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not worker.results.empty():
            service._drain_results()
            time.sleep(0.01)
        service._drain_results()
    assert service.follower.locked
    assert service.follower.bpm is not None
    print(
        f"PCM synthetic lock bpm={service.follower.bpm:.2f} "
        f"phase={service._phase_at(service._now):.3f}"
    )


def test_extra_attack_reaches_a_decoded_frame(tmp_path: Path, monkeypatch) -> None:
    """Synthetic clicks through the worker. No injected onset, BPM, phase, or accent_ready."""
    real = inspect_tempo

    def _maybe_slow(samples, rate):
        worker = _maybe_slow.worker
        if worker is not None and worker.slow:
            worker.entered.set()
            time.sleep(0.30)
        if worker is not None:
            worker.finished.set()
        return real(samples, rate)

    _maybe_slow.worker = None
    monkeypatch.setattr("orng_led.audio.bpm.inspect_tempo", _maybe_slow)
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    for rms in (0.012, 0.022, 0.032):
        root = tmp_path / f"level-{rms:.3f}"
        root.mkdir()
        service = _service(root)
        with _Worker(service) as worker:
            _maybe_slow.worker = worker
            _arm_clicks(service, worker)
            gap = 0.06
            show_end = _offbeat_end(service, gap)
            block, peak = _clicks(2.0, rms, extra_at=2.0 - gap)
            assert peak < 0.98
            novel = block[-int(0.5 * RATE) :]
            slice_h = analyze_slice(novel, RATE)
            unit = slice_h.onset / max(slice_h.rms * slice_h.rms, 1e-8)
            service._now = show_end
            service.look(fixtures, show.layout.placements)
            before = _levels(show, service.look(fixtures, show.layout.placements))
            worker.slow = True
            worker.submit(block, show_end=show_end)
            worker.drain_hearing()
            ready = service.section.accent_ready
            assert ready is not None
            age = float(service._now) - float(ready)
            phase = float(service._phase_at(float(ready))) % 1.0
            after = _levels(show, service.look(fixtures, show.layout.placements))
            assert service.section.accent_ready is None
            jumped = [
                (kind, ident, round(before[(kind, ident)], 3), round(level, 3))
                for (kind, ident), level in after.items()
                if kind != "face" and level > before[(kind, ident)] + 0.05
            ]
            faces = [
                ident
                for (kind, ident), level in after.items()
                if kind == "face" and abs(level - before[(kind, ident)]) > 0.02
            ]
            print(
                f"PCM synthetic extra rms={rms:.3f} peak={peak:.3f} "
                f"onset={slice_h.onset:.5f} unit={unit:.2f} age={age:.4f}s "
                f"phase={phase:.3f} frame={jumped}"
            )
            assert 0.0 < age <= 0.12
            assert min(phase, 1.0 - phase) >= 0.12
            assert unit >= 4.0
            assert jumped
            assert faces == []
            assert service.par_rhythm is False


def test_tempo_wait_drops_an_attack_older_than_120ms(tmp_path: Path, monkeypatch) -> None:
    """The hearing is queued during inspect_tempo. Waiting for tempo makes it stale."""
    real = inspect_tempo

    def _maybe_slow(samples, rate):
        worker = _maybe_slow.worker
        if worker is not None and worker.slow:
            worker.entered.set()
            time.sleep(0.30)
        if worker is not None:
            worker.finished.set()
        return real(samples, rate)

    _maybe_slow.worker = None
    monkeypatch.setattr("orng_led.audio.bpm.inspect_tempo", _maybe_slow)
    service = _service(tmp_path)
    with _Worker(service) as worker:
        _maybe_slow.worker = worker
        _arm_clicks(service, worker)
        length = 2.0
        gap = 0.07
        block, _peak = _clicks(length, 0.02, extra_at=length - gap)
        worker.slow = True
        captured = worker.submit(block, show_end=_offbeat_end(service, gap))
        assert worker.settled.wait(timeout=2.0)
        late = worker.results.get_nowait()
        assert late[0] == "ok"
        worker._apply(late)
        age = time.monotonic() - captured + gap
        print(f"PCM synthetic late-accent age={age:.4f}s ready={service.section.accent_ready}")
        assert age > 0.12
        assert service.section.accent_ready is None

        # A kick on the follower grid, fresh enough, still is not an extra accent.
        grid, _peak = _clicks(length, 0.02, extra_at=length - 0.03)
        worker.slow = True
        worker.submit(grid, show_end=_end_for_phase(service, 0.03, offbeat=False))
        worker.drain_hearing()
        grid_age = None
        if service.section.accent_ready is not None:
            grid_age = float(service._now) - float(service.section.accent_ready)
        print(f"PCM synthetic grid-kick age={grid_age} ready={service.section.accent_ready}")
        assert service.section.accent_ready is None
        worker.drain_until_tempo()

        # Off-grid, but the attack itself is older than the freshness window.
        old, _peak = _clicks(length, 0.02, extra_at=length - 0.28)
        worker.slow = True
        worker.submit(old, show_end=_offbeat_end(service, 0.28))
        worker.drain_hearing()
        old_age = None
        if service.section.accent_ready is not None:
            old_age = float(service._now) - float(service.section.accent_ready)
        print(f"PCM synthetic old-attack age={old_age} ready={service.section.accent_ready}")
        assert service.section.accent_ready is None


def test_off_on_scene_and_repeat_keep_the_attack_time(tmp_path: Path, monkeypatch) -> None:
    real = inspect_tempo

    def _maybe_slow(samples, rate):
        worker = _maybe_slow.worker
        if worker is not None and worker.slow:
            worker.entered.set()
            time.sleep(0.30)
        if worker is not None:
            worker.finished.set()
        return real(samples, rate)

    _maybe_slow.worker = None
    monkeypatch.setattr("orng_led.audio.bpm.inspect_tempo", _maybe_slow)
    service = _service(tmp_path)
    with _Worker(service) as worker:
        _maybe_slow.worker = worker
        _arm_clicks(service, worker)
        block, _peak = _clicks(2.0, 0.02, extra_at=2.0 - 0.07)
        worker.slow = True
        worker.submit(block, show_end=6.0)
        item = worker.drain_hearing()
        first = service.section.accent_ready
        assert first is not None
        worker._apply(item)
        assert service.section.accent_ready == first
        worker.drain_until_tempo()
        born = int(service._generation)
        service.configure(enabled=False)
        service.configure(enabled=True)
        assert service._generation != born
        stale = tuple(item)
        assert stale[6] == born
        worker._apply(stale)
        service._now = 6.0
        assert service.section.accent_ready is None
        service.set_scene("lab")
        assert service.section.accent_ready is None
        service.configure(accent=1.0, enabled=True, scope="auto")
        worker._apply(stale)
        assert service.section.accent_ready is None
        print(
            f"PCM synthetic kept attack={first:.4f} "
            f"dropped generation={born} scene={service.scene_id}"
        )


def test_cached_music_keeps_its_class_across_gain() -> None:
    """Musical recordings, only if already cached. Not a venue take and not a second BPM."""
    from tests.music_clips import DANCE_120, HOUSE_128, cache_dir, read_mono

    present = []
    for spec in (DANCE_120, HOUSE_128):
        path = cache_dir() / f"{spec.ident}.wav"
        if path.is_file() and path.stat().st_size > 8000:
            present.append((spec, read_mono(path)))
    if not present:
        pytest.skip("musical cache is not on this machine")
    for spec, samples in present:
        block = np.asarray(samples[: RATE * 2], dtype=np.float64)
        peak = float(np.max(np.abs(block)))
        block = block / max(peak, 1e-9) * 0.4
        classes = []
        sections = []
        for gain in (0.45, 1.0):
            scaled, scaled_peak = _scale(block, float(np.sqrt(np.mean((block * gain) ** 2))))
            ear = _stream(scaled)
            classes.append(ear.hearing)
            sections.append(ear.section)
            print(
                f"PCM musical {spec.ident} gain={gain:.2f} peak={scaled_peak:.3f} "
                f"hearing={ear.hearing} section={ear.section} role={spec.role}"
            )
        assert classes[0] == classes[1]
        # The even dance loop keeps a groove. The dense bed stays smooth.
        # Neither reading is a second BPM; PAR rhythm is a separate slow bed.
        if spec.role == "even_dance":
            assert classes[0] == "rhythmic"
        if spec.role == "dense_bed":
            assert classes[0] == "melodic"
        assert "drop" not in sections
        assert "riser" not in sections
