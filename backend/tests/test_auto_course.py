"""One Auto course, from the plan through a decoded frame. No Art-Net.

The HTML player on the desktop is the motion record of this same course.
"""

from __future__ import annotations

import json
from pathlib import Path

from orng_led.audio.auto_show import StructuralEvent
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

PAIRS = (
    ("par_1", "par_4"),
    ("par_2", "par_3"),
    ("beam_left", "beam_right"),
    ("bar_1", "bar_4"),
    ("bar_2", "bar_3"),
)


def test_the_auto_course_is_one_plan_from_look_to_the_decoded_frame(tmp_path: Path) -> None:
    show = load_show_config()
    service = BpmService(tmp_path, "orng-bar")
    service.enabled = True
    service.scope = "auto"
    service.palette = "orange"
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    pose = {
        fixture.id: BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
        for fixture in fixtures
        if fixture.kind.value == "beam"
    }
    frames: list[dict[str, object]] = []
    axes: dict[str, tuple[float, float]] = {}
    saw_break_without_collapsing = False
    saw_rise = False
    saw_accent_with_flash = False
    saw_recover = False
    saw_bars_without_pars = False

    def snap(beat: float, event: str) -> dict[str, object]:
        wall = beat * 0.5
        follower = service.follower
        follower.bpm = 120.0
        follower.locked = True
        follower._started = True
        follower._beats = beat
        follower._at = wall
        follower._fresh_until = wall + 30.0
        follower._release_until = None
        service._now = wall
        stage = service.look(fixtures, placements, blackout=False)
        frame = render_stage(show, stage, pose)
        view = decode_simulator_view(show, frame)
        painted = stage
        if not axes:
            axes.update({item.id: (item.pan, item.tilt) for item in view.beams})
        _assert_mirrored(view, axes)
        episode = service.planner.current
        assert episode is not None
        return {
            "t": round(wall, 3),
            "beat": round(beat, 3),
            "event": event,
            "section": episode.section,
            "theme": episode.theme,
            "look": episode.look,
            "ident": episode.ident,
            "reason": episode.reason,
            "next": None if service.planner.upcoming is None else service.planner.upcoming.look,
            "next_reason": None
            if service.planner.upcoming is None
            else service.planner.upcoming.reason,
            "fixtures": _fixtures(
                view,
                painted,
                placements,
                flash=service.planner.strobe_at(beat, wall_s=wall),
            ),
        }

    beat = 0.0
    service.section.section = "groove"
    service.section.level = 0.7
    service.section.shown = 0.7
    while beat < 8.0:
        event = "groove" if beat == 0.0 else ""
        frames.append(snap(beat, event))
        if beat >= 7.5:
            service.section.section = "break"
            service.section.level = 0.3
            service.section.shown = 0.3
            probe = snap(beat, "ear-break")
            frames.append(probe)
            episode = service.planner.current
            assert episode is not None and episode.section == "groove"
            ids = {item["id"] for item in probe["fixtures"] if item["on"]}  # type: ignore[index]
            assert any(str(item).startswith("par_") for item in ids)
            assert any(str(item).startswith("bar_") for item in ids)
            saw_break_without_collapsing = True
        beat = round(beat + 0.25, 3)

    service.section.section = "break"
    service.section.level = 0.3
    service.section.shown = 0.3
    while beat < 24.0:
        frames.append(snap(beat, "break" if beat == 8.0 else ""))
        beat = round(beat + 0.25, 3)

    service.section.section = "riser"
    service.section.level = 0.75
    service.section.shown = 0.75
    service.section.riser_age = 0.2
    while beat < 32.0:
        row = snap(beat, "rise" if beat == 24.0 else "")
        frames.append(row)
        episode = service.planner.current
        assert episode is not None
        if episode.section == "riser" or any(
            row.get("kind") == "intensify" for row in service.planner.journal()
        ):
            saw_rise = True
        beat = round(beat + 0.25, 3)

    service.section.section = "drop"
    service.section.level = 0.95
    service.section.shown = 0.95
    drop_beat = beat
    service.section._events.append(StructuralEvent("drop", 1, beat * 0.5))
    while beat < drop_beat + 8.0:
        row = snap(beat, "drop" if beat == drop_beat else "")
        frames.append(row)
        episode = service.planner.current
        assert episode is not None
        if episode.reason == "drop_next" and episode.role == "accent":
            pars = [item for item in row["fixtures"] if str(item["id"]).startswith("par_")]  # type: ignore[index]
            assert pars and all(item["strobe"] == pars[0]["strobe"] for item in pars)
            if episode.effects and episode.effects[0].role == "accent":
                saw_accent_with_flash = True
                assert episode.start_beat <= drop_beat + 4.0
        beat = round(beat + 0.25, 3)

    service.section.section = "groove"
    service.section.level = 0.6
    service.section.shown = 0.6
    service.section.input_lost = True
    lost = snap(beat, "signal-lost")
    frames.append(lost)
    assert service.planner.current is not None
    assert service.planner.current.reason == "signal_loss"
    assert all(item["strobe"] == 0.0 for item in lost["fixtures"])  # type: ignore[index]
    beat = round(beat + 0.5, 3)
    service.section.input_lost = False
    service.section.section = "groove"
    back = snap(beat, "signal-back")
    frames.append(back)
    assert service.planner.current is not None
    assert service.planner.current.reason == "recover"
    assert service.planner.current.start_beat == beat
    assert all(item["strobe"] == 0.0 for item in back["fixtures"])  # type: ignore[index]
    saw_recover = True

    beat = round(beat + 1.0, 3)
    service.palette = "bars"
    themed = snap(beat, "theme-bars")
    frames.append(themed)
    assert service.planner.current is not None
    assert service.planner.current.theme == "bars"
    assert service.planner.current.groups == ("bar",)
    lit = {item["id"] for item in themed["fixtures"] if item["on"]}  # type: ignore[index]
    assert not any(str(item).startswith("par_") for item in lit)
    assert not any(str(item).startswith("beam_") for item in lit)
    assert any(str(item).startswith("bar_") for item in lit)
    saw_bars_without_pars = True

    service.configure(scope="preset")
    assert service.planner.current is None
    assert service.planner.upcoming is None
    assert service.planner.strobe_at(beat + 1.0) == 0.0
    frames.append(
        {
            "t": round((beat + 0.5) * 0.5, 3),
            "beat": round(beat + 0.5, 3),
            "event": "exit-auto",
            "section": "preset",
            "theme": "bars",
            "look": "",
            "ident": 0,
            "reason": "exit",
            "next": None,
            "next_reason": None,
            "fixtures": [],
        }
    )

    assert saw_break_without_collapsing
    assert saw_rise
    assert saw_accent_with_flash
    assert saw_recover
    assert saw_bars_without_pars
    path = tmp_path / "auto-course.html"
    path.write_text(_player(frames), encoding="utf-8")
    assert path.stat().st_size > 1000
    text = path.read_text(encoding="utf-8")
    assert "signal-back" in text and "theme-bars" in text and "exit-auto" in text


def _assert_mirrored(view: object, axes: dict[str, tuple[float, float]]) -> None:
    faces = {item.id: item for item in view.faces}
    beams = {item.id: item for item in view.beams}
    assert faces["face_par_1"].intensity == 0.0
    assert faces["face_par_2"].intensity == 0.0
    for beam in view.beams:
        assert beam.dimmer in {0.0, 1.0}
        assert beam.strobe == 0.0
        assert abs(beam.pan - axes[beam.id][0]) < 1e-6
        assert abs(beam.tilt - axes[beam.id][1]) < 1e-6
    del beams


def _fixtures(
    view: object,
    stage: object,
    placements: object,
    flash: float = 0.0,
) -> list[dict[str, object]]:
    placed = {item.fixture_id: item for item in placements}
    intents = stage.fixtures
    rows: list[dict[str, object]] = []
    for item in (*view.pars, *view.bars, *view.beams):
        place = placed.get(item.id)
        intent = intents.get(item.id)
        hardware = float(getattr(intent, "strobe", 0.0)) if intent is not None else 0.0
        assert hardware == 0.0
        strobe = float(flash) if str(getattr(item, "kind", "")) == "par" else 0.0
        level = float(getattr(item, "intensity", getattr(item, "dimmer", 0.0)))
        segments = list(getattr(item, "segments", ()) or ())
        rows.append(
            {
                "id": item.id,
                "kind": item.kind,
                "x": 0.5 if place is None else float(place.x),
                "y": 0.5 if place is None else float(place.y),
                "r": float(getattr(item, "r", 0.0)),
                "g": float(getattr(item, "g", 0.0)),
                "b": float(getattr(item, "b", 0.0)),
                "on": level > 0.02 or any(bit > 0.02 for bit in segments),
                "level": level,
                "strobe": strobe,
                "segments": [1 if bit > 0.02 else 0 for bit in segments],
            }
        )
    return rows


def _player(frames: list[dict[str, object]]) -> str:
    payload = json.dumps(frames, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Auto course</title>
<style>
body {{ margin: 0; background: #120e0c; color: #f3e6d8; font: 16px/1.4 sans-serif; }}
main {{ display: grid; grid-template-columns: 640px 1fr; gap: 24px; padding: 24px; }}
canvas {{ background: #1b1410; width: 640px; height: 360px; }}
button {{ background: #e36a1e; color: #1b1410; border: 0; padding: 8px 14px; font: inherit; }}
dt {{ color: #e36a1e; }}
dd {{ margin: 0 0 8px; }}
</style>
</head>
<body>
<main>
  <canvas id="stage" width="640" height="360"></canvas>
  <div>
    <p>
      <button id="play" type="button">Пуск</button>
      <button id="stop" type="button">Стоп</button>
    </p>
    <dl>
      <dt>Время</dt><dd id="time"></dd>
      <dt>Событие</dt><dd id="event"></dd>
      <dt>Сейчас</dt><dd id="now"></dd>
      <dt>Дальше</dt><dd id="next"></dd>
    </dl>
  </div>
</main>
<script>
const frames = {payload};
const canvas = document.getElementById("stage");
const pen = canvas.getContext("2d");
const time = document.getElementById("time");
const event = document.getElementById("event");
const now = document.getElementById("now");
const next = document.getElementById("next");
let cursor = 0;
let timer = 0;
function draw(frame) {{
  pen.fillStyle = "#1b1410";
  pen.fillRect(0, 0, canvas.width, canvas.height);
  pen.strokeStyle = "#5a3b24";
  pen.beginPath();
  pen.moveTo(320, 0);
  pen.lineTo(320, 360);
  pen.stroke();
  for (const item of frame.fixtures) {{
    const x = item.x * 640;
    const y = item.y * 360;
    const lit = item.on ? `rgb(${{Math.round(item.r*255)}},${{Math.round(item.g*255)}},${{Math.round(item.b*255)}})` : "#2a211c";
    pen.fillStyle = item.strobe > 0.5 && Math.floor(cursor / 2) % 2 === 0 ? "#fff4e5" : lit;
    if (item.kind === "bar") {{
      item.segments.forEach((bit, index) => {{
        pen.fillStyle = bit && item.on ? lit : "#2a211c";
        pen.fillRect(x - 10, y - 40 + index * 10, 20, 8);
      }});
    }} else {{
      pen.beginPath();
      pen.arc(x, y, item.kind === "beam" ? 8 : 14, 0, Math.PI * 2);
      pen.fill();
    }}
  }}
  time.textContent = frame.t.toFixed(2) + " с, доля " + frame.beat.toFixed(2);
  event.textContent = frame.event || "—";
  now.textContent = frame.reason + " · " + frame.theme + " · " + frame.section + " · " + frame.look + " #" + frame.ident;
  next.textContent = frame.next ? frame.next + " (" + frame.next_reason + ")" : "—";
}}
function tick() {{
  draw(frames[cursor]);
  cursor = (cursor + 1) % frames.length;
}}
document.getElementById("play").onclick = () => {{
  if (timer) return;
  timer = setInterval(tick, 180);
}};
document.getElementById("stop").onclick = () => {{
  clearInterval(timer);
  timer = 0;
}};
draw(frames[0]);
</script>
</body>
</html>
"""
