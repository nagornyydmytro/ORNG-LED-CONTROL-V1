"""Planning time stays ahead. A tempo jump is not a rewind of the show.

These tests render in memory. They do not arm Art-Net or open a capture device.
"""

from __future__ import annotations

import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import SectionTracker, StructuralEvent
from orng_led.audio.bpm import (
    OCTAVE_AGREE,
    PHASE_PULL,
    TEMPO_GLIDE,
    TEMPO_HOLD_S,
    TEMPO_RELEASE_S,
    TempoFollower,
    TempoReading,
)
from orng_led.audio.planner import EpisodePlanner
from orng_led.presets.models import (
    MUSICAL_CYCLE_VERSION,
    EpisodeCard,
    LookAction,
    PresetDocument,
)
from orng_led.presets.program import YamlPresetProgram

_SEEN_LIMIT = 64


def _lock(bpm: float, now: float = 0.0) -> TempoFollower:
    follower = TempoFollower()
    follower.push(TempoReading(bpm, 0.0, 1.0), now=now)
    follower.push(TempoReading(bpm, 0.0, 1.0), now=now)
    assert follower.locked is True
    assert follower.bpm == pytest.approx(bpm)
    return follower


def _confirm(follower: TempoFollower, bpm: float, now: float) -> None:
    for _ in range(OCTAVE_AGREE):
        follower.push(TempoReading(bpm, 0.0, 1.0), now=now)


def _beats_for(bpm: float, beats: float) -> float:
    return beats * 60.0 / bpm


def test_half_double_and_the_two_thirds_ratios_do_not_scale_the_counter() -> None:
    """About eight beats used to become four when 140 was confirmed as 70."""
    cases = ((140.0, 70.0), (90.0, 180.0), (120.0, 80.0), (100.0, 150.0))
    for current, nxt in cases:
        moment = _beats_for(current, 8.0)
        follower = _lock(current)
        before = follower.beats_at(moment)
        assert before == pytest.approx(8.0, abs=1e-6)
        _confirm(follower, nxt, moment)
        assert follower.bpm == pytest.approx(nxt)
        assert follower.beats_at(moment) >= before - 1e-9
        assert follower.beats_at(moment) < before + 0.5


def test_a_real_accel_still_glides_and_does_not_jump_the_counter() -> None:
    follower = _lock(100.0)
    moment = _beats_for(100.0, 8.0)
    before = follower.beats_at(moment)
    follower.push(TempoReading(140.0, 0.0, 1.0), now=moment)
    assert follower.bpm == pytest.approx(100.0 + (140.0 - 100.0) * TEMPO_GLIDE)
    assert follower.beats_at(moment) == pytest.approx(before)


def test_release_keeps_the_beats_it_reached_and_a_relock_continues() -> None:
    """advance() used to drop 8.8 back to the last commit, about 2."""
    follower = _lock(120.0)
    follower.push(TempoReading(120.0, 0.0, 1.0), now=1.0)
    assert follower.beats_at(1.0) == pytest.approx(2.0, abs=0.05)
    reached = follower.beats_at(4.4)
    assert reached == pytest.approx(8.8, abs=0.15)
    follower.advance(4.6)
    assert follower.bpm is None
    assert follower.locked is False
    kept = follower.beats_at(4.6)
    assert kept >= reached - 1e-9
    coast = follower.beats_at(10.0)
    assert coast > kept + 1.0
    assert follower.phase_at(10.0) == 0.0
    follower.push(TempoReading(120.0, 0.0, 1.0), now=10.0)
    follower.push(TempoReading(120.0, 0.0, 1.0), now=10.0)
    assert follower.locked is True
    assert follower.beats_at(10.0) >= coast - 1e-9
    assert follower.beats_at(10.5) > follower.beats_at(10.0)


def test_one_late_step_and_many_small_steps_stop_at_the_same_beat() -> None:
    jumped = _lock(120.0)
    stepped = _lock(120.0)
    jumped.advance(TEMPO_HOLD_S + TEMPO_RELEASE_S + 5.0)
    moment = 0.0
    while moment < TEMPO_HOLD_S + TEMPO_RELEASE_S + 5.0:
        moment += 0.05
        stepped.advance(moment)
    assert jumped.bpm is None and stepped.bpm is None
    assert jumped.beats_at(99.0) == pytest.approx(stepped.beats_at(99.0), abs=1e-6)
    assert jumped.beats_at(99.0) > 2.0


def test_turning_bpm_off_does_not_drop_the_beats_accumulated_during_release() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    service = runtime.bpm
    service.enabled = True
    service._now = 10.0
    follower = service.follower
    follower.bpm = 120.0
    follower.locked = True
    follower._started = True
    follower._beats = 8.0
    follower._at = 10.0
    follower._fresh_until = 100.0
    service.configure(enabled=False)
    for _ in range(40):
        service.poll(0.05)
    assert follower.bpm is None
    assert follower.beats_at(service.clock_s) >= 10.0 - 1e-6
    service.configure(enabled=True, scope="preset")
    follower.push(TempoReading(120.0, 0.0, 1.0), now=service.clock_s)
    follower.push(TempoReading(120.0, 0.0, 1.0), now=service.clock_s)
    assert follower.beats_at(service.clock_s) >= 10.0 - 1e-6


def test_preset_and_auto_do_not_rewind_the_clock() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    service = runtime.bpm
    service.enabled = True
    service.scope = "preset"
    follower = service.follower
    follower.bpm = 120.0
    follower.locked = True
    follower._started = True
    follower._beats = 8.0
    follower._at = service.clock_s
    before = follower.beats_at(service.clock_s)
    service.configure(scope="auto")
    assert follower.beats_at(service.clock_s) >= before - 1e-9
    service.configure(scope="preset")
    assert follower.beats_at(service.clock_s) >= before - 1e-9
    assert service.planner.current is None


def test_an_octave_does_not_cross_the_episode_or_revive_a_finished_effect() -> None:
    planner = EpisodePlanner()
    opened = planner.advance(0.0, section="groove", level=0.7, theme="orange")
    assert opened.duration_beats >= 8
    planner.advance(
        2.2,
        section="groove",
        level=0.7,
        theme="orange",
        events=(StructuralEvent("drop", 1, 1.0),),
    )
    playing = planner.advance(7.5, section="groove", level=0.7, theme="orange")
    ident = playing.ident
    assert planner.strobe_at(7.5) == 0.0
    # A confirmed 3/2 or double used to jump this beat past the phrase end.
    again = planner.advance(7.5, section="groove", level=0.7, theme="orange")
    assert again.ident == ident
    # A confirmed half used to land back inside the accent that had already ended.
    planner.advance(3.75, section="groove", level=0.7, theme="orange")
    assert planner.current is not None
    assert planner.current.ident == ident
    assert planner.strobe_at(3.75) == 0.0
    forward = planner.advance(7.6, section="groove", level=0.7, theme="orange")
    assert forward.ident == ident


def test_the_follower_octave_does_not_move_the_planner_backward() -> None:
    follower = _lock(100.0)
    moment = _beats_for(100.0, 6.5)
    planner = EpisodePlanner()
    opened = planner.advance(follower.beats_at(moment), section="groove", level=0.7, theme="orange")
    ident = opened.ident
    _confirm(follower, 150.0, moment)
    kept = planner.advance(follower.beats_at(moment), section="groove", level=0.7, theme="orange")
    assert follower.bpm == pytest.approx(150.0)
    assert follower.beats_at(moment) == pytest.approx(6.5, abs=1e-6)
    assert kept.ident == ident


def test_drop_identities_and_section_events_stay_bounded() -> None:
    planner = EpisodePlanner()
    planner.advance(0.0, section="groove", level=0.7, theme="orange")
    for ident in range(1, 90):
        planner.advance(
            float(ident),
            section="groove",
            level=0.7,
            theme="orange",
            events=(StructuralEvent("drop", ident, 0.0),),
        )
    assert len(planner._seen) <= _SEEN_LIMIT
    assert 89 in planner._seen
    assert 1 not in planner._seen
    tracker = SectionTracker()
    for _ in range(40):
        tracker._begin_drop()
        tracker._clock += 0.01
    assert len(tracker._events) <= 16
    assert tracker._events[-1].ident == 40


def test_a_new_scene_does_not_inherit_old_event_ids_or_times() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    runtime._musical_seen.update(range(1, 40))
    runtime._musical_pending = StructuralEvent("drop", 3, 4.0)
    runtime._musical_manual_at = 80.0
    runtime.activate_scene(str(runtime.scene_id))
    assert runtime._musical_seen == set()
    assert runtime._musical_pending is None
    assert runtime._musical_manual_at == -1.0
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    runtime.bpm.section._clock = 0.4
    runtime.bpm.section._begin_drop()
    before = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index
    runtime._ingest_musical_events(0.05)
    assert 1 in runtime._musical_seen
    pending = runtime._musical_pending
    moved = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index != before
    assert (pending is not None and pending.ident == 1) or moved


def test_musical_event_memory_is_capped_and_a_fresh_ident_still_steps() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    for ident in range(1, 200):
        assert runtime._note_musical_ident(ident) is True
        assert runtime._note_musical_ident(ident) is False
    assert len(runtime._musical_seen) <= _SEEN_LIMIT
    assert 199 in runtime._musical_seen
    assert 1 not in runtime._musical_seen
    runtime.bpm.section._events.append(StructuralEvent("drop", 199, 1.0))
    runtime._ingest_musical_events(0.05)
    assert runtime._musical_pending is None
    runtime.bpm.section._events.append(StructuralEvent("drop", 1, 2.0))
    runtime._ingest_musical_events(0.05)
    pending = runtime._musical_pending
    assert pending is not None and pending.ident == 1


def _program() -> YamlPresetProgram:
    episode = EpisodeCard(
        id="mix",
        duration_s=18,
        groups=["par"],
        effect="pulse",
        transition="cut",
        actions=[
            LookAction(id="one", groups=["par"], effect="pulse", cycle_beats=1),
            LookAction(id="two", groups=["bar"], effect="chase", cycle_beats=2),
            LookAction(id="four", groups=["right"], effect="wave", cycle_beats=4),
            LookAction(id="eight", groups=["beam"], effect="breathe", cycle_beats=8),
        ],
    )
    document = PresetDocument(
        id="MUS",
        label="musical",
        builtin=False,
        musical_version=MUSICAL_CYCLE_VERSION,
        episodes=[episode, EpisodeCard(id="next", duration_s=18, groups=["par"], transition="cut")],
    )
    return YamlPresetProgram(document)


def test_a_failed_analysis_does_not_stop_frames_blackout_or_release(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.engine.set_blackout(False)
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    follower = runtime.bpm.follower
    moment = runtime.bpm.clock_s
    follower.bpm = 120.0
    follower.locked = True
    follower._started = True
    follower._beats = 4.0
    follower._at = moment
    follower._fresh_until = moment + 60.0

    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("analysis failed")

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", boom)
    runtime.bpm.submit_block([0.2] * 48000, 48000, captured_at=moment)
    assert runtime.bpm.capture != "unavailable"
    assert runtime.bpm.diagnostics()["analysis_errors"] == 1
    runtime.engine.strobe_press()
    runtime.tick(1.0 / 30.0)
    assert runtime.engine.overlays.strobe_held is True
    runtime.engine.set_blackout(True)
    runtime.tick(1.0 / 30.0)
    assert runtime.engine.overlays.blackout is True
    runtime.engine.strobe_release()
    assert runtime.engine.overlays.strobe_held is False
    runtime.bpm.follower.begin_release(runtime.bpm.clock_s)
    for _ in range(80):
        runtime.tick(1.0 / 30.0)
    assert runtime.bpm.follower.bpm is None
    assert runtime.bpm.follower.beats_at(runtime.bpm.clock_s) >= 4.0


def test_cycles_and_live_strobe_do_not_rewind_on_an_octave(tmp_path) -> None:  # noqa: ANN001
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.engine.presets["MUS"] = _program()
    runtime.engine.select_preset("MUS", reset_clock=True)
    runtime.engine.set_blackout(False)
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="beat")
    follower = runtime.bpm.follower
    moment = runtime.bpm.clock_s
    follower.bpm = 140.0
    follower.locked = True
    follower._started = True
    follower._beats = 8.0
    follower._at = moment
    follower._fresh_until = moment + 60.0
    runtime.tick(0.05)
    turns = runtime.engine.effect_phase_turns
    assert turns >= 8.0
    _confirm(follower, 70.0, runtime.bpm.clock_s)
    assert follower.beats_at(runtime.bpm.clock_s) >= turns - 1e-6
    runtime.engine.strobe_press()
    phase = runtime.engine.overlays.strobe_phase
    runtime.tick(0.05)
    assert runtime.engine.effect_phase_turns >= turns - PHASE_PULL - 1e-6
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.strobe_phase > phase
    index = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index
    runtime.engine.preset_elapsed_s = 2.0
    runtime.bpm.section._events.append(StructuralEvent("drop", 7, 1.0))
    runtime.tick(0.05)
    stepped = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index
    assert stepped == index + 1
    runtime.bpm.section._events.append(StructuralEvent("drop", 1, 1.0))
    for _ in range(10):
        runtime.tick(0.05)
    assert runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index == stepped
