"""Reproducible Auto director programs in model time. No production, no USB."""

from __future__ import annotations

from pathlib import Path

from orng_led.audio.programs import dump_html, program_by_id, programs, run_program
from orng_led.config.io import load_show_config
from orng_led.engine.clock import FRAME_DT
from orng_led.engine.intents import ParIntent, BarIntent


def _show():
    return load_show_config()


def _run(ident: str, *, step_s: float = 0.25, seed: int = 7):
    show = _show()
    return run_program(
        program_by_id(ident),
        list(show.patch.fixtures),
        show.layout.placements,
        seed=seed,
        step_s=step_s,
    )


def test_even_groove_develops_without_a_drop() -> None:
    run = _run("even_groove")
    assert run.program.duration_s >= 60.0
    assert all(frame.source == "script" for frame in run.frames)
    assert "drop" not in {frame.section for frame in run.frames}
    looks = [episode.look for episode in run.episodes]
    assert 1 <= len(set(looks)) < 8
    kinds = [row["kind"] for row in run.journal]
    assert kinds.count("motif") < 8
    assert "variation" in kinds
    for episode in run.episodes:
        assert episode.duration_beats in {2, 4, 8, 16, 32, 64}
        assert episode.picture_cycle in {0.5, 1.0, 2.0, 4.0, 8.0}
    assert all(frame.par_strobe == 0.0 for frame in run.frames)
    assert any(frame.cycle != run.frames[0].cycle for frame in run.frames)


def test_calm_passage_is_long_curves_not_a_label() -> None:
    run = _run("calm_passage")
    assert all(frame.source == "script" for frame in run.frames)
    calm = [frame for frame in run.frames if frame.cue == "calm"]
    assert calm
    assert all(frame.flash == 0.0 for frame in calm)
    assert max(episode.duration_beats for episode in run.episodes) >= 16
    assert max(episode.picture_cycle for episode in run.episodes) >= 4.0
    opening = run.episodes[0].look
    calm_episodes = [episode for episode in run.episodes if episode.section == "break"]
    assert calm_episodes
    assert all(episode.look == opening for episode in calm_episodes)


def test_rise_accent_is_one_shot_and_not_a_prediction() -> None:
    run = _run("rise_accent", step_s=FRAME_DT)
    rise = [frame for frame in run.frames if frame.cue == "rise"]
    accent = [frame for frame in run.frames if frame.reason == "drop_next"]
    after = [frame for frame in run.frames if frame.cue == "after"]
    assert rise and accent and after
    assert all(frame.source == "script" for frame in run.frames)
    consecutive = 0
    longest = 0
    for frame in rise:
        consecutive = consecutive + 1 if frame.flash else 0
        longest = max(longest, consecutive)
    assert longest == 0
    assert all(frame.flash == 0.0 for frame in rise)
    assert any(frame.flash for frame in accent[:3])
    assert after[-1].flash == 0.0
    assert after[-1].look != "accent"
    assert all(frame.par_strobe == 0.0 for frame in run.frames)


def test_lock_loss_does_not_reset_or_replay_a_drop() -> None:
    run = _run("lock_loss")
    hold = [frame for frame in run.frames if frame.cue == "hold"]
    free = next(frame for frame in run.frames if frame.cue == "freewheel")
    silent = [frame for frame in run.frames if frame.cue == "silence"]
    back = [frame for frame in run.frames if frame.cue == "return"]
    assert hold and all(frame.clock != "freewheel" for frame in hold)
    assert free.clock == "freewheel"
    assert free.look == hold[-1].look
    assert free.ident == hold[-1].ident
    assert all(frame.flash == 0.0 for frame in silent)
    assert all(frame.par_strobe == 0.0 for frame in run.frames)
    assert back[0].look != "accent"
    assert back[0].reason != "drop_next"
    assert all(frame.source == "script" for frame in run.frames)


def test_analyzed_ramp_marks_analysis_not_a_script() -> None:
    run = _run("analyzed_ramp", step_s=0.25)
    assert all(frame.source == "analysis" for frame in run.frames)
    assert "riser" in {frame.section for frame in run.frames}
    assert "drop" not in {frame.section for frame in run.frames}


def test_characters_paint_different_frames() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    bits = {}
    cycles = {}
    from orng_led.audio.auto_show import build_auto_stage
    from orng_led.audio.planner import EpisodePlanner

    for character in ("smooth", "rhythmic", "impact"):
        planner = EpisodePlanner(seed=3)
        episode = planner.advance(
            0.0, section="groove", level=0.65, theme="orange", character=character
        )
        samples = []
        for step in range(16):
            beats = step * 0.25
            stage = build_auto_stage(
                fixtures,
                "orange",
                episode.section,
                0.0,
                episode.intensity,
                beats=beats,
                cycle_beats=episode.picture_cycle,
                placements=show.layout.placements,
                look=episode.look,
                actions=episode.actions,
                colour="orange",
                cast=episode.cast,
            )
            packed = []
            for intent in stage.fixtures.values():
                if isinstance(intent, BarIntent):
                    packed.extend(int(bit > 0.5) for bit in intent.segments)
                elif isinstance(intent, ParIntent):
                    packed.append(round(intent.intensity, 2))
            samples.append(tuple(packed))
        bits[character] = tuple(samples)
        cycles[character] = episode.picture_cycle
        assert all(
            not isinstance(intent, ParIntent) or intent.strobe == 0.0
            for intent in stage.fixtures.values()
        )
    assert bits["smooth"] != bits["rhythmic"]
    assert bits["rhythmic"] != bits["impact"]
    assert cycles["smooth"] != cycles["impact"]


def test_palette_does_not_choose_the_cast() -> None:
    show = _show()
    from orng_led.audio.auto_show import build_auto_stage
    from orng_led.audio.planner import EpisodePlanner
    from orng_led.config.models import FixtureKind

    planner = EpisodePlanner(seed=4)
    episode = planner.advance(
        0.0, section="groove", level=0.7, theme="bars", palette="blue"
    )
    kinds: set[FixtureKind] = set()
    for step in range(8):
        stage = build_auto_stage(
            list(show.patch.fixtures),
            "blue",
            episode.section,
            0.0,
            0.7,
            beats=step * 0.25,
            cycle_beats=episode.picture_cycle,
            look=episode.look,
            groups=episode.groups,
            actions=episode.actions,
            colour="blue",
            cast="bars",
            placements=show.layout.placements,
        )
        kinds.update(
            fixture.kind
            for fixture in show.patch.fixtures
            if fixture.id in stage.fixtures
        )
    assert kinds == {FixtureKind.BAR}
    assert episode.palette == "blue"
    assert episode.cast == "bars"


def test_program_dump_is_local_and_labeled(tmp_path: Path) -> None:
    runs = [_run(item.id, step_s=1.0) for item in programs() if item.id != "rise_accent"]
    runs.append(_run("rise_accent", step_s=0.5))
    html = dump_html(runs)
    assert "script" in html and "analysis" in html
    assert "Ровный groove" in html
    path = tmp_path / "orng-auto-programs.html"
    path.write_text(html, encoding="utf-8")
    assert path.stat().st_size > 500
