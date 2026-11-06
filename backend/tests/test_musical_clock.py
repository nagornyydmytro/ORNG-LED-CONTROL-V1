"""Mathematical musical clock. This is not a measurement of the sound card.

Bounds are the constants in ``orng_led.audio.bpm``. They are the contract.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.audio.bpm import (
    BPM_MAX,
    BPM_MIN,
    LOCK_CONFIDENCE,
    MAX_EPISODE_STRETCH,
    MIN_EPISODE_BEATS,
    PHASE_PULL,
    PHASE_SLEW_PER_S,
    TEMPO_HOLD_S,
    TempoFollower,
    TempoReading,
    beat_phase_correction,
    click_track,
    estimate_tempo,
    plan_bar,
)
from orng_led.audio.service import BpmService, mix_down


def _drop_every(samples: list[float], bpm: float, rate: int, every: int) -> list[float]:
    step = max(1, int(rate * 60.0 / bpm))
    out = list(samples)
    for index, start in enumerate(range(0, len(out), step)):
        if index % every != every - 1:
            continue
        for offset in range(min(90, len(out) - start)):
            out[start + offset] = 0.0
    return out


def test_half_and_double_are_not_averaged() -> None:
    follower = TempoFollower()
    follower.push(TempoReading(140, 0.1, 1.0), now=0.0)
    follower.push(TempoReading(140, 0.1, 1.0), now=0.1)
    assert follower.locked is True
    for index, bpm in enumerate((70, 140, 70, 140, 70, 140)):
        follower.push(TempoReading(bpm, 0.1, 1.0), now=float(index + 1))
    assert follower.bpm == pytest.approx(140, abs=3)
    assert follower.locked is True
    weak = TempoFollower()
    refused = weak.push(TempoReading(120, 0.0, LOCK_CONFIDENCE - 0.01), now=0.0)
    assert refused is None
    assert weak.locked is False
    outside = TempoFollower()
    assert outside.choose_tempo(BPM_MIN - 20) is None
    assert outside.choose_tempo(BPM_MAX + 40) is None


def test_confirmed_half_does_not_glide_through_the_middle() -> None:
    """A confirmed half jumps. The next in-between reading must not average it."""
    follower = TempoFollower()
    follower.push(TempoReading(160, 0.0, 1.0), now=0.0)
    follower.push(TempoReading(160, 0.0, 1.0), now=0.5)
    assert follower.locked is True
    for step, bpm in enumerate((80, 80, 80), start=1):
        follower.push(TempoReading(bpm, 0.0, 1.0), now=float(step))
    assert follower.bpm == pytest.approx(80, abs=0.5)
    follower.push(TempoReading(120, 0.0, 1.0), now=4.0)
    assert follower.bpm == pytest.approx(80, abs=0.5)
    for step, bpm in enumerate((160, 160, 160), start=5):
        follower.push(TempoReading(bpm, 0.0, 1.0), now=float(step))
    assert follower.bpm == pytest.approx(160, abs=0.5)


def test_a_real_tempo_change_glides_without_resetting_phase() -> None:
    follower = TempoFollower()
    follower.push(TempoReading(100, 0.2, 1.0), now=0.0)
    follower.push(TempoReading(100, 0.2, 1.0), now=0.1)
    assert follower.locked is True
    for step in range(1, 6):
        follower.push(TempoReading(140, 0.2, 1.0), now=float(step) * 0.5)
    assert follower.bpm is not None
    assert 110 < follower.bpm < 140


def test_phase_correction_is_bounded_and_does_not_drift() -> None:
    follower = TempoFollower()
    follower.push(TempoReading(120, 0.0, 1.0), now=0.0)
    before = follower.beats_at(0.0)
    follower.push(TempoReading(120, 0.9, 1.0), now=0.5)
    assert abs(follower.beats_at(0.5) - (before + 0.5 * 2.0)) <= PHASE_PULL + 1e-6
    rate = 44100
    audio = click_track(120, 20.0, rate)
    clock = TempoFollower()
    moment = 2.0
    while moment <= 16.0:
        start = int((moment - 2.0) * rate)
        end = int(moment * rate)
        reading = estimate_tempo(audio[start:end], rate)
        assert reading is not None and reading.confidence >= LOCK_CONFIDENCE
        clock.push(reading, now=moment)
        before = clock.beats_at(moment)
        clock.push(reading, now=moment)
        assert abs(clock.beats_at(moment) - before) <= PHASE_PULL + 1e-6
        moment += 0.5
    # Estimator bias on a click is already bounded by ±3. The clock must not snap.
    assert clock.bpm == pytest.approx(120, abs=3)
    steady = TempoFollower()
    steady.push(TempoReading(120, 0.0, 1.0), now=0.0)
    assert steady.beats_at(10.0) == pytest.approx(20.0, abs=1e-6)


def test_missed_beats_noise_and_silence_stay_honest() -> None:
    rate = 44100
    missed = _drop_every(click_track(120, 8.0, rate), 120, rate, 4)
    reading = estimate_tempo(missed, rate)
    assert reading is not None
    assert reading.bpm == pytest.approx(120, abs=3)
    assert estimate_tempo([0.0] * rate * 3, rate) is None
    random.seed(1)
    noise = [random.uniform(-0.25, 0.25) for _ in range(rate * 4)]
    noisy = estimate_tempo(noise, rate)
    follower = TempoFollower()
    if noisy is not None:
        follower.push(noisy, now=1.0)
    assert follower.locked is False or (noisy is not None and noisy.confidence < LOCK_CONFIDENCE)


def test_block_size_and_render_rate_keep_the_same_tempo(tmp_path: Path) -> None:
    """Clock math over synthetic blocks. No PortAudio and no sound card."""
    rate = 44100
    audio = click_track(128, 12.0, rate)
    for hop_s, render_hz in ((0.25, 30.0), (0.5, 60.0), (1.0, 30.0)):
        service = BpmService(tmp_path / str(render_hz), "math")
        service.configure(enabled=True, mode="smooth", scope="preset")
        moment = 0.0
        cursor = 0
        while moment < 8.0:
            step = int(hop_s * rate)
            cursor = min(len(audio), cursor + step)
            moment += hop_s
            window = audio[max(0, cursor - rate * 2) : cursor]
            fresh = audio[max(0, cursor - step) : cursor]
            if len(window) >= rate:
                service.feed(
                    window,
                    rate,
                    dt_s=hop_s,
                    now=moment,
                    energy_samples=fresh,
                )
            frames = max(1, int(round(hop_s * render_hz)))
            service.poll(hop_s / frames)
        assert service.follower.locked is True
        assert service.follower.bpm == pytest.approx(128, abs=3)


def test_bar_target_is_chosen_once_and_stays_inside_bounds() -> None:
    plan = plan_bar(0.1, 18.0, 0, 0.0)
    again = plan_bar(0.1, 18.0, 0, 0.0)
    assert plan.target_beat == again.target_beat
    span = plan.target_beat - plan.start_beat
    assert span >= MIN_EPISODE_BEATS
    authored = 18.0 * 2.0
    assert span <= authored * MAX_EPISODE_STRETCH + 4.0
    short = plan_bar(3.95, 0.2, 1, 18.0)
    assert short.target_beat - short.start_beat >= MIN_EPISODE_BEATS
    assert short.target_beat > short.start_beat


def test_beat_correction_never_exceeds_the_slew() -> None:
    step = beat_phase_correction(0.0, 0.5, 2.0, 120.0, 0.05, "pulse")
    assert abs(step) <= PHASE_SLEW_PER_S * 0.05 + 1e-9
    assert beat_phase_correction(0.1, 0.9, 0.17, 120.0, 0.05, "pulse") == 0.0
    assert beat_phase_correction(0.1, 0.2, 2.0, 120.0, 0.05, "glow") == 0.0


def test_effects_are_not_sped_twice_and_blackout_stays(tmp_path: Path) -> None:
    from orng_led.presets.models import EpisodeCard, PresetDocument
    from orng_led.presets.program import YamlPresetProgram

    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    assert runtime.engine.overlays.blackout is True
    episode = EpisodeCard(
        id="e1",
        duration_s=18,
        groups=["all_rear"],
        palette="deep_red",
        intensity=1.0,
        speed=0.5,
        effect="pulse",
        transition="cut",
    )
    document = PresetDocument(id="CLK", label="clk", builtin=False, episodes=[episode])
    runtime.engine.presets["CLK"] = YamlPresetProgram(document)
    runtime.engine.select_preset("CLK", reset_clock=True)
    runtime.apply_bpm_sync(enabled=True, mode="smooth")
    assert runtime.bpm.feed(click_track(120, 4.0), 44100, now=runtime.bpm.clock_s) is not None

    moved: dict[str, float] = {}

    def _turns(bpm: float) -> float:
        runtime.bpm.follower._commit(runtime.bpm.clock_s)
        runtime.bpm.follower.bpm = bpm
        runtime.bpm.follower.locked = True
        runtime.bpm.follower._fresh_until = runtime.bpm.clock_s + 30.0
        runtime.bpm.follower._release_until = None
        start = runtime.engine.effect_phase_turns
        movement = runtime.engine.movement_phase_turns
        for _ in range(40):
            runtime.tick(0.05)
        assert runtime.engine.effect_rate_scale == pytest.approx(1.0)
        assert runtime.engine.bpm_timeline is True
        moved["movement"] = runtime.engine.movement_phase_turns - movement
        return runtime.engine.effect_phase_turns - start

    at_120 = _turns(120)
    move_120 = moved["movement"]
    at_180 = _turns(180)
    move_180 = moved["movement"]
    assert at_120 > 0
    assert at_180 == pytest.approx(at_120 * 1.5, rel=0.08)
    assert at_180 < at_120 * 2.0
    assert move_180 == pytest.approx(move_120 * 1.5, rel=0.08)
    assert runtime.engine.active_preset_id == "CLK"
    assert runtime.engine.overlays.blackout is True


def test_manual_episode_is_kept_and_release_does_not_wait(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.apply_bpm_sync(enabled=True, mode="bar")
    runtime.bpm.feed(click_track(120, 4.0), 44100, now=runtime.bpm.clock_s)
    runtime.tick(0.05)
    before = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0.0).episode_index
    runtime.apply_seek_episode(before + 1 if before == 0 else before - 1)
    after = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0.0).episode_index
    assert after != before
    runtime.tick(0.05)
    assert runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0.0).episode_index == after
    if runtime.bpm._bar_plan is not None:
        assert runtime.bpm._bar_plan.episode_index == after
        span = runtime.bpm._bar_plan.target_beat - runtime.bpm._bar_plan.start_beat
        assert span >= MIN_EPISODE_BEATS
    runtime.engine.bpm_fx = True
    runtime.engine.bpm_beat_phase = 0.5
    runtime.engine.strobe_press()
    runtime.engine.strobe_release()
    assert runtime.engine.overlays.strobe_held is False
    assert runtime.engine.overlays.blackout is True


def test_hold_then_release_returns_the_manual_speed(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.configure(enabled=True, mode="smooth")
    service.follower.push(TempoReading(120, 0.0, 1.0), now=0.0)
    service.follower.push(TempoReading(120, 0.0, 1.0), now=0.0)
    assert service.follower.locked is True
    service._now = 0.0
    service.poll(TEMPO_HOLD_S + 0.1)
    assert service.follower.locked is False
    assert service.follower.bpm is not None
    factor = service.show_factor(2.0)
    assert factor is not None
    service.poll(2.0)
    assert service.follower.bpm is None
    assert service.show_factor(2.0) is None
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.preview_speed = 2.0
    runtime.apply_bpm_sync(enabled=True, mode="beat")
    runtime.bpm.feed(click_track(120, 4.0), 44100, now=runtime.bpm.clock_s)
    runtime.tick(0.05)
    runtime.engine.select_preset(runtime.engine.active_preset_id, reset_clock=False)
    elapsed = runtime.engine.preset_elapsed_s
    runtime.tick(0.05)
    assert runtime.engine.preset_elapsed_s >= elapsed
    assert runtime.engine.active_preset_id != ""


def _phase_error(reading: TempoReading, bpm: float, samples: list[float], rate: int, offset_s: float) -> float:
    period = 60.0 / bpm
    end_s = len(samples) / float(rate)
    true = ((end_s - offset_s) % period) / period
    return (reading.beat_phase - true + 0.5) % 1.0 - 0.5


def test_click_period_is_the_gap_not_the_plateau_edge() -> None:
    """120 used to come back as 118 at confidence 1, loud or quiet."""
    rate = 44100
    loud = click_track(120, 4.0, rate)
    quiet = [sample * 0.02 for sample in loud]
    for audio in (loud, quiet):
        reading = estimate_tempo(audio, rate)
        assert reading is not None
        assert reading.bpm == pytest.approx(120, abs=1)
        assert reading.confidence >= LOCK_CONFIDENCE
        assert abs(_phase_error(reading, 120, audio, rate, 0.0)) <= 0.02
    clock = TempoFollower()
    first = estimate_tempo(loud, rate)
    assert first is not None
    clock.push(first, now=0.0)
    assert clock.locked is False
    clock.push(first, now=0.5)
    assert clock.locked is True
    assert clock.bpm == pytest.approx(120, abs=1)


def test_confirmed_half_tempo_replaces_the_lock() -> None:
    """Three credible 70s after 140 used to glide to 115.5."""
    follower = TempoFollower()
    follower.push(TempoReading(140, 0.0, 1.0), now=0.0)
    follower.push(TempoReading(140, 0.0, 1.0), now=0.0)
    follower.push(TempoReading(70, 0.0, 1.0), now=2.0)
    follower.push(TempoReading(70, 0.0, 1.0), now=2.0)
    before = follower.beats_at(2.0)
    follower.push(TempoReading(70, 0.0, 1.0), now=2.0)
    assert follower.bpm == pytest.approx(70.0, abs=1e-6)
    # The tempo jumps. The beat counter does not. Halving it rewound every deadline.
    assert follower.beats_at(2.0) >= before - 1e-9
    assert follower.bpm != pytest.approx(115.5, abs=1)


def test_audio_half_tempo_reaches_the_follower_without_a_hand_set_bpm() -> None:
    rate = 44100
    clock = TempoFollower()
    fast = click_track(140, 6.0, rate)
    for moment, audio in ((0.0, fast[: rate * 4]), (1.0, fast)):
        reading = estimate_tempo(audio, rate)
        assert reading is not None
        assert reading.bpm == pytest.approx(140, abs=1)
        clock.push(reading, now=moment)
    assert clock.locked is True
    assert clock.bpm == pytest.approx(140, abs=1)
    slow = click_track(70, 8.0, rate)
    for index in range(3):
        reading = estimate_tempo(slow, rate)
        assert reading is not None
        assert reading.bpm == pytest.approx(70, abs=1)
        clock.push(reading, now=2.0 + index)
    assert clock.bpm == pytest.approx(70, abs=1)
    assert abs(clock.bpm - 115.5) > 5


def test_metronome_grid_phase_and_level_stay_inside_one_bpm() -> None:
    for rate in (44100, 48000):
        for bpm in (70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180):
            audio = click_track(float(bpm), 6.0, rate)
            reading = estimate_tempo(audio, rate)
            assert reading is not None
            assert reading.bpm == pytest.approx(bpm, abs=1)
            assert abs(_phase_error(reading, float(bpm), audio, rate, 0.0)) <= 0.02
        for gain in (0.02, 0.2, 1.0):
            audio = [sample * gain for sample in click_track(120, 6.0, rate)]
            reading = estimate_tempo(audio, rate)
            assert reading is not None
            assert reading.bpm == pytest.approx(120, abs=1)
        clipped = [max(-1.0, min(1.0, sample * 4.0)) for sample in click_track(120, 6.0, rate)]
        reading = estimate_tempo(clipped, rate)
        assert reading is not None
        assert reading.bpm == pytest.approx(120, abs=1)
        with_dc = [sample + 0.2 for sample in click_track(120, 6.0, rate)]
        reading = estimate_tempo(with_dc, rate)
        assert reading is not None
        assert reading.bpm == pytest.approx(120, abs=1)
    rate = 44100
    for offset in (0.1, 0.25, 0.4):
        shift = int(offset * rate)
        audio = [0.0] * shift + click_track(120, 6.0, rate)
        audio = audio[: int(6.0 * rate)]
        reading = estimate_tempo(audio, rate)
        assert reading is not None
        assert reading.bpm == pytest.approx(120, abs=1)
        assert abs(_phase_error(reading, 120, audio, rate, offset)) <= 0.02
    for seconds in (2.0, 4.0, 6.0):
        reading = estimate_tempo(click_track(120, seconds, rate), rate)
        assert reading is not None
        assert reading.bpm == pytest.approx(120, abs=1)
    assert estimate_tempo(click_track(120, 1.0, rate), rate) is None


def test_nonperiodic_audio_does_not_lock() -> None:
    rate = 44100
    count = rate * 6
    pure = {
        "dc": [0.2] * count,
        "tone": [0.4 * math.sin(2 * math.pi * 440 * i / rate) for i in range(count)],
        "hum": [0.4 * math.sin(2 * math.pi * 50 * i / rate) for i in range(count)],
    }
    random.seed(1)
    pure["noise"] = [random.uniform(-0.25, 0.25) for _ in range(count)]
    attacks = [0.0] * count
    rng = random.Random(2)
    moment = 0.0
    while moment < 6.0:
        moment += rng.uniform(0.15, 0.9)
        start = int(moment * rate)
        for offset in range(min(90, count - start)):
            attacks[start + offset] = 0.95 * (1.0 - offset / 90.0)
    pure["attacks"] = attacks
    for label, audio in pure.items():
        clock = TempoFollower()
        for index in range(3):
            reading = estimate_tempo(audio, rate)
            if reading is None or reading.confidence < LOCK_CONFIDENCE:
                continue
            clock.push(reading, now=float(index))
        assert clock.locked is False, label


def test_antiphase_channels_still_have_a_tempo() -> None:
    rate = 44100
    clicks = click_track(120, 4.0, rate)
    mixed = mix_down([[sample, -sample] for sample in clicks])
    assert max(abs(sample) for sample in mixed) > 0.5
    reading = estimate_tempo(mixed, rate)
    assert reading is not None
    assert reading.bpm == pytest.approx(120, abs=1)


def test_a_grid_aligned_pause_keeps_the_tempo() -> None:
    rate = 44100
    head = click_track(120, 2.0, rate)
    tail = click_track(120, 3.0, rate)
    audio = head + [0.0] * rate + tail
    reading = estimate_tempo(audio, rate)
    assert reading is not None
    assert reading.bpm == pytest.approx(120, abs=1)
