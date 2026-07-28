/**
 * Klickpin slit-scan splash — ORNG / HOTBOX.
 *
 * Full-viewport orange hairlines. Brand type is a modular block grid sampled
 * through those slits (same color), with a liquid per-column warp like the
 * reference kinetic typography.
 */

export const SPLASH_MIN_MS = 2800;

export const SPLASH_ORANGE = "#ff4d00";
export const SPLASH_ORANGE_RGB = { r: 255, g: 77, b: 0 };

export function edgeFade(t: number, soft = 0.12): number {
  const x = Math.max(0, Math.min(1, t));
  return Math.min(smoothstep(0, soft, x), smoothstep(0, soft, 1 - x));
}

function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

/**
 * Modular block glyphs (rows top→bottom). '#' = fill. Matches the reference's
 * heavy geometric barcode letters far better than Impact/Arial Black.
 */
const GLYPHS: Record<string, string[]> = {
  O: [
    ".######.",
    "##....##",
    "##....##",
    "##....##",
    "##....##",
    "##....##",
    ".######.",
  ],
  R: [
    "#######.",
    "##....##",
    "##....##",
    "#######.",
    "##..##..",
    "##...##.",
    "##....##",
  ],
  N: [
    "##....##",
    "###...##",
    "####..##",
    "##.##.##",
    "##..####",
    "##...###",
    "##....##",
  ],
  G: [
    ".######.",
    "##....##",
    "##......",
    "##..####",
    "##....##",
    "##....##",
    ".######.",
  ],
  H: [
    "##....##",
    "##....##",
    "##....##",
    "########",
    "##....##",
    "##....##",
    "##....##",
  ],
  T: [
    "########",
    "########",
    "...##...",
    "...##...",
    "...##...",
    "...##...",
    "...##...",
  ],
  B: [
    "#######.",
    "##....##",
    "##....##",
    "#######.",
    "##....##",
    "##....##",
    "#######.",
  ],
  X: [
    "##....##",
    ".##..##.",
    "..####..",
    "...##...",
    "..####..",
    ".##..##.",
    "##....##",
  ],
  " ": [
    "........",
    "........",
    "........",
    "........",
    "........",
    "........",
    "........",
  ],
};

export type SplashPainter = {
  width: number;
  height: number;
  paint: (ctx: CanvasRenderingContext2D, timeSec: number) => void;
  resize: (width: number, height: number) => void;
};

function stampWord(
  coverage: Uint8Array,
  w: number,
  h: number,
  word: string,
  originX: number,
  originY: number,
  cellW: number,
  cellH: number,
  tracking: number,
): void {
  let cursor = originX;
  for (const ch of word) {
    const g = GLYPHS[ch] ?? GLYPHS[" "]!;
    const rows = g.length;
    const cols = g[0]!.length;
    for (let row = 0; row < rows; row += 1) {
      const line = g[row]!;
      for (let col = 0; col < cols; col += 1) {
        if (line[col] !== "#") continue;
        const x0 = Math.floor(cursor + col * cellW);
        const y0 = Math.floor(originY + row * cellH);
        const x1 = Math.ceil(cursor + (col + 1) * cellW);
        const y1 = Math.ceil(originY + (row + 1) * cellH);
        for (let y = Math.max(0, y0); y < Math.min(h, y1); y += 1) {
          const rowOff = y * w;
          for (let x = Math.max(0, x0); x < Math.min(w, x1); x += 1) {
            coverage[rowOff + x] = 255;
          }
        }
      }
    }
    cursor += cols * cellW + tracking;
  }
}

function buildCoverage(
  w: number,
  h: number,
  timeSec: number,
): Uint8Array {
  const coverage = new Uint8Array(w * h);

  // Reference COM drift ~±9% over ~2s; keep brand centered while morphing.
  const drift = Math.sin(timeSec * Math.PI) * w * 0.08;
  const scale = 1 + Math.sin(timeSec * 2.2) * 0.08;

  // Block height ≈ 30% of viewport (two lines).
  const blockH = h * 0.3 * scale;
  const cellH = blockH / 15; // 7 + gap + 7
  const cellW = cellH * 0.95;
  const tracking = cellW * 0.55;
  const lineGap = cellH * 1.1;

  const wordWidth = (word: string) => {
    let n = 0;
    for (const ch of word) n += (GLYPHS[ch] ?? GLYPHS[" "]!)[0]!.length;
    return n * cellW + (word.length - 1) * tracking;
  };

  const top = "ORNG";
  const bot = "HOTBOX";
  const topW = wordWidth(top);
  const botW = wordWidth(bot);
  const originY = h * 0.36 - blockH * 0.5 + Math.sin(timeSec * 1.7) * h * 0.01;

  stampWord(
    coverage,
    w,
    h,
    top,
    w * 0.5 - topW * 0.5 + drift,
    originY,
    cellW,
    cellH,
    tracking,
  );
  stampWord(
    coverage,
    w,
    h,
    bot,
    w * 0.5 - botW * 0.5 + drift * 0.85,
    originY + 7 * cellH + lineGap,
    cellW,
    cellH,
    tracking,
  );

  return coverage;
}

export function createSplashPainter(
  width: number,
  height: number,
): SplashPainter {
  let w = Math.max(1, Math.floor(width));
  let h = Math.max(1, Math.floor(height));

  const resize = (nw: number, nh: number) => {
    w = Math.max(1, Math.floor(nw));
    h = Math.max(1, Math.floor(nh));
  };

  const roundCapsule = (
    ctx: CanvasRenderingContext2D,
    x: number,
    y: number,
    ww: number,
    hh: number,
  ) => {
    const rad = Math.min(ww / 2, hh / 2);
    if (hh <= ww) {
      ctx.beginPath();
      ctx.arc(x + ww / 2, y + hh / 2, rad, 0, Math.PI * 2);
      ctx.fill();
      return;
    }
    ctx.beginPath();
    ctx.moveTo(x, y + rad);
    ctx.arc(x + ww / 2, y + rad, rad, Math.PI, 0);
    ctx.lineTo(x + ww, y + hh - rad);
    ctx.arc(x + ww / 2, y + hh - rad, rad, 0, Math.PI);
    ctx.closePath();
    ctx.fill();
  };

  const paint = (ctx: CanvasRenderingContext2D, timeSec: number) => {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#000000";
    ctx.fillRect(0, 0, w, h);
    if (w < 8 || h < 8) return;

    const base = buildCoverage(w, h, timeSec);

    // Reference: ~13.5px pitch @ 720, thin ~3px, thick ~10px.
    const pitch = Math.max(5, Math.min(14, Math.round(w / 70)));
    const thin = Math.max(2, Math.round(pitch * 0.22));
    const thick = Math.max(thin + 3, Math.round(pitch * 0.78));
    const { r, g, b } = SPLASH_ORANGE_RGB;
    const invW = 1 / Math.max(1, w - 1);
    const invH = 1 / Math.max(1, h - 1);
    const edgeSoftX = 0.08;
    const edgeSoftY = 0.1;
    const band = Math.max(3, (h / 50) | 0);
    // Liquid warp amplitude — reference letter edges shred vertically.
    const warpAmp = h * 0.045;

    ctx.fillStyle = `rgb(${r},${g},${b})`;

    for (let i = 0; i < Math.ceil(w / pitch) + 1; i += 1) {
      const x = Math.floor(i * pitch + pitch * 0.5);
      if (x < 0 || x >= w) continue;
      const fadeX = edgeFade(x * invW, edgeSoftX);
      if (fadeX < 0.04) continue;

      for (let y0 = 0; y0 < h; y0 += band) {
        const fadeY = edgeFade((y0 + band * 0.5) * invH, edgeSoftY);
        const a = fadeX * fadeY;
        if (a < 0.05) continue;
        ctx.globalAlpha = a;
        ctx.fillRect(x - (thin >> 1), y0, thin, Math.min(band + 1, h - y0));
      }

      // Per-slit time warp → kinetic typography behind fixed grating.
      const phase = timeSec * 3.2 + i * 0.41;
      const yWarp = Math.sin(phase) * warpAmp;
      const xSample = Math.max(
        0,
        Math.min(w - 1, x + Math.round(Math.sin(phase * 0.7) * pitch * 1.2)),
      );

      let run = -1;
      const flush = (yEnd: number) => {
        if (run < 0) return;
        const y0 = run;
        const hh = yEnd - y0;
        run = -1;
        if (hh < 2) return;
        const drawY = Math.round(y0 + yWarp * 0.35);
        if (drawY >= h || drawY + hh <= 0) return;
        const cy = Math.max(0, drawY);
        const ch = Math.min(h, drawY + hh) - cy;
        if (ch < 2) return;
        const fadeY = edgeFade((cy + ch * 0.5) * invH, edgeSoftY);
        const a = fadeX * fadeY;
        if (a < 0.05) return;
        ctx.globalAlpha = a;
        roundCapsule(ctx, x - (thick >> 1), cy, thick, ch);
      };

      for (let y = 0; y < h; y += 1) {
        const sy = Math.max(0, Math.min(h - 1, Math.round(y - yWarp)));
        const on = base[sy * w + xSample]! > 0;
        if (on) {
          if (run < 0) run = y;
        } else {
          flush(y);
        }
      }
      flush(h);
    }

    ctx.globalAlpha = 1;
  };

  return {
    get width() {
      return w;
    },
    get height() {
      return h;
    },
    paint,
    resize,
  };
}
