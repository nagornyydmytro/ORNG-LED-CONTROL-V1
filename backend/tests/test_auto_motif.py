"""Director holds a motif. It does not flip the gallery on a timer."""

from __future__ import annotations

from pathlib import Path

from orng_led.audio.auto_show import build_auto_stage
from orng_led.audio.library import (
    BY_ID,
    REGRESSION_VERTICAL_WAVE,
    bed_looks,
    director_looks,
    gallery_looks,
    usage_map,
)
from orng_led.audio.planner import EpisodePlanner
from orng_led.audio.programs import program_by_id, run_program
from orng_led.audio.settings import DIRECTOR_HOME
from orng_led.config.io import load_show_config
from orng_led.engine.intents import BarIntent
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

BPM = 120.0
SECONDS = 120.0
BEATS = SECONDS * BPM / 60.0
OLD_MOTIFS = {"smooth": 15, "rhythmic": 30, "impact": 60}
STEP = 0.5


def _show():
    return load_show_config()


def _groove(character: str, *, seed: int = 7) -> EpisodePlanner:
    show = _show()
    fixtures = list(show.patch.fixtures)
    planner = EpisodePlanner(seed=seed)
    beat = 0.0
    while beat < BEATS - 1e-9:
        planner.advance(
            beat,
            section="groove",
            level=0.62,
            theme="orange",
            character=character,
            locked=True,
            fixtures=fixtures,
        )
        beat = round(beat + STEP, 4)
    return planner


def _counts(planner: EpisodePlanner) -> dict[str, int]:
    kinds = [row["kind"] for row in planner.journal()]
    return {
        "motif": kinds.count("motif"),
        "hold": kinds.count("hold"),
        "variation": kinds.count("variation"),
        "accent": kinds.count("accent"),
    }


def _timeline(planner: EpisodePlanner) -> str:
    lines = ["beat    kind        look              role"]
    for row in planner.journal():
        if row["kind"] not in {
            "motif",
            "hold",
            "variation",
            "accent",
            "simplify",
            "intensify",
            "restore",
            "develop",
        }:
            continue
        lines.append(
            f"{float(row['beat']):7.1f}  {row['kind']:10}  {str(row.get('look', '')):16}  {row.get('role', '')}"
        )
    return "\n".join(lines)


def _bar_bits(stage) -> tuple[int, ...]:
    bits: list[int] = []
    for intent in stage.fixtures.values():
        if isinstance(intent, BarIntent):
            bits.extend(1 if float(bit) > 0.5 else 0 for bit in intent.segments)
    return tuple(bits)


def test_usage_splits_gallery_bed_and_director() -> None:
    usage = usage_map()
    gallery = usage["gallery"]
    director = usage["director"]
    bed = usage["bed"]
    assert gallery == gallery_looks()
    assert bed == bed_looks()
    assert len(gallery) >= 100
    assert set(bed) <= set(gallery)
    assert set(director) <= set(gallery)
    assert "breathe_pars" in bed
    assert REGRESSION_VERTICAL_WAVE in director
    working = director_looks(
        "phrase", "orange", character="rhythmic", current=REGRESSION_VERTICAL_WAVE
    )
    assert REGRESSION_VERTICAL_WAVE in working
    assert len(working) >= 40
    assert "breathe_pars" not in working
    assert len(working) < len(gallery)
    assert len(director) <= len(gallery)


def test_two_minute_groove_holds_the_motif_in_all_characters(tmp_path: Path) -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    report: list[str] = []
    planners: dict[str, EpisodePlanner] = {}
    for character in ("smooth", "rhythmic", "impact"):
        planner = _groove(character)
        planners[character] = planner
        counts = _counts(planner)
        old = OLD_MOTIFS[character]
        opening = planner.played[0]
        assert opening.look == DIRECTOR_HOME[character] or opening.look in director_looks(
            "phrase", "orange", character=character, current=DIRECTOR_HOME[character]
        )
        assert counts["motif"] < old
        assert 2 <= counts["motif"] <= 12
        assert counts["accent"] == 0
        assert counts["variation"] > 0
        if character == "impact":
            assert opening.picture_cycle <= 2.0
        looks = [episode.look for episode in planner.played]
        assert looks[0] != "wash"
        unique = []
        for look in looks:
            if not unique or unique[-1] != look:
                unique.append(look)
        report.append(
            f"{character}: motifs {counts['motif']} (was {old}), "
            f"holds {counts['hold']}, variations {counts['variation']}, "
            f"looks {' → '.join(unique)}"
        )
        report.append(_timeline(planner))
        report.append("")
        samples = []
        episode = planner.played[0]
        for step in range(8):
            beats = episode.start_beat + 0.25 + step * 0.5
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
            samples.append(_bar_bits(stage))
        assert any(sample != samples[0] for sample in samples[1:])
    assert planners["rhythmic"].played[0].look == REGRESSION_VERTICAL_WAVE
    path = tmp_path / "auto-motif-timeline.txt"
    path.write_text("\n".join(report), encoding="utf-8")
    assert path.stat().st_size > 80


def test_motif_course_break_mix_and_drop(tmp_path: Path) -> None:
    show = _show()
    run = run_program(
        program_by_id("motif_course"),
        list(show.patch.fixtures),
        show.layout.placements,
        seed=7,
        step_s=0.25,
    )
    kinds = [row["kind"] for row in run.journal]
    opening = run.episodes[0]
    assert opening.look == REGRESSION_VERTICAL_WAVE
    break_frames = [frame for frame in run.frames if frame.cue == "break"]
    assert break_frames
    break_looks = {frame.look for frame in break_frames}
    assert len(break_looks) == 1
    assert "simplify" in kinds
    assert "wash" not in break_looks
    assert "develop" in kinds or any(episode.reason == "develop" for episode in run.episodes)
    drop_frames = [frame for frame in run.frames if frame.reason == "drop_next"]
    assert drop_frames
    drop_episode = next(episode for episode in run.episodes if episode.reason == "drop_next")
    assert drop_episode.role == "accent"
    assert BY_ID[drop_episode.look].pool == "accent"
    after = [frame for frame in run.frames if frame.cue == "after"]
    assert after
    assert after[-1].look != drop_episode.look or after[-1].reason != "drop_next"
    home_after = [frame for frame in after if frame.look == opening.look or frame.look == opening.motif]
    assert home_after
    path = tmp_path / "auto-motif-course.txt"
    path.write_text(_timeline_from_journal(run.journal), encoding="utf-8")
    assert path.stat().st_size > 40


def _timeline_from_journal(journal: tuple[dict[str, object], ...]) -> str:
    planner = EpisodePlanner()
    planner._journal = list(journal)
    return _timeline(planner)


def test_vertical_wave_still_paints_one_bar_canvas() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    planner = EpisodePlanner(seed=1)
    episode = planner.advance(
        0.0,
        section="groove",
        level=0.7,
        theme="orange",
        character="rhythmic",
        fixtures=fixtures,
    )
    assert episode.look == REGRESSION_VERTICAL_WAVE
    first = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.85,
        look=episode.look,
        beats=0.4,
        cycle_beats=episode.picture_cycle,
        placements=show.layout.placements,
        actions=episode.actions,
    )
    later = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.85,
        look=episode.look,
        beats=1.2,
        cycle_beats=episode.picture_cycle,
        placements=show.layout.placements,
        actions=episode.actions,
    )
    assert _bar_bits(first) != _bar_bits(later)
    pose = {}
    frame = render_stage(show, first, pose)
    view = decode_simulator_view(show, frame)
    assert view.bars
    lit_bars = [bar for bar in view.bars if any(level > 0.02 for level in bar.segments)]
    assert len(lit_bars) >= 2
