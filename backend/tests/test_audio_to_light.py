"""Audio callback to rendered frame, on a virtual clock.

The capture thread is not opened. A fake callback pushes blocks into the same
ingest the real stream uses. Listening is not treated as music.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.audio.bpm import (
    PHASE_PULL,
    TEMPO_HOLD_S,
    TEMPO_RELEASE_S,
    click_track,
    estimate_tempo,
    inspect_tempo,
)
from orng_led.audio.service import BpmService, local_diagnostics_allowed
from orng_led.config.io import load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import BeamIntent, Rgbw, StageIntent
from orng_led.engine.renderer import render_stage
from orng_led.engine.show_whitelist import assert_lights_dark
from orng_led.main import create_app

RATE = 44100


def _slice(samples: list[float], start: float, seconds: float, rate: int = RATE) -> list[float]:
    begin = int(start * rate)
    end = begin + int(seconds * rate)
    return samples[begin:end]


def _stream(
    service: BpmService,
    samples: list[float],
    *,
    moment: float = 0.0,
    hop: float = 0.25,
    delay: float = 0.0,
) -> float:
    """Push contiguous blocks. ``delay`` is how old the newest sample is."""
    cursor = 0.0
    total = len(samples) / RATE
    while cursor + hop <= total + 1e-9:
        service.submit_block(
            _slice(samples, cursor, hop),
            RATE,
            captured_at=moment - delay,
            now=moment,
            channels=1,
        )
        cursor += hop
        moment += hop
    return moment


def _sine(seconds: float, amp: float, freq: float = 220.0) -> list[float]:
    count = int(seconds * RATE)
    return [amp * math.sin(2.0 * math.pi * freq * i / RATE) for i in range(count)]


def _ramp(seconds: float, start: float, end: float) -> list[float]:
    count = int(seconds * RATE)
    samples: list[float] = []
    for index in range(count):
        amp = start + (end - start) * (index / max(count - 1, 1))
        samples.append(amp * math.sin(2.0 * math.pi * 180.0 * index / RATE))
    return samples


def _noise(seconds: float, amp: float, seed: int) -> list[float]:
    count = int(seconds * RATE)
    value = seed & 0x7FFFFFFF or 1
    samples: list[float] = []
    for _ in range(count):
        value = (1103515245 * value + 12345) & 0x7FFFFFFF
        samples.append(((value / 0x7FFFFFFF) * 2.0 - 1.0) * amp)
    return samples


def _role_values(show: object, frame: list[int], role: ChannelRole, kind: FixtureKind) -> list[int]:
    found: list[int] = []
    for fixture in show.patch.fixtures:
        if fixture.kind is not kind:
            continue
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            if channel.role is role:
                index = global_channel(fixture.start_address, channel.local) - 1
                found.append(frame[index])
    return found


def test_overlapping_blocks_are_one_lock_vote(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    proofs: list[int] = []
    original = service.follower.push

    def _count(*args: object, **kwargs: object) -> object:
        proofs.append(1)
        return original(*args, **kwargs)

    service.follower.push = _count  # type: ignore[method-assign]
    samples = click_track(120, 1.0, RATE)
    _stream(service, samples, hop=0.1)
    assert not service.follower.locked
    assert len(proofs) <= 2
    _stream(service, click_track(120, 4.0, RATE), moment=1.0, hop=0.1)
    assert service.follower.locked
    assert len(proofs) < 20


def test_variable_blocks_and_capture_delay_move_phase(tmp_path: Path) -> None:
    prompt = click_track(120, 6.0, RATE)
    plain = BpmService(tmp_path / "plain", "room")
    late = BpmService(tmp_path / "late", "room")
    hops = (0.2, 0.5, 0.15, 0.4, 0.35)
    moment = 0.0
    cursor = 0.0
    hop_index = 0
    while cursor + 0.2 <= 6.0:
        hop = hops[hop_index % len(hops)]
        block = _slice(prompt, cursor, hop)
        if not block:
            break
        plain.submit_block(block, RATE, captured_at=moment, now=moment)
        late.submit_block(block, RATE, captured_at=moment - 0.25, now=moment)
        cursor += hop
        moment += hop
        hop_index += 1
    assert plain.follower.locked and late.follower.locked
    assert plain.follower.bpm == pytest.approx(120, abs=1)
    assert late.follower.bpm == pytest.approx(120, abs=1)
    gap = abs(late.follower.phase_at(moment) - plain.follower.phase_at(moment))
    gap = min(gap, 1.0 - gap)
    assert gap >= PHASE_PULL
    assert gap <= 0.5 + PHASE_PULL


def test_a_stopped_callback_holds_then_leaves_the_lock(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    moment = _stream(service, click_track(120, 4.0, RATE))
    assert service.follower.locked
    service.poll(0.6)
    assert service.follower.locked
    assert service.diagnostics()["lock_reason"] == "hold"
    service.poll(TEMPO_HOLD_S)
    assert not service.follower.locked
    assert service.diagnostics()["lock_reason"] == "release"
    held = service.status()
    assert held["bpm"] is None
    assert held["locked"] is False
    assert held["sync"] == "hold"
    assert service.diagnostics()["accepted_bpm"] is None
    service.poll(TEMPO_RELEASE_S)
    assert service.follower.bpm is None
    assert service.diagnostics()["lock_reason"] == "idle"
    assert moment > 0.0


def test_one_late_frame_matches_many_small_ones_and_can_finish_the_release(
    tmp_path: Path,
) -> None:
    samples = click_track(120, 4.0, RATE)
    stepped = BpmService(tmp_path / "step", "room")
    jumped = BpmService(tmp_path / "jump", "room")
    end = _stream(stepped, samples)
    _stream(jumped, samples)
    for _ in range(10):
        stepped.poll(0.05)
    jumped.poll(0.5)
    gap = abs(stepped.follower.phase_at(stepped.clock_s) - jumped.follower.phase_at(jumped.clock_s))
    gap = min(gap, 1.0 - gap)
    assert gap == pytest.approx(0.0, abs=1e-6)
    stalled = BpmService(tmp_path / "stall", "room")
    _stream(stalled, samples)
    stalled.poll(TEMPO_HOLD_S + TEMPO_RELEASE_S + 0.1)
    assert stalled.follower.bpm is None
    assert end > 0.0


def test_the_capture_thread_cannot_rewind_the_show_clock(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service._now = 50.0
    stop = threading.Event()
    rewound: list[tuple[float, float]] = []

    def _feed() -> None:
        block = [0.0] * 200
        while not stop.is_set():
            service.feed(block, RATE)

    def _poll() -> None:
        last = service.clock_s
        while not stop.is_set():
            service.poll(0.01)
            current = service.clock_s
            if current + 1e-9 < last:
                rewound.append((last, current))
            last = current

    threads = [threading.Thread(target=_feed), threading.Thread(target=_poll)]
    for thread in threads:
        thread.start()
    stop.wait(0.3)
    stop.set()
    for thread in threads:
        thread.join(timeout=2)
    assert not rewound
    assert service.clock_s >= 50.0


def test_a_failed_audio_block_does_not_stop_the_frame_or_a_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    show = load_show_config()
    runtime = AppRuntime.create(autostart_loop=False, show=show, config_dir=tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("capture failed")

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", _boom)
    runtime.bpm.submit_block([0.2] * RATE, RATE, captured_at=1.0, now=1.0)
    runtime.engine.strobe_press()
    snapshot = runtime.tick(0.05)
    assert len(snapshot.frame) == 512
    assert runtime.engine.overlays.strobe_held is True
    runtime.engine.strobe_release()
    assert runtime.engine.overlays.strobe_held is False
    assert runtime.input_dispatcher is not None
    runtime.input_dispatcher.force_release_all()
    report = runtime.bpm.diagnostics()
    assert report["capture"] != "unavailable"
    assert int(report["analysis_errors"]) == 1
    assert report["lock_reason"] == "analysis_error"


def test_diagnostics_are_local_levels_not_a_recording(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.capture = "listening"
    loud = [0.5] * int(0.25 * RATE)
    service.submit_block(loud, RATE, captured_at=0.0, now=0.05, channels=2)
    report = service.diagnostics()
    assert report["capture"] == "listening"
    assert report["listening_means_music"] is False
    assert report["accepted_bpm"] is None
    assert report["sample_rate"] == RATE
    assert report["channels"] == 2
    assert float(report["rms"]) == pytest.approx(0.5, abs=1e-3)
    assert float(report["peak"]) == pytest.approx(0.5, abs=1e-3)
    assert float(report["callback_age_s"]) < 0.2
    assert report["history_trimmed"] == 0
    assert "samples" not in report
    clipped_service = BpmService(tmp_path / "clip", "room")
    clipped_service.submit_block([1.2] * int(0.5 * RATE), RATE, captured_at=0.0, now=0.5)
    assert float(clipped_service.diagnostics()["clipping"]) == pytest.approx(1.0)
    assert local_diagnostics_allowed("127.0.0.1")
    assert local_diagnostics_allowed("::1")
    assert not local_diagnostics_allowed("10.0.0.8")
    assert not local_diagnostics_allowed("testclient")


def test_diagnostics_route_stays_off_the_desk_and_off_the_lan(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application, client=("127.0.0.1", 50000)) as local:
        body = local.get("/api/audio/diagnostics")
        assert body.status_code == 200
        payload = body.json()
        assert payload["listening_means_music"] is False
        state = local.get("/api/state").json()
        assert "listening_means_music" not in state["bpm"]
        assert "raw_bpm" not in state["bpm"]
    with TestClient(application, client=("10.0.0.8", 50000)) as remote:
        assert remote.get("/api/audio/diagnostics").status_code == 403


def _reset_ear(runtime: AppRuntime) -> None:
    from orng_led.audio.auto_show import SectionTracker
    from orng_led.audio.bpm import TempoFollower

    runtime.bpm.section = SectionTracker()
    runtime.bpm.follower = TempoFollower()
    runtime.bpm._history = runtime.bpm._history[:0]
    runtime.bpm._novel = runtime.bpm._novel[:0]
    runtime.bpm._unvoted_s = 0.0
    runtime.bpm._last_proof_at = None
    runtime.bpm._heard_at = None


def _play_show(
    runtime: AppRuntime,
    samples: list[float],
    *,
    moment: float = 0.0,
    hop: float = 0.25,
) -> tuple[float, list[str], bool]:
    names: list[str] = []
    saw_strobe = False
    cursor = 0.0
    total = len(samples) / RATE
    while cursor + hop <= total + 1e-9:
        runtime.bpm.submit_block(
            _slice(samples, cursor, hop),
            RATE,
            captured_at=moment,
            now=moment,
        )
        frame = runtime.tick(hop).frame
        if any(
            value > 0
            for value in _role_values(
                runtime.engine.show, frame, ChannelRole.STROBE_SPEED, FixtureKind.PAR
            )
        ):
            saw_strobe = True
        moment += hop
        cursor += hop
        section = runtime.bpm.section.section
        if not names or names[-1] != section:
            names.append(section)
    return moment, names, saw_strobe


def test_preset_and_auto_frames_follow_the_saved_rules(tmp_path: Path) -> None:
    show = load_show_config()
    runtime = AppRuntime.create(autostart_loop=False, show=show, config_dir=tmp_path)
    chosen = runtime.engine.active_preset_id
    runtime.engine.set_face(True, brightness=0.8)
    runtime.engine.strobe_press()
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    moment, names, _saw = _play_show(runtime, click_track(120, 4.0, RATE))
    dark = runtime.tick(0.05)
    assert_lights_dark(show, dark.frame)
    assert runtime.engine.overlays.face_on is True
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.active_preset_id == chosen
    assert "drop" not in names

    runtime.engine.set_blackout(False)
    runtime.engine.strobe_release()
    _reset_ear(runtime)
    moment, names, saw_strobe = _play_show(runtime, _ramp(5.0, 0.02, 0.4), moment=moment)
    assert "riser" not in names
    assert "drop" not in names
    assert not saw_strobe

    runtime.engine.strobe_press()
    for _ in range(8):
        moment += 0.2
        runtime.tick(0.2)
    lost_frames = [runtime.tick(0.05).frame for _ in range(30)]
    assert runtime.bpm.section.section == "break"
    assert runtime.engine.overlays.strobe_held is True
    assert all(
        value == 0
        for frame in lost_frames
        for value in _role_values(show, frame, ChannelRole.STROBE_SPEED, FixtureKind.PAR)
    )
    assert any(
        value > 0
        for frame in lost_frames
        for value in _role_values(show, frame, ChannelRole.DIMMER, FixtureKind.PAR)
    )
    runtime.engine.strobe_release()
    assert runtime.engine.overlays.strobe_held is False

    runtime.engine.select_preset("NONE")
    runtime.apply_bpm_sync(scope="auto", palette="blue")
    _play_show(runtime, click_track(100, 3.0, RATE), moment=moment)
    assert runtime.engine.active_preset_id == "NONE"
    blue = runtime.engine.auto_base
    assert blue is not None
    runtime.apply_bpm_sync(palette="orange")
    runtime.tick(0.05)
    orange = runtime.engine.auto_base
    assert orange is not None
    assert blue.fixtures.keys() != set() or orange.fixtures.keys() != set()
    shared = set(blue.fixtures) & set(orange.fixtures)
    assert shared
    assert any(blue.fixtures[item].color != orange.fixtures[item].color for item in shared)

    runtime.engine.select_preset(chosen)
    current = runtime.tick(0.05)
    if current.episode_index == 0:
        runtime.engine.seek_episode(1)
        current = runtime.tick(0.05)
    before = current.episode_index
    runtime.apply_bpm_sync(enabled=True, scope="preset")
    held_episode = runtime.tick(0.05)
    assert runtime.engine.auto_base is None
    assert runtime.engine.active_preset_id == chosen
    assert held_episode.episode_index == before
    runtime.apply_bpm_sync(enabled=False)
    runtime.tick(0.05)
    assert runtime.engine.auto_base is None
    assert runtime.engine.active_preset_id == chosen


def test_gain_tone_noise_and_a_long_groove_do_not_invent_a_drop(tmp_path: Path) -> None:
    show = load_show_config()
    runtime = AppRuntime.create(autostart_loop=False, show=show, config_dir=tmp_path)
    runtime.engine.set_blackout(False)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    quiet = (
        _sine(2.0, 0.05) + _sine(2.0, 0.45),
        _sine(4.0, 0.4, freq=440.0),
        _noise(4.0, 0.5, 3),
    )
    for samples in quiet:
        _reset_ear(runtime)
        _, names, saw_strobe = _play_show(runtime, samples)
        assert "drop" not in names
        assert "riser" not in names
        assert not saw_strobe
        assert not runtime.bpm.follower.locked
    _reset_ear(runtime)
    _, names, saw_strobe = _play_show(runtime, click_track(118, 8.0, RATE))
    assert "drop" not in names
    assert "riser" not in names
    assert not saw_strobe
    assert runtime.bpm.follower.locked


def test_beam_brightness_stays_binary_and_face_keeps_its_own_fader() -> None:
    """Recorded canon: head RGB is on or off, and Face does not follow show master."""
    show = load_show_config()
    beam = next(fixture for fixture in show.patch.fixtures if fixture.kind is FixtureKind.BEAM)
    dim = StageIntent(
        fixtures={
            beam.id: BeamIntent(
                pan=0.4,
                tilt=0.4,
                dimmer=0.25,
                color=Rgbw(r=1, g=0, b=0),
                shutter_open=True,
            )
        }
    )
    full = StageIntent(
        fixtures={
            beam.id: BeamIntent(
                pan=0.4,
                tilt=0.4,
                dimmer=1.0,
                color=Rgbw(r=1, g=0, b=0),
                shutter_open=True,
            )
        }
    )
    pose = {beam.id: BeamMotionState(pan=0.4, tilt=0.4)}
    dim_frame = render_stage(show, dim, pose, master=0.2)
    full_frame = render_stage(show, full, pose, master=1.0)
    red = next(
        global_channel(beam.start_address, channel.local) - 1
        for channel in show.profile_for(beam).channels
        if channel.role is ChannelRole.RED
    )
    assert dim_frame[red] == int(round(0.25 * 255))
    assert full_frame[red] == 255
    same = render_stage(show, dim, pose, master=1.0)
    assert same[red] == dim_frame[red]
    face = next(fixture for fixture in show.patch.fixtures if fixture.kind is FixtureKind.FACE_PAR)
    from orng_led.engine.intents import ParIntent

    stage = StageIntent(
        fixtures={face.id: ParIntent(color=Rgbw(r=1, g=1, b=1), intensity=0.8)}
    )
    low = render_stage(show, stage, {}, master=0.2)
    high = render_stage(show, stage, {}, master=1.0)
    dimmer = next(
        global_channel(face.start_address, channel.local) - 1
        for channel in show.profile_for(face).channels
        if channel.role is ChannelRole.DIMMER
    )
    assert low[dimmer] == high[dimmer]
    assert low[dimmer] > 0


def _bed_clicks(bpm: float, seconds: float, rate: int = RATE) -> list[float]:
    """Clicks on a steady tone. The RMS stays up; the rhythm is the clicks."""
    total = int(seconds * rate)
    samples = [0.12 * math.sin(2.0 * math.pi * 220.0 * i / rate) for i in range(total)]
    step = max(1, int(rate * 60.0 / bpm))
    for start in range(0, total, step):
        for offset in range(min(90, total - start)):
            samples[start + offset] += 0.2 * (1.0 - offset / 90.0)
    return samples


def test_dense_bed_keeps_a_lock_and_is_not_silence(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.capture = "listening"
    service.configure(enabled=True)
    clean = click_track(120, 4.0, RATE)
    service.feed(clean, RATE, now=1.0, dt_s=0.5)
    service.feed(clean, RATE, now=1.5, dt_s=0.5)
    assert service.follower.locked is True
    dense = _bed_clicks(120, 4.0)
    reading = estimate_tempo(dense, RATE)
    assert reading is not None
    assert reading.bpm == pytest.approx(120, abs=3)
    moment = 2.0
    for _ in range(8):
        moment += 0.5
        service.feed(dense, RATE, now=moment, dt_s=0.5)
    assert service.follower.locked is True
    assert service.status()["bpm"] == pytest.approx(120, abs=3)
    assert service.status()["signal"] is True
    tone = _sine(4.0, 0.4)
    assert estimate_tempo(tone, RATE) is None
    service.feed(tone, RATE, now=moment + 0.5, dt_s=0.5)
    assert service.status()["signal"] is True
    assert service.status()["bpm"] == pytest.approx(120, abs=3)


def test_status_names_silence_and_seeking_separately(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.capture = "no_device"
    assert service.status()["signal"] is False
    service.capture = "listening"
    service.feed([0.0] * RATE, RATE, now=1.0, dt_s=1.0)
    quiet = service.status()
    assert quiet["bpm"] is None
    assert quiet["signal"] is False
    service.feed(_sine(2.0, 0.4), RATE, now=2.0, dt_s=1.0)
    seeking = service.status()
    assert seeking["bpm"] is None
    assert seeking["signal"] is True
    assert seeking["capture"] == "listening"


def test_bar_wait_does_not_freeze_the_episode(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.configure(enabled=True, mode="bar", scope="preset")
    clicks = click_track(120, 4.0, RATE)
    service.feed(clicks, RATE, now=1.0, dt_s=0.5)
    service.feed(clicks, RATE, now=1.5, dt_s=0.5)
    assert service.follower.locked is True
    preset = 0.0
    moment = 1.5
    run = 0
    longest = 0
    for _ in range(30 * 6):
        moment += 1.0 / 30.0
        service.poll(1.0 / 30.0)
        if preset < 1.0 and moment > 1.7:
            preset = 3.0
        delta = service.bar_delta(
            episode_index=0,
            episode_time=0.0,
            duration=8.0,
            preset_elapsed=preset,
            wall=1.0 / 30.0,
        )
        if delta is None:
            break
        if delta <= 1e-9:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
            preset += delta
    assert longest < 10
    assert preset > 3.2


def test_feed_clock_engine_keeps_moving_through_gaps(tmp_path: Path) -> None:
    """Real ingest, clock and engine tick. No sound card and no Art-Net."""
    from orng_led.input.contract import InputAction, InputEvent, InputSource

    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.engine.set_blackout(True)
    runtime.apply_bpm_sync(enabled=True, mode="smooth", scope="preset")
    audio = click_track(120, 10.0, RATE)
    # A few missed kicks and a softer bar, still one continuous recording.
    soft = [sample * 0.15 for sample in audio[int(4.0 * RATE) : int(5.0 * RATE)]]
    audio[int(4.0 * RATE) : int(5.0 * RATE)] = soft
    step = max(1, int(RATE * 0.5))
    for start in range(int(6.0 * RATE), int(7.0 * RATE), step * 4):
        for offset in range(min(90, len(audio) - start)):
            audio[start + offset] = 0.0
    cursor = 0.0
    hop = 0.2
    locked_at: float | None = None
    phase0 = 0.0
    phase_error = 0.0
    stalled = 0
    longest_stall = 0
    held_without_audio = False
    while cursor + hop <= 9.0:
        block = audio[int(cursor * RATE) : int((cursor + hop) * RATE)]
        runtime.bpm.submit_block(
            block,
            RATE,
            captured_at=runtime.bpm.clock_s,
            now=runtime.bpm.clock_s,
        )
        frames = 6 if abs(cursor - 3.0) > 0.05 else 30
        if frames == 30:
            held_without_audio = True
        for _ in range(frames):
            before = runtime.engine.preset_elapsed_s
            runtime.tick(1.0 / 30.0)
            moved = runtime.engine.preset_elapsed_s - before
            if runtime.bpm.follower.locked and moved <= 1e-6:
                stalled += 1
                longest_stall = max(longest_stall, stalled)
            else:
                stalled = 0
        if runtime.bpm.follower.locked:
            if locked_at is None:
                locked_at = runtime.bpm.clock_s
                phase0 = runtime.bpm.follower.phase_at(locked_at)
            elapsed = runtime.bpm.clock_s - locked_at
            expected = (phase0 + elapsed * (runtime.bpm.follower.bpm or 120.0) / 60.0) % 1.0
            gap = abs(runtime.bpm.follower.phase_at(runtime.bpm.clock_s) - expected)
            phase_error = max(phase_error, min(gap, 1.0 - gap))
        cursor += hop
    assert held_without_audio is True
    assert runtime.bpm.follower.locked is True
    assert runtime.bpm.status()["bpm"] == pytest.approx(120, abs=3)
    assert phase_error < 0.2
    assert longest_stall < 10
    assert runtime.engine.bpm_fx is False
    runtime.engine.strobe_press()
    runtime.tick(1.0 / 30.0)
    phase = runtime.engine.overlays.strobe_phase
    runtime.tick(1.0 / 30.0)
    assert runtime.engine.overlays.strobe_phase != phase
    runtime.bpm.configure(scope="auto")
    held = runtime.input_dispatcher.dispatch(
        InputEvent(InputAction.STROBE_PRESS, InputSource.KEYBOARD)
    )
    assert held.accepted is True
    assert runtime.engine.overlays.strobe_held is True
    blackout = runtime.input_dispatcher.dispatch(
        InputEvent(InputAction.BLACKOUT_TOGGLE, InputSource.KEYBOARD)
    )
    assert blackout.accepted is True
    phone = runtime.input_dispatcher.dispatch(
        InputEvent(InputAction.STROBE_PRESS, InputSource.UI)
    )
    assert phone.accepted is True
    phone_blackout = runtime.input_dispatcher.dispatch(
        InputEvent(InputAction.BLACKOUT_TOGGLE, InputSource.UI)
    )
    assert phone_blackout.accepted is True


def test_analysis_error_then_clicks_restore_tempo_and_the_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    calls = {"n": 0}

    def flaky(samples: list[float], rate: int):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tempo failed")
        return inspect_tempo(samples, rate)

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", flaky)
    clicks = click_track(120, 8.0, RATE)
    moment = 0.0
    hop = 1.0
    cursor = 0.0
    while cursor + hop <= 8.0 + 1e-9:
        runtime.bpm.submit_block(
            clicks[int(cursor * RATE) : int((cursor + hop) * RATE)],
            RATE,
            captured_at=moment,
            now=moment,
        )
        runtime.tick(hop)
        cursor += hop
        moment += hop
    status = runtime.bpm.status()
    report = runtime.bpm.diagnostics()
    assert status["capture"] == "listening"
    assert status["signal"] is True
    assert status["locked"] is True
    assert status["bpm"] == pytest.approx(120, abs=1)
    assert int(report["analysis_errors"]) == 1
    assert float(report["callback_age_s"]) < 0.5
    assert float(report["analysis_age_s"]) < 0.5
    assert report["analysis_error"] is None


def test_a_loud_tone_is_signal_without_pretending_the_cable_is_gone(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.submit_block(_sine(1.2, 0.4), RATE, captured_at=0.0, now=1.2)
    status = service.status()
    assert status["capture"] == "listening"
    assert status["signal"] is True
    assert status["bpm"] is None
    assert status["capture"] != "unavailable"


def test_non_finite_audio_does_not_kill_the_input(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.capture = "listening"
    block = [0.2] * RATE
    block[10] = float("nan")
    block[11] = float("inf")
    service.submit_block(block, RATE, captured_at=0.0, now=1.0)
    assert service.capture == "listening"
    assert service.diagnostics()["analysis_errors"] == 0


def test_disabled_live_input_does_not_estimate_tempo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BpmService(tmp_path, "room")
    service.enabled = False

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tempo ran while BPM was off")

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", boom)
    started = time.monotonic()
    service.note_live_input([0.2] * RATE, RATE)
    assert time.monotonic() - started < 0.05
    assert service.capture == "listening"
    assert service.diagnostics()["analysis_errors"] == 0


def test_history_trim_is_not_an_input_overflow(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.submit_block([0.05] * (RATE * 5), RATE, captured_at=0.0, now=5.0)
    report = service.diagnostics()
    assert report["overflows"] == 0
    assert int(report["history_trimmed"]) >= 1


def test_blackout_does_not_wait_for_a_stuck_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    started = threading.Event()
    release = threading.Event()

    def blocked(*_args: object, **_kwargs: object) -> None:
        started.set()
        release.wait(2)
        return None

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", blocked)
    worker = threading.Thread(
        target=lambda: runtime.bpm.submit_block([0.2] * RATE, RATE, captured_at=1.0, now=1.0)
    )
    worker.start()
    assert started.wait(1)
    began = time.monotonic()
    runtime.apply_blackout(True)
    assert time.monotonic() - began < 0.1
    assert runtime.engine.overlays.blackout is True
    release.set()
    worker.join(2)
    assert not worker.is_alive()


def test_slow_state_sender_does_not_stall_frames(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)

    async def slow(_payload: dict) -> None:
        await asyncio.sleep(0.12)

    async def run() -> int:
        runtime.subscribe(slow)
        runtime._running = True
        task = asyncio.create_task(runtime._tick_loop())
        await asyncio.sleep(0.45)
        runtime._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return runtime.output.frames_sent

    sent = asyncio.run(run())
    assert sent >= 10


def test_bare_sender_keeps_one_task_and_the_latest_state(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    seen: list[int] = []
    gate = asyncio.Event()

    async def slow(payload: dict) -> None:
        seen.append(int(payload["state"]["sequence"]))
        await gate.wait()

    async def run() -> None:
        runtime.subscribe(slow)
        runtime.sequence = 1
        runtime.request_broadcast()
        await asyncio.sleep(0.02)
        runtime.sequence = 7
        for _ in range(30):
            runtime.request_broadcast()
        assert len(runtime._sender_tasks) == 1
        gate.set()
        await asyncio.sleep(0.05)
        runtime.unsubscribe(slow)
        assert runtime._sender_tasks == {}

    asyncio.run(run())
    assert seen[0] == 1
    assert seen[-1] == 7
    assert len(seen) <= 3


def test_old_buffer_keeps_callback_time_when_analysis_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def late(*_args: object, **_kwargs: object) -> None:
        time.sleep(0.25)
        raise RuntimeError("late analysis")

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", late)
    service = BpmService(tmp_path, "room")
    service.configure(enabled=True)
    captured = time.monotonic() - 5.0
    service.submit_block([0.2] * RATE, RATE, captured_at=captured)
    report = service.diagnostics()
    assert int(report["analysis_errors"]) == 1
    assert float(report["callback_age_s"]) >= 4.5
    assert report["capture"] != "unavailable"


def test_bpm_off_does_not_run_tempo_or_stretch_the_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tempo ran while BPM was off")

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", forbidden)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.apply_bpm_sync(enabled=False)
    started = time.perf_counter()
    for _ in range(20):
        runtime.bpm.note_live_input([0.2] * 2048, RATE, callback_at=time.monotonic())
        runtime.tick(1.0 / 30.0)
    assert time.perf_counter() - started < 0.25
    assert runtime.bpm.enabled is False
    assert len(runtime.bpm._history) == 0


def test_capture_recovers_and_a_bad_block_keeps_the_callback_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orng_led.audio.devices import InputDevice

    service = BpmService(tmp_path, "room")
    service.configure(enabled=True)
    service._stop.wait = lambda timeout=None: None  # type: ignore[method-assign]
    original_turn = service._capture_turn
    calls = {"n": 0}

    def turn(*_args: object, **_kwargs: object) -> tuple[None, int, None, list[float]]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("iteration failed")
        service.capture = "listening"
        service._stop.set()
        return None, RATE, None, []

    monkeypatch.setattr(service, "_capture_turn", turn)
    monkeypatch.setattr("orng_led.audio.service._import_sounddevice", lambda: object())
    service._loop()
    assert calls["n"] == 2
    assert service.capture == "listening"
    service._capture_turn = original_turn  # type: ignore[method-assign]

    device = InputDevice(id="line-1", name="Line In", inputs=2, index=1)
    listed = {"devices": []}

    def query(_sd: object) -> list[InputDevice]:
        return list(listed["devices"])

    opens = {"n": 0}

    class _Stream:
        active = True

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

    def open_line(_sd: object, _device: InputDevice, _chunks: list, _lock: object, meta: dict) -> object:
        opens["n"] += 1
        if opens["n"] == 1:
            return None
        now = time.monotonic()
        meta["opened"] = now
        meta["last"] = now
        meta["channels"] = 1.0
        meta["device_latency"] = 0.0
        return _Stream(), RATE

    monkeypatch.setattr("orng_led.audio.service._query", query)
    monkeypatch.setattr("orng_led.audio.service._open_line", open_line)
    guard = threading.Lock()
    meta = {"last": 0.0, "opened": 0.0, "channels": 1.0}
    service._capture_turn(object(), None, [], [], guard, meta, RATE, None)
    assert service.capture == "no_device"
    listed["devices"] = [device]
    service._capture_turn(object(), None, [], [], guard, meta, RATE, None)
    assert service.capture == "unavailable"
    service._capture_turn(object(), None, [], [], guard, meta, RATE, None)
    assert service.capture == "listening"

    stale_at = time.monotonic() - 10.0
    meta = {"last": stale_at, "opened": stale_at, "channels": 1.0}
    stream = _Stream()
    chunks = [float("nan"), float("inf"), 0.2]
    service._capture_turn(object(), stream, chunks, [], guard, meta, RATE, device.id)
    assert service.capture == "listening"
    fresh_meta = {
        "last": time.monotonic() - 6.0,
        "opened": time.monotonic(),
        "channels": 1.0,
    }
    quiet = _Stream()
    service._capture_turn(
        object(),
        quiet,
        [float("nan"), 0.2],
        [],
        guard,
        fresh_meta,
        RATE,
        device.id,
    )
    assert float(service.diagnostics()["callback_age_s"]) >= 5.0
    assert service.capture == "listening"


def test_audio_history_and_error_journal_stay_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orng_led.audio.planner import _JOURNAL_LIMIT
    from orng_led.audio.service import MAX_BUFFER_S

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("x" * 400)

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", boom)
    service = BpmService(tmp_path, "room")
    service.configure(enabled=True)
    for index in range(6):
        service.submit_block([0.2] * (RATE * 2), RATE, captured_at=float(index), now=float(index))
    assert len(service._history) <= int(RATE * MAX_BUFFER_S)
    assert service._analysis_error is not None
    assert len(str(service._analysis_error)) <= 180
    for index in range(40):
        service.planner._note(float(index), "wash")
    assert len(service.planner.journal()) <= _JOURNAL_LIMIT


def test_bpm_off_does_not_open_or_poll_the_capture_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = BpmService(tmp_path, "room")
    service.configure(enabled=False)
    called = {"import": 0}

    def boom() -> None:
        called["import"] += 1
        raise AssertionError("portaudio")

    monkeypatch.setattr("orng_led.audio.service._import_sounddevice", boom)
    service._stop.wait = lambda timeout=None: service._stop.set()  # type: ignore[method-assign]
    service._loop()
    assert called["import"] == 0
    assert service.capture == "idle"


def test_cached_network_probe_does_not_block_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    from orng_led.raspberry import venue_ready

    def slow(*, target: str | None = None):
        time.sleep(0.3)
        return True, True, None

    monkeypatch.setattr(venue_ready, "probe_network", slow)
    venue_ready._probe_cache["running"] = False
    venue_ready._probe_cache["mono"] = 0.0
    began = time.monotonic()
    venue_ready.cached_probe_network(target="2.0.0.11")
    assert time.monotonic() - began < 0.05
