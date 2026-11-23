"""Flagship Mix: every required picture, extra episodes, Look-2 pace, no amber/green."""

from __future__ import annotations

from orng_led.config import load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.engine import Engine
from orng_led.engine.intents import BarIntent, BeamIntent
from orng_led.engine.renderer import render_stage
from orng_led.engine.show_whitelist import assert_only_mapped_show_channels, forbidden_nonzero_channels
from orng_led.presets.models import EpisodeCard, PresetDocument
from orng_led.presets.program import YamlPresetProgram
from orng_led.presets.store import PresetStore

BANNED = {"amber", "mint", "white_warm", "green"}
LOOKS = {
    "Дихання барів": "breathe_bars",
    "Заповнення згори": "fill_down",
    "Смуга вліво": "band_left",
    "Дві смуги вліво": "bands_2_left",
    "Дихання PAR": "breathe_pars",
    "Зустріч по діагоналі": "meet_diag",
    "Смуга вправо": "band_right",
    "Пульс по індексу бара": "pulse_by_bar",
    "Зворотна діагональна смуга": "band_diag_flip",
    "Дві діагональні смуги": "bands_2_diag",
    "Удар тільки барів": "hit_bars",
}
OLD_LOOKS = {
    "Було — Дихання барів": "breathe_bars",
    "Було — Смуга вліво": "band_left",
    "Було — Дві смуги вліво": "bands_2_left",
    "Було — Дихання PAR": "breathe_pars",
    "Було — Удар PAR": "hit_pars",
    "Було — Ірис полотна": "iris",
    "Було — Діагональна смуга": "band_diag",
    "Було — Зустріч по діагоналі": "meet_diag",
    "Було — Пульсуюча дірка": "negative_pulse_hole",
    "Було — Смуга вправо": "band_right",
    "Було — Пульс по індексу бара": "pulse_by_bar",
    "Було — Зворотна діагональна смуга": "band_diag_flip",
    "Було — Дві діагональні смуги": "bands_2_diag",
    "Було — Удар тільки барів": "hit_bars",
}
COLOUR_PATH = (
    "deep_red",
    "magenta",
    "warm_orange",
    "cyan",
    "cool_blue",
    "deep_blue",
    "violet",
)
NOTES = {
    "Дихання барів",
    "Смуга вліво",
    "Смуга вправо",
    "Зворотна діагональна смуга",
    "Зустріч по діагоналі",
    "Дві діагональні смуги",
    "Дві смуги вліво",
    "Пульс по індексу бара",
    "Удар тільки барів",
    "Дихання PAR",
    "Look 9 — епізод 1",
    "Look 9 — епізод 2",
    "Look 1 — епізод 6",
    "Look 7 — епізод 6",
    "Static 1 — епізод 8",
    "Заповнення згори",
    "Look 3 — епізод 3",
    "Look 5 — епізод 4",
    "Look 6 — епізод 1",
    "Look 7 — епізод 5",
    "Look 7 — епізод 7",
    "Static 3 — хвиля",
}
OLD_NOTES = {f"Було — {note}" for note in (
    "Дихання барів",
    "Look 9 — епізод 1",
    "Static 1 — епізод 8",
    "Смуга вліво",
    "Дихання PAR",
    "Дві смуги вліво",
    "Look 1 — епізод 6",
    "Look 4 — епізод 5",
    "Удар PAR",
    "Ірис полотна",
    "Діагональна смуга",
    "Зустріч по діагоналі",
    "Пульсуюча дірка",
    "Смуга вправо",
    "Пульс по індексу бара",
    "Зворотна діагональна смуга",
    "Дві діагональні смуги",
    "Look 7 — епізод 6",
    "Удар тільки барів",
    "Look 9 — епізод 2",
)}
CLEARED_HEADS = {
    "Дихання барів",
    "Смуга вліво",
    "Look 1 — епізод 6",
    "Смуга вправо",
    "Дві діагональні смуги",
}


def _episode_start(doc: PresetDocument, note: str) -> float:
    cursor = 0.0
    for episode in doc.episodes:
        if episode.notes == note:
            return cursor
        cursor += float(episode.duration_s)
    raise AssertionError(note)


def test_mix_is_on_the_pad_and_walks_colour() -> None:
    show = load_show_config()
    assert show.app.pad_presets[2] == "Mix"
    doc = PresetStore.load().get("Mix")
    assert doc.label == "Mix"
    assert len(doc.episodes) == 42
    assert {ep.notes for ep in doc.episodes} == NOTES | OLD_NOTES
    assert sum(1 for ep in doc.episodes if str(ep.notes).startswith("Було — ")) == 20
    assert all(abs(ep.duration_s - 18.0) < 1e-6 for ep in doc.episodes)
    assert abs(sum(ep.duration_s for ep in doc.episodes) - 42 * 18.0) < 1e-6
    assert {ep.palette for ep in doc.episodes}.isdisjoint(BANNED)
    assert "cyan" in {ep.palette for ep in doc.episodes}
    assert "rose" in {ep.palette for ep in doc.episodes}
    assert all(0.18 <= ep.speed <= 0.26 for ep in doc.episodes)
    assert all(ep.intensity >= 0.9 for ep in doc.episodes)
    assert {ep.transition for ep in doc.episodes} == {"fade", "soft"}
    assert all(ep.head is None and "beam" not in ep.groups for ep in doc.episodes if ep.notes in CLEARED_HEADS)
    assert all((ep.head is None) == ("beam" not in ep.groups) for ep in doc.episodes)
    wave = next(ep for ep in doc.episodes if ep.notes == "Static 3 — хвиля")
    assert wave.effect == "glow_eq" and wave.look is None and wave.groups == ["bar"]
    top = next(ep for ep in doc.episodes if ep.notes == "Заповнення згори")
    assert top.look == "fill_down" and top.groups == ["bar"] and top.head is None
    corrected = [ep for ep in doc.episodes if not str(ep.notes).startswith("Було — ")]
    classic = [ep for ep in doc.episodes if str(ep.notes).startswith("Було — ")]
    assert all(ep.mix_style == "picture" for ep in corrected)
    assert all(ep.mix_style == "classic" for ep in classic)
    restored = [
        ("Було — Дихання барів", "sweep", ["bar", "beam"]),
        ("Було — Смуга вліво", "cross", ["bar", "beam"]),
        ("Було — Look 1 — епізод 6", "nod", ["par", "beam"]),
        ("Було — Діагональна смуга", "nod", ["bar", "beam"]),
        ("Було — Смуга вправо", "rise", ["bar", "beam"]),
        ("Було — Дві діагональні смуги", "cross", ["bar", "beam"]),
    ]
    for note, head, groups in restored:
        episode = next(ep for ep in doc.episodes if ep.notes == note)
        assert episode.head == head and episode.groups == groups, note
    assert [doc.episodes[index].notes for index in (22, 25, 28, 32, 35, 38)] == [
        note for note, _head, _groups in restored
    ]
    assert {ep.notes: ep.look for ep in corrected if ep.look} == LOOKS
    assert {ep.notes: ep.look for ep in doc.episodes if ep.notes in OLD_LOOKS} == OLD_LOOKS
    hues = [ep.palette for ep in doc.episodes]
    assert hues[0] == "deep_red"
    positions = [hues.index(name) for name in COLOUR_PATH]
    assert positions == sorted(positions)
    assert "face" not in {group for ep in doc.episodes for group in ep.groups}


def test_mix_renders_without_face_or_unmapped_channels() -> None:
    show = load_show_config()
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            fixture.spatial.beam_calibration_confirmed = True
    doc = PresetStore.load().get("Mix")
    program = YamlPresetProgram(doc)
    for note in CLEARED_HEADS:
        moment = _episode_start(doc, note) + 9.0
        quiet = program.evaluate(moment, show)
        assert not any(isinstance(item, BeamIntent) for item in quiet.fixtures.values()), note
    assert any(isinstance(item, BarIntent) for item in program.evaluate(9.0, show).fixtures.values())
    stage = program.evaluate(_episode_start(doc, "Look 9 — епізод 1") + 4.0, show)
    assert any(isinstance(item, BeamIntent) for item in stage.fixtures.values())
    face_ids = {fx.id for fx in show.patch.fixtures if fx.kind is FixtureKind.FACE_PAR}
    assert face_ids.isdisjoint(stage.fixtures)
    motion = {
        fx.id: BeamMotionState.from_home(fx.spatial.home_pan, fx.spatial.home_tilt)
        for fx in show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }
    frame = render_stage(show, stage, motion, master=1.0, scrub=True)
    assert_only_mapped_show_channels(show, frame)
    assert forbidden_nonzero_channels(show, frame) == []
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            if channel.role is not ChannelRole.UNUSED:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            assert frame[index] == 0


def test_look_overlay_moves_heads_and_skips_face() -> None:
    show = load_show_config()
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            fixture.spatial.beam_calibration_confirmed = True
    episode = EpisodeCard(
        id="band",
        duration_s=18,
        groups=["bar", "beam"],
        palette="deep_red",
        effect="wave",
        speed=0.22,
        intensity=0.92,
        transition="fade",
        look="band_left",
        head="sweep",
    )
    program = YamlPresetProgram(
        PresetDocument(
            id="MixProbe",
            label="MixProbe",
            builtin=False,
            episodes=[
                episode,
                episode.model_copy(update={"id": "next", "head": "nod", "transition": "cut"}),
            ],
        )
    )
    early = program.evaluate(0.2, show)
    later = program.evaluate(5.0, show)
    early_pan = [item.pan for item in early.fixtures.values() if isinstance(item, BeamIntent)]
    later_pan = [item.pan for item in later.fixtures.values() if isinstance(item, BeamIntent)]
    assert early_pan and early_pan != later_pan
    face_ids = {fx.id for fx in show.patch.fixtures if fx.kind is FixtureKind.FACE_PAR}
    assert face_ids.isdisjoint(early.fixtures)


def _forbidden_rgb(r: int, g: int, b: int) -> bool:
    if r + g + b < 8:
        return False
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    yellow = rf > 0.45 and gf > 0.40 and bf < 0.28 and gf > bf + 0.15
    green = gf > 0.45 and gf > rf + 0.12 and gf > bf + 0.12
    white = rf > 0.8 and gf > 0.75 and bf > 0.65
    return yellow or green or white


def test_mix_cycle_frames_avoid_amber_and_keep_heads_down() -> None:
    """30 fps of final frames, including the wrap into episode 1.

    Orange used to blend into cyan and land bar DMX 136 (amber) on channel 101.
    """
    show = load_show_config()
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            fixture.spatial.beam_calibration_confirmed = True
    doc = PresetStore.load().get("Mix")
    program = YamlPresetProgram(doc)
    allowed_bar = {0, 46, 61, 106, 166, 196}
    color_channels: list[tuple[str, int]] = []
    rgb_channels: list[tuple[str, int, int, int]] = []
    face_channels: list[int] = []
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        roles = {channel.role: channel for channel in profile.channels}
        if fixture.kind is FixtureKind.BAR:
            for channel in profile.channels:
                if channel.role is ChannelRole.SEGMENT_COLOR:
                    color_channels.append(
                        (fixture.id, global_channel(fixture.start_address, channel.local) - 1)
                    )
        if fixture.kind in {FixtureKind.PAR, FixtureKind.BEAM}:
            red = roles.get(ChannelRole.RED)
            green = roles.get(ChannelRole.GREEN)
            blue = roles.get(ChannelRole.BLUE)
            if red and green and blue:
                rgb_channels.append(
                    (
                        fixture.id,
                        global_channel(fixture.start_address, red.local) - 1,
                        global_channel(fixture.start_address, green.local) - 1,
                        global_channel(fixture.start_address, blue.local) - 1,
                    )
                )
        if fixture.kind is FixtureKind.FACE_PAR:
            for channel in profile.channels:
                if channel.role in {
                    ChannelRole.DIMMER,
                    ChannelRole.RED,
                    ChannelRole.GREEN,
                    ChannelRole.BLUE,
                    ChannelRole.WHITE,
                }:
                    face_channels.append(global_channel(fixture.start_address, channel.local) - 1)

    motion = {
        fx.id: BeamMotionState.from_home(fx.spatial.home_pan, fx.spatial.home_tilt)
        for fx in show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }
    def _paint(moment: float) -> list[int]:
        return render_stage(show, program.evaluate(moment, show), motion, master=1.0, scrub=True)

    band = _paint(_episode_start(doc, "Смуга вліво") + 9.0)
    lit_bars = [band[index] for _fixture_id, index in color_channels if band[index] != 0]
    assert lit_bars and set(lit_bars) == {46}
    pink_start = _episode_start(doc, "Дихання PAR")
    pink_frames = [_paint(pink_start + step * 0.5) for step in range(1, 34)]
    assert any(
        frame[ri] > 40 and frame[bi] > frame[ri] * 0.35 and frame[gi] < frame[ri] * 0.25
        for frame in pink_frames
        for _fixture_id, ri, gi, bi in rgb_channels
    )
    wave = _paint(_episode_start(doc, "Static 3 — хвиля") + 4.0)
    heights: dict[str, int] = {}
    for fixture_id, index in color_channels:
        heights[fixture_id] = heights.get(fixture_id, 0) + int(wave[index] != 0)
    assert len(set(heights.values())) > 1

    breathe_lo = _episode_start(doc, "Дихання PAR") + 2.0
    hit_lo = _episode_start(doc, "Було — Удар PAR") + 2.0
    join = _episode_start(doc, "Було — Діагональна смуга") + 0.533
    beams_lo = _episode_start(doc, "Look 7 — епізод 5")
    fill_lo = _episode_start(doc, "Заповнення згори")
    fps = 30
    span = program.total_duration_s + 2.0
    steps = int(span * fps)
    breathe_lamps: list[float] = []
    hit_lamps: list[float] = []
    saw_join = False
    saw_top = False
    for step in range(steps):
        moment = step / fps
        stage = program.evaluate(moment, show)
        if beams_lo <= moment < beams_lo + 18.0:
            assert not any(isinstance(item, BarIntent) for item in stage.fixtures.values()), moment
        if fill_lo + 1.0 <= moment < fill_lo + 17.0:
            tops = [
                item.segments
                for item in stage.fixtures.values()
                if isinstance(item, BarIntent)
            ]
            assert tops
            for segments in tops:
                lit = [index for index, value in enumerate(segments) if value >= 0.42]
                if not lit or len(lit) == len(segments):
                    continue
                saw_top = True
                assert lit == list(range(min(lit), 8)), (moment, lit)
        for item in stage.fixtures.values():
            if isinstance(item, BeamIntent):
                assert item.tilt <= 0.640001, (moment, item.tilt)
                if breathe_lo <= moment <= breathe_lo + 12.0:
                    breathe_lamps.append(item.dimmer)
                if hit_lo <= moment <= hit_lo + 12.0:
                    hit_lamps.append(item.dimmer)
        frame = render_stage(show, stage, motion, master=1.0, scrub=True)
        for fixture_id, index in color_channels:
            value = frame[index]
            assert value in allowed_bar, (moment, fixture_id, index + 1, value)
        for fixture_id, ri, gi, bi in rgb_channels:
            assert not _forbidden_rgb(frame[ri], frame[gi], frame[bi]), (
                moment,
                fixture_id,
                frame[ri],
                frame[gi],
                frame[bi],
            )
        for index in face_channels:
            assert frame[index] == 0, (moment, index + 1, frame[index])
        if abs(moment - join) < (0.5 / fps):
            saw_join = True
            assert frame[100] != 136
    assert saw_join
    assert saw_top
    assert breathe_lamps and min(breathe_lamps) < 0.2
    # The classic block keeps the shared head curve. It does not follow the
    # picture down to a near-black hit.
    assert hit_lamps and min(hit_lamps) > 0.2
    assert breathe_lamps != hit_lamps

    engine = Engine(show=show, presets={"Mix": program}, active_preset_id="Mix")
    engine.set_blackout(False)

    def _tick_window(start: float, frames: int) -> list[list[int]]:
        engine.preset_elapsed_s = start
        engine.beam_motion.clear()
        painted: list[list[int]] = []
        for _ in range(frames):
            snap = engine.tick(1.0 / fps)
            assert snap.episode_count == 42
            for state in engine.beam_motion.values():
                assert state.tilt <= 0.640001
            for _fixture_id, index in color_channels:
                assert snap.frame[index] in allowed_bar
            for index in face_channels:
                assert snap.frame[index] == 0
            painted.append(snap.frame)
        return painted

    joined = _tick_window(join - 0.4, 60)
    assert joined[12][100] != 136
    _tick_window(program.total_duration_s - 0.5, 45)
    heads = _tick_window(_episode_start(doc, "Було — Look 4 — епізод 5") + 1.0, 30)
    assert engine.beam_motion
    assert any(frame[100] in allowed_bar for frame in heads)


def _prepare_show():
    show = load_show_config()
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            fixture.spatial.beam_calibration_confirmed = True
    return show


def _bar_color_bytes(show, frame: list[int]) -> list[int]:
    values: list[int] = []
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BAR:
            continue
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            if channel.role is not ChannelRole.SEGMENT_COLOR:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            values.append(frame[index])
    return values


def test_classic_heads_keep_their_curve_inside_the_tilt_cap() -> None:
    """The old block is not redrawn with the picture-following head curve."""
    show = _prepare_show()
    doc = PresetStore.load().get("Mix")
    program = YamlPresetProgram(doc)

    def beams(moment: float) -> list[BeamIntent]:
        stage = program.evaluate(moment, show)
        return [item for item in stage.fixtures.values() if isinstance(item, BeamIntent)]

    # Past the opening join, still on the first nod target.
    picture = _episode_start(doc, "Look 9 — епізод 1") + 3.2
    classic = _episode_start(doc, "Було — Look 9 — епізод 1") + 3.2
    picture_beams = beams(picture)
    classic_beams = beams(classic)
    assert picture_beams and classic_beams
    assert {round(item.tilt, 3) for item in picture_beams} == {0.28}
    assert {round(item.tilt, 3) for item in classic_beams} == {0.24}
    assert max(item.tilt for item in picture_beams + classic_beams) <= 0.64
    picture_lamps = [round(item.dimmer, 4) for item in beams(picture + 1.5)]
    classic_lamps = [round(item.dimmer, 4) for item in beams(classic + 1.5)]
    assert picture_lamps != classic_lamps
    high = beams(_episode_start(doc, "Було — Look 9 — епізод 1") + 4.0)
    assert high
    assert max(item.tilt for item in high) <= 0.640001


def test_mix_rules_follow_the_episode_into_a_solo_test_and_a_copy() -> None:
    """EditorPreview and a renamed copy used to drop every Mix rule."""
    from orng_led.api.runtime import AppRuntime

    show = _prepare_show()
    doc = PresetStore.load().get("Mix")
    runtime = AppRuntime.create(autostart_loop=False)
    second = doc.episodes[1]
    runtime.start_editor_episode_preview(
        preset_id="Mix",
        preset_label="Mix",
        episode_index=1,
        episode=second.model_dump(mode="json"),
    )
    solo = runtime.engine.editor_preview_program
    assert solo is not None
    assert solo.document.id == "EditorPreview"
    high = solo.evaluate(4.0, show)
    tilts = [item.tilt for item in high.fixtures.values() if isinstance(item, BeamIntent)]
    assert tilts
    assert all(abs(tilt - 0.64) < 1e-6 for tilt in tilts)

    seventh = doc.episodes[6]
    runtime.start_editor_episode_preview(
        preset_id="Mix",
        preset_label="Mix",
        episode_index=6,
        episode=seventh.model_dump(mode="json"),
    )
    bars = runtime.engine.editor_preview_program
    assert bars is not None
    motion = {
        fx.id: BeamMotionState.from_home(fx.spatial.home_pan, fx.spatial.home_tilt)
        for fx in show.patch.fixtures
        if fx.kind is FixtureKind.BEAM
    }
    painted: list[int] = []
    for step in range(36):
        frame = render_stage(
            show,
            bars.evaluate(step * 0.5, show),
            motion,
            master=1.0,
            scrub=True,
        )
        painted = [value for value in _bar_color_bytes(show, frame) if value]
        if painted:
            break
    assert painted
    assert set(painted) == {46}
    assert 166 not in painted

    copy = PresetStore.load().duplicate("Mix", "MixCopy")
    assert copy.id == "MixCopy"
    assert [episode.mix_style for episode in copy.episodes] == [
        episode.mix_style for episode in doc.episodes
    ]
    moment = _episode_start(doc, "Було — Діагональна смуга") + 0.533
    assert abs(moment - 576.533) < 1e-6
    original = render_stage(
        show, YamlPresetProgram(doc).evaluate(moment, show), motion, master=1.0, scrub=True
    )
    copied = render_stage(
        show, YamlPresetProgram(copy).evaluate(moment, show), motion, master=1.0, scrub=True
    )
    assert original[100] == 61
    assert copied[100] == 61
    assert copied[100] != 136


def test_preview_clip_uses_the_mix_document_duration() -> None:
    import time

    from orng_led.api.runtime import AppRuntime

    runtime = AppRuntime.create(autostart_loop=False)
    started = time.perf_counter()
    late = runtime.preview_clip("Mix", seconds=1, fps=4, start_s=738)
    assert time.perf_counter() - started < 3.0
    assert late["start_s"] == 738.0
    assert len(late["frames"]) == 4
    assert any(float(item.get("dimmer") or 0) > 0.05 for item in late["frames"][0]["beams"])
    earlier = runtime.preview_clip("Mix", seconds=1, fps=4, start_s=600)
    assert earlier["start_s"] == 600.0
    assert late["frames"][0] != earlier["frames"][0]
    for number in range(35, 43):
        start = float((number - 1) * 18)
        clip = runtime.preview_clip("Mix", seconds=1, fps=4, start_s=start)
        assert clip["start_s"] == start
        assert len(clip["frames"]) == 4
    wrap = runtime.preview_clip("Mix", seconds=4, fps=4, start_s=754)
    assert wrap["start_s"] == 754.0

    def _lit(frame: dict, kind: str) -> bool:
        return any(float(item.get("dimmer") or 0) > 0.05 for item in frame[kind])

    assert any(_lit(frame, "beams") and not _lit(frame, "bars") for frame in wrap["frames"])
    assert any(_lit(frame, "bars") and not _lit(frame, "beams") for frame in wrap["frames"])
