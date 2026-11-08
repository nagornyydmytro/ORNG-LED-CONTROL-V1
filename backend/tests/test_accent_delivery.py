"""Accents follow the fresh analysis path and do not invent light."""

from __future__ import annotations

import pytest

from orng_led.audio.auto_show import SliceHearing, consume_accent
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view


def _service(tmp_path) -> BpmService:
    service = BpmService(tmp_path, "orng-bar")
    service.configure(enabled=True, scope="auto", accent=1.0, palette="blue", cast="heads")
    return service


def _hearing(**updates) -> SliceHearing:
    fields = {"rms": 0.1, "onset": 0.9}
    fields.update(updates)
    return SliceHearing(**fields)


def test_fresh_analysis_arms_the_accent_the_backlog_used_to_own(tmp_path) -> None:
    service = _service(tmp_path)
    service._accept_analysis(
        None, "few_onsets", {}, [0.0], dt_s=0.2, now=5.0, latency_s=0.0,
        hearing=_hearing(attacks=(0.2,)), energy=0.1,
    )
    assert service.section.accent_ready == 5.0


def test_carried_attack_keeps_its_time_and_does_not_repeat(tmp_path) -> None:
    service = _service(tmp_path)
    service._now = 5.05
    hearing = _hearing(origin_s=5.0, attacks=(0.05,))
    service._apply_pending_hearings((hearing,), dt_s=0.2)
    assert service.section.accent_ready == 5.0
    service._apply_pending_hearings((hearing,), dt_s=0.2)
    assert service.section.accent_ready == 5.0
    service.section.accent_ready = None
    service._now = 6.0
    service._apply_pending_hearings((_hearing(origin_s=5.0, attacks=(0.05,)),), dt_s=0.2)
    assert service.section.accent_ready is None
    service._apply_pending_hearings((_hearing(),), dt_s=0.2)
    assert service.section.accent_ready is None


def _worker_result(
    service: BpmService,
    *,
    captured_at: float,
    novel_s: float,
    attack: float | None,
    generation: int,
    hearing_generation: int | None = None,
    onset: float = 0.9,
    computed_at: float | None = None,
    enqueue_mono: float | None = None,
) -> tuple[object, ...]:
    hearing = _hearing(
        attacks=() if attack is None else (attack,),
        generation=hearing_generation,
        onset=onset,
        rms=0.2,
    )
    stamp = captured_at if computed_at is None else computed_at
    queued = captured_at if enqueue_mono is None else enqueue_mono
    return (
        "ok",
        None,
        "few_onsets",
        {},
        captured_at,
        captured_at,
        generation,
        queued,
        hearing,
        novel_s,
        0.2,
        0.2,
        0.0,
        (),
        stamp,
        1,
        captured_at,
    )


def _lock_at(service: BpmService, moment: float, phase: float) -> None:
    service.follower.locked = True
    service.follower.bpm = 120.0
    service.follower._started = True
    service.follower._at = float(moment)
    service.follower._beats = 0.0
    service.follower._phase = float(phase)
    service._now = float(moment)


def test_late_worker_attack_uses_the_capture_and_is_dropped(tmp_path, monkeypatch) -> None:
    """captured_at=100, window=0.5, attack=0.49, drain=100.8 → attack 99.99, not 100.79."""
    service = _service(tmp_path)
    _lock_at(service, 100.8, 0.4)
    monkeypatch.setattr("orng_led.audio.service.time.monotonic", lambda: 100.8)
    from queue import Queue

    service._results = Queue()
    service._results.put(
        _worker_result(
            service,
            captured_at=100.0,
            novel_s=0.5,
            attack=0.49,
            generation=service._generation,
            computed_at=100.75,
            enqueue_mono=100.8,
        )
    )
    service._drain_results()
    assert service.section.accent_ready is None


def test_phase_is_taken_at_the_attack_not_at_the_drain(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    _lock_at(service, 20.0, 0.0)
    monkeypatch.setattr("orng_led.audio.service.time.monotonic", lambda: 20.0)
    from queue import Queue

    service._results = Queue()
    service._results.put(
        _worker_result(
            service,
            captured_at=20.0,
            novel_s=0.5,
            attack=0.40,
            generation=service._generation,
        )
    )
    service._drain_results()
    assert service.section.accent_ready == pytest.approx(19.90)


def test_overflow_keeps_the_original_attack_and_generation(tmp_path, monkeypatch) -> None:
    from queue import Queue

    from orng_led.audio.tempo_worker import put_latest

    service = _service(tmp_path)
    _lock_at(service, 30.05, 0.4)
    monkeypatch.setattr("orng_led.audio.service.time.monotonic", lambda: 30.05)
    monkeypatch.setattr("orng_led.audio.tempo_worker.time.monotonic", lambda: 30.05)
    results: Queue = Queue(maxsize=1)
    previous = service._generation
    put_latest(
        results,
        _worker_result(
            service,
            captured_at=30.0,
            novel_s=0.5,
            attack=0.49,
            generation=previous,
            hearing_generation=previous,
        ),
    )
    service._retire_session("device")
    assert service._generation != previous
    put_latest(
        results,
        _worker_result(
            service,
            captured_at=30.05,
            novel_s=0.5,
            attack=None,
            generation=service._generation,
            onset=0.1,
        ),
    )
    service._results = results
    service._drain_results()
    assert service.section.accent_ready is None


def test_overflow_converts_the_carried_attack(tmp_path, monkeypatch) -> None:
    from queue import Queue

    from orng_led.audio.tempo_worker import put_latest

    service = _service(tmp_path)
    _lock_at(service, 10.0, 0.4)
    monkeypatch.setattr("orng_led.audio.service.time.monotonic", lambda: 30.05)
    monkeypatch.setattr("orng_led.audio.tempo_worker.time.monotonic", lambda: 30.05)
    results: Queue = Queue(maxsize=1)
    generation = service._generation
    put_latest(
        results,
        _worker_result(
            service,
            captured_at=30.0,
            novel_s=0.5,
            attack=0.49,
            generation=generation,
            hearing_generation=generation,
        ),
    )
    put_latest(
        results,
        _worker_result(
            service,
            captured_at=30.05,
            novel_s=0.5,
            attack=None,
            generation=generation,
            onset=0.1,
            enqueue_mono=30.05,
        ),
    )
    service._results = results
    service._drain_results()
    assert service.section.accent_ready == pytest.approx(9.94)


def test_show_clock_converts_a_monotonic_attack(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    _lock_at(service, 50.0, 0.4)
    monkeypatch.setattr("orng_led.audio.service.time.monotonic", lambda: 100.8)
    from queue import Queue

    service._results = Queue()
    service._results.put(
        _worker_result(
            service,
            captured_at=100.0,
            novel_s=0.5,
            attack=0.49,
            generation=service._generation,
            computed_at=100.7,
        )
    )
    service._drain_results()
    assert service.section.accent_ready is None


def test_old_generation_and_disabled_accent_do_not_arm(tmp_path) -> None:
    service = _service(tmp_path)
    service._generation = 4
    service._now = 3.0
    service._apply_pending_hearings((_hearing(origin_s=3.0, generation=3),), dt_s=0.1)
    assert service.section.accent_ready is None
    service.configure(accent=0.0)
    service._accept_analysis(
        None, "few_onsets", {}, [0.0], dt_s=0.2, now=3.0, latency_s=0.0,
        hearing=_hearing(), energy=0.1,
    )
    assert service.section.accent_ready is None
    service._retire_session("device")
    service.configure(accent=1.0)
    service._now = 4.0
    service._apply_pending_hearings((_hearing(origin_s=4.0, generation=4),), dt_s=0.1)
    assert service.section.accent_ready is None


def test_zero_accent_lights_nobody_and_live_path_keeps_the_palette(tmp_path) -> None:
    show = load_show_config()
    service = _service(tmp_path)
    fixtures = list(show.patch.fixtures)
    service._accept_analysis(
        None, "few_onsets", {}, [0.0], dt_s=0.2, now=2.0, latency_s=0.0,
        hearing=_hearing(), energy=0.1,
    )
    stage = service.look(fixtures, show.layout.placements)
    motion = {
        fixture.id: BeamMotionState.from_home(fixture.spatial.home_pan, fixture.spatial.home_tilt)
        for fixture in fixtures
        if fixture.kind is FixtureKind.BEAM
    }
    view = decode_simulator_view(show, render_stage(show, stage, motion))
    assert all(item.intensity <= 0.02 for item in view.faces)
    lit = [item for item in view.beams if item.dimmer > 0.02]
    heads = {item.id for item in lit if item.id in {"beam_left", "beam_right"}}
    assert len(heads) != 1
    assert all(item.b >= item.r for item in lit)
    from orng_led.audio.palettes import rgbw_of
    from orng_led.engine.intents import BeamIntent, StageIntent

    blue = rgbw_of("blue")
    dark = StageIntent(
        fixtures={
            "beam_left": BeamIntent(dimmer=0.0, color=blue),
            "beam_right": BeamIntent(dimmer=0.0, color=blue),
        }
    )
    silent, spent = consume_accent(
        dark, fixtures, ("beam",), 0.0, now=2.0, ready_at=2.0,
    )
    assert spent is True
    decoded = decode_simulator_view(show, render_stage(show, silent, motion))
    assert all(item.dimmer <= 0.02 for item in decoded.beams)
    assert all(item.r <= 0.05 for item in decoded.beams)
