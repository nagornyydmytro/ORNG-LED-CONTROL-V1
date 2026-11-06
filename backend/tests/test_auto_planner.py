"""Auto plans episodes on the shared clock. It does not render to Art-Net."""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from orng_led.audio.auto_show import StructuralEvent, build_auto_stage
from orng_led.audio.library import BY_ID
from orng_led.audio.planner import EpisodePlanner
from orng_led.config.io import load_show_config
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

_LENGTHS = {1, 2, 4, 8, 16, 32, 64}


def _step(planner: EpisodePlanner, beat: float, section: str, level: float, **kwargs: object):
    return planner.advance(beat, section=section, level=level, theme="orange", **kwargs)


def test_a_loud_frame_does_not_shorten_the_episode() -> None:
    planner = EpisodePlanner(seed=1)
    first = _step(planner, 0.0, "groove", 0.5)
    assert first.duration_beats in _LENGTHS
    assert first.duration_beats >= 8
    same = _step(planner, 1.0, "groove", 1.0)
    assert same.ident == first.ident
    assert same.duration_beats == first.duration_beats
    assert same.look == first.look


def test_gain_and_a_false_drop_do_not_open_an_accent() -> None:
    from orng_led.audio.auto_show import SectionTracker

    tracker = SectionTracker()
    for _ in range(8):
        tracker.push(0.05, 0.25, onset=0.001)
    for _ in range(8):
        tracker.push(0.45, 0.25, onset=0.01)
    assert tracker.take_events() == ()
    planner = EpisodePlanner(seed=2)
    episode = _step(planner, 0.0, "groove", 0.6, events=())
    later = _step(planner, 3.0, "groove", 0.95, events=())
    assert later.ident == episode.ident
    assert [row["kind"] for row in planner.journal() if row["kind"] == "drop_next"] == []


def test_one_drop_event_is_one_accent_and_a_new_next_plan() -> None:
    planner = EpisodePlanner(seed=3)
    opening = _step(planner, 0.0, "groove", 0.6)
    event = StructuralEvent("drop", 4, 1.0)
    held = _step(planner, 1.2, "groove", 0.9, events=(event,))
    assert held.ident == opening.ident
    assert planner.strobe_at(1.2) == 0.0
    assert planner.upcoming is not None
    assert planner.upcoming.reason == "drop_next"
    assert planner.upcoming.duration_beats == 2
    assert planner.upcoming.effects and planner.upcoming.effects[0].role == "accent"
    assert planner.upcoming.start_beat == 2.0
    assert planner.upcoming.start_beat < opening.start_beat + opening.duration_beats
    played = _step(planner, planner.upcoming.start_beat, "drop", 0.9)
    assert played.ident != opening.ident
    assert played.reason == "drop_next"
    assert played.role == "accent"
    assert BY_ID[played.look].pool == "accent"
    assert played.start_beat == 2.0
    assert planner.strobe_at(played.start_beat) == 1.0
    assert planner.strobe_at(played.start_beat + 0.25) == 0.0
    assert planner.strobe_at(played.start_beat + played.duration_beats + 0.1) == 0.0
    _step(planner, played.start_beat + 0.2, "drop", 0.9, events=(event,))
    assert [row["kind"] for row in planner.journal()].count("drop_next") == 1


def test_theme_change_drops_only_the_future_plan() -> None:
    planner = EpisodePlanner(seed=4)
    current = planner.advance(0.0, section="groove", level=0.6, theme="orange")
    old_next = planner.upcoming.ident if planner.upcoming else None
    stayed = planner.advance(2.0, section="groove", level=0.6, theme="bars")
    assert stayed.ident == current.ident
    assert stayed.look == current.look
    assert stayed.theme == "bars"
    assert stayed.groups == ("bar",)
    assert planner.upcoming is not None
    assert planner.upcoming.theme == "bars"
    assert planner.upcoming.groups == ("bar",)
    assert planner.upcoming.ident != old_next


def test_signal_loss_and_blackout_end_effects_without_waiting() -> None:
    planner = EpisodePlanner(seed=5)
    rise = _step(planner, 0.0, "riser", 0.7)
    assert rise.section == "riser"
    assert all(effect.role != "rise" for effect in rise.effects)
    assert planner.strobe_at(0.0) == 0.0
    armed = planner.advance(
        0.2, section="riser", level=0.7, theme="orange", drive=True, drive_id=1, drive_density=0.8
    )
    assert armed.ident == rise.ident
    assert planner.strobe_at(0.2) == 1.0
    held = planner.advance(0.4, section="riser", level=0.7, theme="orange", blackout=True)
    assert held.ident == rise.ident
    assert held.start_beat == rise.start_beat
    assert planner.strobe_at(0.4) == 0.0
    calm = planner.advance(
        1.0, section="riser", level=0.7, theme="orange", blackout=False, signal_lost=True
    )
    assert calm.reason == "signal_loss"
    assert calm.look == "wash"
    assert calm.effects == ()
    assert calm.duration_beats <= 4
    assert planner.strobe_at(1.2) == 0.0
    assert planner.upcoming is None
    back = planner.advance(1.4, section="groove", level=0.55, theme="orange")
    assert back.reason == "recover"
    assert back.start_beat == 1.4
    assert back.effects == () or all(effect.role != "accent" for effect in back.effects)
    assert planner.strobe_at(1.4) == 0.0


def test_course_is_a_sequence_of_episodes_not_a_frame_lottery(tmp_path: Path) -> None:
    planner = EpisodePlanner(seed=6)
    seen: list[tuple[float, int, str]] = []
    strobes: list[tuple[float, float]] = []
    event = StructuralEvent("drop", 9, 52.0)
    beat = 0.0
    while beat < 80.0:
        if beat < 20:
            section, level = "groove", 0.6
        elif beat < 40:
            section, level = "break", 0.28
        elif beat < 52:
            section, level = "riser", 0.72
        elif beat < 58:
            section, level = "drop", 0.9
        else:
            section, level = "groove", 0.55
        events = (event,) if math.isclose(beat, 52.0) else ()
        episode = _step(planner, beat, section, level, events=events)
        seen.append((beat, episode.ident, episode.look))
        strobes.append((beat, planner.strobe_at(beat)))
        beat = round(beat + 0.5, 3)
    reasons = [episode.reason for episode in planner.played]
    assert "start" in reasons
    assert "drop_next" in reasons
    opening = planner.played[0]
    assert any(episode.section == "break" and episode.look == opening.look for episode in planner.played)
    assert all(
        not (episode.section == "break" and episode.look == "wash")
        for episode in planner.played
        if episode.reason != "signal_loss"
    )
    assert any(episode.section == "riser" for episode in planner.played)
    for episode in planner.played:
        assert episode.duration_beats in _LENGTHS
        assert episode.duration_beats >= 2
        cycles = [action.cycle_beats for action in episode.actions]
        assert cycles
        if episode.motion_cycle is not None:
            assert episode.motion_cycle != episode.picture_cycle
    starts = [episode.start_beat for episode in planner.played]
    assert starts == sorted(starts)
    for episode in planner.played:
        window = [look for moment, ident, look in seen if ident == episode.ident]
        assert window
        assert len(set(window)) == 1
    assert any(level == 1.0 for _, level in strobes)
    assert strobes[-1][1] == 0.0
    assert len(planner.journal()) <= 96
    _draw(planner, tmp_path / "auto-episodes.bmp")


def test_desk_status_hides_the_planner_journal(tmp_path: Path) -> None:
    from orng_led.api.runtime import AppRuntime

    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    before = {
        path.name: path.read_bytes()
        for path in Path(runtime.config_dir).rglob("*.yaml")
        if path.parent.name == "presets"
    }
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    runtime.tick(0.05)
    assert "planner" not in runtime.bpm.status()
    assert "planner" in runtime.bpm.diagnostics()
    runtime.engine.set_blackout(True)
    runtime.tick(0.05)
    assert runtime.bpm.planner.strobe_at(runtime.bpm.follower.beats_at(runtime.bpm.clock_s)) == 0.0
    after = {
        path.name: path.read_bytes()
        for path in Path(runtime.config_dir).rglob("*.yaml")
        if path.parent.name == "presets"
    }
    assert after == before


def _draw(planner: EpisodePlanner, dest: Path) -> None:
    show = load_show_config()
    pose = {
        fixture.id: BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
        for fixture in show.patch.fixtures
        if fixture.kind.value == "beam"
    }
    width, height = 220, 120
    rows = planner.played[:8]
    image_w, image_h = width, height * len(rows)
    pixels = bytearray(image_w * image_h * 3)
    for index, episode in enumerate(rows):
        sample = episode.start_beat + min(1.0, episode.duration_beats / 2)
        stage = build_auto_stage(
            list(show.patch.fixtures),
            episode.theme if episode.theme != "bars" else "orange",
            episode.section,
            0.0,
            episode.intensity or 0.5,
            pattern=0,
            beats=sample,
            cycle_beats=episode.picture_cycle,
            placements=show.layout.placements,
            look=episode.look,
            motion_cycle=episode.motion_cycle,
            strobe_hold=0.0,
        )
        frame = render_stage(show, stage, pose)
        view = decode_simulator_view(show, frame)
        _paint(pixels, image_w, index * height, width, height, show, view)
        lit = {item.id for item in view.pars + view.bars + view.beams if _lit(item)}
        again = build_auto_stage(
            list(show.patch.fixtures),
            "orange",
            episode.section,
            0.0,
            episode.intensity or 0.5,
            beats=sample + 0.05,
            cycle_beats=episode.picture_cycle,
            placements=show.layout.placements,
            look=episode.look,
            motion_cycle=episode.motion_cycle,
            strobe_hold=0.0,
        )
        if episode.look == "wash":
            assert set(stage.fixtures) == set(again.fixtures)
            for fixture_id, intent in stage.fixtures.items():
                other = again.fixtures[fixture_id]
                assert type(intent) is type(other)
                if hasattr(intent, "segments"):
                    assert intent.segments == other.segments
        assert "face_par_1" not in stage.fixtures
        _ = lit
    dest.write_bytes(_bmp(image_w, image_h, pixels))
    assert dest.stat().st_size > 54


def test_leaving_auto_drops_the_queued_plan() -> None:
    from orng_led.api.runtime import AppRuntime

    runtime = AppRuntime.create(autostart_loop=False)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    planner = runtime.bpm.planner
    planner.advance(0.0, section="groove", level=0.6, theme="orange")
    held = planner.advance(
        0.5,
        section="drop",
        level=0.9,
        theme="orange",
        events=(StructuralEvent("drop", 7, 0.5),),
    )
    assert planner.upcoming is not None
    assert planner.upcoming.reason == "drop_next"
    assert planner.upcoming.effects
    assert planner.strobe_at(0.5) == 0.0
    runtime.apply_bpm_sync(scope="preset")
    assert planner.current is None
    assert planner.upcoming is None
    assert planner.strobe_at(0.5) == 0.0
    assert runtime.engine.auto_base is None
    runtime.apply_bpm_sync(scope="auto")
    fresh = planner.advance(4.0, section="groove", level=0.4, theme="orange")
    assert fresh.reason != "drop_next"
    assert planner.strobe_at(4.0) == 0.0
    assert planner.upcoming is None or planner.upcoming.reason != "drop_next"


def test_played_history_stays_bounded() -> None:
    planner = EpisodePlanner(seed=3)
    beat = 0.0
    for _ in range(40):
        episode = planner.advance(beat, section="groove", level=0.7, theme="orange")
        beat = episode.start_beat + episode.duration_beats + 0.01
    assert len(planner.played) <= 32
    assert len(planner.journal()) <= 96


def test_hold_repeat_and_blackout_share_one_runtime(tmp_path: Path) -> None:
    """Audio block, plan, frame, and a repeated command on one runtime. No Art-Net."""
    from orng_led.api.runtime import AppRuntime
    from orng_led.engine.show_whitelist import assert_lights_dark

    show = load_show_config()
    runtime = AppRuntime.create(autostart_loop=False, show=show, config_dir=tmp_path)
    preset = runtime.engine.active_preset_id
    runtime.engine.set_blackout(False)
    runtime.engine.strobe_press()
    runtime.bpm.submit_block([0.2] * 2048, 48000, captured_at=0.1, now=0.1)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="bars")
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.active_preset_id == preset
    lit = runtime.tick(0.05)
    assert lit.frame
    runtime.engine.set_blackout(True)
    assert_lights_dark(show, runtime.tick(0.05).frame)
    runtime.apply_bpm_sync(scope="preset", client_command_id="leave-1")
    from orng_led.api.runtime import CommandConflictError

    with pytest.raises(CommandConflictError):
        runtime.apply_bpm_sync(scope="auto", client_command_id="leave-1")
    assert runtime.bpm.scope == "preset"
    assert runtime.bpm.planner.current is None
    before = runtime.bpm.clock_s
    runtime.bpm.poll(0.2)
    assert runtime.bpm.clock_s >= before + 0.2
    state = runtime.build_state()
    assert "planner" not in (state.bpm or {})


def test_a_drop_in_a_long_bars_break_meets_the_accent_on_the_next_bar() -> None:
    planner = EpisodePlanner(seed=8)
    opening = planner.advance(
        0.0, section="break", level=0.2, theme="bars", character="smooth"
    )
    assert opening.duration_beats == 32
    planner.advance(
        1.2,
        section="break",
        level=0.2,
        theme="bars",
        events=(StructuralEvent("drop", 3, 1.2),),
    )
    assert planner.current is not None and planner.current.ident == opening.ident
    assert planner.upcoming is not None
    assert planner.upcoming.start_beat == 2.0
    assert planner.upcoming.reason == "drop_next"
    assert planner.strobe_at(1.5) == 0.0
    accent = planner.advance(2.0, section="drop", level=0.9, theme="bars")
    assert accent.reason == "drop_next"
    assert accent.role == "accent"
    assert BY_ID[accent.look].pool == "accent"
    assert accent.start_beat == 2.0
    assert planner.strobe_at(2.0) == 1.0
    assert planner.strobe_at(2.2) == 0.0
    assert accent.ident != opening.ident


def test_signal_return_does_not_keep_the_emergency_phrase() -> None:
    planner = EpisodePlanner(seed=9)
    planner.advance(0.0, section="break", level=0.2, theme="bars")
    lost = planner.advance(2.0, section="break", level=0.2, theme="bars", signal_lost=True)
    assert lost.reason == "signal_loss"
    assert lost.duration_beats <= 4
    back = planner.advance(2.5, section="groove", level=0.6, theme="bars", signal_lost=False)
    assert back.reason == "recover"
    assert back.start_beat == 2.5
    assert back.start_beat < lost.start_beat + 32
    assert planner.strobe_at(2.5) == 0.0
    assert planner.upcoming is None or planner.upcoming.reason != "drop_next"


def test_a_large_jump_skips_expired_episodes_and_effects() -> None:
    planner = EpisodePlanner(seed=10)
    planner.advance(0.0, section="groove", level=0.7, theme="orange")
    planner.advance(
        1.0,
        section="groove",
        level=0.7,
        theme="orange",
        events=(StructuralEvent("drop", 1, 1.0),),
    )
    before = len(planner.played)
    landed = planner.advance(80.0, section="groove", level=0.7, theme="orange")
    assert len(planner.played) - before == 1
    assert landed.start_beat == 80.0
    assert landed.reason != "drop_next"
    assert planner.strobe_at(80.0) == 0.0


def test_a_stuck_beat_cannot_hold_an_auto_flash_open() -> None:
    planner = EpisodePlanner(seed=11)
    planner.advance(
        0.0,
        section="groove",
        level=0.7,
        theme="orange",
        drive=True,
        drive_id=1,
        drive_density=0.7,
        beat_period_s=0.5,
    )
    assert planner.strobe_at(0.0, wall_s=10.0) == 1.0
    assert planner.strobe_at(0.0, wall_s=12.6) == 0.0


def test_a_planned_look_survives_a_section_change() -> None:
    from orng_led.audio.compositions import pair_look

    held = pair_look(
        "groups",
        section="break",
        phase=0.1,
        amount=0.8,
        cap=1.0,
        accent=False,
        rank=1,
        ranks=2,
        count=1,
        honor_look=True,
    )
    replaced = pair_look(
        "groups",
        section="break",
        phase=0.1,
        amount=0.8,
        cap=1.0,
        accent=False,
        rank=1,
        ranks=2,
        count=1,
    )
    assert held.include is False
    assert replaced.include is True


def _lit(item: object) -> bool:
    return float(getattr(item, "intensity", getattr(item, "dimmer", 0.0))) > 0.02


def _paint(pixels, image_w, top, width, height, show, view) -> None:
    for y in range(height):
        for x in range(width):
            _put(pixels, image_w, x, top + y, (12, 10, 8))
    axis = int(0.5 * (width - 1))
    for y in range(height):
        _put(pixels, image_w, axis, top + y, (80, 60, 30))
    placed = {item.fixture_id: item for item in show.layout.placements}
    bars = {item.id: item for item in view.bars}
    pars = {item.id: item for item in view.pars}
    beams = {item.id: item for item in view.beams}
    for fixture_id, place in placed.items():
        px = int(place.x * (width - 1))
        py = top + int(place.y * (height - 1))
        if fixture_id in bars:
            bar = bars[fixture_id]
            for segment, level in enumerate(bar.segments):
                color = (
                    int(bar.segment_r[segment] * 255),
                    int(bar.segment_g[segment] * 255),
                    int(bar.segment_b[segment] * 255),
                ) if level > 0.02 else (20, 16, 12)
                for dy in range(5):
                    for dx in range(-2, 3):
                        _put(pixels, image_w, px + dx, py - 20 + segment * 5 + dy, color)
        elif fixture_id in pars:
            par = pars[fixture_id]
            color = (
                int(par.r * par.intensity * 255),
                int(par.g * par.intensity * 255),
                int(par.b * par.intensity * 255),
            )
            for dy in range(-5, 6):
                for dx in range(-5, 6):
                    _put(pixels, image_w, px + dx, py + dy, color)
        elif fixture_id in beams:
            beam = beams[fixture_id]
            color = (
                int(beam.r * beam.dimmer * 255),
                int(beam.g * beam.dimmer * 255),
                int(beam.b * beam.dimmer * 255),
            )
            for dy in range(-3, 4):
                for dx in range(-3, 4):
                    _put(pixels, image_w, px + dx, py + dy, color)


def _put(pixels: bytearray, width: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    height = len(pixels) // (width * 3)
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    start = (y * width + x) * 3
    pixels[start : start + 3] = bytes(color)


def _bmp(width: int, height: int, rgb: bytearray) -> bytes:
    row = (width * 3 + 3) & ~3
    pixels = bytearray()
    for y in range(height - 1, -1, -1):
        line = bytearray()
        for x in range(width):
            start = (y * width + x) * 3
            red, green, blue = rgb[start : start + 3]
            line.extend((blue, green, red))
        line.extend(b"\x00" * (row - width * 3))
        pixels.extend(line)
    header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


def test_colour_change_is_not_a_new_picture() -> None:
    planner = EpisodePlanner(seed=21)
    first = planner.advance(0.0, section="groove", level=0.6, theme="orange", palette="orange")
    later = planner.advance(1.5, section="groove", level=0.6, theme="orange", palette="blue")
    assert later.ident == first.ident
    assert later.look == first.look
    assert later.palette == "blue"
    assert later.cast == first.cast
    assert planner.upcoming is not None


def test_cast_is_not_a_palette() -> None:
    from orng_led.audio.settings import colour_of, split_theme

    colour, cast = split_theme("bars")
    assert colour == "orange"
    assert cast == "bars"
    assert colour_of("bars") == "orange"
    planner = EpisodePlanner(seed=22)
    episode = planner.advance(0.0, section="groove", level=0.6, theme="bars", palette="red")
    assert episode.cast == "bars"
    assert episode.groups == ("bar",)
    assert episode.palette == "red"


def test_characters_change_cycles_and_flashes() -> None:
    from orng_led.engine.clock import FRAME_DT

    cycles = {}
    flashes = {}
    for character in ("smooth", "rhythmic", "impact"):
        planner = EpisodePlanner(seed=23)
        episode = planner.advance(
            0.0, section="riser", level=0.7, theme="orange", character=character
        )
        cycles[character] = episode.picture_cycle
        wall = 0.0
        ons = 0
        for _ in range(int(1.0 / FRAME_DT)):
            if planner.strobe_at(wall * 2.0, wall_s=wall):
                ons += 1
            wall += FRAME_DT
        flashes[character] = ons
    assert cycles["smooth"] > cycles["rhythmic"] >= cycles["impact"]
    assert flashes["smooth"] == flashes["rhythmic"] == flashes["impact"] == 0


def test_rise_flash_respects_thirty_hertz() -> None:
    from orng_led.engine.clock import FRAME_DT

    planner = EpisodePlanner(seed=24)
    planner.advance(
        0.0,
        section="riser",
        level=0.8,
        theme="orange",
        character="impact",
        drive=True,
        drive_id=1,
        drive_density=1.0,
        beat_period_s=0.5,
    )
    consecutive = 0
    longest = 0
    wall = 0.0
    for _ in range(int(2.0 / FRAME_DT)):
        on = planner.flash_on(wall * 2.0, wall_s=wall)
        consecutive = consecutive + 1 if on else 0
        longest = max(longest, consecutive)
        wall += FRAME_DT
    assert 1 <= longest <= 2


def test_lock_loss_keeps_the_picture_then_freewheels() -> None:
    planner = EpisodePlanner(seed=25)
    first = planner.advance(0.0, section="groove", level=0.6, theme="orange", locked=True)
    held = planner.advance(
        2.0, section="groove", level=0.6, theme="orange", locked=False, tempo_held=True
    )
    assert held.ident == first.ident
    lost = planner.advance(4.0, section="groove", level=0.6, theme="orange", locked=False)
    assert lost.look == first.look
    assert lost.clock == "freewheel"
    assert lost.effects == ()
    assert planner.strobe_at(4.0) == 0.0
    back = planner.advance(8.0, section="groove", level=0.6, theme="orange", locked=True)
    assert back.look != "accent"
    assert back.reason != "drop_next"
    assert all(effect.role != "accent" for effect in back.effects)


def test_parallel_bed_is_not_a_second_estimator() -> None:
    planner = EpisodePlanner(seed=26)
    groove = planner.advance(0.0, section="groove", level=0.65, theme="orange", character="rhythmic")
    roles = {action.role for action in groove.actions}
    assert "picture" in roles
    assert "bed" in roles
    cycles = {action.role: action.cycle_beats for action in groove.actions}
    assert cycles["bed"] > cycles["picture"]
    impact = EpisodePlanner(seed=26).advance(
        0.0, section="groove", level=0.65, theme="orange", character="impact"
    )
    assert all(action.role != "bed" for action in impact.actions)
