/**
 * Klickpin-style vertical-slit typography.
 *
 * Reference: dense full-height vertical lines on black; glyphs appear where
 * those lines thicken into rounded capsules. Orange replaces reference red.
 */

export const SPLASH_MIN_MS = 1800;

/** Hot orange (reference red → brand orange). */
export const SPLASH_ORANGE = "#ff4d00";
export const SPLASH_ORANGE_RGB = { r: 255, g: 77, b: 0 };

export type SplashMask = {
  width: number;
  height: number;
  /** 0/255 coverage, row-major, length = width * height */
  coverage: Uint8Array;
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
  // Ultra-black condensed stack — closest system match to the reference blocks.
  return `900 ${size}px "Arial Black",Impact,"Arial Narrow",Arial,sans-serif`;
}

/**
 * Build a binary glyph mask for ORNG / HOTBOX.
 * Horizontally centered; block sits near 30% of screen height.
 */
export function buildSplashMask(width: number, height: number): SplashMask {
  const w = Math.max(1, Math.floor(width));
  const h = Math.max(1, Math.floor(height));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const coverage = new Uint8Array(w * h);
  if (!ctx) return { width: w, height: h, coverage };

  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.lineJoin = "round";
  ctx.miterLimit = 2;

  // Large block type like the reference (fills ~half the frame width).
  const maxTextW = w * 0.88;
  let fontPx = Math.min(w * 0.34, h * 0.17);
  ctx.font = fontFor(fontPx);
  while (fontPx > 32 && ctx.measureText("HOTBOX").width > maxTextW) {
    fontPx -= 2;
    ctx.font = fontFor(fontPx);
  }

  const gap = fontPx * 0.02;
  const blockH = fontPx * 2 + gap;
  const textTop = h * 0.3 - blockH * 0.28;
  const cx = w * 0.5;
  // Fat stroke so slits read as solid bars after sampling.
  ctx.lineWidth = Math.max(4, fontPx * 0.1);

  for (const [label, y] of [
    ["ORNG", textTop],
    ["HOTBOX", textTop + fontPx + gap],
  ] as const) {
    ctx.strokeText(label, cx, y);
    ctx.fillText(label, cx, y);
  }

  const data = ctx.getImageData(0, 0, w, h).data;
  for (let i = 0, p = 0; i < coverage.length; i += 1, p += 4) {
    coverage[i] = data[p]! > 40 ? 255 : 0;
  }
  return { width: w, height: h, coverage };
}

/** @deprecated alias — SplashScreen historically imported buildSplashGlyph */
export function buildSplashGlyph(width: number, height: number): SplashMask {
  return buildSplashMask(width, height);
}

function roundCapsule(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
): void {
  const r = Math.min(w / 2, h / 2);
  if (h <= w) {
    ctx.beginPath();
    ctx.arc(x + w / 2, y + h / 2, r, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  ctx.beginPath();
  ctx.moveTo(x, y + r);
  ctx.arc(x + w / 2, y + r, r, Math.PI, 0);
  ctx.lineTo(x + w, y + h - r);
  ctx.arc(x + w / 2, y + h - r, r, 0, Math.PI);
  ctx.closePath();
  ctx.fill();
}

/**
 * Paint the reference slit field + thickened glyph capsules.
 * Lines fade before touching the screen edges (user requirement).
 */
export function drawSplashStripes(
  ctx: CanvasRenderingContext2D,
  mask: SplashMask,
  _timeSec = 0,
): void {
  const { width: w, height: h, coverage } = mask;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
  if (w < 8 || h < 8) return;

  // Reference: ~13px pitch on 720px → ~55 slits.
  const pitch = Math.max(4, Math.round(w / 55));
  const thin = Math.max(1, Math.round(pitch * 0.16));
  const thick = Math.max(thin + 2, Math.round(pitch * 0.52));
  const { r, g, b } = SPLASH_ORANGE_RGB;
  const invW = 1 / Math.max(1, w - 1);
  const invH = 1 / Math.max(1, h - 1);

  // Soft vertical fade only — keep slits strong through the text band.
  // Horizontal fade keeps slits off the left/right screen borders.
  const edgeSoftX = 0.1;
  const edgeSoftY = 0.16;

  for (let i = 0; i < Math.ceil(w / pitch); i += 1) {
    const x = Math.floor(i * pitch + pitch * 0.5);
    if (x < 0 || x >= w) continue;
    const fadeX = edgeFade(x * invW, edgeSoftX);
    if (fadeX < 0.05) continue;

    // --- thin full-height slit (reference grid) ---
    // Draw as one vertical strip with alpha gradient via multiple bands.
    const band = Math.max(4, (h / 40) | 0);
    for (let y0 = 0; y0 < h; y0 += band) {
      const fadeY = edgeFade((y0 + band * 0.5) * invH, edgeSoftY);
      const a = 0.85 * fadeX * fadeY;
      if (a < 0.03) continue;
      ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
      ctx.fillRect(x - (thin >> 1), y0, thin, Math.min(band + 1, h - y0));
    }

    // --- thick rounded capsules where the glyph covers this column ---
    let run = -1;
    const flush = (yEnd: number) => {
      if (run < 0) return;
      const y0 = run;
      const hh = yEnd - y0;
      run = -1;
      if (hh < 2) return;
      const midY = (y0 + yEnd) * 0.5 * invH;
      const fadeY = edgeFade(midY, edgeSoftY);
      const a = fadeX * fadeY;
      if (a < 0.05) return;
      ctx.globalAlpha = a;
      ctx.fillStyle = SPLASH_ORANGE;
      roundCapsule(ctx, x - (thick >> 1), y0, thick, hh);
      ctx.globalAlpha = 1;
    };

    for (let y = 0; y < h; y += 1) {
      const on = coverage[y * w + x]! > 0;
      if (on) {
        if (run < 0) run = y;
      } else {
        flush(y);
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
  drawSplashStripes(ctx, buildSplashMask(width, height), timeSec);
}
