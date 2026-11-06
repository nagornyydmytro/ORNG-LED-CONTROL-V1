"""BPM sync stays inside one preset and ignores the manual speed controls."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import (
    PALETTE_IDS,
    SectionTracker,
    analyze_slice,
    build_auto_stage,
    window_energy,
)
from orng_led.audio.bpm import (
    TempoFollower,
    TempoReading,
    click_track,
    estimate_tempo,
    inspect_tempo,
    show_scale,
)
from orng_led.audio.devices import InputDevice, choose_input
from orng_led.audio.service import BpmService, append_capped, mix_down
from orng_led.config.models import FixtureKind
from orng_led.desktop.remote_desk import command_for_request, executor_supports
from orng_led.engine.intents import BeamIntent, ParIntent


def _feed(runtime: AppRuntime, bpm: float, times: int = 8) -> None:
    samples = click_track(bpm, 4.0)
    for _ in range(times):
        assert runtime.bpm.feed(samples, 44100) is not None


def test_click_track_reads_near_the_tempo() -> None:
    slow = estimate_tempo(click_track(96, 6.0), 44100)
    mid = estimate_tempo(click_track(120, 6.0), 44100)
    fast = estimate_tempo(click_track(140, 6.0), 44100)
    faster = estimate_tempo(click_track(150, 6.0), 44100)
    floor = estimate_tempo(click_track(80, 6.0), 44100)
    assert None not in (slow, mid, fast, faster, floor)
    assert slow.bpm == pytest.approx(96, abs=3)
    assert mid.bpm == pytest.approx(120, abs=3)
    assert fast.bpm == pytest.approx(140, abs=3)
    assert faster.bpm == pytest.approx(150, abs=3)
    assert floor.bpm == pytest.approx(80, abs=3)
    assert estimate_tempo([0.0] * 44100 * 3, 44100) is None


def test_one_accent_does_not_erase_the_other_attacks() -> None:
    """A single louder hit must not become the threshold for the whole bar."""
    rate = 44100
    base = np.asarray(click_track(120, 4, rate), dtype=np.float64) * 0.3
    plain = estimate_tempo(base.tolist(), rate)
    assert plain is not None
    assert plain.bpm == pytest.approx(120, abs=1)
    for factor in (2.0, 3.0):
        accented = base.copy()
        accented[44100:44190] *= factor
        assert float(np.max(np.abs(accented))) < 1.0
        reading = estimate_tempo(accented.tolist(), rate)
        assert reading is not None
        assert reading.bpm == pytest.approx(plain.bpm, abs=1)
        assert reading.confidence >= 0.55


def _hit(samples: np.ndarray, start: int, level: float, length: int = 90) -> None:
    end = min(samples.size, start + length)
    if start >= end:
        return
    ramp = np.linspace(level, 0.0, end - start, endpoint=False)
    samples[start:end] += ramp


def test_grid_stays_readable_through_real_bar_damage() -> None:
    """Accents, holes and a bed keep the tempo. Non-rhythm does not lock."""
    rate = 44100
    step = int(rate * 60.0 / 120.0)

    several = np.asarray(click_track(120, 4, rate), dtype=np.float64) * 0.25
    for beat in (1, 4, 6):
        several[beat * step : beat * step + 90] *= 3.0
    reading = estimate_tempo(several.tolist(), rate)
    assert reading is not None and reading.bpm == pytest.approx(120, abs=1)

    alternate = np.asarray(click_track(120, 4, rate), dtype=np.float64) * 0.2
    for beat in range(0, 8, 2):
        alternate[beat * step : beat * step + 90] *= 3.0
    reading = estimate_tempo(alternate.tolist(), rate)
    assert reading is not None and reading.bpm == pytest.approx(120, abs=1)

    missed = np.asarray(click_track(120, 6, rate), dtype=np.float64)
    missed[3 * step : 3 * step + 90] = 0.0
    reading = estimate_tempo(missed.tolist(), rate)
    assert reading is not None and reading.bpm == pytest.approx(120, abs=1)

    syncopated = np.asarray(click_track(120, 4, rate), dtype=np.float64) * 0.35
    _hit(syncopated, 2 * step + step // 2, 0.25, 70)
    _hit(syncopated, 5 * step + step // 2, 0.2, 70)
    reading = estimate_tempo(syncopated.tolist(), rate)
    assert reading is not None and reading.bpm == pytest.approx(120, abs=1)

    bed = np.asarray(click_track(120, 4, rate), dtype=np.float64) * 0.3
    times = np.arange(bed.size, dtype=np.float64) / rate
    bed += 0.05 * np.sin(2.0 * np.pi * 220.0 * times)
    reading = estimate_tempo(bed.tolist(), rate)
    assert reading is not None and reading.bpm == pytest.approx(120, abs=3)

    quiet = np.asarray(click_track(132, 4, rate), dtype=np.float64) * 0.08
    loud = quiet * 4.0
    low = estimate_tempo(quiet.tolist(), rate)
    high = estimate_tempo(loud.tolist(), rate)
    assert low is not None and high is not None
    assert low.bpm == pytest.approx(132, abs=1)
    assert high.bpm == pytest.approx(low.bpm, abs=1)

    silence, silence_why, _detail = inspect_tempo([0.0] * rate * 3, rate)
    assert silence is None and silence_why == "few_onsets"
    hum = (0.2 * np.sin(2.0 * np.pi * 50.0 * np.arange(rate * 4) / rate)).tolist()
    hum_reading, _hum_why, _detail = inspect_tempo(hum, rate)
    assert hum_reading is None or hum_reading.confidence < 0.55
    rng = np.random.default_rng(1)
    noise = rng.uniform(-0.2, 0.2, rate * 4)
    noise_reading, noise_why, _detail = inspect_tempo(noise.tolist(), rate)
    assert noise_reading is None or noise_reading.confidence < 0.55
    assert noise_why != "ok" or (noise_reading is not None and noise_reading.confidence < 0.55)
    single = np.zeros(rate * 4, dtype=np.float64)
    _hit(single, rate, 0.8, 90)
    single_reading, single_why, _detail = inspect_tempo(single.tolist(), rate)
    assert single_reading is None and single_why == "few_onsets"
    clipped = np.clip(np.asarray(click_track(120, 4, rate), dtype=np.float64) * 4.0, -1.0, 1.0)
    clipped_reading = estimate_tempo(clipped.tolist(), rate)
    assert clipped_reading is not None
    assert clipped_reading.bpm == pytest.approx(120, abs=1)
    assert clipped_reading.confidence >= 0.55


def _decay_grid(times: np.ndarray, rate: int = 44100, seconds: float = 4.0) -> np.ndarray:
    samples = np.zeros(int(rate * seconds), dtype=np.float64)
    shape = 0.285 * (1.0 - np.arange(90) / 90.0)
    for when in times:
        start = int(round(float(when) * rate))
        if start < 0 or start >= samples.size:
            continue
        end = min(samples.size, start + 90)
        samples[start:end] = shape[: end - start]
    return samples


def test_stray_first_attack_does_not_steal_the_grid() -> None:
    """The first attack is not the origin. A consistent group still sets period and phase."""
    rate = 44100
    period = 60.0 / 124.0
    times = np.arange(0.27, 3.99, period)
    plain, why, _detail = inspect_tempo(_decay_grid(times, rate).tolist(), rate)
    assert plain is not None and why == "ok"
    assert plain.bpm == pytest.approx(124, abs=1)

    stray = _decay_grid(times, rate)
    stray[:90] = 0.285 * (1.0 - np.arange(90) / 90.0)
    reading, reason, detail = inspect_tempo(stray.tolist(), rate)
    assert reading is not None, (reason, detail)
    assert reading.bpm == pytest.approx(plain.bpm, abs=1)
    assert reading.confidence >= 0.55
    assert abs(reading.beat_phase - plain.beat_phase) < 0.12

    mid = _decay_grid(times, rate)
    off_grid = 0.27 + 3.0 * period + period * 0.37
    start = int(round(off_grid * rate))
    mid[start : start + 90] = 0.285 * (1.0 - np.arange(90) / 90.0)
    middle = estimate_tempo(mid.tolist(), rate)
    assert middle is not None
    assert middle.bpm == pytest.approx(124, abs=1)

    long = _decay_grid(np.arange(0.27, 6.0, period), rate, seconds=6.0)
    for offset_s in (0.05, 0.13, 0.40):
        begin = int(offset_s * rate)
        sliced = estimate_tempo(long[begin : begin + 4 * rate].tolist(), rate)
        assert sliced is not None
        assert sliced.bpm == pytest.approx(124, abs=1)


def test_four_inliers_do_not_outrank_the_bar() -> None:
    """A sparse 4-hit accident must not beat an 8-hit grid in the same window."""
    rate = 44100
    period = 60.0 / 124.0
    times = np.arange(0.27, 3.99, period)
    mixed = _decay_grid(times, rate)
    shape = 0.285 * (1.0 - np.arange(90) / 90.0)
    for when in (0.11, 0.61, 1.11, 1.61):
        start = int(round(when * rate))
        mixed[start : start + 90] = np.maximum(mixed[start : start + 90], shape)
    reading, reason, detail = inspect_tempo(mixed.tolist(), rate)
    assert reading is not None, (reason, detail)
    assert reading.bpm == pytest.approx(124, abs=1)
    assert int(detail["inliers"] or 0) >= 6
    assert reading.confidence >= 0.55


def test_offbeat_hits_do_not_hide_the_beat_grid() -> None:
    """Hats between kicks used to drop the bar below the all-attack fraction."""
    rate = 44100
    mix = np.asarray(click_track(124, 4, rate), dtype=np.float64) * 0.15
    times = np.arange(mix.size, dtype=np.float64) / rate
    mix += 0.04 * np.sin(2.0 * np.pi * 90.0 * times)
    mix += 0.03 * np.sin(2.0 * np.pi * 220.0 * times)
    step = int(rate * 0.125)
    for start in range(0, mix.size, step):
        mix[start : start + 40] += 0.08
    reading, reason, detail = inspect_tempo(mix.tolist(), rate)
    assert reading is not None, (reason, detail)
    assert reading.bpm == pytest.approx(124, abs=1)
    assert reading.confidence >= 0.55
    assert float(detail["coverage"] or 0) >= 0.55


def test_tempo_reject_is_local_and_absent_from_the_desk(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.capture = "listening"
    service.configure(enabled=True)
    service.feed([0.0] * 44100 * 3, 44100, now=1.0, dt_s=1.0)
    assert "tempo_reject" not in service.status()
    assert "tempo_fit" not in service.status()
    assert service.diagnostics()["tempo_reject"] == "few_onsets"
    assert service.diagnostics()["tempo_fit"]["onsets"] == 0
    assert service.diagnostics()["lock_reason"] == "quiet"


def _kick_and_snare(bpm: float, seconds: float, level: float) -> list[float]:
    rate = 44100
    step = max(1, int(rate * 60.0 / bpm))
    total = int(seconds * rate)
    samples = [0.0] * total
    for index, start in enumerate(range(0, total, step)):
        for offset in range(min(80, total - start)):
            samples[start + offset] = 0.08 * level * (1.0 - offset / 80.0)
        if index % 2 == 1:
            for offset in range(min(120, total - start)):
                samples[start + offset] = min(
                    1.0,
                    samples[start + offset] + 0.55 * level * (1.0 - offset / 120.0),
                )
    return samples


def test_a_louder_backbeat_keeps_the_same_tempo_at_two_levels() -> None:
    loud = estimate_tempo(_kick_and_snare(124, 4.0, 1.0), 44100)
    quiet = estimate_tempo(_kick_and_snare(124, 4.0, 0.16), 44100)
    assert loud is not None and quiet is not None
    assert loud.bpm == pytest.approx(124, abs=3)
    assert quiet.bpm == pytest.approx(loud.bpm, abs=2)
    noise = [((index * 17) % 100) / 100.0 * 0.02 - 0.01 for index in range(44100 * 4)]
    assert estimate_tempo(noise, 44100) is None
    assert estimate_tempo([0.0] * 44100 * 4, 44100) is None


def test_three_modes_share_one_tempo_ratio(tmp_path: Path) -> None:
    """Bar and beat do not chase the episode with a new error every frame."""
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    _feed(runtime, 120)
    runtime.apply_bpm_sync(enabled=True, mode="smooth")
    smooth = runtime.bpm.scale_for(10.4, 18.0)
    runtime.bpm.follower.beat_phase = 0.05
    runtime.apply_bpm_sync(enabled=True, mode="bar")
    bar = runtime.bpm.scale_for(10.4, 18.0)
    runtime.bpm.follower.beat_phase = 0.85
    bar_later = runtime.bpm.scale_for(10.4, 18.0)
    runtime.apply_bpm_sync(enabled=True, mode="beat")
    beat = runtime.bpm.scale_for(10.4, 18.0)
    assert smooth == pytest.approx(1.0, abs=0.03)
    assert bar == pytest.approx(smooth, abs=0.03)
    assert bar_later == pytest.approx(bar, abs=0.03)
    assert beat == pytest.approx(smooth, abs=0.03)
    assert show_scale("smooth", 144, 0, 18) == pytest.approx(1.2)


def test_tempo_moves_the_episode_and_keeps_the_preset(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    preset = runtime.engine.active_preset_id
    runtime.apply_bpm_sync(enabled=True, mode="smooth")
    _feed(runtime, 120)
    start = runtime.engine.preset_elapsed_s
    for _ in range(40):
        runtime.tick(0.05)
    at_120 = runtime.engine.preset_elapsed_s - start
    _feed(runtime, 156)
    start = runtime.engine.preset_elapsed_s
    episode = runtime.engine.episode_window()[0]
    for _ in range(40):
        runtime.tick(0.05)
    at_156 = runtime.engine.preset_elapsed_s - start
    assert at_120 == pytest.approx(2.0, rel=0.08)
    assert at_156 > at_120 * 1.2
    assert runtime.engine.active_preset_id == preset
    assert runtime.engine.episode_window()[0] != episode or at_156 < 18


def test_speed_controls_do_nothing_while_sync_is_on(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.preview_speed = 2.0
    runtime.apply_bpm_sync(enabled=True, mode="bar")
    runtime.apply_nudge_program_speed(0.25)
    assert runtime.preview_speed == pytest.approx(2.25)
    _feed(runtime, 120)
    runtime.tick(0.05)
    runtime.apply_nudge_program_speed(0.25)
    runtime.apply_preview_speed(3.0)
    assert runtime.preview_speed == pytest.approx(2.25)
    runtime.apply_bpm_sync(enabled=False, mode="bar")
    assert runtime.preview_speed == pytest.approx(2.25)
    runtime.apply_nudge_program_speed(0.25)
    assert runtime.preview_speed == pytest.approx(2.5)


def test_a_palette_command_without_enabled_leaves_sync_running(tmp_path: Path) -> None:
    from orng_led.cloud.agent import bpm_sync_args

    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.apply_bpm_sync(enabled=True, palette="blue")
    runtime.apply_bpm_sync(**bpm_sync_args({"mode": "bar", "palette": "red"}))
    assert runtime.bpm.enabled is True
    assert runtime.bpm.mode == "bar"
    assert runtime.bpm.palette == "red"
    stored = (tmp_path / "location-show" / f"{runtime.scene_id}.json").read_text(encoding="utf-8")
    assert "enabled" not in stored


def test_mode_is_stored_per_scene(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.apply_bpm_sync(enabled=True, mode="beat")
    runtime.bpm.set_scene("other-room")
    assert runtime.bpm.mode == "smooth"
    assert runtime.bpm.enabled is False
    runtime.bpm.set_scene(runtime.scene_id)
    assert runtime.bpm.mode == "beat"
    assert runtime.bpm.enabled is False


def test_changing_tempo_stays_inside_one_preset(tmp_path: Path) -> None:
    """A DJ tempo change moves the episode clock. It never jumps the preset."""
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    preset = runtime.engine.active_preset_id
    elapsed: dict[str, float] = {}
    for mode in ("smooth", "bar", "beat"):
        runtime.engine.preset_elapsed_s = 0.0
        runtime.bpm.follower.bpm = None
        runtime.apply_bpm_sync(enabled=True, mode=mode)
        _feed(runtime, 100)
        for _ in range(40):
            runtime.tick(0.05)
        _feed(runtime, 140)
        for _ in range(40):
            runtime.tick(0.05)
        elapsed[mode] = runtime.engine.preset_elapsed_s
        assert runtime.engine.active_preset_id == preset
        window_time, duration = runtime.engine.episode_window()
        assert 0 <= window_time <= duration + 0.01
    # 2s at 100 BPM plus 2s climbing toward 140 BPM. 120 BPM would be exactly 4s.
    assert 3.3 < elapsed["smooth"] < 4.6
    assert 3.0 < elapsed["bar"] < 5.5
    assert 3.0 < elapsed["beat"] < 5.5


def test_windows_mapper_is_not_the_line() -> None:
    devices = [
        InputDevice("8", "Первичный драйвер записи звука", 2),
        InputDevice("0", "Переназначение звуковых устр. - Input", 2),
        InputDevice("11", "Microphone Array (Realtek(R) Audio)", 2),
        InputDevice("1", "Microphone (fifine Microphone)", 2),
    ]
    assert choose_input(devices) is None
    with_cable = devices + [InputDevice("40", "USB Audio Device", 2)]
    chosen = choose_input(with_cable)
    assert chosen is not None and chosen.id == "40"


def test_plain_microphone_is_not_the_line() -> None:
    builtin = [InputDevice("0", "Микрофон", 1)]
    assert choose_input(builtin) is None
    usb_mic = builtin + [InputDevice("4", "Microphone (USB Audio Device)", 2)]
    assert choose_input(usb_mic) is None
    webcam = [InputDevice("5", "USB Webcam", 1), InputDevice("6", "USB Headset", 1)]
    assert choose_input(webcam) is None


def test_usb_line_is_chosen_over_the_built_in_mic() -> None:
    devices = [
        InputDevice("0", "Microphone Array (Realtek)", 2),
        InputDevice("1", "USB Audio Device", 2),
        InputDevice("2", "Speakers", 0),
    ]
    chosen = choose_input(devices)
    assert chosen is not None and chosen.id == "1"
    assert choose_input(devices[:1]) is None
    hot = devices + [InputDevice("3", "Scarlett 2i2 USB", 2)]
    assert choose_input(hot, current_id="1").id == "3"


def test_desk_forwards_bpm_only_when_the_executor_has_it() -> None:
    mapped = command_for_request("/api/commands/bpm-sync", {"enabled": True, "mode": "bar"})
    assert mapped == ("bpm_sync", {"enabled": True, "mode": "bar"})
    old = {"capabilities": ["scene_sync", "blackout"]}
    new = {"capabilities": ["scene_sync", "bpm_sync"]}
    assert executor_supports("bpm_sync", old) is False
    assert executor_supports("set_program_speed", old) is True
    assert executor_supports("bpm_sync", new) is True
    assert executor_supports("set_program_speed", None) is True
    auto = command_for_request(
        "/api/commands/bpm-sync",
        {"enabled": True, "scope": "auto", "palette": "bars"},
    )
    assert auto == ("bpm_sync", {"enabled": True, "scope": "auto", "palette": "bars"})


def _hear(tracker: SectionTracker, samples: list[float], rate: int = 44100) -> None:
    hop_s = 0.25
    hop = int(rate * hop_s)
    beat = 0.5
    for start in range(0, len(samples) - hop + 1, hop):
        hearing = analyze_slice(samples[start : start + hop], rate)
        tracker.push(
            hearing.rms,
            hop_s,
            onset=hearing.onset,
            hearing=hearing,
            beat_period_s=beat,
        )


def _accelerating(seconds: float, rate: int = 44100) -> list[float]:
    total = int(seconds * rate)
    samples = [0.0] * total
    moment = 0.0
    while moment < seconds:
        start = int(moment * rate)
        for offset in range(min(48, total - start)):
            samples[start + offset] = max(
                samples[start + offset],
                0.9 * (1.0 - offset / 48.0),
            )
        frac = min(1.0, moment / max(seconds * 0.65, 0.1))
        div = 1.0 + 3.0 * frac
        moment += 60.0 / (120.0 * div)
    return samples


def _drop_kick(seconds: float = 0.45, rate: int = 44100) -> list[float]:
    samples = [0.0] * int(seconds * rate)
    tau = 0.07 * rate
    for offset in range(len(samples)):
        env = 0.98 * math.exp(-offset / tau)
        samples[offset] = env * math.sin(2.0 * math.pi * 55.0 * offset / rate)
    return samples


def test_drop_is_a_break_then_a_hit() -> None:
    """A drop is a rise that already broke, then one hit. Not a loud number."""
    tracker = SectionTracker()
    _hear(tracker, click_track(120, 3.0))
    assert tracker.section == "groove"
    hats = [0.0] * int(2.5 * 44100)
    _hear(tracker, hats)
    assert tracker.section == "break"
    _hear(tracker, _accelerating(3.5))
    assert tracker.section == "riser"
    _hear(tracker, _drop_kick())
    assert tracker.section == "drop"


def test_auto_palettes_stay_on_mapped_kinds_and_park_heads(tmp_path: Path) -> None:
    fixtures = [
        _Lamp("par-1", FixtureKind.PAR),
        _Lamp("bar-1", FixtureKind.BAR),
        _Lamp("beam-1", FixtureKind.BEAM),
        _Lamp("face-1", FixtureKind.FACE_PAR),
    ]
    assert len(PALETTE_IDS) >= 12
    assert "ember" in PALETTE_IDS and "amber" in PALETTE_IDS
    bars = build_auto_stage(fixtures, "bars", "drop", 0.1, 1.0, 0.4)
    assert set(bars.fixtures) == {"bar-1"}
    heads = build_auto_stage(fixtures, "heads", "groove", 0.2, 0.8)
    assert set(heads.fixtures) == {"par-1", "beam-1"}
    beam = heads.fixtures["beam-1"]
    assert isinstance(beam, BeamIntent)
    assert beam.pan is None and beam.tilt is None and beam.strobe == 0.0
    par = heads.fixtures["par-1"]
    assert isinstance(par, ParIntent) and par.strobe == 0.0
    riser = build_auto_stage(fixtures, "orange", "riser", 0.4, 0.7, flash_hold=1.0)
    assert isinstance(riser.fixtures["par-1"], ParIntent)
    assert riser.fixtures["par-1"].strobe == 0.0
    assert riser.fixtures["par-1"].intensity == 1.0
    assert isinstance(riser.fixtures["beam-1"], BeamIntent)
    assert riser.fixtures["beam-1"].strobe == 0.0
    drop = build_auto_stage(fixtures, "orange", "drop", 0.0, 1.0, 0.4)
    assert isinstance(drop.fixtures["par-1"], ParIntent)
    assert drop.fixtures["par-1"].strobe == 0.0
    assert drop.fixtures["par-1"].intensity == 1.0
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    live = build_auto_stage(
        list(runtime.engine.show.patch.fixtures),
        "heads",
        "drop",
        0.0,
        1.0,
        placements=runtime.engine.show.layout.placements,
    )
    for fixture in runtime.engine.show.patch.fixtures:
        intent = live.fixtures.get(fixture.id)
        if fixture.kind is FixtureKind.FACE_PAR:
            assert intent is None
        if isinstance(intent, BeamIntent):
            assert intent.pan is None and intent.tilt is None and intent.strobe == 0.0


def test_auto_does_not_chase_the_preset_clock(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    preset = runtime.engine.active_preset_id
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="heads", mode="smooth")
    _feed(runtime, 160)
    runtime.bpm.section.section = "groove"
    runtime.bpm.section.level = 0.8
    start = runtime.engine.preset_elapsed_s
    for _ in range(40):
        runtime.tick(0.05)
    assert runtime.engine.preset_elapsed_s - start == pytest.approx(2.0, abs=0.15)
    assert runtime.engine.active_preset_id == preset
    assert runtime.engine.auto_base is not None
    assert runtime.bpm.status()["scope"] == "auto"
    assert runtime.bpm.status()["palette"] == "white"
    assert runtime.bpm.status()["cast"] == "heads"


class _Spot:
    def __init__(self, side: str, order: int) -> None:
        self.side = side
        self.order = order


class _Lamp:
    def __init__(
        self,
        lamp_id: str,
        kind: FixtureKind,
        side: str = "center",
        order: int = 1,
    ) -> None:
        self.id = lamp_id
        self.kind = kind
        self.spatial = _Spot(side, order)
        self.layout_x = 0.5
        self.layout_y = 0.5


def test_auto_pairs_are_mirrors() -> None:
    fixtures = [
        _Lamp("par-l", FixtureKind.PAR, "left", 2),
        _Lamp("par-r", FixtureKind.PAR, "right", 2),
        _Lamp("bar-l", FixtureKind.BAR, "left", 1),
        _Lamp("bar-r", FixtureKind.BAR, "right", 1),
        _Lamp("beam-l", FixtureKind.BEAM, "left", 1),
        _Lamp("beam-r", FixtureKind.BEAM, "right", 1),
    ]
    stage = build_auto_stage(fixtures, "orange", "groove", 0.12, 0.8)
    assert stage.fixtures["par-l"] == stage.fixtures["par-r"]
    assert stage.fixtures["beam-l"] == stage.fixtures["beam-r"]
    assert isinstance(stage.fixtures["beam-l"], BeamIntent)
    assert stage.fixtures["beam-l"].pan is None
    left_bar = stage.fixtures["bar-l"]
    right_bar = stage.fixtures["bar-r"]
    assert hasattr(left_bar, "segments") and hasattr(right_bar, "segments")
    assert left_bar.segments == right_bar.segments


def test_two_usb_inputs_do_not_swap_when_indexes_move() -> None:
    first = InputDevice("a", "USB Audio A", 2, index=1)
    second = InputDevice("b", "USB Audio B", 2, index=2)
    assert choose_input([first, second], current_id=first.id).id == first.id
    reindexed = [
        InputDevice(second.id, second.name, 2, index=0),
        InputDevice(first.id, first.name, 2, index=7),
    ]
    chosen = choose_input(reindexed, current_id=first.id)
    assert chosen.id == first.id
    assert chosen.index == 7


def test_phase_advances_between_windows_then_expires() -> None:
    follower = TempoFollower()
    follower.push(TempoReading(120.0, 0.0), now=100.0)
    assert follower.locked is False
    assert follower.phase_at(100.25) == pytest.approx(0.5, abs=0.02)
    assert follower.phase_at(100.5) == pytest.approx(0.0, abs=0.02)
    assert follower.grid_phase(100.5) == pytest.approx(0.25, abs=0.02)
    follower.push(TempoReading(120.0, 0.0), now=100.5)
    assert follower.locked is True
    follower.advance(102.9)
    assert follower.locked is True
    follower.advance(103.1)
    assert follower.locked is False
    assert follower.bpm is not None
    held = follower.phase_at(103.1)
    later = follower.phase_at(103.5)
    error = (later - ((held + 0.8) % 1.0) + 0.5) % 1.0 - 0.5
    assert abs(error) < 0.05
    follower.advance(104.1)
    assert follower.bpm is None
    assert follower.phase_at(104.1) == 0.0


def test_right_channel_is_audible_at_either_rate() -> None:
    for rate in (44100, 48000):
        clicks = click_track(120, 4.0, rate)
        mixed = mix_down([[0.0, sample] for sample in clicks])
        reading = estimate_tempo(mixed, rate)
        assert reading is not None
        assert reading.bpm == pytest.approx(120, abs=3)
        assert window_energy(mixed) > 0.05
        assert estimate_tempo([0.0] * rate * 3, rate) is None


def test_silence_after_music_clears_the_held_tempo(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    heard = service.feed(click_track(120, 4.0), 44100, dt_s=0.4, now=10.0)
    assert heard is not None and service.follower.bpm is not None
    assert service.follower.locked is False
    again = service.feed(click_track(120, 4.0), 44100, dt_s=0.4, now=10.0)
    assert again is not None and service.follower.locked is True
    quiet = [0.0] * 4410
    service.feed(quiet, 44100, dt_s=0.5, now=12.0, energy_samples=quiet)
    assert service.follower.bpm is not None
    service.feed(quiet, 44100, dt_s=0.5, now=13.0, energy_samples=quiet)
    assert service.follower.locked is False
    assert service.follower.bpm is not None
    service.feed(quiet, 44100, dt_s=0.5, now=14.2, energy_samples=quiet)
    assert service.follower.bpm is None
    assert service.status()["bpm"] is None


def _reach_riser() -> SectionTracker:
    tracker = SectionTracker()
    _hear(tracker, click_track(120, 3.0) + _accelerating(3.5))
    assert tracker.section == "riser"
    return tracker


def test_riser_ends_on_silence_and_on_its_own_limit() -> None:
    quiet = _reach_riser()
    for _ in range(4):
        quiet.push(0.0, 0.25)
    assert quiet.section != "riser"
    stalled = _reach_riser()
    stalled.push(0.3, 6.5, onset=0.001)
    assert stalled.section != "riser"
    dark = build_auto_stage(
        [_Lamp("par-1", FixtureKind.PAR)],
        "orange",
        quiet.section,
        0.2,
        0.1,
    )
    assert dark.fixtures["par-1"].strobe == 0.0
    late = build_auto_stage(
        [_Lamp("par-1", FixtureKind.PAR)],
        "orange",
        "riser",
        0.2,
        0.8,
        riser_age=2.0,
    )
    assert late.fixtures["par-1"].strobe == 0.0


def test_sections_follow_an_audio_stream(tmp_path: Path) -> None:
    """Silence is a break. A louder steady tone is a gain change, not a drop."""
    service = BpmService(tmp_path, "room")
    now = 0.0
    quiet = [0.0] * 12000
    for _ in range(8):
        now += 0.25
        service.feed(quiet, 48000, dt_s=0.25, now=now, energy_samples=quiet)
    assert service.section.section == "break"
    medium = [0.05] * 12000
    for _ in range(4):
        now += 0.25
        service.feed(medium, 48000, dt_s=0.25, now=now, energy_samples=medium)
    assert service.section.section != "drop"
    loud = [0.2] * 12000
    now += 0.25
    before = service.section.section
    service.feed(loud, 48000, dt_s=0.25, now=now, energy_samples=loud)
    assert service.section.section == before
    assert service.section.section != "drop"


def test_capture_buffer_stays_capped() -> None:
    import threading

    chunks: list[float] = []
    lock = threading.Lock()
    append_capped(chunks, [0.2] * 5000, 1000, lock)
    append_capped(chunks, [0.2] * 5000, 1000, lock)
    assert len(chunks) <= 4000


def test_held_strobe_keeps_its_own_clock() -> None:
    from orng_led.config import default_scene_dir, load_show_config
    from orng_led.engine.engine import Engine, FakeClock
    from orng_led.presets.store import PresetStore

    show = load_show_config()
    presets = PresetStore.load(default_scene_dir() / "presets").programs()
    engine = Engine(show=show, presets=presets, active_preset_id="P01", clock=FakeClock())
    engine.set_blackout(False)
    engine.set_master_brightness(1.0)
    engine.bpm_fx = True
    engine.bpm_beat_phase = 0.0
    engine.strobe_press()
    engine.tick(0.05)
    phase = engine.overlays.strobe_phase
    engine.bpm_beat_phase = 0.9
    engine.bpm_bar_phase = 0.9
    engine.musical_beats = 3.0
    engine.tick(0.05)
    assert engine.overlays.strobe_held is True
    assert engine.overlays.strobe_phase > phase
    assert engine.overlays.strobe_phase != pytest.approx(0.9)
