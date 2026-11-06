"""Home checks on legally obtained music. Independent tempo, not inspect_tempo.

Not a USB venue feed. The estimator is not fitted to these clips.
"""

from __future__ import annotations

import time
from pathlib import Path

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import INPUT_LOSS_S
from orng_led.audio.bpm import BPM_MAX, BPM_MIN, TEMPO_HOLD_S, TEMPO_RELEASE_S
from orng_led.config.io import load_show_config
from orng_led.engine.clock import FRAME_DT
from orng_led.output.mock import MockTransport
from tests.music_clips import (
    DANCE_120,
    HOUSE_128,
    RATE,
    ensure_clip,
    loop_to,
    published_bpm_from_page_math,
    read_mono,
    scale,
    sparse_phrase,
    with_dropout,
)

HOP = 0.5


def _runtime(tmp_path: Path) -> AppRuntime:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path, show=load_show_config())
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(False)
    runtime.engine.set_master_brightness(1.0)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange", character="rhythmic")
    return runtime


def _stop(runtime: AppRuntime) -> None:
    runtime.bpm._stop_worker()


def _feed(service, samples: list[float], *, hops: int | None = None) -> None:
    hop_n = int(HOP * RATE)
    limit = len(samples) if hops is None else min(len(samples), hops * hop_n)
    cursor = 0
    while cursor + hop_n <= limit:
        block = samples[cursor : cursor + hop_n]
        service.submit_block(block, RATE, captured_at=time.monotonic())
        service.poll(HOP)
        time.sleep(0.02)
        cursor += hop_n


def _wait_lock(service, samples: list[float], *, seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + 14.0
    hop_n = int(HOP * RATE)
    index = 0
    while time.monotonic() < deadline and not service.follower.locked:
        begin = (index * hop_n) % max(hop_n, len(samples) - hop_n)
        service.submit_block(samples[begin : begin + hop_n], RATE, captured_at=time.monotonic())
        service.poll(HOP)
        time.sleep(0.03)
        index += 1
        if index * HOP > seconds + 6.0:
            break
    return bool(service.follower.locked)


def _near_published(got: float, published: float) -> bool:
    if not (BPM_MIN <= got <= BPM_MAX):
        return False
    ratio = got / published
    return any(abs(ratio - harmonic) <= 0.08 for harmonic in (0.5, 2.0 / 3.0, 1.0, 1.5, 2.0))


def test_published_markup_is_not_the_estimator() -> None:
    page_bpm = published_bpm_from_page_math(measures=4.0, seconds=8.0)
    assert page_bpm == DANCE_120.published_bpm
    assert page_bpm == 120.0
    assert "183890" not in "".join(HOUSE_128.urls)
    assert all("546251" in url for url in HOUSE_128.urls)


def test_even_dance_loop_locks_near_published_tempo(tmp_path: Path) -> None:
    path = ensure_clip(DANCE_120)
    audio = loop_to(read_mono(path), 10.0)
    runtime = _runtime(tmp_path)
    runtime.bpm._start_worker()
    try:
        assert _wait_lock(runtime.bpm, audio) is True
        got = float(runtime.bpm.follower.bpm or 0.0)
        assert _near_published(got, DANCE_120.published_bpm), (
            f"estimator={got} published={DANCE_120.published_bpm} source={DANCE_120.page}"
        )
        assert runtime.output.armed is False
    finally:
        _stop(runtime)


def test_dense_house_bed_is_a_second_piece(tmp_path: Path) -> None:
    spec = HOUSE_128
    audio = loop_to(read_mono(ensure_clip(spec)), 10.0)
    runtime = _runtime(tmp_path)
    runtime.bpm._start_worker()
    try:
        locked = _wait_lock(runtime.bpm, audio)
        got = float(runtime.bpm.follower.bpm or 0.0)
        if locked:
            assert _near_published(got, spec.published_bpm), (
                f"bed estimator={got} published={spec.published_bpm} source={spec.page}"
            )
        else:
            raise AssertionError(
                f"dense bed did not lock; published={spec.published_bpm} source={spec.page}"
            )
    finally:
        _stop(runtime)


def test_sparse_section_and_signal_levels(tmp_path: Path) -> None:
    audio = loop_to(read_mono(ensure_clip(DANCE_120)), 8.0)

    def run_gain(label: str, gain: float, expect_lock: bool | None) -> bool:
        runtime = _runtime(tmp_path / label)
        runtime.bpm._start_worker()
        try:
            locked = _wait_lock(runtime.bpm, scale(audio, gain), seconds=8.0)
            if expect_lock is True:
                assert locked is True
                got = float(runtime.bpm.follower.bpm or 0.0)
                assert _near_published(got, DANCE_120.published_bpm), (
                    f"{label} estimator={got} published={DANCE_120.published_bpm}"
                )
            if expect_lock is False:
                assert locked is False
            elif locked:
                got = float(runtime.bpm.follower.bpm or 0.0)
                assert _near_published(got, DANCE_120.published_bpm)
            return locked
        finally:
            _stop(runtime)

    run_gain("full", 1.0, True)
    run_gain("reduced", 0.5, True)
    run_gain("faint", 0.02, False)
    sparse_audio = loop_to(sparse_phrase(audio), 8.0)
    runtime = _runtime(tmp_path / "sparse-clip")
    runtime.bpm._start_worker()
    try:
        sparse_locked = _wait_lock(runtime.bpm, sparse_audio, seconds=6.0)
        if sparse_locked:
            got = float(runtime.bpm.follower.bpm or 0.0)
            assert _near_published(got, DANCE_120.published_bpm)
    finally:
        _stop(runtime)


def test_dropout_releases_and_relocks(tmp_path: Path) -> None:
    audio = loop_to(read_mono(ensure_clip(DANCE_120)), 6.0)
    runtime = _runtime(tmp_path)
    runtime.bpm._start_worker()
    try:
        assert _wait_lock(runtime.bpm, audio) is True
        silence = [0.0] * int(4.0 * RATE)
        _feed(runtime.bpm, silence)
        for _ in range(int((TEMPO_HOLD_S + TEMPO_RELEASE_S + INPUT_LOSS_S + 0.5) / FRAME_DT)):
            runtime.tick(FRAME_DT)
        assert runtime.bpm.follower.locked is False
        assert _wait_lock(runtime.bpm, audio) is True
        got = float(runtime.bpm.follower.bpm or 0.0)
        assert _near_published(got, DANCE_120.published_bpm)
        dropped = with_dropout(audio[: int(4 * RATE)], silence_s=3.5)
        assert len(dropped) > RATE * 10
    finally:
        _stop(runtime)
