"""Auto from the plan to a decoded frame. No Art-Net and no second engine.

The player written beside this test is the motion record. A still image is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import StructuralEvent, build_auto_stage
from orng_led.audio.library import BY_ID
from orng_led.audio.bpm import TempoFollower
from orng_led.audio.planner import EpisodePlanner
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

PAIRS = (
    ("par_1", "par_4"),
    ("par_2", "par_3"),
    ("bar_1", "bar_4"),
    ("bar_2", "bar_3"),
    ("beam_left", "beam_right"),
)


class _Ghost:
    def __init__(self) -> None:
        self.id = "ghost"
        self.kind = FixtureKind.PAR
        self.spatial = type("S", (), {"segment_count": 1, "invert_segments": False})()


class _Center:
    def __init__(self) -> None:
        self.id = "center_par"
        self.kind = FixtureKind.PAR
        self.layout_x = 0.5
        self.layout_y = 0.2
        self.layout_z = 0.8
        self.spatial = type("S", (), {"segment_count": 1, "invert_segments": False})()


def _pin(service: BpmService, beat: float, wall: float) -> None:
    service._now = wall
    follower: TempoFollower = service.follower
    follower.bpm = 120.0
    follower.locked = True
    follower._started = True
    follower._beats = beat
    follower._at = wall
    follower._fresh_until = wall + 30.0
    follower._release_until = None
    follower.confidence = 1.0


def _pose(show) -> dict:
    return {
        fixture.id: BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
        for fixture in show.patch.fixtures
        if fixture.kind is FixtureKind.BEAM
    }


def _kinds(show, stage) -> set[str]:
    by_id = {fixture.id: fixture.kind for fixture in show.patch.fixtures}
    return {by_id[fixture_id].value for fixture_id in stage.fixtures if fixture_id in by_id}


def test_a_drop_in_a_long_bar_phrase_changes_the_look_with_the_flash() -> None:
    planner = EpisodePlanner(seed=11)
    opening = planner.advance(
        0.0, section="break", level=0.3, theme="bars", character="smooth"
    )
    assert opening.duration_beats == 32
    event = StructuralEvent("drop", 1, 1.2)
    held = planner.advance(1.2, section="drop", level=0.9, theme="bars", events=(event,))
    assert held.ident == opening.ident
    assert held.look != "accent"
    assert planner.strobe_at(1.2) == 0.0
    assert planner.upcoming is not None
    assert planner.upcoming.start_beat == pytest.approx(2.0)
    assert planner.upcoming.start_beat < 32
    accent = planner.advance(2.0, section="drop", level=0.9, theme="bars")
    assert accent.ident != opening.ident
    assert accent.reason == "drop_next"
    assert accent.role == "accent"
    assert accent.start_beat == pytest.approx(2.0)
    assert accent.effects and accent.effects[0].start_beat == pytest.approx(2.0)
    assert planner.strobe_at(2.0) == 1.0
    assert planner.strobe_at(2.2) == 0.0
    assert planner.strobe_at(4.2) == 0.0


def test_signal_returns_without_the_old_phrase_or_its_flash() -> None:
    planner = EpisodePlanner(seed=12)
    planner.advance(0.0, section="groove", level=0.7, theme="orange")
    lost = planner.advance(3.0, section="groove", level=0.2, theme="orange", signal_lost=True)
    assert lost.reason == "signal_loss"
    assert lost.duration_beats <= 4
    assert lost.effects == ()
    assert planner.strobe_at(3.2) == 0.0
    back = planner.advance(3.2, section="groove", level=0.6, theme="orange", signal_lost=False)
    assert back.reason != "signal_loss"
    assert back.look != "accent"
    assert all(effect.role != "accent" for effect in back.effects)
    assert planner.strobe_at(3.2) == 0.0
    assert planner.upcoming is None or planner.upcoming.reason != "drop_next"


def test_a_frozen_clock_cannot_hold_an_auto_flash_open() -> None:
    planner = EpisodePlanner(seed=13)
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
    assert planner.strobe_at(0.0, wall_s=13.0) == 0.0


def test_a_large_jump_does_not_replay_the_missed_queue() -> None:
    planner = EpisodePlanner(seed=14)
    planner.advance(0.0, section="groove", level=0.7, theme="orange")
    before = len(planner.played)
    landed = planner.advance(90.0, section="groove", level=0.7, theme="orange")
    assert len(planner.played) == before + 1
    assert landed.start_beat == pytest.approx(90.0)
    assert planner.strobe_at(90.0) == 0.0
    assert landed.reason != "drop_next"


def test_the_plan_not_the_live_section_owns_the_decoded_frame(tmp_path: Path) -> None:
    show = load_show_config()
    service = BpmService(tmp_path, "orng-bar")
    service.palette = "orange"
    service.section.section = "groove"
    service.section.level = 0.7
    service.section.shown = 0.7
    _pin(service, 1.0, 10.0)
    fixtures = list(show.patch.fixtures)
    first = service.look(fixtures, show.layout.placements)
    assert service.planner.current is not None
    assert service.planner.current.section == "groove"
    ident = service.planner.current.ident
    opened = _kinds(show, first)
    assert "par" in opened and "bar" in opened
    service.section.section = "break"
    _pin(service, 1.4, 10.2)
    second = service.look(fixtures, show.layout.placements)
    assert service.planner.current.ident == ident
    assert service.planner.current.section == "groove"
    assert _kinds(show, second) == opened
    service.palette = "bars"
    _pin(service, 1.6, 10.4)
    themed = service.look(fixtures, show.layout.placements)
    assert service.planner.current.theme == "bars"
    assert service.planner.current.groups == ("bar",)
    assert _kinds(show, themed) == {"bar"}
    frame = render_stage(show, themed, _pose(show))
    view = decode_simulator_view(show, frame)
    assert all(item.intensity == 0.0 for item in view.pars)
    assert all(item.intensity == 0.0 for item in view.faces)
    assert any(item.dimmer > 0.0 for item in view.bars)


def test_unplaced_fixtures_stay_dark_and_a_centre_is_explicit(tmp_path: Path) -> None:
    show = load_show_config()
    fixtures = [*show.patch.fixtures, _Ghost(), _Center()]
    stage = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.2,
        0.8,
        placements=show.layout.placements,
        look="pulse",
        beats=1.0,
        cycle_beats=2,
        groups=("par", "bar", "beam"),
    )
    assert "ghost" not in stage.fixtures
    assert "center_par" in stage.fixtures
    assert "face_par_1" not in stage.fixtures
    frame = render_stage(show, stage, _pose(show))
    view = decode_simulator_view(show, frame)
    _assert_auto_invariants(view)
    homes = {
        fixture.id: (float(fixture.spatial.home_pan), float(fixture.spatial.home_tilt))
        for fixture in show.patch.fixtures
        if fixture.kind is FixtureKind.BEAM
    }
    for beam in view.beams:
        assert beam.dimmer in {0.0, 1.0}
        assert beam.strobe == 0.0
        assert (beam.pan, beam.tilt) == pytest.approx(homes[beam.id], abs=0.02)


def test_the_show_can_be_played_back_from_the_real_layout(tmp_path: Path) -> None:
    show = load_show_config()
    service = BpmService(tmp_path, "orng-bar")
    service.palette = "orange"
    runtime = AppRuntime.create(autostart_loop=False, show=show, config_dir=tmp_path)
    runtime.engine.set_blackout(False)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    pose = _pose(show)
    fixtures = list(show.patch.fixtures)
    positions = {
        item.fixture_id: {"x": float(item.x), "y": float(item.y)}
        for item in show.layout.placements
    }
    script = _script()
    rows: list[dict[str, object]] = []
    drop_ident = 1
    for beat, section, level, label, lost, theme in script:
        wall = beat * 0.5
        service.palette = theme
        service.section.section = section
        service.section.input_lost = lost
        service.section.level = level
        service.section.shown = level
        if label == "drop":
            service.section._events.append(StructuralEvent("drop", drop_ident, wall))
            service.section.drop_age = 0.1
        _pin(service, beat, wall)
        stage = service.look(fixtures, show.layout.placements)
        frame = render_stage(show, stage, pose)
        view = decode_simulator_view(show, frame)
        _assert_auto_invariants(view)
        assert all(item.intensity == 0.0 for item in view.faces)
        for beam in view.beams:
            assert beam.dimmer in {0.0, 1.0}
            assert beam.strobe == 0.0
        episode = service.planner.current
        upcoming = service.planner.upcoming
        assert episode is not None
        rows.append(
            {
                "t": round(wall, 3),
                "beat": beat,
                "label": label,
                "episode": episode.ident,
                "look": episode.look,
                "section": episode.section,
                "theme": episode.theme,
                "reason": episode.reason,
                "next": None if upcoming is None else upcoming.look,
                "next_reason": None if upcoming is None else upcoming.reason,
                "strobe": service.planner.strobe_at(beat, wall_s=wall),
                "fixtures": _draw_items(view),
            }
        )
    rise_plan = [row for row in rows if row["label"] == "rise"]
    assert rise_plan
    motif_look = str(rows[0]["look"])
    rise_look = str(rise_plan[0]["look"])
    assert rise_look in BY_ID
    assert rise_look == motif_look or rise_plan[0]["reason"] == "flow"
    break_levels = {
        item["level"]
        for row in rows
        if row["label"] == "break"
        for item in row["fixtures"]
        if item["id"] == "par_1"
    }
    assert max(break_levels) - min(break_levels) > 0.02
    drop_rows = [row for row in rows if row["label"] == "drop"]
    assert drop_rows
    drop_beat = float(drop_rows[0]["beat"])
    assert float(rise_plan[0]["beat"]) < drop_beat
    accent_rows = [
        row
        for row in rows
        if row["reason"] == "drop_next" and float(row["beat"]) <= drop_beat + 2.0
    ]
    assert accent_rows
    assert BY_ID[accent_rows[0]["look"]].pool == "accent"
    assert accent_rows[0]["strobe"] == 1.0
    assert drop_beat <= float(accent_rows[0]["beat"]) <= drop_beat + 2.0
    lost_rows = [row for row in rows if row["label"] == "signal_loss"]
    assert lost_rows and lost_rows[0]["reason"] == "signal_loss"
    returned = next(row for row in rows if row["label"] == "signal_back")
    assert returned["reason"] != "signal_loss"
    assert returned["look"] != "accent"
    bars = [row for row in rows if row["label"] == "theme_bars"]
    assert bars and all(row["theme"] == "bars" for row in bars)
    assert all(item["kind"] != "par" or item["level"] == 0.0 for row in bars for item in row["fixtures"])
    runtime.apply_bpm_sync(scope="preset")
    assert runtime.bpm.planner.current is None
    assert runtime.engine.auto_base is None
    exit_frame = runtime.tick(0.05)
    assert runtime.engine.auto_base is None
    assert exit_frame.frame
    exit_view = decode_simulator_view(show, exit_frame.frame)
    rows.append(
        {
            "t": round(float(rows[-1]["t"]) + 0.5, 3),
            "beat": round(float(rows[-1]["beat"]) + 1.0, 2),
            "label": "exit",
            "episode": "—",
            "look": "preset",
            "section": "—",
            "theme": "—",
            "reason": "left_auto",
            "next": None,
            "next_reason": None,
            "strobe": 0.0,
            "fixtures": _draw_items(exit_view),
        }
    )
    path = tmp_path / "orng-auto-show.html"
    path.write_text(_player(rows, positions), encoding="utf-8")
    assert path.stat().st_size > 2000
    assert "signal_back" in path.read_text(encoding="utf-8")


def _script() -> list[tuple[float, str, float, str, bool, str]]:
    """Sections change on the planner's own phrase lengths, not every sample."""
    rows: list[tuple[float, str, float, str, bool, str]] = []

    def span(until: float, section: str, level: float, label: str, lost: bool, theme: str) -> None:
        beat = 0.0 if not rows else round(float(rows[-1][0]) + 0.4, 2)
        while beat < until - 1e-9:
            rows.append((beat, section, level, label, lost, theme))
            beat = round(beat + 0.4, 2)

    span(8.0, "groove", 0.7, "groove", False, "orange")
    span(24.0, "break", 0.28, "break", False, "orange")
    span(30.0, "riser", 0.75, "rise", False, "orange")
    rows.append((30.0, "drop", 0.95, "drop", False, "orange"))
    span(36.0, "groove", 0.62, "groove_again", False, "orange")
    span(39.0, "groove", 0.05, "signal_loss", True, "orange")
    rows.append((39.0, "groove", 0.65, "signal_back", False, "orange"))
    span(46.0, "groove", 0.7, "theme_bars", False, "bars")
    return rows


def _draw_items(view) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for par in view.pars:
        items.append(
            {
                "id": par.id,
                "kind": "par",
                "level": round(float(par.intensity), 3),
                "color": [round(par.r, 3), round(par.g, 3), round(par.b, 3)],
            }
        )
    for bar in view.bars:
        items.append(
            {
                "id": bar.id,
                "kind": "bar",
                "level": round(float(bar.dimmer), 3),
                "segments": [round(float(bit), 3) for bit in bar.segments],
                "color": [round(bar.r, 3), round(bar.g, 3), round(bar.b, 3)],
            }
        )
    for beam in view.beams:
        items.append(
            {
                "id": beam.id,
                "kind": "beam",
                "level": round(float(beam.dimmer), 3),
                "color": [round(beam.r, 3), round(beam.g, 3), round(beam.b, 3)],
            }
        )
    return items


def _assert_auto_invariants(view) -> None:
    faces = {item.id: item for item in view.faces}
    assert faces["face_par_1"].intensity == 0.0
    assert faces["face_par_2"].intensity == 0.0
    for beam in view.beams:
        assert beam.dimmer in {0.0, 1.0}
        assert beam.strobe == 0.0


def _player(rows: list[dict[str, object]], positions: dict[str, dict[str, float]]) -> str:
    payload = json.dumps({"frames": rows, "positions": positions}, ensure_ascii=False)
    payload = payload.replace("<", "\\u003c")
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>ORNG Auto — воспроизведение</title>
<style>
  body {{ margin: 0; background: #120e0c; color: #f3e6dc; font: 15px/1.4 system-ui, sans-serif; }}
  main {{ display: grid; grid-template-columns: 640px 1fr; gap: 24px; padding: 20px; }}
  canvas {{ background: #1b1412; border-radius: 12px; width: 640px; height: 420px; }}
  button {{ background: #e25b1a; color: #1b100c; border: 0; border-radius: 8px; padding: 8px 14px; font-weight: 700; }}
  input {{ width: 100%; }}
  dd {{ margin: 0 0 8px; }}
  dt {{ color: #c9a28f; }}
</style>
</head>
<body>
<main>
  <canvas id="stage" width="640" height="420"></canvas>
  <section>
    <p>
      <button id="play" type="button">Пуск</button>
      <input id="scrub" type="range" min="0" value="0">
    </p>
    <dl>
      <dt>Время / доля</dt><dd id="when"></dd>
      <dt>Событие сценария</dt><dd id="label"></dd>
      <dt>Эпизод</dt><dd id="episode"></dd>
      <dt>Следующий</dt><dd id="next"></dd>
    </dl>
    <p>Ноутбук, layout ORNG BAR, без Art-Net. Головы только вкл/выкл. Face не участвует.</p>
  </section>
</main>
<script>
const SHOW = {payload};
const frames = SHOW.frames;
const pos = SHOW.positions;
const scrub = document.getElementById("scrub");
scrub.max = String(frames.length - 1);
const canvas = document.getElementById("stage");
const pen = canvas.getContext("2d");
function color(item) {{
  const level = item.level || 0;
  const c = item.color || [0, 0, 0];
  const gain = level;
  return `rgb(${{Math.round(c[0] * gain * 255)}},${{Math.round(c[1] * gain * 255)}},${{Math.round(c[2] * gain * 255)}})`;
}}
function draw(index) {{
  const frame = frames[index];
  pen.fillStyle = "#1b1412";
  pen.fillRect(0, 0, 640, 420);
  pen.strokeStyle = "#3a2a24";
  pen.beginPath();
  pen.moveTo(320, 20);
  pen.lineTo(320, 400);
  pen.stroke();
  for (const item of frame.fixtures) {{
    const at = pos[item.id];
    if (!at) continue;
    const x = 40 + at.x * 560;
    const y = 40 + at.y * 300;
    const flash = frame.strobe > 0.2 && item.kind === "par" && item.level > 0;
    pen.fillStyle = flash ? "#fff4e8" : color(item);
    if (item.kind === "bar") {{
      const bits = item.segments || [];
      bits.forEach((bit, i) => {{
        pen.globalAlpha = Math.max(0.15, bit * (item.level || 0));
        pen.fillRect(x - 10, y + i * 7, 20, 6);
      }});
      pen.globalAlpha = 1;
    }} else if (item.kind === "beam") {{
      pen.globalAlpha = item.level > 0.5 ? 1 : 0.2;
      pen.beginPath();
      pen.moveTo(x, y);
      pen.lineTo(x - 14, y + 28);
      pen.lineTo(x + 14, y + 28);
      pen.fill();
      pen.globalAlpha = 1;
    }} else {{
      pen.beginPath();
      pen.arc(x, y, 12 + (item.level || 0) * 6, 0, Math.PI * 2);
      pen.fill();
    }}
  }}
  document.getElementById("when").textContent = frame.t + " с  ·  доля " + frame.beat;
  document.getElementById("label").textContent = frame.label;
  document.getElementById("episode").textContent =
    "#" + frame.episode + "  " + frame.look + "  " + frame.section + "  " + frame.theme + "  (" + frame.reason + ")";
  document.getElementById("next").textContent =
    frame.next ? frame.next + "  (" + frame.next_reason + ")" : "—";
}}
let timer = 0;
document.getElementById("play").onclick = () => {{
  if (timer) {{ clearInterval(timer); timer = 0; return; }}
  timer = setInterval(() => {{
    const next = Math.min(frames.length - 1, Number(scrub.value) + 1);
    scrub.value = String(next);
    draw(next);
    if (next === frames.length - 1) {{ clearInterval(timer); timer = 0; }}
  }}, 120);
}};
scrub.oninput = () => draw(Number(scrub.value));
draw(0);
</script>
</body>
</html>
"""
