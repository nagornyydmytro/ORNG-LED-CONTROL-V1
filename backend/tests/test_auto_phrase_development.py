"""Accelerated Auto / Preset-BPM phrase development. Model time, not a venue take."""

from __future__ import annotations

from orng_led.audio.auto_show import StructuralEvent, build_auto_stage
from orng_led.audio.bpm import MAX_ANALYSIS_AGE_S, PHRASE_CAP_BEATS, plan_bar
from orng_led.audio.library import BY_ID, director_looks
from orng_led.audio.planner import EpisodePlanner
from orng_led.audio.settings import CHARACTER_DURATION, CAST_IDS, CHARACTER_IDS, director_home
from orng_led.config.io import load_show_config
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent
from orng_led.engine.renderer import render_stage
from orng_led.engine.timing import beat_hit
from orng_led.simulator.decode import decode_simulator_view

STAFF_SEED = 0x4F524E47
BPM = 120.0
BEATS_10_MIN = 10.0 * 60.0 * BPM / 60.0
SEEDS = (STAFF_SEED, 1, 7, 11, 99)


def _show():
    return load_show_config()


def _picture(stage) -> tuple:
    bars = []
    pars = []
    beams = []
    for ident, intent in sorted(stage.fixtures.items()):
        if isinstance(intent, BarIntent):
            bars.append((ident, round(float(intent.dimmer), 3), tuple(1 if bit > 0.5 else 0 for bit in intent.segments)))
        elif isinstance(intent, ParIntent):
            pars.append((ident, round(float(intent.intensity), 3)))
        elif isinstance(intent, BeamIntent):
            beams.append((ident, round(float(intent.dimmer), 3)))
    return (tuple(bars), tuple(pars), tuple(beams))


def _stage(fixtures, placements, episode, beats: float):
    return build_auto_stage(
        fixtures,
        episode.palette,
        episode.section,
        0.0,
        episode.intensity,
        beats=beats,
        cycle_beats=episode.picture_cycle,
        placements=placements,
        look=episode.look,
        actions=episode.actions,
        colour=episode.palette,
        cast=episode.cast,
    )


def test_director_looks_are_reachable_and_distinct_per_cast() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    for character in CHARACTER_IDS:
        for cast in CAST_IDS:
            theme = "orange" if cast == "all" else cast
            home = director_home(character, theme)
            names = director_looks("phrase", theme, character=character, current=home, fixtures=fixtures)
            assert names, (character, cast)
            assert all(BY_ID[name].needs_segments is False or cast != "heads" for name in names)
            if cast == "heads":
                assert all("bar" not in BY_ID[name].groups or "par" in BY_ID[name].groups for name in names)
            pictures = []
            for name in names:
                episode = type("E", (), {
                    "palette": "orange",
                    "section": "groove",
                    "intensity": 0.7,
                    "picture_cycle": 2.0,
                    "look": name,
                    "actions": (),
                    "cast": cast,
                })()
                pictures.append(_picture(_stage(fixtures, show.layout.placements, episode, 0.25)))
            assert len(set(pictures)) >= min(3, len(names)), (character, cast, names)


def test_empty_heads_neighborhood_does_not_paint_bars() -> None:
    show = _show()
    fixtures = [item for item in show.patch.fixtures if item.kind.value in {"par", "beam"}]
    planner = EpisodePlanner(seed=3)
    episode = planner.advance(
        0.0,
        section="groove",
        level=0.7,
        theme="heads",
        character="impact",
        locked=True,
        fixtures=fixtures,
    )
    assert "bar" not in episode.groups
    item = BY_ID[episode.look]
    assert item.needs_segments is False
    stage = _stage(fixtures, show.layout.placements, episode, 0.0)
    assert all(not ident.startswith("bar") for ident in stage.fixtures)


def test_ten_minute_groove_holds_within_phrase_and_changes_picture() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    report = []
    for seed in SEEDS:
        for character in CHARACTER_IDS:
            for cast in CAST_IDS:
                theme = "orange" if cast == "all" else cast
                planner = EpisodePlanner(seed=seed)
                beat = 0.0
                last = None
                hold = 0.0
                longest = 0.0
                changes: list[tuple[float, str, tuple]] = []
                picture = None
                while beat < BEATS_10_MIN - 1e-9:
                    episode = planner.advance(
                        beat,
                        section="groove",
                        level=0.7,
                        theme=theme,
                        character=character,
                        locked=True,
                        fixtures=fixtures,
                    )
                    if last is None or episode.look != last:
                        now = _picture(_stage(fixtures, placements, episode, beat))
                        if last is None:
                            picture = now
                            changes.append((0.0, episode.look, now))
                        else:
                            longest = max(longest, hold)
                            hold = 0.0
                            if now != picture:
                                changes.append((beat, episode.look, now))
                                picture = now
                        last = episode.look
                    else:
                        hold += 1.0
                    beat += 1.0
                longest = max(longest, hold)
                phrase = float(CHARACTER_DURATION[character]["phrase"])
                assert longest <= phrase + 4.0, (seed, character, cast, longest, phrase)
                assert len({item[2] for item in changes}) >= 2, (seed, character, cast)
                report.append((seed, character, cast, longest, len(changes)))
    assert report


def test_develop_runs_at_the_assigned_bar() -> None:
    show = _show()
    planner = EpisodePlanner(seed=STAFF_SEED)
    fixtures = list(show.patch.fixtures)
    opening = planner.advance(
        0.0, section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures
    )
    assert opening.duration_beats >= 16
    planner.advance(
        8.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        fixtures=fixtures,
        texture_id=1,
    )
    assert planner.upcoming is not None
    assert planner.upcoming.reason == "develop"
    assigned = float(planner.upcoming.start_beat)
    assert assigned <= 12.0
    assert assigned < opening.start_beat + opening.duration_beats
    played = planner.advance(
        assigned, section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures
    )
    assert played.reason == "develop"
    assert played.start_beat == assigned
    assert played.look != opening.look or played.ident != opening.ident


def test_lock_loss_keeps_developing_and_does_not_replay_drop() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    planner = EpisodePlanner(seed=STAFF_SEED)
    planner.advance(0.0, section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures)
    drop = StructuralEvent("drop", 4, 1.0)
    lost = planner.advance(
        8.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=False,
        tempo_held=False,
        events=(drop,),
        fixtures=fixtures,
    )
    assert lost.clock == "freewheel"
    assert lost.effects == ()
    assert planner.flash_gate(8.0) is None
    later = planner.advance(
        72.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=False,
        tempo_held=False,
        fixtures=fixtures,
    )
    assert later.clock == "freewheel"
    assert later.look != lost.look or later.ident != lost.ident
    back = planner.advance(
        72.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        events=(drop,),
        fixtures=fixtures,
    )
    assert back.reason != "drop_next"
    assert planner.upcoming is None or planner.upcoming.reason != "drop_next"


def test_true_silence_stays_calm_without_flashes() -> None:
    planner = EpisodePlanner(seed=3)
    episode = planner.advance(
        0.0, section="groove", level=0.7, theme="orange", locked=True, signal_lost=True
    )
    assert episode.reason == "signal_loss"
    assert planner.flash_gate(0.0) is None
    assert planner.strobe_at(0.0) == 0.0
    again = planner.advance(
        12.0, section="break", level=0.0, theme="orange", locked=False, signal_lost=True
    )
    assert again.reason == "signal_loss"
    assert planner.flash_gate(12.0) is None


def test_rhythmic_hit_peaks_on_the_grid_hit() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    on = build_auto_stage(
        fixtures, "orange", "groove", 0.0, 0.85, look="hit_heads", beats=0.0, cycle_beats=1.0,
        placements=placements, colour="orange", cast="heads",
    )
    off = build_auto_stage(
        fixtures, "orange", "groove", 0.0, 0.85, look="hit_heads", beats=0.5, cycle_beats=1.0,
        placements=placements, colour="orange", cast="heads",
    )
    on_peak = max((intent.intensity for intent in on.fixtures.values() if isinstance(intent, ParIntent)), default=0.0)
    off_peak = max((intent.intensity for intent in off.fixtures.values() if isinstance(intent, ParIntent)), default=0.0)
    assert on_peak > off_peak
    assert beat_hit(0.0) > beat_hit(0.5)


def test_phrase_cap_does_not_stretch_a_short_author_duration() -> None:
    short = plan_bar(0.0, 8.0, 0, 0.0)
    assert short.target_beat - short.start_beat <= 16.0 * 1.35 + 4.0
    long = plan_bar(0.0, 60.0, 0, 0.0)
    assert long.target_beat - long.start_beat <= PHRASE_CAP_BEATS + 4.0
    assert MAX_ANALYSIS_AGE_S >= 0.85
    assert MAX_ANALYSIS_AGE_S < 10.0


def test_preset_pulse_decoded_peak_is_on_the_hit() -> None:
    from orng_led.presets.models import EpisodeCard, LookAction, PresetDocument
    from orng_led.presets.program import YamlPresetProgram

    show = _show()
    program = YamlPresetProgram(
        PresetDocument(
            id="HIT",
            label="hit",
            builtin=False,
            musical_version=1,
            episodes=[
                EpisodeCard(
                    id="pulse",
                    duration_s=18,
                    groups=["par"],
                    effect="pulse",
                    intensity=1,
                    transition="cut",
                    actions=[
                        LookAction(
                            id="one",
                            groups=["par"],
                            effect="pulse",
                            cycle_beats=1,
                            intensity=1,
                        )
                    ],
                )
            ],
        )
    )
    loud = program.evaluate(0.0, show, musical_beats=0.0)
    quiet = program.evaluate(0.0, show, musical_beats=0.5)
    loud_frame = render_stage(show, loud, {})
    quiet_frame = render_stage(show, quiet, {})
    loud_view = decode_simulator_view(show, loud_frame)
    quiet_view = decode_simulator_view(show, quiet_frame)
    on = max((item.intensity for item in loud_view.pars if not item.face), default=0.0)
    off = max((item.intensity for item in quiet_view.pars if not item.face), default=0.0)
    assert on > off


def test_preset_bpm_caps_a_long_quiet_episode(tmp_path) -> None:  # noqa: ANN001
    from orng_led.api.runtime import AppRuntime
    from orng_led.presets.models import EpisodeCard, PresetDocument
    from orng_led.presets.program import YamlPresetProgram

    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path, show=_show())
    runtime.engine.presets["LONG"] = YamlPresetProgram(
        PresetDocument(
            id="LONG",
            label="long",
            builtin=False,
            musical_version=1,
            episodes=[
                EpisodeCard(id="a", duration_s=60, groups=["par"], effect="pulse", transition="cut"),
                EpisodeCard(id="b", duration_s=60, groups=["par"], effect="breathe", transition="cut"),
            ],
        )
    )
    runtime.engine.select_preset("LONG", reset_clock=True)
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    follower = runtime.bpm.follower
    moment = runtime.bpm.clock_s
    follower.bpm = 120.0
    follower.locked = True
    follower._started = True
    follower._beats = 0.0
    follower._at = moment
    follower._fresh_until = moment + 120.0
    before = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index
    for _ in range(34):
        runtime.tick(1.0)
    after = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0).episode_index
    assert after != before
