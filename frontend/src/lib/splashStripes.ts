/** Vertical barcode / slit-scan splash typography (Klickpin-style). */

export const SPLASH_MIN_MS = 1500;

/** Brand orange — replaces reference red. */
export const SPLASH_ORANGE = { r: 255, g: 106, b: 0 };

export type SplashMask = {
  width: number;
  height: number;
  pixels: Uint8ClampedArray;
};

export function edgeFade(t: number, soft = 0.14): number {
  // 0 at edges → 1 in the safe inner band (stripes never reach screen borders).
  const x = Math.max(0, Math.min(1, t));
  const a = smoothstep(0, soft, x);
  const b = smoothstep(0, soft, 1 - x);
  return Math.min(a, b);
}

function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

/** Build the ORNG / HOTBOX glyph mask once per size. */
export function buildSplashMask(width: number, height: number): SplashMask {
  const w = Math.max(1, Math.floor(width));
  const h = Math.max(1, Math.floor(height));
  const mask = document.createElement("canvas");
  mask.width = w;
  mask.height = h;
  const m = mask.getContext("2d", { willReadFrequently: true });
  if (!m) {
    return { width: w, height: h, pixels: new Uint8ClampedArray(w * h * 4) };
  }

  m.clearRect(0, 0, w, h);
  m.fillStyle = "#ffffff";
  m.textAlign = "center";
  m.textBaseline = "top";

  const maxTextW = w * 0.78;
  let fontPx = Math.min(w * 0.22, h * 0.12);
  const fontFor = (size: number) =>
    `900 ${size}px "Arial Black", "Helvetica Neue", Impact, system-ui, sans-serif`;
  m.font = fontFor(fontPx);
  while (fontPx > 18 && m.measureText("HOTBOX").width > maxTextW) {
    fontPx -= 2;
    m.font = fontFor(fontPx);
  }

  const lineGap = fontPx * 0.08;
  const blockH = fontPx * 2 + lineGap;
  // Text block around ~30% of screen height, centered horizontally.
  const textTop = h * 0.3 - blockH * 0.35;
  const cx = w * 0.5;
  m.fillText("ORNG", cx, textTop);
  m.fillText("HOTBOX", cx, textTop + fontPx + lineGap);

  return { width: w, height: h, pixels: m.getImageData(0, 0, w, h).data };
}

/**
 * Draw full-screen black + orange vertical-stripe glyphs.
 * Stripes fade/blur out before reaching the screen edges.
 */
export function drawSplashStripes(
  ctx: CanvasRenderingContext2D,
  mask: SplashMask,
  timeSec = 0,
): void {
  const { width: w, height: h, pixels } = mask;

  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
  if (w < 8 || h < 8) return;

  const out = ctx.createImageData(w, h);
  const dst = out.data;

  // ~14px period on a 720-wide reference → ~51 stripes.
  const period = Math.max(4, Math.round(w / 51));
  const thin = Math.max(1, Math.round(period * 0.14));
  const thick = Math.max(thin + 1, Math.round(period * 0.62));
  const { r, g, b } = SPLASH_ORANGE;
  const invW = 1 / Math.max(1, w - 1);
  const invH = 1 / Math.max(1, h - 1);

  for (let i = 0; i < Math.ceil(w / period); i += 1) {
    const x = Math.floor(i * period + period * 0.5);
    if (x < 0 || x >= w) continue;

    const fadeX = edgeFade(x * invW, 0.12);
    if (fadeX < 0.02) continue;

    // Liquid stagger between adjacent stripes.
    const shear = Math.sin(i * 0.85 + timeSec * 2.2) * (h * 0.004);

    for (let y = 0; y < h; y += 1) {
      const fadeY = edgeFade(y * invH, 0.16);
      const edge = fadeX * fadeY;
      if (edge < 0.02) continue;

      const sy = Math.max(0, Math.min(h - 1, (y + shear) | 0));
      const inGlyph = pixels[(sy * w + x) * 4]! > 128;
      const lineW = inGlyph ? thick : thin;
      // Soften toward edges so stripes dissolve before the border.
      const soft = edge * edge;
      const alpha = (inGlyph ? 1 : 0.42) * soft;
      if (alpha < 0.03) continue;

      const pr = (r * alpha) | 0;
      const pg = (g * alpha) | 0;
      const pb = (b * alpha) | 0;
      const half = (lineW / 2) | 0;
      let x0 = x - half;
      let x1 = x0 + lineW;
      if (x0 < 0) x0 = 0;
      if (x1 > w) x1 = w;
      const row = y * w;
      for (let px = x0; px < x1; px += 1) {
        const di = (row + px) * 4;
        // Keep the brighter sample (thick glyph over thin field).
        if (pr > dst[di]!) {
          dst[di] = pr;
          dst[di + 1] = pg;
          dst[di + 2] = pb;
          dst[di + 3] = 255;
        }
      }
    }
  }

  ctx.putImageData(out, 0, 0);
}
