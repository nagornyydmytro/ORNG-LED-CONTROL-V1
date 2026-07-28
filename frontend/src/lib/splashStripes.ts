/** Vertical barcode / slit-scan splash typography (Klickpin-style). */

export const SPLASH_MIN_MS = 1800;

/** Brand orange — replaces reference red. */
export const SPLASH_ORANGE = "#ff5a00";
export const SPLASH_ORANGE_RGB = { r: 255, g: 90, b: 0 };

export type SplashGlyph = {
  width: number;
  height: number;
  canvas: HTMLCanvasElement;
};

export function edgeFade(t: number, soft = 0.18): number {
  const x = Math.max(0, Math.min(1, t));
  return Math.min(smoothstep(0, soft, x), smoothstep(0, soft, 1 - x));
}

function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

function fontFor(size: number): string {
  return `900 ${size}px "Arial Black",Impact,"Helvetica Neue",Arial,sans-serif`;
}

/**
 * Solid orange ORNG / HOTBOX on black — source for vertical slit sampling.
 * Text sits near 30% of height, centered horizontally.
 */
export function buildSplashGlyph(width: number, height: number): SplashGlyph {
  const w = Math.max(1, Math.floor(width));
  const h = Math.max(1, Math.floor(height));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return { width: w, height: h, canvas };

  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);

  ctx.fillStyle = SPLASH_ORANGE;
  ctx.strokeStyle = SPLASH_ORANGE;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.lineJoin = "round";
  ctx.miterLimit = 2;

  const maxTextW = w * 0.84;
  let fontPx = Math.min(w * 0.3, h * 0.155);
  ctx.font = fontFor(fontPx);
  while (fontPx > 28 && ctx.measureText("HOTBOX").width > maxTextW) {
    fontPx -= 2;
    ctx.font = fontFor(fontPx);
  }

  const gap = fontPx * 0.05;
  const blockH = fontPx * 2 + gap;
  const textTop = h * 0.3 - blockH * 0.32;
  const cx = w * 0.5;
  ctx.lineWidth = Math.max(3, fontPx * 0.07);

  for (const [label, y] of [
    ["ORNG", textTop],
    ["HOTBOX", textTop + fontPx + gap],
  ] as const) {
    ctx.strokeText(label, cx, y);
    ctx.fillText(label, cx, y);
  }

  return { width: w, height: h, canvas };
}

/** @deprecated use buildSplashGlyph — kept for older call sites/tests */
export function buildSplashMask(width: number, height: number): SplashGlyph {
  return buildSplashGlyph(width, height);
}

/**
 * Klickpin slit look:
 * 1) dense thin vertical orange lines on black
 * 2) 1px columns of the glyph canvas stretched into thick bars (the letters)
 * 3) vignette so slits dissolve before the screen edges
 */
export function drawSplashStripes(
  ctx: CanvasRenderingContext2D,
  glyph: SplashGlyph,
  _timeSec = 0,
): void {
  const w = glyph.width;
  const h = glyph.height;
  const src = glyph.canvas;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
  if (w < 8 || h < 8) return;

  const pitch = Math.max(3, Math.round(w / 80));
  const thin = Math.max(1, Math.round(pitch * 0.32));
  const thick = Math.max(thin + 2, Math.round(pitch * 0.95));
  const { r, g, b } = SPLASH_ORANGE_RGB;
  const invW = 1 / Math.max(1, w - 1);
  const invH = 1 / Math.max(1, h - 1);

  // 1) Field of thin slits — always present, like the reference grid.
  for (let x = Math.floor(pitch / 2); x < w; x += pitch) {
    const fadeX = edgeFade(x * invW, 0.13);
    if (fadeX < 0.05) continue;
    const band = Math.max(6, (h / 20) | 0);
    for (let y0 = 0; y0 < h; y0 += band) {
      const fadeY = edgeFade(((y0 + band * 0.5) * invH), 0.2);
      const a = 0.62 * fadeX * fadeY;
      if (a < 0.04) continue;
      ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
      ctx.fillRect(x - (thin >> 1), y0, thin, Math.min(band, h - y0));
    }
  }

  // 2) Letter slits — classic column stretch of the solid glyph.
  ctx.imageSmoothingEnabled = false;
  for (let x = Math.floor(pitch / 2); x < w; x += pitch) {
    const fadeX = edgeFade(x * invW, 0.13);
    if (fadeX < 0.05) continue;
    ctx.globalAlpha = fadeX;
    ctx.drawImage(src, x, 0, 1, h, x - (thick >> 1), 0, thick, h);
  }
  ctx.globalAlpha = 1;

  // Vertical dissolve of the stretched bars near top/bottom edges.
  const topFade = ctx.createLinearGradient(0, 0, 0, h);
  topFade.addColorStop(0, "rgba(0,0,0,1)");
  topFade.addColorStop(0.14, "rgba(0,0,0,0)");
  topFade.addColorStop(0.86, "rgba(0,0,0,0)");
  topFade.addColorStop(1, "rgba(0,0,0,1)");
  ctx.fillStyle = topFade;
  ctx.fillRect(0, 0, w, h);

  // 3) Soft vignette — slits must not reach the physical screen border.
  const vig = ctx.createRadialGradient(
    w * 0.5,
    h * 0.36,
    Math.min(w, h) * 0.18,
    w * 0.5,
    h * 0.4,
    Math.max(w, h) * 0.7,
  );
  vig.addColorStop(0, "rgba(0,0,0,0)");
  vig.addColorStop(0.5, "rgba(0,0,0,0)");
  vig.addColorStop(0.78, "rgba(0,0,0,0.45)");
  vig.addColorStop(1, "#000000");
  ctx.fillStyle = vig;
  ctx.fillRect(0, 0, w, h);
}

export function paintSplash(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  timeSec = 0,
): void {
  drawSplashStripes(ctx, buildSplashGlyph(width, height), timeSec);
}
