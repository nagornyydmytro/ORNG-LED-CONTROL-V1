"""Home Auto-show demonstration. Not an operator page.

Two chapters: a scripted director course, and hearing (a cached legal clip
when present, otherwise a synthetic accelerating roll). MockTransport only.
"""

# ruff: noqa: E501

from __future__ import annotations

import base64
import io
import json
import math
import os
import tempfile
import wave
from pathlib import Path

from orng_led.audio.auto_show import (
    SectionTracker,
    StructuralEvent,
    analyze_slice,
    build_auto_stage,
)
from orng_led.audio.bpm import click_track
from orng_led.audio.service import BpmService
from orng_led.audio.planner import EpisodePlanner
from orng_led.audio.programs import BEAT_S, BPM, Program, ProgramCue
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import ParIntent
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

LISTEN_RATE = 22050
STEP_S = 0.1

SCRIPT = Program(
    id="home_script",
    title="Сценарний хід",
    duration_s=32.0,
    character="rhythmic",
    notes="Секції задані сценарієм. Аналіз фонограми не бере участі.",
    cues=(
        ProgramCue(0.0, 12.0, "groove", "script", "groove", 0.62, character="rhythmic"),
        ProgramCue(12.0, 18.0, "break", "script", "break", 0.22, character="rhythmic"),
        ProgramCue(18.0, 24.0, "mix", "script", "groove", 0.6, texture=True, character="rhythmic"),
        ProgramCue(24.0, 26.0, "drop", "script", "drop", 0.95, drop=True, character="rhythmic"),
        ProgramCue(26.0, 32.0, "after", "script", "groove", 0.58, character="rhythmic"),
    ),
)


def _pose(show: object) -> dict[str, BeamMotionState]:
    pose: dict[str, BeamMotionState] = {}
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        pose[fixture.id] = BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
    return pose


def _positions(show: object) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for item in show.layout.placements:
        out[item.fixture_id] = (float(item.x), float(item.y))
    return out


def _cue_at(program: Program, t: float) -> ProgramCue:
    for cue in program.cues:
        if cue.start_s <= t < cue.end_s - 1e-9:
            return cue
    return program.cues[-1]


def _par_fields(stage: object) -> tuple[float, float]:
    strobe = 0.0
    intensity = 0.0
    for intent in stage.fixtures.values():
        if isinstance(intent, ParIntent):
            strobe = max(strobe, float(intent.strobe))
            intensity = max(intensity, float(intent.intensity))
    return strobe, intensity


def _motion_label(*, reason: str, look_changed: bool, strobe: float, series: bool) -> str:
    if series or strobe > 0.2:
        return "strobe"
    if reason in {"drop_next", "accent"}:
        return "accent"
    if look_changed or reason in {"variation", "develop"}:
        return "move"
    return "hold"


def _draw(view: object, positions: dict[str, tuple[float, float]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for par in view.pars:
        at = positions.get(par.id)
        if at is None:
            continue
        items.append(
            {
                "id": par.id,
                "k": "p",
                "x": round(at[0], 3),
                "y": round(at[1], 3),
                "r": round(par.r, 3),
                "g": round(par.g, 3),
                "b": round(par.b, 3),
                "l": round(par.intensity, 3),
            }
        )
    for bar in view.bars:
        at = positions.get(bar.id)
        if at is None:
            continue
        items.append(
            {
                "id": bar.id,
                "k": "b",
                "x": round(at[0], 3),
                "y": round(at[1], 3),
                "r": round(bar.r, 3),
                "g": round(bar.g, 3),
                "b": round(bar.b, 3),
                "l": round(bar.dimmer, 3),
                "s": [round(bit, 2) for bit in bar.segments],
            }
        )
    for beam in view.beams:
        at = positions.get(beam.id)
        if at is None:
            continue
        items.append(
            {
                "id": beam.id,
                "k": "h",
                "x": round(at[0], 3),
                "y": round(at[1], 3),
                "r": round(beam.r, 3),
                "g": round(beam.g, 3),
                "b": round(beam.b, 3),
                "l": round(beam.dimmer, 3),
            }
        )
    return items


def _pcm(samples: list[float], rate: int = LISTEN_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for value in samples:
            clipped = max(-1.0, min(1.0, value))
            frames.extend(int(clipped * 32767.0).to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(frames))
    return buf.getvalue()


def _resample(samples: list[float], src: int, dest: int) -> list[float]:
    if src == dest or not samples:
        return list(samples)
    ratio = dest / src
    count = int(len(samples) * ratio)
    out: list[float] = []
    for index in range(count):
        pos = index / ratio
        left = int(pos)
        right = min(left + 1, len(samples) - 1)
        frac = pos - left
        out.append(samples[left] * (1.0 - frac) + samples[right] * frac)
    return out


def _script_audio(program: Program, rate: int = LISTEN_RATE) -> list[float]:
    total = int(program.duration_s * rate)
    out = [0.0] * total
    beat = 60.0 / BPM
    t = 0.0
    while t < program.duration_s:
        cue = _cue_at(program, t)
        index = int(t * rate)
        if index >= total:
            break
        if cue.section == "break":
            amp = 0.18
            width = 90
        elif cue.drop:
            amp = 0.95
            width = 220
        elif cue.texture:
            amp = 0.55
            width = 70
        else:
            amp = 0.7
            width = 120
        for offset in range(min(width, total - index)):
            out[index + offset] = max(out[index + offset], amp * (1.0 - offset / width))
        if cue.texture:
            hat = index + int(0.125 * beat * rate)
            if hat < total:
                for offset in range(min(40, total - hat)):
                    out[hat + offset] = max(out[hat + offset], 0.25 * (1.0 - offset / 40))
        t += beat
    return out


def _cached_clip() -> tuple[list[float], int, str] | None:
    cache_root = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    root = Path(cache_root) / "ORNG-LED-CONTROL" / "cache" / "music"
    path = root / "dance_120_emsoundmoves.wav"
    if not path.is_file() or path.stat().st_size < 8000:
        return None
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        return None
    samples = [
        int.from_bytes(frames[index : index + 2], "little", signed=True) / 32768.0
        for index in range(0, len(frames) - 1, 2)
    ]
    title = "Аналіз справжньої фонограми (CC0 Drumloop 003, emsoundmoves)"
    return samples, rate, title


def _accelerating(
    seconds: float, rate: int, start_div: float = 1.0, end_div: float = 8.0
) -> list[float]:
    total = int(seconds * rate)
    samples = [0.0] * total
    moment = 0.0
    while moment < seconds:
        start = int(moment * rate)
        width = 48
        for offset in range(min(width, total - start)):
            samples[start + offset] = max(samples[start + offset], 0.9 * (1.0 - offset / width))
        frac = min(1.0, moment / max(seconds * 0.65, 0.1))
        div = start_div + (end_div - start_div) * frac
        moment += 60.0 / (BPM * max(0.5, div))
    return samples


def _hit(seconds: float = 0.45, amp: float = 0.98, rate: int = 44100) -> list[float]:
    samples = [0.0] * int(seconds * rate)
    tau = 0.07 * rate
    for offset in range(len(samples)):
        env = amp * math.exp(-offset / tau)
        samples[offset] = env * math.sin(2.0 * math.pi * 55.0 * offset / rate)
    return samples


def _roll_audio(rate: int = 44100) -> list[float]:
    return (
        click_track(120, 3.0, rate)
        + _accelerating(4.0, rate)
        + _hit(0.4, rate=rate)
        + click_track(120, 2.5, rate)
    )


def _timeline(frames: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for frame in frames:
        kind = str(frame["motion"])
        if rows and rows[-1]["kind"] == kind and rows[-1]["look"] == frame["look"]:
            rows[-1]["end"] = frame["t"]
            continue
        rows.append(
            {
                "start": frame["t"],
                "end": frame["t"],
                "kind": kind,
                "look": frame["look"],
                "section": frame["section"],
            }
        )
    if rows:
        rows[-1]["end"] = frames[-1]["t"]
    return rows


def _run_script(show: object) -> dict[str, object]:
    planner = EpisodePlanner(seed=0x4F524E47)
    planner.character = SCRIPT.character
    fixtures = list(show.patch.fixtures)
    planner.bind_scene(fixtures)
    pose = _pose(show)
    positions = _positions(show)
    frames: list[dict[str, object]] = []
    drop_sent = False
    texture_id = 0
    texture_cue = None
    last_look = None
    t = 0.0
    while t < SCRIPT.duration_s - 1e-9:
        cue = _cue_at(SCRIPT, t)
        events: tuple[StructuralEvent, ...] = ()
        if cue.drop and not drop_sent:
            events = (StructuralEvent("drop", 1, t),)
            drop_sent = True
        if cue.texture and cue.name != texture_cue:
            texture_id += 1
            texture_cue = cue.name
        beat = t / BEAT_S
        episode = planner.advance(
            beat,
            section=cue.section,
            level=cue.level,
            theme=cue.palette,
            events=events,
            signal_lost=cue.signal_lost,
            locked=cue.locked,
            tempo_held=cue.tempo_held,
            character=cue.character,
            period=cue.period,
            palette=cue.palette,
            wall_s=t,
            fixtures=fixtures,
            texture_id=texture_id,
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
            placements=show.layout.placements,
            look=episode.look,
            motion_cycle=episode.motion_cycle,
            flash_hold=flash,
            groups=episode.groups,
            actions=episode.actions,
            colour=cue.palette,
            cast=episode.cast,
        )
        strobe, _intensity = _par_fields(stage)
        frame = render_stage(show, stage, pose)
        view = decode_simulator_view(show, frame)
        flash_value = 0.0 if flash is None else float(flash)
        look_changed = last_look is not None and last_look != episode.look
        frames.append(
            {
                "t": round(t, 2),
                "look": episode.look,
                "section": episode.section,
                "reason": episode.reason,
                "source": "script",
                "strobe": round(strobe, 3),
                "flash": flash_value,
                "motion": _motion_label(
                    reason=episode.reason,
                    look_changed=look_changed,
                    strobe=strobe,
                    series=bool(planner._series_on),
                ),
                "fixtures": _draw(view, positions),
            }
        )
        last_look = episode.look
        t = round(t + STEP_S, 6)
    wav = _pcm(_script_audio(SCRIPT))
    return {
        "id": "script",
        "title": SCRIPT.title,
        "kind": "script",
        "kind_title": "Сценарна демонстрація",
        "note": SCRIPT.notes,
        "duration": SCRIPT.duration_s,
        "audio": "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii"),
        "frames": frames,
        "timeline": _timeline(frames),
    }


def _run_hearing(
    show: object,
    *,
    ident: str,
    samples: list[float],
    rate: int,
    duration_s: float,
    title: str,
    kind_title: str,
    note: str,
    listen: list[float],
    hop_s: float = STEP_S,
) -> dict[str, object]:
    planner = EpisodePlanner(seed=0x4F524E47)
    planner.character = "rhythmic"
    fixtures = list(show.patch.fixtures)
    planner.bind_scene(fixtures)
    pose = _pose(show)
    positions = _positions(show)
    tracker = SectionTracker()
    frames: list[dict[str, object]] = []
    last_look = None
    t = 0.0
    hop = hop_s
    while t < duration_s - 1e-9:
        start = int(t * rate)
        end = int((t + hop) * rate)
        chunk = samples[start:end]
        if len(chunk) < 8:
            chunk = list(chunk) + [0.0] * (8 - len(chunk))
        hearing = analyze_slice(chunk, rate)
        tracker.push(
            hearing.rms,
            hop,
            onset=hearing.onset,
            hearing=hearing,
            beat_period_s=BEAT_S,
        )
        beat = t / BEAT_S
        episode = planner.advance(
            beat,
            section=tracker.section,
            level=tracker.shown,
            theme="orange",
            events=tracker.take_events(),
            locked=True,
            character="rhythmic",
            palette="orange",
            wall_s=t,
            fixtures=fixtures,
            drive=bool(tracker.roll_drive),
            drive_id=int(tracker.drive_id),
            drive_density=float(tracker.roll_density),
            beat_period_s=BEAT_S,
        )
        flash = planner.flash_gate(beat, wall_s=t)
        stage = build_auto_stage(
            fixtures,
            "orange",
            episode.section,
            0.0,
            episode.intensity,
            beats=beat,
            cycle_beats=episode.picture_cycle,
            placements=show.layout.placements,
            look=episode.look,
            motion_cycle=episode.motion_cycle,
            flash_hold=flash,
            groups=episode.groups,
            actions=episode.actions,
            colour="orange",
            cast=episode.cast,
        )
        strobe, _intensity = _par_fields(stage)
        frame = render_stage(show, stage, pose)
        view = decode_simulator_view(show, frame)
        flash_value = 0.0 if flash is None else float(flash)
        look_changed = last_look is not None and last_look != episode.look
        frames.append(
            {
                "t": round(t, 2),
                "look": episode.look,
                "section": episode.section,
                "reason": episode.reason,
                "source": "analysis",
                "strobe": round(strobe, 3),
                "flash": flash_value,
                "motion": _motion_label(
                    reason=episode.reason,
                    look_changed=look_changed,
                    strobe=strobe,
                    series=bool(planner._series_on),
                ),
                "fixtures": _draw(view, positions),
            }
        )
        last_look = episode.look
        t = round(t + hop, 6)
    wav = _pcm(listen)
    return {
        "id": ident,
        "title": title,
        "kind": "analysis",
        "kind_title": kind_title,
        "grid": "given",
        "note": note,
        "duration": duration_s,
        "audio": "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii"),
        "frames": frames,
        "timeline": _timeline(frames),
    }


def _analysis_chapters(show: object) -> list[dict[str, object]]:
    chapters: list[dict[str, object]] = []
    cached = _cached_clip()
    if cached is not None:
        samples, rate, title = cached
        duration_s = min(8.0, len(samples) / rate)
        chapters.append(
            _run_hearing(
                show,
                ident="analysis_clip",
                samples=samples,
                rate=rate,
                duration_s=duration_s,
                title=title,
                kind_title="Аналіз справжньої фонограми",
                note=(
                    "Секції з аудіо. Сітка задана: доля = t / BEAT_S, lock увімкнено. "
                    "Це сценарій аналізу, не прийомка синхрону."
                ),
                listen=_resample(samples[: int(duration_s * rate)], rate, LISTEN_RATE),
            )
        )
    rate = 44100
    samples = _roll_audio(rate)
    duration_s = len(samples) / rate
    chapters.append(
        _run_hearing(
            show,
            ident="analysis_roll",
            samples=samples,
            rate=rate,
            duration_s=duration_s,
            title="Аналіз синтетичної дроби",
            kind_title="Аналіз фрагмента",
            note=(
                "Розпізнавання синтетичного фрагмента: рівний удар, прискорювана дріб, дроп. "
                "Сітка задана: доля = t / BEAT_S, lock увімкнено. Це сценарій аналізу, "
                "не прийомка синхрону."
            ),
            listen=_resample(samples, rate, LISTEN_RATE),
            hop_s=0.25,
        )
    )
    return chapters


_SYNC_REASON = {
    "no_input": "Підключіть звук",
    "waiting": "Чекаємо музику",
    "searching": "Шукаємо ритм",
    "hold": "Утримання",
    "fallback": "Без сітки",
    "locked": "",
}


def _run_sync(
    show: object,
    *,
    ident: str,
    samples: list[float],
    rate: int,
    duration_s: float,
    title: str,
    note: str,
    listen: list[float],
    hop_s: float = 0.5,
) -> dict[str, object]:
    """Capture-time blocks → stock analyzer/follower → planner → decoded frame."""
    fixtures = list(show.patch.fixtures)
    pose = _pose(show)
    positions = _positions(show)
    frames: list[dict[str, object]] = []
    last_look = None
    highlight_s: float | None = None
    with tempfile.TemporaryDirectory(prefix="orng-sync-demo-") as raw:
        service = BpmService(Path(raw), "demo")
        service.configure(enabled=True, scope="auto", palette="orange", character="rhythmic")
        service.capture = "listening"
        service.device_name = "Demo"
        t = 0.0
        hop = hop_s
        hop_n = max(8, int(hop * rate))
        while t < duration_s - 1e-9:
            start = int(t * rate)
            chunk = samples[start : start + hop_n]
            if len(chunk) < 8:
                chunk = list(chunk) + [0.0] * (8 - len(chunk))
            service.submit_block(chunk, rate, captured_at=t, now=t)
            service.poll(0.0)
            status = service.status()
            accepted = status["bpm"] if status.get("locked") else None
            if highlight_s is None and accepted is not None:
                highlight_s = t
            stage = service.look(fixtures, show.layout.placements)
            strobe, _intensity = _par_fields(stage)
            frame = render_stage(show, stage, pose)
            view = decode_simulator_view(show, frame)
            episode = service.planner.current
            look = "" if episode is None else episode.look
            section = "" if episode is None else episode.section
            reason = "" if episode is None else episode.reason
            flash = service.planner.flash_gate(
                0.0 if episode is None else service.follower.action_beats_at(service.clock_s),
                wall_s=t,
            )
            look_changed = last_look is not None and last_look != look
            frames.append(
                {
                    "t": round(t, 2),
                    "look": look,
                    "section": section,
                    "reason": reason,
                    "source": "sync",
                    "bpm": None if accepted is None else round(float(accepted), 1),
                    "bpm_reason": _SYNC_REASON.get(str(status.get("sync") or ""), "Шукаємо ритм"),
                    "strobe": round(strobe, 3),
                    "flash": 0.0 if flash is None else float(flash),
                    "motion": _motion_label(
                        reason=reason,
                        look_changed=look_changed,
                        strobe=strobe,
                        series=bool(service.planner._series_on),
                    ),
                    "fixtures": _draw(view, positions),
                }
            )
            last_look = look
            t = round(t + hop, 6)
    wav = _pcm(listen)
    return {
        "id": ident,
        "title": title,
        "kind": "sync",
        "kind_title": "Прийнятий темп",
        "note": note,
        "duration": duration_s,
        "highlight_s": highlight_s,
        "audio": "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii"),
        "frames": frames,
        "timeline": _timeline(frames),
    }


def _sync_chapters(show: object) -> list[dict[str, object]]:
    note = (
        "Той самий звук іде блоками за часом захоплення в штатний аналізатор і follower, "
        "далі в планувальник і декодований кадр. BPM, фазу, lock і секцію не підставлено."
    )
    chapters: list[dict[str, object]] = []
    rate = 44100
    samples = click_track(124.0, 8.0, rate)
    chapters.append(
        _run_sync(
            show,
            ident="sync_click_124",
            samples=samples,
            rate=rate,
            duration_s=8.0,
            title="Синхрон синтетичного 124 BPM",
            note=note + " Розмітка 124 незалежна від inspect_tempo.",
            listen=_resample(samples, rate, LISTEN_RATE),
        )
    )
    cached = _cached_clip()
    if cached is not None:
        audio, src_rate, title = cached
        duration_s = min(8.0, len(audio) / src_rate)
        chapters.append(
            _run_sync(
                show,
                ident="sync_clip",
                samples=audio,
                rate=src_rate,
                duration_s=duration_s,
                title=title.replace("Аналіз", "Синхрон"),
                note=note,
                listen=_resample(audio[: int(duration_s * src_rate)], src_rate, LISTEN_RATE),
            )
        )
    return chapters


def build_demo(show: object | None = None) -> dict[str, object]:
    loaded = show if show is not None else load_show_config()
    chapters = [_run_script(loaded), *_sync_chapters(loaded), *_analysis_chapters(loaded)]
    return {
        "title": "Auto-шоу — домашня оцінка",
        "note": (
            "Не пульт і не керування залом. Слухайте фрагмент і дивіться, "
            "коли світло тримає мотив, змінює рух і дозволяє строб. "
            "Глава «Прийнятий темп» — домашня прийомка синхрону."
        ),
        "listen": "http://127.0.0.1:8766/",
        "chapters": chapters,
    }


def html_text(*, show: object | None = None) -> str:
    return _page(build_demo(show))


def dump_html(dest: Path, *, show: object | None = None) -> Path:
    html = html_text(show=show)
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _page(payload: dict[str, object]) -> str:
    blob = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Auto-шоу — домашня оцінка</title>
<style>
:root {{ --orng:#ff6a00; --bg:#0a0a0a; --bg1:#121212; --bg2:#1a1a1a; --text:#f2f2f2; --dim:#a0a0a0; --line:rgba(255,255,255,.14); --ok:#3d9a5f; --warn:#c4922a; }}
html,body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.45 system-ui,sans-serif; }}
header {{ padding:16px 20px; background:var(--bg1); border-bottom:1px solid var(--line); }}
header p {{ margin:8px 0 0; color:var(--dim); max-width:52rem; }}
main {{ display:grid; gap:20px; padding:20px; }}
article {{ background:var(--bg1); border:1px solid var(--line); border-radius:12px; padding:16px; }}
.kind {{ display:inline-block; font-size:12px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; padding:2px 8px; border-radius:999px; }}
.kind-script {{ background:#242424; color:var(--warn); }}
.kind-analysis {{ background:#242424; color:var(--warn); }}
.kind-sync {{ background:#242424; color:var(--ok); }}
canvas {{ width:100%; max-width:720px; height:auto; background:#0a0a0a; border:1px solid var(--line); border-radius:10px; }}
.row {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:12px 0; }}
button {{ background:var(--orng); color:#0a0a0a; border:0; border-radius:8px; padding:8px 14px; font-weight:700; cursor:pointer; }}
button.ghost {{ background:transparent; color:var(--text); border:1px solid var(--line); }}
input[type=range] {{ flex:1; min-width:160px; }}
.timeline {{ display:flex; height:22px; border-radius:6px; overflow:hidden; border:1px solid var(--line); margin:8px 0 4px; }}
.timeline i {{ display:block; height:100%; }}
.hold {{ background:var(--orng); }}
.move {{ background:var(--warn); }}
.strobe {{ background:#f2f2f2; }}
.accent {{ background:#d64545; }}
.legend {{ display:flex; flex-wrap:wrap; gap:12px; color:var(--dim); font-size:12px; }}
.legend b {{ color:var(--text); font-weight:650; }}
.status {{ min-height:1.4em; font-weight:650; }}
.note {{ color:var(--dim); font-size:13px; }}
</style>
</head>
<body>
<header>
  <h1>Auto-шоу — домашня оцінка</h1>
  <p>{payload["note"]}</p>
</header>
<main id="root"></main>
<script>
const SHOW = {blob};
const LABELS = {{ hold:"Тримає мотив", move:"Змінює рух", strobe:"Строб дозволено", accent:"Короткий акцент" }};
const SECTIONS = {{ groove:"Грув", break:"Спад", riser:"Наростання", drop:"Дроп" }};
const root = document.getElementById("root");
SHOW.chapters.forEach((chapter, index) => root.appendChild(mount(chapter, index)));
function mount(chapter, index) {{
  const article = document.createElement("article");
  article.innerHTML = `
    <span class="kind kind-${{chapter.kind}}">${{chapter.kind_title}}</span>
    <h2>${{chapter.title}}</h2>
    <p class="note">${{chapter.note}}</p>
    <canvas width="720" height="420"></canvas>
    <div class="timeline" data-line></div>
    <div class="legend">
      <span><i class="hold" style="display:inline-block;width:10px;height:10px;border-radius:2px"></i> <b>мотив</b></span>
      <span><i class="move" style="display:inline-block;width:10px;height:10px;border-radius:2px"></i> <b>рух</b></span>
      <span><i class="strobe" style="display:inline-block;width:10px;height:10px;border-radius:2px"></i> <b>строб</b></span>
      <span><i class="accent" style="display:inline-block;width:10px;height:10px;border-radius:2px"></i> <b>акцент</b></span>
    </div>
    <audio preload="auto" src="${{chapter.audio}}"></audio>
    <div class="row">
      <button type="button" data-play>Грати</button>
      <button type="button" class="ghost" data-stop>Стоп</button>
      <button type="button" class="ghost" data-jump ${{chapter.highlight_s == null ? "hidden" : ""}}>Показовий фрагмент</button>
      <input type="range" min="0" max="${{Math.max(0, chapter.frames.length-1)}}" value="0"/>
    </div>
    <p class="status" data-status></p>
  `;
  const canvas = article.querySelector("canvas");
  const pen = canvas.getContext("2d");
  const audio = article.querySelector("audio");
  const scrub = article.querySelector("input");
  const status = article.querySelector("[data-status]");
  const line = article.querySelector("[data-line]");
  const duration = chapter.duration || 1;
  chapter.timeline.forEach((span) => {{
    const mark = document.createElement("i");
    mark.className = span.kind;
    mark.style.flex = String(Math.max(0.02, (span.end - span.start) / duration));
    line.appendChild(mark);
  }});
  function frameAt(time) {{
    let chosen = chapter.frames[0];
    for (const frame of chapter.frames) {{
      if (frame.t <= time) chosen = frame; else break;
    }}
    return chosen;
  }}
  function draw(frame) {{
    pen.fillStyle = "#0a0a0a";
    pen.fillRect(0, 0, 720, 420);
    for (const item of frame.fixtures) {{
      const x = 40 + item.x * 640;
      const y = 36 + item.y * 348;
      const flash = frame.motion === "strobe" && item.k === "p" && item.l > 0.05;
      const gain = flash ? 1 : (item.l || 0);
      pen.fillStyle = `rgb(${{Math.round(item.r*gain*255)}},${{Math.round(item.g*gain*255)}},${{Math.round(item.b*gain*255)}})`;
      if (item.k === "b") {{
        (item.s || []).forEach((bit, i) => {{
          pen.globalAlpha = Math.max(0.12, bit * (item.l || 0));
          pen.fillRect(x - 9, y + i * 7, 18, 6);
        }});
        pen.globalAlpha = 1;
      }} else if (item.k === "h") {{
        pen.globalAlpha = item.l > 0.4 ? 1 : 0.2;
        pen.beginPath();
        pen.moveTo(x, y);
        pen.lineTo(x - 12, y + 24);
        pen.lineTo(x + 12, y + 24);
        pen.fill();
        pen.globalAlpha = 1;
      }} else {{
        pen.beginPath();
        pen.arc(x, y, 11 + (item.l || 0) * 5, 0, Math.PI * 2);
        pen.fill();
      }}
    }}
    function bpmText(frame) {{
      if (chapter.kind !== "sync") return "";
      if (typeof frame.bpm === "number") return Math.round(frame.bpm) + " BPM";
      return "— " + (frame.bpm_reason || "Шукаємо ритм");
    }}
    status.textContent = [
      bpmText(frame),
      LABELS[frame.motion] || frame.motion,
      frame.look,
      SECTIONS[frame.section] || frame.section,
      frame.t.toFixed(1) + " с"
    ].filter(Boolean).join(" · ");
  }}
  function showIndex(index) {{
    const frame = chapter.frames[index] || chapter.frames[0];
    scrub.value = String(index);
    draw(frame);
  }}
  article.querySelector("[data-play]").onclick = () => {{
    if (audio.paused) audio.play(); else audio.pause();
  }};
  article.querySelector("[data-stop]").onclick = () => {{
    audio.pause();
    audio.currentTime = 0;
    showIndex(0);
  }};
  const jump = article.querySelector("[data-jump]");
  if (jump && chapter.highlight_s != null) {{
    jump.onclick = () => {{
      audio.currentTime = chapter.highlight_s;
      audio.play();
    }};
  }}
  audio.ontimeupdate = () => {{
    const frame = frameAt(audio.currentTime);
    const index = chapter.frames.indexOf(frame);
    if (index >= 0) showIndex(index);
  }};
  audio.onplay = () => {{ article.querySelector("[data-play]").textContent = "Пауза"; }};
  audio.onpause = () => {{ article.querySelector("[data-play]").textContent = "Грати"; }};
  scrub.oninput = () => {{
    const frame = chapter.frames[Number(scrub.value)] || chapter.frames[0];
    audio.currentTime = frame.t;
    draw(frame);
  }};
  showIndex(0);
  if (index === 0) article.dataset.first = "1";
  return article;
}}
</script>
</body>
</html>
"""
