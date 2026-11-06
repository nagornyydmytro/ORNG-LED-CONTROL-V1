"""Developer gallery: real renderer frames of the ORNG BAR layout.

This is not the operator desk and not a substitute USB/light check.
"""

from __future__ import annotations

import json
from pathlib import Path

from orng_led.audio.auto_show import build_auto_stage
from orng_led.audio.library import BY_ID, COMPOSITIONS, FAMILIES, describe
from orng_led.audio.palettes import COLOR_IDS, known_palette
from orng_led.audio.settings import CHARACTER_FAMILIES, CHARACTER_IDS
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import StageIntent
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view


def _pose(show: object) -> dict[str, BeamMotionState]:
    pose = {}
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        pose[fixture.id] = BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
    return pose


FEATURED_LOOKS = (
    "vertical_wave",
    "hit_bars",
    "cascade_up",
    "pulse_by_y",
    "sine_vertical",
)

CHARACTER_LOOKS = {
    "smooth": "sine_vertical",
    "rhythmic": "vertical_wave",
    "impact": "hit_bars",
}


def catalog() -> dict[str, object]:
    return {
        "families": [{"id": item.id, "title": item.title, "motion": item.motion, "envelope": item.envelope} for item in FAMILIES],
        "compositions": [describe(item) for item in COMPOSITIONS],
        "cycles": [0.5, 1.0, 2.0, 4.0, 8.0],
        "tempos": [60, 90, 120, 140, 174],
        "palettes": list(COLOR_IDS),
        "characters": list(CHARACTER_IDS),
        "character_families": {name: sorted(families) for name, families in CHARACTER_FAMILIES.items()},
        "character_looks": dict(CHARACTER_LOOKS),
        "featured": list(FEATURED_LOOKS),
        "sources": [
            {"id": "script", "title": "Сценарна секція (задана вручну, не з мікрофона)"},
            {"id": "analysis", "title": "Розпізнавання аудіо SectionTracker"},
        ],
        "regression": "vertical_wave",
        "note": "Кадри галереї — renderer ORNG BAR у модельному часі. Секції сценарні, не розпізнавання майданчика.",
    }


def render_look(
    look: str,
    *,
    beats: float,
    cycle_beats: float,
    palette: str,
    bpm: float = 120.0,
    show: object | None = None,
    section: str = "groove",
    source: str = "script",
) -> dict[str, object]:
    """One decoded frame from the real renderer and ORNG BAR patch."""
    del bpm
    loaded = show if show is not None else load_show_config()
    composition = BY_ID.get(look)
    if composition is None:
        raise KeyError(look)
    if palette not in composition.palettes and not known_palette(palette):
        palette = composition.palettes[0]
    picture = section if section in {"groove", "break", "riser", "drop"} else "groove"
    origin = source if source in {"script", "analysis"} else "script"
    fixtures = list(loaded.patch.fixtures)
    stage = build_auto_stage(
        fixtures,
        palette,
        picture,
        0.0,
        0.85,
        look=look,
        beats=float(beats),
        cycle_beats=float(cycle_beats),
        placements=loaded.layout.placements,
        groups=composition.groups,
    )
    pose = _pose(loaded)
    parked = render_stage(loaded, StageIntent(), pose)
    frame = render_stage(loaded, stage, pose)
    view = decode_simulator_view(loaded, frame)
    beams_parked = True
    for fixture in fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        from orng_led.config.models import ChannelRole
        from orng_led.config.validation import global_channel

        profile = loaded.profile_for(fixture)
        for channel in profile.channels:
            if channel.role not in {ChannelRole.PAN_COARSE, ChannelRole.TILT_COARSE}:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            if parked[index] != frame[index]:
                beams_parked = False
    faces = {item.id: item.intensity for item in view.faces}
    return {
        "look": look,
        "beats": float(beats),
        "cycle_beats": float(cycle_beats),
        "palette": palette,
        "section": picture,
        "source": origin,
        "beams_parked": beams_parked,
        "face_dark": all(value <= 0.02 for value in faces.values()),
        "bars": [
            {
                "id": item.id,
                "dimmer": item.dimmer,
                "segments": list(item.segments),
                "r": list(item.segment_r),
                "g": list(item.segment_g),
                "b": list(item.segment_b),
            }
            for item in view.bars
        ],
        "pars": [
            {
                "id": item.id,
                "intensity": item.intensity,
                "r": item.r,
                "g": item.g,
                "b": item.b,
            }
            for item in view.pars
            if not item.face
        ],
        "beams": [
            {"id": item.id, "dimmer": item.dimmer, "r": item.r, "g": item.g, "b": item.b}
            for item in view.beams
        ],
        "layout": [
            {
                "id": place.fixture_id,
                "kind": place.kind.value if hasattr(place.kind, "value") else str(place.kind),
                "x": place.x,
                "y": place.y,
                "height": float(getattr(place, "height", 0.0) or 0.0),
            }
            for place in loaded.layout.placements
        ],
    }


def render_sequence(
    look: str,
    *,
    cycle_beats: float,
    palette: str,
    bpm: float = 120.0,
    frames: int = 24,
    show: object | None = None,
    section: str = "groove",
    source: str = "script",
) -> list[dict[str, object]]:
    count = max(2, min(48, int(frames)))
    loaded = show if show is not None else load_show_config()
    steps = []
    for index in range(count):
        beats = (index / count) * float(cycle_beats)
        steps.append(
            render_look(
                look,
                beats=beats,
                cycle_beats=cycle_beats,
                palette=palette,
                bpm=bpm,
                show=loaded,
                section=section,
                source=source,
            )
        )
    return steps


def dump_portable(dest: Path, *, frames: int = 16) -> Path:
    """Self-contained HTML of real renderer frames. Not the operator desk."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    loaded = load_show_config()
    payload = {
        "catalog": catalog(),
        "sequences": {},
        "characters": {},
        "note": catalog()["note"],
    }
    for look in FEATURED_LOOKS:
        item = BY_ID.get(look)
        if item is None:
            continue
        palette = "orange" if "orange" in item.palettes else item.palettes[0]
        payload["sequences"][look] = render_sequence(
            look,
            cycle_beats=4.0,
            palette=palette,
            frames=frames,
            show=loaded,
            source="script",
        )
    for character, look in CHARACTER_LOOKS.items():
        item = BY_ID.get(look)
        if item is None:
            continue
        palette = "orange" if "orange" in item.palettes else item.palettes[0]
        payload["characters"][character] = {
            "look": look,
            "family": item.family,
            "frames": render_sequence(
                look,
                cycle_beats=4.0,
                palette=palette,
                frames=max(8, frames // 2),
                show=loaded,
                source="script",
            ),
        }
    html = _portable_page(payload)
    dest.write_text(html, encoding="utf-8")
    return dest


def _portable_page(payload: dict[str, object]) -> str:
    blob = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!DOCTYPE html>
<html lang="uk"><head><meta charset="utf-8"/>
<title>Auto gallery — ORNG BAR renderer</title>
<style>
body{{font-family:sans-serif;background:#161412;color:#e8e0d4;margin:0}}
header{{padding:12px 16px;background:#221c18;border-bottom:1px solid #3a322c}}
.note{{color:#e6c27a;font-size:13px;margin-top:6px}}
main{{display:flex;gap:16px;padding:16px}}
aside{{width:300px}}
canvas{{background:#0e0c0b;border:1px solid #3a322c}}
label{{display:block;margin:8px 0 4px;font-size:12px;color:#b5aa9a}}
select,button{{background:#1c1816;color:#e8e0d4;border:1px solid #3a322c;padding:6px}}
.meta{{font-size:12px;color:#b5aa9a;margin-top:8px;white-space:pre-wrap}}
</style></head><body>
<header>
  Auto gallery — кадри справжнього renderer, сцена ORNG BAR. Не пульт.
  <div class="note">Секції тут сценарні (script). Розпізнавання аудіо (analysis) — окрема колонка в програмах режисера, не ці кадри.</div>
</header>
<main>
<aside>
<label>Композиція</label><select id="look"></select>
<label>Характер</label><select id="character"></select>
<button id="play" type="button">Відтворити</button>
<div class="meta" id="meta"></div>
</aside>
<canvas id="stage" width="640" height="480"></canvas>
</main>
<script>
const data = {blob};
const lookSel=document.getElementById('look');
const charSel=document.getElementById('character');
const meta=document.getElementById('meta');
const canvas=document.getElementById('stage');
const ctx=canvas.getContext('2d');
let frames=[], timer=null, index=0;
const featured=data.catalog.featured||Object.keys(data.sequences);
for (const id of featured){{
  const opt=document.createElement('option');
  const item=(data.catalog.compositions||[]).find(c=>c.id===id)||{{}};
  opt.value=id; opt.textContent=id+(item.title?(' — '+item.title):'');
  lookSel.appendChild(opt);
}}
for (const id of (data.catalog.characters||[])){{
  const opt=document.createElement('option');
  opt.value=id; opt.textContent=id+' — '+(data.characters[id]&&data.characters[id].look||'');
  charSel.appendChild(opt);
}}
function current(){{
  if(charSel.value && data.characters[charSel.value] && document.activeElement===charSel)
    return data.characters[charSel.value].frames||[];
  return data.sequences[lookSel.value]||[];
}}
function load(){{
  frames=data.sequences[lookSel.value]||[];
  index=0;
  const first=frames[0]||{{}};
  const item=(data.catalog.compositions||[]).find(c=>c.id===lookSel.value)||{{}};
  meta.textContent=[
    (item.id||first.look)+' — '+(item.title||''),
    'родина: '+(item.family_title||item.family||''),
    'джерело: '+(first.source||'script')+' / секція: '+(first.section||'groove'),
    first.face_dark?'Face темний':'FACE LIT',
    first.beams_parked?'голови на місці':'HEADS MOVED',
    data.note||''
  ].join('\\n');
  draw();
}}
function loadCharacter(){{
  const row=data.characters[charSel.value];
  if(!row) return;
  frames=row.frames||[];
  index=0;
  meta.textContent=['характер '+charSel.value, 'look '+row.look, 'родина '+row.family, 'джерело script (не analysis)'].join('\\n');
  draw();
}}
function draw(){{
  if(!frames.length) return;
  const frame=frames[index%frames.length];
  ctx.fillStyle='#0e0c0b';
  ctx.fillRect(0,0,canvas.width,canvas.height);
  const layout=Object.fromEntries((frame.layout||[]).map(p=>[p.id,p]));
  for(const bar of frame.bars||[]){{
    const p=layout[bar.id]; if(!p) continue;
    const x=p.x*canvas.width, y=p.y*canvas.height, h=Math.max(80,(p.height||0.3)*canvas.height);
    for(let i=0;i<8;i++){{
      const lit=bar.segments[i]>0.05 && bar.dimmer>0.02;
      const t=(i+0.5)/8;
      ctx.fillStyle=lit?`rgb(${{Math.round((bar.r[i]||0)*255)}},${{Math.round((bar.g[i]||0)*255)}},${{Math.round((bar.b[i]||0)*255)}})`:'#1a1614';
      ctx.fillRect(x-6, y+h/2-t*h-6, 12, h/8-1);
    }}
  }}
  for(const par of frame.pars||[]){{
    const p=layout[par.id]; if(!p) continue;
    const i=par.intensity||0;
    ctx.fillStyle=`rgb(${{Math.round(par.r*i*255)}},${{Math.round(par.g*i*255)}},${{Math.round(par.b*i*255)}})`;
    ctx.beginPath(); ctx.arc(p.x*canvas.width,p.y*canvas.height,10,0,6.28); ctx.fill();
  }}
}}
lookSel.onchange=load; charSel.onchange=loadCharacter;
document.getElementById('play').onclick=()=>{{
  if(timer){{clearInterval(timer);timer=null;return;}}
  timer=setInterval(()=>{{index+=1;draw();}}, 80);
}};
load();
</script></body></html>
"""
