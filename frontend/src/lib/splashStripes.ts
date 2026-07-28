/**
 * Klickpin slit-scan splash — vertical barcode typography.
 *
 * Reference: dense full-height hairline slits; glyphs appear only by thickening
 * those slits. Orange replaces reference red. Soft fade before screen edges.
 */

export const SPLASH_MIN_MS = 1800;

/** Neon orange (reference was neon red). */
export const SPLASH_ORANGE = "#ff4d00";
export const SPLASH_ORANGE_RGB = { r: 255, g: 77, b: 0 };

export type SplashGlyph = {
  width: number;
  height: number;
  /** 0..1 coverage per pixel, length = width * height (row-major). */
  coverage: Float32Array;
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
  // Heavy condensed block face — closest system match to the reference.
  return `900 ${size}px Impact,"Arial Black","Helvetica Neue Condensed",Arial,sans-serif`;
}

/**
 * Build glyph coverage with a short vertical smear so letter tops/bottoms
 * bleed into the thin slits (reference “liquid” taper).
 */
export function buildSplashGlyph(width: number, height: number): SplashGlyph {
  const w = Math.max(1, Math.floor(width));
  const h = Math.max(1, Math.floor(height));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const coverage = new Float32Array(w * h);
  if (!ctx) return { width: w, height: h, coverage };

  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  const maxTextW = w * 0.88;
  let fontPx = Math.min(w * 0.34, h * 0.17);
  ctx.font = fontFor(fontPx);
  while (fontPx > 32 && ctx.measureText("HOTBOX").width > maxTextW) {
    fontPx -= 2;
    ctx.font = fontFor(fontPx);
  }

  // Two lines, block centered near 30% height.
  const lineGap = fontPx * 0.12;
  const blockH = fontPx * 2 + lineGap;
  const blockMidY = h * 0.3 + blockH * 0.15;
  const cx = w * 0.5;
  const y1 = blockMidY - blockH * 0.25;
  const y2 = blockMidY + blockH * 0.25;

  // Hard fill first (crisp barcode core).
  ctx.fillText("ORNG", cx, y1);
  ctx.fillText("HOTBOX", cx, y2);

  // Vertical smear passes — reference letter edges taper into thin slits.
  const smear = Math.max(4, Math.round(fontPx * 0.12));
  ctx.globalCompositeOperation = "lighter";
  for (let dy = 1; dy <= smear; dy += 1) {
    const a = 1 - dy / (smear + 1);
    ctx.globalAlpha = 0.35 * a;
    ctx.fillText("ORNG", cx, y1 - dy);
    ctx.fillText("ORNG", cx, y1 + dy);
    ctx.fillText("HOTBOX", cx, y2 - dy);
    ctx.fillText("HOTBOX", cx, y2 + dy);
  }
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";

  const data = ctx.getImageData(0, 0, w, h).data;
  for (let i = 0, p = 0; i < coverage.length; i += 1, p += 4) {
    coverage[i] = data[p]! / 255;
  }
  return { width: w, height: h, coverage };
}

/** @deprecated alias */
export function buildSplashMask(width: number, height: number): SplashGlyph {
  return buildSplashGlyph(width, height);
}

/**
 * Paint the slit field. Thin slits everywhere; thick where coverage is high.
 * Fade only near the viewport edges so slits never touch the border.
 */
export function drawSplashStripes(
  ctx: CanvasRenderingContext2D,
  glyph: SplashGlyph,
  _timeSec = 0,
): void {
  const { width: w, height: h, coverage } = glyph;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
  if (w < 8 || h < 8) return;

  // Reference ≈ 13px pitch on 720px → ~55 slits.
  const pitch = Math.max(4, Math.round(w / 55));
  const thin = Math.max(1, Math.round(pitch * 0.12));
  const thick = Math.max(thin + 2, pitch - 1);
  const { r, g, b } = SPLASH_ORANGE_RGB;
  const invW = 1 / Math.max(1, w - 1);
  const invH = 1 / Math.max(1, h - 1);

  // Sample each slit column and draw continuous vertical runs.
  for (let col = 0; ; col += 1) {
    const x = Math.floor(col * pitch + pitch * 0.5);
    if (x >= w) break;

    const fadeX = edgeFade(x * invW, 0.1);
    if (fadeX < 0.03) continue;

    // Walk the column; coverage drives line width (thin field → thick glyph).
    let runY = 0;
    let runCov = coverage[x] ?? 0;

    const flush = (yEnd: number) => {
      if (yEnd <= runY) return;
      const midY = (runY + yEnd) * 0.5 * invH;
      const fadeY = edgeFade(midY, 0.1);
      const a = fadeX * fadeY;
      if (a < 0.03) {
        runY = yEnd;
        return;
      }
      // Reference lines are bright neon, not milky translucent glass.
      const width = thin + (thick - thin) * Math.min(1, runCov * 1.35);
      const half = width * 0.5;
      const brightness = 0.55 + 0.45 * Math.min(1, runCov * 1.2);
      ctx.fillStyle = `rgba(${r},${g},${b},${(a * brightness).toFixed(3)})`;
      ctx.fillRect(x - half, runY, width, yEnd - runY);
      runY = yEnd;
    };

    for (let y = 1; y < h; y += 1) {
      const c = coverage[y * w + x] ?? 0;
      // Quantize coverage so we get clean thin vs thick runs.
      const q = c > 0.55 ? 1 : c > 0.12 ? 0.35 : 0;
      if (Math.abs(q - runCov) > 0.2) {
        flush(y);
        runCov = q;
      }
    }
    flush(h);
  }
}

export function paintSplash(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  timeSec = 0,
): void {
  drawSplashStripes(ctx, buildSplashGlyph(width, height), timeSec);
}
