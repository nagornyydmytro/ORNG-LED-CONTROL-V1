/**
 * Klickpin slit-scan splash — vertical barcode typography.
 *
 * Technique (matches reference):
 * 1) dense hairline vertical orange slits on black
 * 2) solid glyph rendered once, then each 1px column stretched to a thick bar
 *    → letters form by slit thickening, stay readable
 * 3) soft fade before viewport edges (user: slits must not touch borders)
 */

export const SPLASH_MIN_MS = 1800;

/** Neon orange — reference was neon red. */
export const SPLASH_ORANGE = "#ff3b00";
export const SPLASH_ORANGE_RGB = { r: 255, g: 59, b: 0 };

export type SplashGlyph = {
  width: number;
  height: number;
  canvas: HTMLCanvasElement;
};

export function edgeFade(t: number, soft = 0.12): number {
  const x = Math.max(0, Math.min(1, t));
  return Math.min(smoothstep(0, soft, x), smoothstep(0, soft, 1 - x));
}

function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

function fontFor(size: number): string {
  return `900 ${size}px Impact,"Arial Black","Helvetica Neue",Arial,sans-serif`;
}

/** Solid orange ORNG / HOTBOX — source for 1px column sampling. */
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
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  const maxTextW = w * 0.9;
  let fontPx = Math.min(w * 0.36, h * 0.18);
  ctx.font = fontFor(fontPx);
  while (fontPx > 36 && ctx.measureText("HOTBOX").width > maxTextW) {
    fontPx -= 2;
    ctx.font = fontFor(fontPx);
  }

  const lineGap = fontPx * 0.18;
  const blockH = fontPx * 2 + lineGap;
  // Block sits with its optical center near ~30–35% of height.
  const midY = h * 0.32;
  const cx = w * 0.5;
  ctx.fillText("ORNG", cx, midY - blockH * 0.28);
  ctx.fillText("HOTBOX", cx, midY + blockH * 0.28);

  return { width: w, height: h, canvas };
}

export function buildSplashMask(width: number, height: number): SplashGlyph {
  return buildSplashGlyph(width, height);
}

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

  // Reference ≈ 13px pitch @ 720 → ~55 slits across.
  const pitch = Math.max(4, Math.round(w / 55));
  const thin = Math.max(1, Math.round(pitch * 0.15));
  const thick = Math.max(thin + 2, pitch - 1);
  const { r, g, b } = SPLASH_ORANGE_RGB;
  const invW = 1 / Math.max(1, w - 1);
  const invH = 1 / Math.max(1, h - 1);

  // 1) Hairline field — bright neon, full height, fade only near borders.
  for (let x = Math.floor(pitch * 0.5); x < w; x += pitch) {
    const fadeX = edgeFade(x * invW, 0.11);
    if (fadeX < 0.04) continue;
    // Soft top/bottom dissolve so slits don't hit the physical edge.
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    const top = 0.08;
    const bot = 0.92;
    grad.addColorStop(0, `rgba(${r},${g},${b},0)`);
    grad.addColorStop(top, `rgba(${r},${g},${b},${0.85 * fadeX})`);
    grad.addColorStop(bot, `rgba(${r},${g},${b},${0.85 * fadeX})`);
    grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
    ctx.fillStyle = grad;
    ctx.fillRect(x - (thin >> 1), 0, thin, h);
  }

  // 2) Glyph slits — 1px source column → thick bar (the reference look).
  ctx.imageSmoothingEnabled = false;
  for (let x = Math.floor(pitch * 0.5); x < w; x += pitch) {
    const fadeX = edgeFade(x * invW, 0.11);
    if (fadeX < 0.04) continue;
    ctx.globalAlpha = fadeX;
    ctx.drawImage(src, x, 0, 1, h, x - (thick >> 1), 0, thick, h);
  }
  ctx.globalAlpha = 1;

  // Soften glyph bars at top/bottom edges of the viewport only.
  const edgeWash = ctx.createLinearGradient(0, 0, 0, h);
  edgeWash.addColorStop(0, "rgba(0,0,0,1)");
  edgeWash.addColorStop(0.08, "rgba(0,0,0,0)");
  edgeWash.addColorStop(0.92, "rgba(0,0,0,0)");
  edgeWash.addColorStop(1, "rgba(0,0,0,1)");
  ctx.fillStyle = edgeWash;
  ctx.fillRect(0, 0, w, h);

  // Left/right edge wash — slits must not reach the side borders.
  const side = Math.max(24, w * 0.08);
  const left = ctx.createLinearGradient(0, 0, side, 0);
  left.addColorStop(0, "#000");
  left.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = left;
  ctx.fillRect(0, 0, side, h);
  const right = ctx.createLinearGradient(w - side, 0, w, 0);
  right.addColorStop(0, "rgba(0,0,0,0)");
  right.addColorStop(1, "#000");
  ctx.fillStyle = right;
  ctx.fillRect(w - side, 0, side, h);

  void invH;
}

export function paintSplash(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  timeSec = 0,
): void {
  drawSplashStripes(ctx, buildSplashGlyph(width, height), timeSec);
}
