"""Developer Auto gallery. Real ORNG BAR renderer frames, not the operator desk.

Run from the repo with the backend on PYTHONPATH:

    python scripts/auto_gallery.py

Isolated. No Art-Net, no venue USB, no deploy.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from orng_led.audio.gallery import catalog, dump_portable, render_sequence  # noqa: E402
from orng_led.config.io import load_show_config  # noqa: E402

_SHOW = load_show_config()
_PAGE = """<!DOCTYPE html>
<html lang="uk"><head><meta charset="utf-8"/>
<title>Auto gallery — ORNG BAR renderer</title>
<style>
body{font-family:sans-serif;background:#161412;color:#e8e0d4;margin:0}
header{padding:12px 16px;background:#221c18;border-bottom:1px solid #3a322c}
main{display:flex;gap:16px;padding:16px}
aside{width:280px}
canvas{background:#0e0c0b;border:1px solid #3a322c}
label{display:block;margin:8px 0 4px;font-size:12px;color:#b5aa9a}
select,input{width:100%;background:#1c1816;color:#e8e0d4;border:1px solid #3a322c;padding:4px}
.meta{font-size:12px;color:#b5aa9a;margin-top:8px;white-space:pre-wrap}
</style></head><body>
<header>Auto gallery — кадри справжнього renderer, сцена ORNG BAR. Не пульт.<div class="meta">Секції сценарні (script). Це не розпізнавання аудіо (analysis).</div></header>
<main>
<aside>
<label>Композиція</label><select id="look"></select>
<label>Період (долі)</label><select id="cycle"></select>
<label>Темп (BPM)</label><select id="tempo"></select>
<label>Палітра</label><select id="palette"></select>
<label>Характер (фільтр родин)</label><select id="character"></select>
<label>Кадрів</label><input id="frames" type="number" min="8" max="48" value="24"/>
<button id="play" type="button">Відтворити</button>
<div class="meta" id="meta"></div>
</aside>
<canvas id="stage" width="640" height="480"></canvas>
</main>
<script>
const lookSel=document.getElementById('look');
const cycleSel=document.getElementById('cycle');
const tempoSel=document.getElementById('tempo');
const palSel=document.getElementById('palette');
const charSel=document.getElementById('character');
const meta=document.getElementById('meta');
const canvas=document.getElementById('stage');
const ctx=canvas.getContext('2d');
let frames=[], timer=null, index=0, catalog=null, bpm=120;
function fill(sel, items, val){
  sel.innerHTML='';
  for (const item of items){
    const opt=document.createElement('option');
    opt.value=item.id||item;
    opt.textContent=item.title? (item.id+' — '+item.title): String(item);
    sel.appendChild(opt);
  }
  if(val) sel.value=val;
}
fetch('/catalog').then(r=>r.json()).then(data=>{
  catalog=data;
  fill(lookSel, data.compositions, data.regression);
  fill(cycleSel, data.cycles.map(v=>({id:String(v), title:String(v)})));
  fill(tempoSel, (data.tempos||[60,90,120,140,174]).map(v=>({id:String(v), title:String(v)})));
  fill(palSel, data.palettes.map(v=>({id:v, title:v})));
  fill(charSel, [{id:'', title:'усі'}].concat((data.characters||[]).map(v=>({id:v, title:v}))));
  cycleSel.value='2';
  tempoSel.value='120';
  load();
});
function load(){
  bpm=Number(tempoSel.value||120);
  const q=new URLSearchParams({
    look:lookSel.value, cycle:cycleSel.value, palette:palSel.value,
    frames:document.getElementById('frames').value, bpm:String(bpm)
  });
  fetch('/sequence?'+q.toString()).then(r=>r.json()).then(data=>{
    frames=data.frames||[];
    index=0;
    const first=frames[0]||{};
    const item=(catalog&&catalog.compositions||[]).find(c=>c.id===lookSel.value)||{};
    const period=Number(cycleSel.value||2);
    const seconds=(period*60)/(bpm||120);
    meta.textContent=[
      (item.id||first.look)+' — '+(item.title||''),
      'родина: '+(item.family_title||item.family||''),
      'рух: '+(item.motion||''),
      'джерело: script (сценарно, не analysis) / секція groove',
      'огинаюча: '+(item.envelope||'')+' / '+period+' дол. / '+bpm+' BPM / '+seconds.toFixed(2)+' с',
      'групи: '+(item.groups||[]).join(', '),
      'перехід: '+(item.transition||'')+' / обмеження: '+(item.constraints||''),
      first.face_dark?'Face темний':'FACE LIT',
      first.beams_parked?'голови на місці':'HEADS MOVED',
      item.notes||''
    ].join('\\n');
    draw();
  });
}
function draw(){
  if(!frames.length) return;
  const frame=frames[index%frames.length];
  ctx.fillStyle='#0e0c0b';
  ctx.fillRect(0,0,canvas.width,canvas.height);
  const layout=Object.fromEntries((frame.layout||[]).map(p=>[p.id,p]));
  for(const bar of frame.bars||[]){
    const p=layout[bar.id]; if(!p) continue;
    const x=p.x*canvas.width, y=p.y*canvas.height, h=Math.max(80,(p.height||0.3)*canvas.height);
    for(let i=0;i<8;i++){
      const lit=bar.segments[i]>0.05 && bar.dimmer>0.02;
      const t=(i+0.5)/8;
      ctx.fillStyle=lit?`rgb(${Math.round((bar.r[i]||0)*255)},${Math.round((bar.g[i]||0)*255)},${Math.round((bar.b[i]||0)*255)})`:'#1a1614';
      ctx.fillRect(x-6, y+h/2-t*h-6, 12, h/8-1);
    }
  }
  for(const par of frame.pars||[]){
    const p=layout[par.id]; if(!p) continue;
    const i=par.intensity||0;
    ctx.fillStyle=`rgb(${Math.round(par.r*i*255)},${Math.round(par.g*i*255)},${Math.round(par.b*i*255)})`;
    ctx.beginPath(); ctx.arc(p.x*canvas.width,p.y*canvas.height,10,0,6.28); ctx.fill();
  }
  for(const beam of frame.beams||[]){
    const p=layout[beam.id]; if(!p) continue;
    const i=beam.dimmer||0;
    ctx.fillStyle=`rgb(${Math.round(beam.r*i*255)},${Math.round(beam.g*i*255)},${Math.round(beam.b*i*255)})`;
    ctx.fillRect(p.x*canvas.width-6,p.y*canvas.height-6,12,12);
  }
}
document.getElementById('play').onclick=()=>{
  if(timer){clearInterval(timer);timer=null;return;}
  const period=Number(cycleSel.value||2);
  const count=Math.max(2, frames.length||24);
  const ms=Math.max(40, (period/count)*(60/(bpm||120))*1000);
  timer=setInterval(()=>{index+=1;draw();}, ms);
};
charSel.onchange=()=>{
  const families=(catalog&&catalog.character_families&&catalog.character_families[charSel.value])||null;
  const preferred=(catalog&&catalog.character_looks&&catalog.character_looks[charSel.value])||null;
  const items=(catalog.compositions||[]).filter(c=>!families||families.includes(c.family));
  fill(lookSel, items, preferred||catalog.regression);
  load();
};
lookSel.onchange=load; cycleSel.onchange=load; palSel.onchange=load; tempoSel.onchange=load;
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("gallery " + (fmt % args) + "\n")

    def _send(self, payload: object, *, html: bool = False) -> None:
        data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send(_PAGE, html=True)
            return
        if parsed.path == "/catalog":
            self._send(catalog())
            return
        if parsed.path == "/sequence":
            query = parse_qs(parsed.query)
            look = (query.get("look") or ["vertical_wave"])[0]
            cycle = float((query.get("cycle") or ["2"])[0])
            palette = (query.get("palette") or ["orange"])[0]
            frames = int((query.get("frames") or ["24"])[0])
            bpm = float((query.get("bpm") or ["120"])[0])
            sequence = render_sequence(
                look,
                cycle_beats=cycle,
                palette=palette,
                bpm=bpm,
                frames=frames,
                show=_SHOW,
            )
            self._send({"frames": sequence, "bpm": bpm, "cycle_beats": cycle})
            return
        self.send_error(404)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", type=Path, default=None, help="Write a portable HTML and exit")
    args = parser.parse_args()
    if args.dump is not None:
        path = dump_portable(args.dump)
        print(path)
        return
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Auto gallery http://127.0.0.1:8765  (Ctrl+C to stop)")
    server.serve_forever()


if __name__ == "__main__":
    main()
