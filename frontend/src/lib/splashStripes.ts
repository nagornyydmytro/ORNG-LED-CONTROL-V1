/**
 * Klickpin slit-scan splash — vertical barcode typography.
 *
 * Matches reference geometry: ~13–14px slit pitch, hairline field, letters from
 * thickening those slits. Orange replaces red. Soft fade before screen edges.
 */

export const SPLASH_MIN_MS = 1800;

export const SPLASH_ORANGE = "#ff2a00";
export const SPLASH_ORANGE_RGB = { r: 255, g: 42, b: 0 };

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
  return `900 ${size}px Impact,"Arial Black",Arial,sans-serif`;
}

/** Hard-edged orange glyphs (no AA mush after slit stretch). */
export function buildSplashGlyph(width: number, height: number): SplashGlyph {
  const w = Math.max(1, Math.floor(width));
  const h = Math.max(1, Math.floor(height));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return { width: w, height: h, canvas };

  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);

  ctx.fillStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  // Prefer crisp rasterization where the engine supports it.
  (ctx as CanvasRenderingContext2D & { textRendering?: string }).textRendering =
    "geometricPrecision";

  const maxTextW = w * 0.9;
  let fontPx = Math.min(w * 0.34, h * 0.16);
  ctx.font = fontFor(fontPx);
  while (fontPx > 40 && ctx.measureText("HOTBOX").width > maxTextW) {
    fontPx -= 2;
    ctx.font = fontFor(fontPx);
  }

  const lineGap = fontPx * 0.16;
  const blockH = fontPx * 2 + lineGap;
  const midY = h * 0.32;
  const cx = w * 0.5;
  ctx.fillText("ORNG", cx, midY - blockH * 0.28);
  ctx.fillText("HOTBOX", cx, midY + blockH * 0.28);

  // Threshold → pure black / pure orange (kills AA that shreds slit letters).
  const img = ctx.getImageData(0, 0, w, h);
  const d = img.data;
  const { r, g, b } = SPLASH_ORANGE_RGB;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i]! > 40) {
      d[i] = r;
      d[i + 1] = g;
      d[i + 2] = b;
      d[i + 3] = 255;
    } else {
      d[i] = 0;
      d[i + 1] = 0;
      d[i + 2] = 0;
      d[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);

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

  // Lock pitch near the reference (~13px @ 720), even on ultrawide screens.
  const pitch = Math.max(5, Math.min(14, Math.round(w / 70)));
  const thin = Math.max(1, Math.round(pitch * 0.18));
  const thick = Math.max(thin + 2, pitch - 2);
  const { r, g, b } = SPLASH_ORANGE_RGB;
  const invW = 1 / Math.max(1, w - 1);

  // 1) Full-field hairlines — solid neon (reference brightness).
  ctx.fillStyle = `rgb(${r},${g},${b})`;
  for (let x = Math.floor(pitch * 0.5); x < w; x += pitch) {
    if (edgeFade(x * invW, 0.1) < 0.05) continue;
    ctx.fillRect(x - (thin >> 1), 0, thin, h);
  }

  // 2) Letter slits via 1px column stretch.
  ctx.imageSmoothingEnabled = false;
  for (let x = Math.floor(pitch * 0.5); x < w; x += pitch) {
    if (edgeFade(x * invW, 0.1) < 0.05) continue;
    ctx.drawImage(src, x, 0, 1, h, x - (thick >> 1), 0, thick, h);
  }

  // 3) Edge dissolve — slits fade before touching the border.
  const padY = Math.max(16, h * 0.07);
  const top = ctx.createLinearGradient(0, 0, 0, padY);
  top.addColorStop(0, "#000");
  top.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = top;
  ctx.fillRect(0, 0, w, padY);

  const bot = ctx.createLinearGradient(0, h - padY, 0, h);
  bot.addColorStop(0, "rgba(0,0,0,0)");
  bot.addColorStop(1, "#000");
  ctx.fillStyle = bot;
  ctx.fillRect(0, h - padY, w, padY);

  const padX = Math.max(20, w * 0.07);
  const left = ctx.createLinearGradient(0, 0, padX, 0);
  left.addColorStop(0, "#000");
  left.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = left;
  ctx.fillRect(0, 0, padX, h);

  const right = ctx.createLinearGradient(w - padX, 0, w, 0);
  right.addColorStop(0, "rgba(0,0,0,0)");
  right.addColorStop(1, "#000");
  ctx.fillStyle = right;
  ctx.fillRect(w - padX, 0, padX, h);
}

export function paintSplash(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  timeSec = 0,
): void {
  drawSplashStripes(ctx, buildSplashGlyph(width, height), timeSec);
}
