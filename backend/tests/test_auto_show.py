"""Auto show: audio goes in, rendered channels come out.

These are synthetic signals with a fixed hop. They are not a claim that the
ear understands records, speech, or a live room.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import (
    DROP_ACCENT_S,
    PHRASE_BARS,
    STROBE_FRAGMENT_S,
    SectionTracker,
    analyze_slice,
    build_auto_stage,
)
from orng_led.audio.bpm import TempoReading, click_track
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import BeamIntent, ParIntent, StageIntent
from orng_led.engine.renderer import render_stage

RATE = 44100
HOP_S = 0.25


class _Spot:
    def __init__(
        self,
        side: str,
        order: int,
        invert: bool = False,
        segments: int | None = None,
    ) -> None:
        self.side = side
        self.order = order
        self.invert_segments = invert
        self.segment_count = segments


class _Lamp:
    def __init__(
        self,
        lamp_id: str,
        kind: FixtureKind,
        side: str = "center",
        order: int = 1,
        *,
        invert: bool = False,
        segments: int | None = None,
        layout_x: float | None = None,
    ) -> None:
        self.id = lamp_id
        self.kind = kind
        self.spatial = _Spot(side, order, invert, segments)
        self.segment_count = segments
        self.layout_x = 0.5 if layout_x is None else float(layout_x)
        self.layout_y = 0.5


def _sine(seconds: float, amp: float, freq: float = 220.0, rate: int = RATE) -> list[float]:
    count = int(seconds * rate)
    return [amp * math.sin(2.0 * math.pi * freq * i / rate) for i in range(count)]


def _ramp(seconds: float, start: float, end: float, rate: int = RATE) -> list[float]:
    count = int(seconds * rate)
    samples: list[float] = []
    for index in range(count):
        amp = start + (end - start) * (index / max(count - 1, 1))
        samples.append(amp * math.sin(2.0 * math.pi * 180.0 * index / rate))
    return samples


def _noise(seconds: float, amp: float, seed: int, rate: int = RATE) -> list[float]:
    count = int(seconds * rate)
    value = seed & 0x7FFFFFFF or 1
    samples: list[float] = []
    for _ in range(count):
        value = (1103515245 * value + 12345) & 0x7FFFFFFF
        samples.append(((value / 0x7FFFFFFF) * 2.0 - 1.0) * amp)
    return samples


def _add(left: list[float], right: list[float]) -> list[float]:
    count = max(len(left), len(right))
    mixed = [0.0] * count
    for index in range(count):
        value = (left[index] if index < len(left) else 0.0) + (
            right[index] if index < len(right) else 0.0
        )
        mixed[index] = max(-1.0, min(1.0, value))
    return mixed


def _clicks(
    seconds: float,
    bpm: float = 120.0,
    division: float = 1.0,
    amp: float = 0.9,
    rate: int = RATE,
) -> list[float]:
    total = int(seconds * rate)
    samples = [0.0] * total
    step = max(1, int(rate * 60.0 / (bpm * division)))
    width = 90 if division <= 2.0 else 48
    for start in range(0, total, step):
        for offset in range(min(width, total - start)):
            samples[start + offset] = amp * (1.0 - offset / width)
    return samples


def _accelerating(
    seconds: float,
    bpm: float = 120.0,
    start_div: float = 1.0,
    end_div: float = 4.0,
    rate: int = RATE,
) -> list[float]:
    total = int(seconds * rate)
    samples = [0.0] * total
    moment = 0.0
    while moment < seconds:
        start = int(moment * rate)
        width = 48
        for offset in range(min(width, total - start)):
            samples[start + offset] = max(
                samples[start + offset],
                0.9 * (1.0 - offset / width),
            )
        frac = min(1.0, moment / max(seconds * 0.65, 0.1))
        div = start_div + (end_div - start_div) * frac
        moment += 60.0 / (bpm * max(0.5, div))
    return samples


def _hit(seconds: float, amp: float = 0.98, rate: int = RATE) -> list[float]:
    samples = [0.0] * int(seconds * rate)
    tau = 0.07 * rate
    for offset in range(len(samples)):
        env = amp * math.exp(-offset / tau)
        samples[offset] = env * math.sin(2.0 * math.pi * 55.0 * offset / rate)
    return samples


def _play(samples: list[float], rate: int = RATE) -> list[tuple[float, str, float, float]]:
    tracker = SectionTracker()
    hop = int(rate * HOP_S)
    lamp = _Lamp("par", FixtureKind.PAR)
    rows: list[tuple[float, str, float, float]] = []
    moment = 0.0
    beat = 60.0 / 120.0
    for start in range(0, len(samples) - hop + 1, hop):
        hearing = analyze_slice(samples[start : start + hop], rate)
        tracker.push(
            hearing.rms,
            HOP_S,
            onset=hearing.onset,
            hearing=hearing,
            beat_period_s=beat,
        )
        moment += HOP_S
        stage = build_auto_stage(
            [lamp],
            "orange",
            tracker.section,
            0.0,
            tracker.level,
            tracker.drop_age,
            riser_age=tracker.riser_age,
        )
        strobe = float(stage.fixtures["par"].strobe)
        rows.append((round(moment, 2), tracker.section, round(tracker.level, 3), strobe))
    return rows


def _names(rows: list[tuple[float, str, float, float]]) -> list[str]:
    names: list[str] = []
    for _, section, _, _ in rows:
        if not names or names[-1] != section:
            names.append(section)
    return names


def _strobe_hops(rows: list[tuple[float, str, float, float]]) -> int:
    longest = 0
    current = 0
    for _, _, _, strobe in rows:
        if strobe > 0.0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def test_even_groove_stays_a_groove() -> None:
    rows = _play(click_track(120, 8.0, RATE))
    assert "drop" not in _names(rows)
    assert rows[-1][1] == "groove"
    assert _strobe_hops(rows) == 0


def test_quiet_passage_dims_without_a_drop() -> None:
    loud = [sample * 0.9 for sample in click_track(120, 4.0, RATE)]
    quiet = [sample * 0.12 for sample in click_track(120, 3.0, RATE)]
    rows = _play(loud + quiet)
    assert "drop" not in _names(rows)
    early = [level for moment, _, level, _ in rows if moment <= 3.5]
    late = [level for moment, section, level, _ in rows if moment >= 6.0]
    assert late
    assert sum(late) / len(late) < sum(early) / len(early) * 0.85
    assert rows[-1][1] in {"break", "groove"}
    assert _strobe_hops(rows) == 0


def test_pad_swell_with_a_steady_kick_is_not_a_riser() -> None:
    rows = _play(_add(click_track(120, 6.0, RATE), _ramp(6.0, 0.02, 0.38)))
    assert "riser" not in _names(rows)
    assert "drop" not in _names(rows)
    assert rows[-1][1] == "groove"


def test_build_is_a_riser_not_a_prediction() -> None:
    rows = _play(click_track(120, 3.5, RATE) + _accelerating(4.0))
    assert "riser" in _names(rows)
    assert "drop" not in _names(rows)


def test_build_without_a_drop_ends_the_rise_and_the_strobe() -> None:
    rows = _play(click_track(120, 3.0, RATE) + _accelerating(5.0) + _clicks(4.0, division=1.0))
    assert "riser" in _names(rows)
    assert "drop" not in _names(rows)
    assert rows[-1][1] != "riser"
    assert rows[-1][3] == 0.0
    assert _strobe_hops(rows) * HOP_S <= STROBE_FRAGMENT_S + HOP_S


def test_drop_is_one_accent_then_the_strobe_is_gone() -> None:
    rows = _play(
        click_track(120, 3.0, RATE)
        + _accelerating(3.5)
        + _hit(0.4)
        + click_track(120, 2.0, RATE)
    )
    assert "drop" in _names(rows)
    assert rows[-1][1] != "drop"
    assert rows[-1][3] == 0.0
    assert _strobe_hops(rows) * HOP_S <= STROBE_FRAGMENT_S + HOP_S


def test_loud_constant_tone_is_not_a_drop() -> None:
    rows = _play(_sine(6.0, 0.45))
    assert _names(rows) == ["groove"]
    assert rows[-1][3] == 0.0


def test_gain_step_does_not_change_section() -> None:
    rows = _play(_sine(2.0, 0.05) + _sine(2.0, 0.45))
    assert "drop" not in _names(rows)
    assert "riser" not in _names(rows)
    before = next(section for moment, section, _, _ in rows if moment == 2.0)
    after = next(section for moment, section, _, _ in rows if moment == 2.25)
    assert after == before


def test_noise_speech_and_clipping_do_not_stick_on_a_drop() -> None:
    speech: list[float] = []
    cursor = 0.0
    seed = 7
    while cursor < 6.0:
        speech.extend(_noise(0.08, 0.15 + (seed % 5) * 0.08, seed))
        speech.extend([0.0] * int(0.14 * RATE))
        cursor += 0.22
        seed += 3
    clipped = [max(-1.0, min(1.0, sample * 8.0)) for sample in _sine(4.0, 0.4, freq=90.0)]
    for samples in (speech, _noise(5.0, 0.5, 99), clipped):
        rows = _play(samples)
        drops = sum(1 for _, section, _, _ in rows if section == "drop")
        assert drops / len(rows) < 0.15
        assert rows[-1][3] == 0.0
        assert _strobe_hops(rows) * HOP_S <= STROBE_FRAGMENT_S + HOP_S


def test_music_without_a_kick_is_not_a_drop() -> None:
    count = int(8.0 * RATE)
    samples = []
    for index in range(count):
        tremolo = 0.55 + 0.45 * math.sin(2.0 * math.pi * 0.3 * index / RATE)
        samples.append(0.3 * tremolo * math.sin(2.0 * math.pi * 196.0 * index / RATE))
    rows = _play(samples)
    assert "drop" not in _names(rows)
    assert "groove" in _names(rows)
    assert "riser" not in _names(rows)
    assert _strobe_hops(rows) * HOP_S <= STROBE_FRAGMENT_S + HOP_S


def test_cable_pull_ends_in_silence() -> None:
    samples = click_track(118, 2.0, RATE) + [0.0] * int(1.5 * RATE)
    rows = _play(samples)
    assert rows[-1][1] == "break"
    assert rows[-1][2] == 0.0
    assert rows[-1][3] == 0.0


def test_a_missing_capture_ends_the_rise_without_a_tempo(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.feed(click_track(120, 2.0, RATE), RATE, dt_s=0.25, now=10.0)
    held = service.section.section
    service.poll(0.2)
    assert service.section.section == held
    service.poll(1.3)
    assert service.section.section == "break"
    assert service.section.riser_age == 0.0
    assert service.section.shown == 0.0


def test_bpm_is_not_treated_as_loudness(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    tone = _sine(2.0, 0.25)
    service.feed(tone, RATE, dt_s=2.0, now=4.0, energy_samples=tone)
    section = service.section.section
    level = service.section.level
    service.follower.push(TempoReading(70.0, 0.2), now=4.0)
    assert service.section.section == section
    assert service.section.level == level
    service.follower.push(TempoReading(160.0, 0.2), now=5.0)
    assert service.section.section == section
    assert service.section.level == level


def test_pattern_changes_on_a_phrase_and_pulses_on_the_beat() -> None:
    tracker = SectionTracker()
    for _ in range(8):
        tracker.push(0.2, 0.25, onset=0.004)
    assert tracker.section == "groove"
    tracker.observe(0.0, 0.1, 0.1)
    pattern = tracker.pattern
    moment = 0.0
    for _ in range(PHRASE_BARS - 1):
        tracker.observe(moment, 0.2, 0.92)
        moment += 0.05
        tracker.observe(moment, 0.2, 0.04)
        moment += 0.05
    assert tracker.pattern == pattern
    tracker.observe(moment, 0.2, 0.92)
    moment += 0.05
    tracker.observe(moment, 0.2, 0.04)
    assert tracker.pattern != pattern
    down = build_auto_stage([_Lamp("par", FixtureKind.PAR)], "red", "groove", 0.0, 0.8)
    up = build_auto_stage([_Lamp("par", FixtureKind.PAR)], "red", "groove", 0.85, 0.8)
    assert down.fixtures["par"].intensity > up.fixtures["par"].intensity
    again = build_auto_stage([_Lamp("par", FixtureKind.PAR)], "red", "groove", 0.85, 0.8)
    assert again.fixtures["par"] == up.fixtures["par"]


def test_drop_accent_is_full_then_the_palette_returns() -> None:
    lamp = _Lamp("par", FixtureKind.PAR)
    accent = build_auto_stage([lamp], "blue", "drop", 0.2, 0.3, drop_age=0.1)
    after = build_auto_stage([lamp], "blue", "drop", 0.2, 0.3, drop_age=DROP_ACCENT_S + 0.05)
    assert accent.fixtures["par"].intensity == 1.0
    assert after.fixtures["par"].intensity < 1.0
    assert accent.fixtures["par"].strobe == 0.0
    assert after.fixtures["par"].strobe == 0.0


def test_right_bar_is_mirrored_once() -> None:
    left = _Lamp("bar-l", FixtureKind.BAR, "left", 1, layout_x=0.2)
    plain = _Lamp("bar-r", FixtureKind.BAR, "right", 1, layout_x=0.8)
    flipped = _Lamp("bar-flip", FixtureKind.BAR, "right", 1, invert=True, layout_x=0.5)
    stage = build_auto_stage([left, plain, flipped], "orange", "groove", 0.18, 0.8)
    assert stage.fixtures["bar-r"].segments == stage.fixtures["bar-l"].segments
    assert stage.fixtures["bar-flip"].segments == stage.fixtures["bar-l"].segments


def test_scene_shape_is_taken_from_the_fixtures() -> None:
    lamps = [
        _Lamp("p1", FixtureKind.PAR, "left", 1, layout_x=0.2),
        _Lamp("p2", FixtureKind.PAR, "center", 1, layout_x=0.8),
        _Lamp("b1", FixtureKind.BAR, "left", 2, segments=4),
        _Lamp("face", FixtureKind.FACE_PAR),
    ]
    stage = build_auto_stage(lamps, "violet", "groove", 0.3, 0.7)
    assert set(stage.fixtures) == {"p1", "p2", "b1"}
    assert len(stage.fixtures["b1"].segments) == 4
    assert stage.fixtures["p1"] == stage.fixtures["p2"]
    heads = build_auto_stage(lamps, "heads", "groove", 0.3, 0.7)
    assert set(heads.fixtures) == {"p1", "p2"}
    bars = build_auto_stage(lamps, "bars", "groove", 0.3, 0.7)
    assert set(bars.fixtures) == {"b1"}


def _role_index(show: object, fixture: object, role: ChannelRole) -> int | None:
    profile = show.profile_for(fixture)
    for channel in profile.channels:
        if channel.role is role:
            return global_channel(fixture.start_address, channel.local) - 1
    return None


def test_rendered_auto_respects_master_face_and_head_axes() -> None:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    pose = {
        fixture.id: BeamMotionState(pan=0.33, tilt=0.66)
        for fixture in fixtures
        if fixture.kind is FixtureKind.BEAM
    }
    assert pose
    stage = build_auto_stage(
        fixtures,
        "orange",
        "drop",
        0.0,
        1.0,
        drop_age=0.1,
        placements=show.layout.placements,
    )
    full = render_stage(show, stage, pose, master=1.0)
    halved = render_stage(show, stage, pose, master=0.4)
    dark = render_stage(show, StageIntent(), pose, master=0.4)
    for fixture in fixtures:
        if fixture.kind is FixtureKind.FACE_PAR:
            start = fixture.start_address - 1
            width = len(show.profile_for(fixture).channels)
            assert halved[start : start + width] == dark[start : start + width]
            assert fixture.id not in stage.fixtures
        if fixture.kind is FixtureKind.BEAM:
            intent = stage.fixtures[fixture.id]
            assert isinstance(intent, BeamIntent)
            assert intent.pan is None and intent.tilt is None and intent.strobe == 0.0
            for role in (ChannelRole.PAN_COARSE, ChannelRole.TILT_COARSE):
                index = _role_index(show, fixture, role)
                if index is None:
                    continue
                assert halved[index] == full[index]
            strobe_index = _role_index(show, fixture, ChannelRole.STROBE_SPEED)
            if strobe_index is not None:
                assert halved[strobe_index] == dark[strobe_index]
        if fixture.kind in {FixtureKind.PAR, FixtureKind.BAR}:
            index = _role_index(show, fixture, ChannelRole.DIMMER)
            if index is None:
                continue
            assert halved[index] <= int(round(full[index] * 0.4)) + 1
            assert full[index] > 0
    parked = build_auto_stage(
        fixtures, "heads", "groove", 0.4, 0.8, placements=show.layout.placements
    )
    for fixture in fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        intent = parked.fixtures.get(fixture.id)
        assert isinstance(intent, BeamIntent)
        assert intent.pan is None and intent.tilt is None and intent.strobe == 0.0
        assert isinstance(intent, BeamIntent) and intent.dimmer > 0.0


def test_auto_leaves_no_overlay_and_keeps_a_manual_hold(tmp_path: Path) -> None:
    show = load_show_config()
    runtime = AppRuntime.create(autostart_loop=False, show=show, config_dir=tmp_path)
    preset = runtime.engine.active_preset_id
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    runtime.engine.strobe_press()
    runtime.tick(0.05)
    assert runtime.engine.auto_base is not None
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.beam_motion
    for state in runtime.engine.beam_motion.values():
        state.pan = 0.33
        state.tilt = 0.66
        state.last_valid_pan = 0.33
        state.last_valid_tilt = 0.66
    for _ in range(8):
        runtime.tick(0.05)
    for state in runtime.engine.beam_motion.values():
        assert state.pan == pytest.approx(0.33)
        assert state.tilt == pytest.approx(0.66)
    runtime.engine.set_blackout(True)
    runtime.apply_bpm_sync(palette="heads")
    assert runtime.engine.active_preset_id == preset
    assert runtime.engine.overlays.blackout is True
    assert runtime.engine.overlays.strobe_held is True
    runtime.apply_bpm_sync(scope="preset")
    assert runtime.engine.auto_base is None
    assert runtime.engine.active_preset_id == preset
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.blackout is True
    runtime.engine.strobe_release()
    assert runtime.engine.overlays.strobe_held is False


def test_analysis_of_ten_seconds_stays_under_a_frame_budget() -> None:
    samples = click_track(120, 10.0, 48000)
    started = time.perf_counter()
    _play(samples, 48000)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.75, f"10s of audio took {elapsed:.3f}s"


def test_riser_strobe_does_not_move_heads() -> None:
    beam = _Lamp("beam", FixtureKind.BEAM)
    par = _Lamp("par", FixtureKind.PAR)
    early = build_auto_stage(
        [par, beam], "orange", "riser", 0.2, 0.6, riser_age=0.2, flash_hold=1.0
    )
    late = build_auto_stage(
        [par, beam],
        "orange",
        "riser",
        0.2,
        0.6,
        riser_age=STROBE_FRAGMENT_S + 0.1,
        flash_hold=0.0,
    )
    assert isinstance(early.fixtures["par"], ParIntent)
    assert early.fixtures["par"].strobe == 0.0
    assert early.fixtures["par"].intensity == 1.0
    assert late.fixtures["par"].strobe == 0.0
    assert early.fixtures["beam"].strobe == 0.0
    assert late.fixtures["beam"].strobe == 0.0
    assert early.fixtures["beam"].pan is None and early.fixtures["beam"].tilt is None
