"""Audio session generation, stale delivery, and worker recovery.

click_track and injected worker payloads are synthetic. Not a venue feed.
"""

from __future__ import annotations

import time
from pathlib import Path
from queue import Empty

import pytest

import numpy as np

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import analyze_slice, window_energy
from orng_led.audio.bpm import MAX_ANALYSIS_AGE_S, STALE_ANALYSIS_S, click_track, inspect_tempo
from orng_led.audio.planner import AutoAction, AutoEpisode
from orng_led.audio.service import BpmService
from orng_led.audio.tempo_worker import put_latest
from orng_led.config.io import load_show_config
from orng_led.engine.clock import FRAME_DT
from orng_led.output.mock import MockTransport
from orng_led.simulator.decode import decode_simulator_view

RATE = 44100


def _stop(service: BpmService) -> None:
    service._stop_worker()


def _ok_payload(
    service: BpmService,
    *,
    captured_at: float,
    sample_now: float | None,
    generation: int | None = None,
    enqueue_mono: float | None = None,
) -> tuple[object, ...]:
    audio = click_track(120.0, 4.0, RATE)
    reading, reason, detail = inspect_tempo(audio, RATE)
    assert reading is not None
    assert reason == "ok"
    novel = audio[-RATE // 2 :]
    hearing = analyze_slice(novel, RATE)
    energy = window_energy(novel)
    block = np.asarray(novel, dtype=np.float64).reshape(-1)
    peak = float(np.max(np.abs(block))) if block.size else 0.0
    clipping = float(np.mean(np.abs(block) >= 0.98)) if block.size else 0.0
    queued = time.monotonic() if enqueue_mono is None else enqueue_mono
    return (
        "ok",
        reading,
        reason,
        detail,
        captured_at,
        sample_now,
        service._generation if generation is None else generation,
        queued,
        hearing,
        0.5,
        float(energy),
        peak,
        clipping,
        (),
        queued,
        1,
        queued,
    )


def _wait_lock(service: BpmService, samples: list[float], *, seconds: float = 6.0) -> bool:
    hop = 0.5
    blocks: list[list[float]] = []
    cursor = 0.0
    while cursor + hop <= seconds + 1e-9:
        begin = int(cursor * RATE)
        end = int((cursor + hop) * RATE)
        blocks.append(samples[begin:end])
        cursor += hop
    deadline = time.monotonic() + 12.0
    index = 0
    while time.monotonic() < deadline and not service.follower.locked:
        service.submit_block(blocks[index % len(blocks)], RATE, captured_at=time.monotonic())
        service.poll(0.0)
        time.sleep(0.03)
        index += 1
    return bool(service.follower.locked)


def _hold_pulse(runtime: AppRuntime) -> None:
    episode = AutoEpisode(
        ident=1,
        look="pulse",
        groups=("par", "bar", "beam"),
        theme="orange",
        intensity=0.85,
        start_beat=0.0,
        duration_beats=256,
        actions=(AutoAction("picture", "pulse", 1.0),),
        transition="bar",
        effects=(),
        end_early=("signal_loss", "blackout"),
        reason="start",
        section="groove",
    )
    planner = runtime.bpm.planner
    planner.current = episode
    planner.upcoming = None
    planner.advance = lambda *args, **kwargs: episode  # type: ignore[method-assign]
    planner.strobe_at = lambda *args, **kwargs: 0.0  # type: ignore[method-assign]
    runtime.bpm.section.section = "groove"
    runtime.bpm.section.level = 0.85
    runtime.bpm.section.shown = 0.85


def _par_mean(runtime: AppRuntime, frame: list[int]) -> float:
    view = decode_simulator_view(runtime.show, frame)
    pars = [item.intensity for item in view.pars if not item.face]
    return sum(pars) / max(len(pars), 1)


def test_spawn_worker_stale_captured_at_does_not_lock(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._start_worker()
    try:
        assert service._worker_alive()
        now = service.clock_s
        payload = _ok_payload(
            service,
            captured_at=now - 10.0,
            sample_now=now,
            enqueue_mono=time.monotonic(),
        )
        for _ in range(2):
            put_latest(service._results, payload)
            service.poll(0.0)
        assert service.follower.locked is False
        assert service._signal is False
        assert service._heard_at is None
    finally:
        _stop(service)


def test_zero_latency_hint_cannot_hide_capture_age(tmp_path: Path) -> None:
    """Drain used to throw captured_at away and trust a pre-queue latency_s=0."""
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._now = 20.0
    service._start_worker()
    try:
        age = service._result_age(10.0, 20.0, time.monotonic())
        assert age >= 10.0
        assert age > STALE_ANALYSIS_S
        put_latest(
            service._results,
            _ok_payload(service, captured_at=10.0, sample_now=20.0, enqueue_mono=time.monotonic()),
        )
        service.poll(0.0)
        assert service.follower.locked is False
        assert service._signal is False
    finally:
        _stop(service)


def test_delayed_spawn_result_is_dropped_as_stale(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._start_worker()
    try:
        clicks = click_track(120.0, 4.0, RATE)
        captured = time.monotonic()
        service._enqueue_analysis(
            clicks,
            RATE,
            captured_at=captured,
            now=None,
            latency_s=0.0,
            novel=clicks[-RATE:],
            novel_s=0.5,
        )
        # qsize() and get_nowait() can miss a put that is still in the feeder.
        # Take the result with a bounded wait, put it back, then let it age.
        assert service._results is not None
        deadline = time.monotonic() + 4.0
        delivered = None
        while time.monotonic() < deadline and delivered is None:
            try:
                delivered = service._results.get(timeout=0.05)
            except Empty:
                continue
        assert delivered is not None
        service._results.put(delivered)
        time.sleep(STALE_ANALYSIS_S + 0.15)
        service.poll(0.0)
        assert service.follower.locked is False
        assert service.follower.bpm is None
        assert service._last_proof_at is None
        assert service._signal is False
        reasons = [item.get("reason") for item in service._windows]
        assert "stale_result" in reasons
    finally:
        _stop(service)


def test_live_usb_stale_capture_does_not_lock_even_if_computed_now(tmp_path: Path) -> None:
    """captured_at ten seconds ago plus computed_at now is not a lock."""
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._start_worker()
    try:
        captured = time.monotonic() - 10.0
        payload = _ok_payload(
            service,
            captured_at=captured,
            sample_now=None,
            enqueue_mono=time.monotonic() - 0.05,
        )
        computed = time.monotonic()
        payload = payload[:14] + (computed, 7, captured)
        put_latest(service._results, payload)
        service.poll(0.0)
        assert service.follower.locked is False
        assert service._signal is False
        assert any(item.get("reason") == "stale_capture" for item in service._windows)
        assert MAX_ANALYSIS_AGE_S < 10.0
        assert MAX_ANALYSIS_AGE_S >= 1.0
    finally:
        _stop(service)


def test_usb_latest_snapshot_is_not_refused_for_compute_time(tmp_path: Path) -> None:
    """Live USB capture-to-drain includes inspect+pickle. That is not a leftover slot."""
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._start_worker()
    try:
        payload = _ok_payload(
            service,
            captured_at=time.monotonic(),
            sample_now=None,
            enqueue_mono=time.monotonic(),
        )
        captured = time.monotonic() - 0.85
        computed = time.monotonic()
        payload = (
            payload[:4]
            + (captured, None, payload[6], captured)
            + payload[8:14]
            + (computed, 7, captured)
        )
        put_latest(service._results, payload)
        service.poll(0.0)
        assert service._signal is True
        assert service._analysis_mono is not None
        assert service._last_proof_at is not None
        old = _ok_payload(
            service,
            captured_at=time.monotonic(),
            sample_now=None,
            enqueue_mono=time.monotonic(),
        )
        leftover = time.monotonic() - (STALE_ANALYSIS_S + 0.2)
        old = (
            old[:4]
            + (leftover, None, old[6], leftover)
            + old[8:14]
            + (leftover, 8, leftover)
        )
        before = service._analysis_mono
        put_latest(service._results, old)
        service.poll(0.0)
        assert service._analysis_mono == before
        reasons = [item.get("reason") for item in service._windows]
        assert "applied" in reasons
        assert "stale_result" in reasons
    finally:
        _stop(service)


def test_results_before_bpm_off_cannot_lock_after(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._start_worker()
    try:
        born = service._generation
        now = service.clock_s
        payload = _ok_payload(
            service,
            captured_at=now,
            sample_now=now,
            generation=born,
            enqueue_mono=time.monotonic(),
        )
        service.configure(enabled=False)
        assert service._generation != born
        assert service.enabled is False
        put_latest(service._results, payload)
        service.poll(0.0)
        assert service.follower.locked is False
        service.configure(enabled=True)
        for _ in range(2):
            put_latest(service._results, payload)
            service.poll(0.0)
        assert service.follower.locked is False
        stale = _ok_payload(service, captured_at=now, sample_now=now, generation=born)
        for _ in range(2):
            put_latest(service._results, stale)
            service.poll(0.0)
        assert service.follower.locked is False
    finally:
        _stop(service)


def test_scene_change_drops_prior_generation(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room-a")
    service.configure(enabled=True)
    service._start_worker()
    try:
        born = service._generation
        now = service.clock_s
        payload = _ok_payload(service, captured_at=now, sample_now=now, generation=born)
        service.set_scene("room-b")
        assert service._generation != born
        for _ in range(2):
            put_latest(service._results, payload)
            service.poll(0.0)
        assert service.follower.locked is False
        assert service.scene_id == "room-b"
    finally:
        _stop(service)


def test_device_change_drops_prior_generation(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._start_worker()
    try:
        service.device_id = "line-a"
        born = service._generation
        now = service.clock_s
        payload = _ok_payload(service, captured_at=now, sample_now=now, generation=born)
        service.device_id = "line-b"
        service._retire_session("device")
        assert service._generation != born
        for _ in range(2):
            put_latest(service._results, payload)
            service.poll(0.0)
        assert service.follower.locked is False
    finally:
        _stop(service)


def test_dead_worker_recovers_without_blocking_a_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("orng_led.audio.service.WORKER_RESTART_DELAY_S", 0.05)
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._start_worker()
    try:
        assert service._worker_alive()
        old = service._worker
        old_pid = getattr(old, "pid", None)
        old_gen = service._generation
        old.terminate()
        old.join(timeout=1.0)
        started = time.perf_counter()
        for _ in range(10):
            service.poll(1.0 / 30.0)
        assert time.perf_counter() - started < 0.5
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and not service._worker_alive():
            service.poll(0.05)
            time.sleep(0.03)
        assert service._worker_alive()
        assert getattr(service._worker, "pid", None) != old_pid
        assert service._generation != old_gen
        now = service.clock_s
        leftover = _ok_payload(service, captured_at=now, sample_now=now, generation=old_gen)
        for _ in range(2):
            put_latest(service._results, leftover)
            service.poll(0.0)
        assert service.follower.locked is False
        clicks = click_track(120.0, 6.0, RATE)
        assert _wait_lock(service, clicks) is True
        assert service.follower.bpm == pytest.approx(120.0, abs=3)
    finally:
        _stop(service)


def test_spawn_worker_decoded_frame_phase_ignores_stale_result(tmp_path: Path) -> None:
    runtime = AppRuntime.create(
        autostart_loop=False, config_dir=tmp_path, show=load_show_config()
    )
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(False)
    runtime.engine.set_master_brightness(1.0)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange", character="rhythmic")
    runtime.bpm._start_worker()
    try:
        assert runtime.bpm._worker_alive()
        clicks = click_track(120.0, 6.0, RATE)
        assert _wait_lock(runtime.bpm, clicks) is True
        _hold_pulse(runtime)
        times: list[float] = []
        levels: list[float] = []
        phases: list[float] = []
        origin = runtime.engine.clock.time()
        prev = runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s)
        crossings: list[float] = []
        for _ in range(int(2.0 / FRAME_DT)):
            snap = runtime.tick(FRAME_DT)
            now_beats = runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s)
            phase = runtime.bpm.follower.phase_at(runtime.bpm.clock_s)
            times.append(runtime.engine.clock.time() - origin)
            levels.append(_par_mean(runtime, snap.frame))
            phases.append(phase)
            if int(now_beats) > int(prev):
                crossings.append(phase)
            prev = now_beats
        assert crossings
        assert max(min(phase, 1.0 - phase) for phase in crossings) < 0.12
        assert max(levels) > 0.05
        before_phase = runtime.bpm.follower.phase_at(runtime.bpm.clock_s)
        before_action = runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s)
        stale_now = runtime.bpm.clock_s
        put_latest(
            runtime.bpm._results,
            _ok_payload(
                runtime.bpm,
                captured_at=stale_now - 10.0,
                sample_now=stale_now,
                enqueue_mono=time.monotonic(),
            ),
        )
        snap = runtime.tick(FRAME_DT)
        after_action = runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s)
        after_phase = runtime.bpm.follower.phase_at(runtime.bpm.clock_s)
        expected = before_action + (120.0 / 60.0) * FRAME_DT
        assert after_action == pytest.approx(expected, abs=1e-3)
        assert min(abs(after_phase - before_phase), 1.0 - abs(after_phase - before_phase)) < 0.12
        view = decode_simulator_view(runtime.show, snap.frame)
        assert view.pars or view.bars
    finally:
        _stop(runtime.bpm)


def test_spawn_worker_runtime_loop_two_clients_bpm_off_on(tmp_path: Path) -> None:
    """Full loop: real worker, two desk sockets, off/on. Tick time is not the proof."""
    from orng_led.main import create_app
    from fastapi.testclient import TestClient

    runtime = AppRuntime.create(
        autostart_loop=False, config_dir=tmp_path, show=load_show_config()
    )
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(False)
    runtime.engine.set_master_brightness(1.0)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    runtime.bpm._start_worker()
    app = create_app(runtime=runtime, autostart_loop=False)
    try:
        with TestClient(app) as client:
            with (
                client.websocket_connect("/api/ws") as ws1,
                client.websocket_connect("/api/ws") as ws2,
            ):
                hello1 = ws1.receive_json()
                hello2 = ws2.receive_json()
                assert hello1["type"] == "hello" and hello2["type"] == "hello"
                assert hello1["source_id"] != hello2["source_id"]
                clicks = click_track(120.0, 6.0, RATE)
                assert _wait_lock(runtime.bpm, clicks) is True
                fresh = [
                    float(item["delivery_s"])
                    for item in runtime.bpm._windows
                    if item.get("reason") == "applied" and isinstance(item.get("delivery_s"), (int, float))
                ]
                assert fresh, runtime.bpm._windows
                assert min(fresh) >= 0.0
                assert max(fresh) <= STALE_ANALYSIS_S
                assert runtime.bpm.follower.locked is True
                assert runtime.bpm.status()["locked"] is True
                _hold_pulse(runtime)
                delivered: list[float] = []
                show_steps: list[float] = []
                previous_show = runtime.engine.clock.time()
                previous_wall = time.perf_counter()
                sent0 = runtime.output.frames_sent
                for _ in range(30):
                    snap = runtime.tick(FRAME_DT)
                    now_wall = time.perf_counter()
                    now_show = runtime.engine.clock.time()
                    delivered.append(now_wall - previous_wall)
                    show_steps.append(now_show - previous_show)
                    previous_wall = now_wall
                    previous_show = now_show
                    assert snap.frame
                assert runtime.output.frames_sent - sent0 == 30
                assert all(abs(step - FRAME_DT) < 1e-6 for step in show_steps)
                # Delivery cadence is the show clock, not merely that tick() returned.
                assert max(delivered) < 0.25
                one = client.get("/api/state").json()
                two = client.get("/api/state").json()
                assert one["bpm"]["locked"] is True
                assert two["bpm"]["locked"] is True
                assert one["bpm"]["enabled"] is True
                client.post(
                    "/api/commands/bpm-sync",
                    json={
                        "enabled": False,
                        "scope": "auto",
                        "source_id": hello1["source_id"],
                        "client_command_id": "bpm-off-a",
                    },
                )
                off1 = ws1.receive_json()
                off2 = ws2.receive_json()
                assert off1["state"]["bpm"]["enabled"] is False
                assert off2["state"]["bpm"]["enabled"] is False
                runtime.tick(FRAME_DT)
                assert runtime.bpm.follower.locked is False
                client.post(
                    "/api/commands/bpm-sync",
                    json={
                        "enabled": True,
                        "scope": "auto",
                        "palette": "orange",
                        "source_id": hello2["source_id"],
                        "client_command_id": "bpm-on-b",
                    },
                )
                on1 = ws1.receive_json()
                on2 = ws2.receive_json()
                assert on1["state"]["bpm"]["enabled"] is True
                assert on2["state"]["bpm"]["enabled"] is True
                runtime.bpm._windows.clear()
                assert _wait_lock(runtime.bpm, clicks) is True
                assert runtime.bpm.status()["locked"] is True
                assert any(
                    item.get("reason") == "applied"
                    and isinstance(item.get("delivery_s"), (int, float))
                    and float(item["delivery_s"]) <= STALE_ANALYSIS_S
                    for item in runtime.bpm._windows
                )
    finally:
        _stop(runtime.bpm)


def test_manual_fx_and_ordinary_preset_stay_independent(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path, show=load_show_config())
    runtime.engine.set_blackout(True)
    runtime.apply_bpm_sync(enabled=True, mode="smooth", scope="preset")
    runtime.bpm.feed(click_track(120.0, 4.0), RATE, now=runtime.bpm.clock_s)
    preset = runtime.engine.active_preset_id
    runtime.engine.strobe_press()
    runtime.tick(FRAME_DT)
    assert runtime.engine.overlays.strobe_held is True
    runtime.engine.strobe_release()
    assert runtime.engine.overlays.strobe_held is False
    assert runtime.engine.active_preset_id == preset
    assert runtime.engine.overlays.blackout is True


def _error_payload(
    service: BpmService,
    *,
    generation: int,
    captured_at: float,
    sample_now: float | None,
    enqueue_mono: float | None = None,
) -> tuple[object, ...]:
    return (
        "error",
        "RuntimeError: old analysis",
        None,
        None,
        captured_at,
        sample_now,
        generation,
        time.monotonic() if enqueue_mono is None else enqueue_mono,
        None,
        None,
    )


def test_worker_start_failure_does_not_inspect_on_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("orng_led.audio.service.WORKER_RESTART_DELAY_S", 0.0)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("no spawn")

    monkeypatch.setattr("orng_led.audio.tempo_worker.start_analysis_process", boom)
    inspected = {"n": 0}

    def forbidden(*_args: object, **_kwargs: object):
        inspected["n"] += 1
        return None, "few_onsets", {}

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", forbidden)
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._start_worker()
    try:
        assert service._use_worker is True
        assert service._worker_alive() is False
        assert service._worker_died_at is not None
        service.submit_block([0.2] * RATE, RATE, captured_at=1.0, now=1.0)
        assert inspected["n"] == 0
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and service._worker_restarts < 1:
            service.poll(0.0)
            time.sleep(0.02)
        assert service._worker_restarts >= 1
        assert inspected["n"] == 0
        assert service._use_worker is True
        service.feed([0.2] * RATE, RATE, now=1.0)
        assert inspected["n"] == 1
    finally:
        _stop(service)


def test_old_generation_error_is_ignored_before_it_is_applied(tmp_path: Path) -> None:
    from queue import Queue

    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._use_worker = True
    service._results = Queue()
    born = service._generation
    now = service.clock_s
    service._retire_session("scene")
    assert service._generation != born
    put_latest(
        service._results,
        _error_payload(service, generation=born, captured_at=now, sample_now=now),
    )
    service._drain_results()
    assert int(service.diagnostics()["analysis_errors"]) == 0
    put_latest(
        service._results,
        _error_payload(
            service,
            generation=service._generation,
            captured_at=now - 1.0,
            sample_now=now - 1.0,
            enqueue_mono=time.monotonic() - 1.0,
        ),
    )
    service._drain_results()
    assert int(service.diagnostics()["analysis_errors"]) == 0
    put_latest(
        service._results,
        _error_payload(
            service,
            generation=service._generation,
            captured_at=service.clock_s,
            sample_now=service.clock_s,
        ),
    )
    service._drain_results()
    assert int(service.diagnostics()["analysis_errors"]) == 1
    assert service.diagnostics()["analysis_error"] is not None


def test_next_hearing_does_not_evict_a_ready_tempo(tmp_path: Path) -> None:
    """put(ok 120) then put(hearing) must still drain the tempo."""
    from queue import Queue

    from orng_led.audio.auto_show import SliceHearing
    from orng_led.audio.bpm import TempoReading

    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._use_worker = True
    now = time.monotonic()
    service._now = now
    ready = _ok_payload(service, captured_at=now, sample_now=None, enqueue_mono=now)
    ready = (ready[0], TempoReading(120.0, 0.0, 1.0), "ok") + ready[3:]
    hearing = (
        "hearing",
        None,
        "",
        {},
        now,
        None,
        service._generation,
        now,
        SliceHearing(0.2, 0.5, attacks=(0.2,), generation=service._generation),
        0.5,
        0.2,
        0.4,
        0.0,
        (),
        now,
        8,
        now,
    )
    results: Queue = Queue(maxsize=1)
    put_latest(results, ready)
    put_latest(results, hearing)
    service._results = results
    service._drain_results()
    assert [item.get("raw_bpm") for item in service._windows] == [120.0]
    assert any(item.get("reason") != "hearing" for item in service._windows)


def test_spawn_overlap_keeps_an_unread_tempo(tmp_path: Path) -> None:
    """Two live jobs without waiting on each reply. A hearing must not be the only result."""
    from orng_led.audio.bpm import TempoReading

    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._start_worker()
    try:
        now = time.monotonic()
        service._now = now
        ready = _ok_payload(service, captured_at=now, sample_now=None, enqueue_mono=now)
        ready = (ready[0], TempoReading(120.0, 0.0, 1.0), "ok") + ready[3:]
        put_latest(service._results, ready)
        window = np.asarray(click_track(100.0, 2.0, RATE), dtype=np.float64)
        novel = window[-RATE // 2 :]
        mono = time.monotonic()
        service._enqueue_analysis(
            window,
            RATE,
            captured_at=mono,
            now=None,
            latency_s=0.0,
            novel=novel,
            novel_s=float(novel.size / RATE),
        )
        second = np.asarray(click_track(90.0, 2.0, RATE), dtype=np.float64)
        service._enqueue_analysis(
            second,
            RATE,
            captured_at=mono,
            now=None,
            latency_s=0.0,
            novel=second[-RATE // 2 :],
            novel_s=0.5,
        )
        time.sleep(0.05)
        service._drain_results()
        assert 120.0 in [item.get("raw_bpm") for item in service._windows]
    finally:
        _stop(service)


def test_replaced_snapshot_carries_hearing_not_bpm(tmp_path: Path) -> None:
    from queue import Queue

    service = BpmService(tmp_path, "lab")
    now = service.clock_s
    first = _ok_payload(service, captured_at=now, sample_now=now)
    second = _ok_payload(service, captured_at=now, sample_now=now)
    q: Queue = Queue(maxsize=1)
    put_latest(q, first)
    put_latest(q, second)
    got = q.get_nowait()
    assert got[8] is second[8]
    assert any(item is first[8] for item in got[13])
    assert all(item is not second[8] for item in got[13])


def test_poll_skips_pcm_and_does_not_analyze_slice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def boom(*_args: object, **_kwargs: object):
        calls["n"] += 1
        time.sleep(0.12)
        raise AssertionError("analyze_slice on the frame path")

    monkeypatch.setattr("orng_led.audio.service.analyze_slice", boom)
    service = BpmService(tmp_path, "lab")
    service.configure(enabled=True)
    service._use_worker = True
    from queue import Queue

    service._results = Queue(maxsize=1)
    now = service.clock_s
    compact = _ok_payload(service, captured_at=now, sample_now=now)
    started = time.perf_counter()
    put_latest(
        service._results,
        (
            "ok",
            None,
            "ok",
            {},
            now,
            now,
            service._generation,
            time.monotonic(),
            click_track(120.0, 0.5, RATE),
            0.5,
        ),
    )
    service.poll(0.0)
    put_latest(service._results, compact)
    service.poll(0.0)
    elapsed = time.perf_counter() - started
    assert calls["n"] == 0
    assert elapsed < 0.05
    assert service.follower.bpm == pytest.approx(120.0, abs=3)
