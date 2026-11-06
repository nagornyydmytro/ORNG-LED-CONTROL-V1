"""Reproducible Auto director programs in model time.

Sections marked ``script`` are injected. Sections marked ``analysis`` come
from SectionTracker on synthetic audio. These are not venue takes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orng_led.audio.auto_show import StructuralEvent, SectionTracker, analyze_slice, build_auto_stage
from orng_led.audio.library import BY_ID
from orng_led.audio.planner import AutoEpisode, EpisodePlanner
from orng_led.config.models import FixtureKind
from orng_led.engine.clock import FRAME_DT, FPS
from orng_led.engine.intents import ParIntent

BPM = 120.0
BEAT_S = 60.0 / BPM


@dataclass(frozen=True)
class ProgramCue:
    """One stretch of a program. ``source`` says who chose the section."""

    start_s: float
    end_s: float
    name: str
    source: str
    section: str
    level: float
    locked: bool = True
    tempo_held: bool = False
    signal_lost: bool = False
    drop: bool = False
    texture: bool = False
    character: str = "rhythmic"
    palette: str = "orange"
    cast: str = "all"
    period: float | str = "auto"


@dataclass(frozen=True)
class Program:
    id: str
    title: str
    duration_s: float
    character: str
    cues: tuple[ProgramCue, ...]
    notes: str


@dataclass
class ProgramFrame:
    t: float
    beat: float
    source: str
    cue: str
    section: str
    look: str
    family: str | None
    reason: str
    ident: int
    duration_beats: int
    cycle: float
    flash: float
    clock: str
    par_strobe: float
    par_intensity: float
    bar_bits: tuple[int, ...]


@dataclass
class ProgramRun:
    program: Program
    frames: list[ProgramFrame] = field(default_factory=list)
    episodes: list[AutoEpisode] = field(default_factory=list)
    journal: tuple[dict[str, object], ...] = ()


def programs() -> tuple[Program, ...]:
    return (
        Program(
            id="even_groove",
            title="Ровный groove",
            duration_s=90.0,
            character="rhythmic",
            notes="Секции заданы сценарием. Анализ аудио не участвует.",
            cues=(
                ProgramCue(0.0, 90.0, "groove", "script", "groove", 0.62, character="rhythmic"),
            ),
        ),
        Program(
            id="calm_passage",
            title="Спокойный участок",
            duration_s=75.0,
            character="smooth",
            notes="Секции заданы сценарием. Плавний характер, длинные циклы.",
            cues=(
                ProgramCue(0.0, 12.0, "open", "script", "groove", 0.45, character="smooth"),
                ProgramCue(12.0, 75.0, "calm", "script", "break", 0.22, character="smooth"),
            ),
        ),
        Program(
            id="rise_accent",
            title="Нарастание и акцент",
            duration_s=70.0,
            character="impact",
            notes="Секции заданы сценарием. Drop — одно событие, не предсказание.",
            cues=(
                ProgramCue(0.0, 20.0, "groove", "script", "groove", 0.55, character="impact"),
                ProgramCue(20.0, 36.0, "rise", "script", "riser", 0.78, character="impact"),
                ProgramCue(36.0, 40.0, "accent", "script", "drop", 0.95, drop=True, character="impact"),
                ProgramCue(40.0, 70.0, "after", "script", "groove", 0.58, character="impact"),
            ),
        ),
        Program(
            id="lock_loss",
            title="Потеря и возврат ритма",
            duration_s=80.0,
            character="rhythmic",
            notes="Секции заданы сценарием. Краткий hold, затем freewheel, затем recover без старого drop.",
            cues=(
                ProgramCue(0.0, 24.0, "locked", "script", "groove", 0.6, locked=True),
                ProgramCue(24.0, 28.0, "hold", "script", "groove", 0.55, locked=False, tempo_held=True),
                ProgramCue(28.0, 48.0, "freewheel", "script", "groove", 0.4, locked=False, tempo_held=False),
                ProgramCue(48.0, 56.0, "silence", "script", "break", 0.05, locked=False, signal_lost=True),
                ProgramCue(56.0, 80.0, "return", "script", "groove", 0.6, locked=True),
            ),
        ),
        Program(
            id="analyzed_ramp",
            title="Нарастание по анализу аудио",
            duration_s=16.0,
            character="rhythmic",
            notes="Секция из анализа ускоряющейся дроби, не сценарий и не предсказание дропа.",
            cues=(
                ProgramCue(0.0, 16.0, "audio", "analysis", "groove", 0.4),
            ),
        ),
        Program(
            id="motif_course",
            title="Мотив: спад, сведение, дроп",
            duration_s=96.0,
            character="rhythmic",
            notes="Один мотив. Спад упрощает его, сведение делает родственный шаг, дроп даёт короткий акцент и возврат.",
            cues=(
                ProgramCue(0.0, 24.0, "groove", "script", "groove", 0.62, character="rhythmic"),
                ProgramCue(24.0, 40.0, "break", "script", "break", 0.22, character="rhythmic"),
                ProgramCue(40.0, 56.0, "return", "script", "groove", 0.58, character="rhythmic"),
                ProgramCue(56.0, 72.0, "mix", "script", "groove", 0.6, texture=True, character="rhythmic"),
                ProgramCue(72.0, 76.0, "drop", "script", "drop", 0.95, drop=True, character="rhythmic"),
                ProgramCue(76.0, 96.0, "after", "script", "groove", 0.58, character="rhythmic"),
            ),
        ),
    )


def program_by_id(ident: str) -> Program:
    for item in programs():
        if item.id == ident:
            return item
    raise KeyError(ident)


def _cue_at(program: Program, t: float) -> ProgramCue:
    for cue in program.cues:
        if cue.start_s <= t < cue.end_s - 1e-9:
            return cue
    return program.cues[-1]


def _bar_bits(stage) -> tuple[int, ...]:
    bits: list[int] = []
    for intent in stage.fixtures.values():
        segments = getattr(intent, "segments", None)
        if segments is None:
            continue
        bits.extend(1 if float(bit) > 0.5 else 0 for bit in segments)
    return tuple(bits)


def _par_fields(stage) -> tuple[float, float]:
    strobe = 0.0
    intensity = 0.0
    for intent in stage.fixtures.values():
        if isinstance(intent, ParIntent):
            strobe = max(strobe, float(intent.strobe))
            intensity = max(intensity, float(intent.intensity))
    return strobe, intensity


def run_program(
    program: Program,
    fixtures: list[object],
    placements: object = None,
    *,
    seed: int = 0x4F524E47,
    step_s: float | None = None,
) -> ProgramRun:
    """Play the director in model time. Default step is one renderer frame."""
    planner = EpisodePlanner(seed=seed)
    planner.character = program.character
    planner.bind_scene(list(fixtures))
    hop = FRAME_DT if step_s is None else float(step_s)
    tracker = SectionTracker() if any(cue.source == "analysis" for cue in program.cues) else None
    run = ProgramRun(program=program)
    drop_sent = False
    texture_id = 0
    texture_cue = None
    t = 0.0
    last_ident = None
    while t < program.duration_s - 1e-9:
        cue = _cue_at(program, t)
        if tracker is not None and cue.source == "analysis":
            samples = _ramp_block(t, hop)
            hearing = analyze_slice(samples, 48000)
            tracker.push(
                hearing.rms,
                hop,
                onset=hearing.onset,
                hearing=hearing,
                beat_period_s=BEAT_S,
            )
            section = tracker.section
            level = tracker.shown
            events = tracker.take_events()
            source = "analysis"
        else:
            section = cue.section
            level = cue.level
            events = ()
            source = cue.source
            if cue.drop and not drop_sent:
                events = (StructuralEvent("drop", 1, t),)
                drop_sent = True
            if cue.texture and cue.name != texture_cue:
                texture_id += 1
                texture_cue = cue.name
        drive = False
        drive_id = 0
        drive_density = 0.0
        if tracker is not None and cue.source == "analysis":
            drive = bool(tracker.roll_drive) and bool(cue.locked)
            drive_id = int(tracker.drive_id)
            drive_density = float(tracker.roll_density)
        beat = t / BEAT_S
        theme = cue.cast if cue.cast in {"bars", "heads"} else cue.palette
        episode = planner.advance(
            beat,
            section=section,
            level=level,
            theme=theme,
            events=events,
            signal_lost=cue.signal_lost,
            locked=cue.locked,
            tempo_held=cue.tempo_held,
            character=cue.character,
            period=cue.period,
            palette=cue.palette,
            wall_s=t,
            fixtures=list(fixtures),
            texture_id=texture_id,
            drive=drive,
            drive_id=drive_id,
            drive_density=drive_density,
            beat_period_s=BEAT_S,
        )
        flash = planner.flash_gate(beat, wall_s=t)
        stage = build_auto_stage(
            fixtures,
            cue.palette,
            episode.section,
            0.0,
            episode.intensity,
            beats=beat,
            cycle_beats=episode.picture_cycle,
            placements=placements,
            look=episode.look,
            motion_cycle=episode.motion_cycle,
            flash_hold=flash,
            groups=episode.groups,
            actions=episode.actions,
            colour=cue.palette,
            cast=episode.cast,
        )
        strobe, intensity = _par_fields(stage)
        chosen = BY_ID.get(episode.look)
        if last_ident != episode.ident:
            run.episodes.append(episode)
            last_ident = episode.ident
        run.frames.append(
            ProgramFrame(
                t=round(t, 4),
                beat=round(beat, 4),
                source=source,
                cue=cue.name,
                section=section,
                look=episode.look,
                family=None if chosen is None else chosen.family,
                reason=episode.reason,
                ident=episode.ident,
                duration_beats=episode.duration_beats,
                cycle=episode.picture_cycle,
                flash=0.0 if flash is None else flash,
                clock=episode.clock,
                par_strobe=strobe,
                par_intensity=intensity,
                bar_bits=_bar_bits(stage),
            )
        )
        t = round(t + hop, 6)
    run.journal = planner.journal()
    return run


def _ramp_block(t: float, hop: float, rate: int = 48000) -> list[float]:
    """Accelerating click train. A sine pad is a fader, not a musical rise."""
    count = max(8, int(rate * hop))
    samples = [0.0] * count
    end = t + hop
    moment = 0.0
    start_period = 0.5
    end_period = 0.07
    while moment < end:
        if moment >= t:
            start = int((moment - t) * rate)
            for offset in range(min(90, count - start)):
                samples[start + offset] = max(
                    samples[start + offset],
                    0.9 * (1.0 - offset / 90.0),
                )
        frac = min(1.0, moment / 10.0)
        nxt = moment + start_period + (end_period - start_period) * frac
        if nxt <= moment:
            break
        moment = nxt
    return samples


def dump_html(runs: list[ProgramRun]) -> str:
    rows = []
    for run in runs:
        rows.append(f"<h2>{run.program.title}</h2>")
        rows.append(f"<p>{run.program.notes} Модельне время {run.program.duration_s:.0f} с, {FPS:.0f} Гц.</p>")
        rows.append("<table><thead><tr><th>t</th><th>источник</th><th>секция</th><th>look</th><th>семья</th><th>доли</th><th>цикл</th><th>часы</th><th>вспышка</th></tr></thead><tbody>")
        last = None
        for frame in run.frames:
            key = (frame.ident, frame.source, frame.section, frame.look, frame.clock)
            if key == last:
                continue
            last = key
            flash = "on" if frame.flash else ""
            rows.append(
                "<tr>"
                f"<td>{frame.t:.1f}</td><td>{frame.source}</td><td>{frame.section}</td>"
                f"<td>{frame.look}</td><td>{frame.family or ''}</td><td>{frame.duration_beats}</td>"
                f"<td>{frame.cycle:g}</td><td>{frame.clock}</td><td>{flash}</td>"
                "</tr>"
            )
        rows.append("</tbody></table>")
    return (
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'/>"
        "<title>Auto director programs</title>"
        "<style>body{font:14px/1.4 sans-serif;background:#161412;color:#e8e0d4;margin:24px}"
        "table{border-collapse:collapse;margin:12px 0 28px}td,th{border:1px solid #3a322c;padding:4px 8px}"
        "th{color:#b5aa9a}</style></head><body>"
        "<h1>Программы режиссёра Auto</h1>"
        "<p>Лабораторное модельное время. Не площадка и не USB.</p>"
        "<p><b>script</b> — сценарная секция, заданная программой. "
        "<b>analysis</b> — распознавание аудио SectionTracker. Это не одно и то же.</p>"
        + "".join(rows)
        + "</body></html>"
    )


def fixture_kinds_present(stage) -> set[str]:
    kinds = set()
    for fixture_id, intent in stage.fixtures.items():
        del fixture_id
        if getattr(intent, "segments", None) is not None:
            kinds.add("bar")
        elif isinstance(intent, ParIntent):
            kinds.add("par")
        else:
            kinds.add("beam")
    return kinds


def assert_no_face(stage, fixtures: list[object]) -> None:
    faces = {fixture.id for fixture in fixtures if getattr(fixture, "kind", None) is FixtureKind.FACE_PAR}
    assert faces.isdisjoint(stage.fixtures)
