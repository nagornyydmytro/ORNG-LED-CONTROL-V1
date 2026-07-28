/**
 * Klickpin slit-scan splash.
 *
 * Fixed vertical orange slits. Solid orange ORNG / HOTBOX scrolls horizontally
 * *behind* that grating — slits thicken where they intersect the moving text.
 * Same color for field and letters. Soft fade before screen edges.
 */

export const SPLASH_MIN_MS = 3200;

/** Hot orange (reference red → brand orange). */
export const SPLASH_ORANGE = "#ff4d00";
export const SPLASH_ORANGE_RGB = { r: 255, g: 77, b: 0 };

/** Horizontal scroll speed in viewport-widths per second. */
export const SPLASH_SCROLL_VW_PER_SEC = 0.28;

export type SplashMask = {
  /** Viewport width */
  width: number;
  height: number;
  /** Seamless strip width (= 2 * viewport) */
  stripWidth: number;
  /** 0/255 coverage, row-major, length = stripWidth * height */
  coverage: Uint8Array;
};

/** @deprecated alias */
export type SplashGlyph = SplashMask;

export function edgeFade(t: number, soft = 0.12): number {
  const x = Math.max(0, Math.min(1, t));
  return Math.min(smoothstep(0, soft, x), smoothstep(0, soft, 1 - x));
}

function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

function fontFor(size: number): string {
  return `900 ${size}px "Arial Black",Impact,Arial,sans-serif`;
}

function paintTextBlock(
  ctx: CanvasRenderingContext2D,
  cx: number,
  h: number,
  maxTextW: number,
): void {
  let fontPx = Math.min(maxTextW * 0.38, h * 0.14);
  ctx.font = fontFor(fontPx);
  while (fontPx > 28 && ctx.measureText("HOTBOX").width > maxTextW) {
    fontPx -= 2;
    ctx.font = fontFor(fontPx);
  }
  const gap = fontPx * 0.05;
  const blockH = fontPx * 2 + gap;
  const textTop = h * 0.36 - blockH * 0.5;
  ctx.lineWidth = Math.max(4, fontPx * 0.12);
  ctx.strokeText("ORNG", cx, textTop);
  ctx.fillText("ORNG", cx, textTop);
  ctx.strokeText("HOTBOX", cx, textTop + fontPx + gap);
  ctx.fillText("HOTBOX", cx, textTop + fontPx + gap);
}

/**
 * Wide seamless strip: two copies of ORNG / HOTBOX so horizontal scroll loops.
 */
export function buildSplashMask(width: number, height: number): SplashMask {
  const w = Math.max(1, Math.floor(width));
  const h = Math.max(1, Math.floor(height));
  const stripWidth = w * 2;
  const canvas = document.createElement("canvas");
  canvas.width = stripWidth;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const coverage = new Uint8Array(stripWidth * h);
  if (!ctx) return { width: w, height: h, stripWidth, coverage };

  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, stripWidth, h);
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.lineJoin = "round";

  const maxTextW = w * 0.9;
  // Two tiles → seamless loop when scroll advances by `w`.
  paintTextBlock(ctx, w * 0.5, h, maxTextW);
  paintTextBlock(ctx, w * 1.5, h, maxTextW);

  const data = ctx.getImageData(0, 0, stripWidth, h).data;
  for (let i = 0, p = 0; i < coverage.length; i += 1, p += 4) {
    coverage[i] = data[p]! > 40 ? 255 : 0;
  }
  return { width: w, height: h, stripWidth, coverage };
}

export function buildSplashGlyph(width: number, height: number): SplashMask {
  return buildSplashMask(width, height);
}

function roundCapsule(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  ww: number,
  hh: number,
): void {
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
}

/**
 * Fixed slit grating + text scrolling behind it (sampled into thick capsules).
 */
export function drawSplashStripes(
  ctx: CanvasRenderingContext2D,
  mask: SplashMask,
  timeSec = 0,
): void {
  const { width: w, height: h, stripWidth, coverage } = mask;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
  if (w < 8 || h < 8 || stripWidth < 1) return;

  const pitch = Math.max(5, Math.min(14, Math.round(w / 70)));
  const thin = Math.max(1, Math.round(pitch * 0.18));
  const thick = Math.max(thin + 3, Math.round(pitch * 0.78));
  const { r, g, b } = SPLASH_ORANGE_RGB;
  const invW = 1 / Math.max(1, w - 1);
  const invH = 1 / Math.max(1, h - 1);
  const edgeSoftX = 0.1;
  const edgeSoftY = 0.14;

  // Text travels behind locked slits (loop every stripWidth/2 = viewport width).
  const scroll = ((timeSec * SPLASH_SCROLL_VW_PER_SEC * w) % w + w) % w;

  ctx.fillStyle = `rgb(${r},${g},${b})`;

  for (let i = 0; i < Math.ceil(w / pitch) + 1; i += 1) {
    const x = Math.floor(i * pitch + pitch * 0.5);
    if (x < 0 || x >= w) continue;
    const fadeX = edgeFade(x * invW, edgeSoftX);
    if (fadeX < 0.04) continue;

    // Sample column from the moving text strip behind this slit.
    const sx = Math.floor(x + scroll) % stripWidth;
    const sampleX = sx < 0 ? sx + stripWidth : sx;

    // Thin full-height field line (same orange).
    const band = Math.max(4, (h / 40) | 0);
    for (let y0 = 0; y0 < h; y0 += band) {
      const fadeY = edgeFade((y0 + band * 0.5) * invH, edgeSoftY);
      const a = fadeX * fadeY;
      if (a < 0.05) continue;
      ctx.globalAlpha = a;
      ctx.fillRect(x - (thin >> 1), y0, thin, Math.min(band + 1, h - y0));
    }

    // Thick capsules where the scrolling text intersects this slit.
    let run = -1;
    const flush = (yEnd: number) => {
      if (run < 0) return;
      const y0 = run;
      const hh = yEnd - y0;
      run = -1;
      if (hh < 2) return;
      const fadeY = edgeFade((y0 + yEnd) * 0.5 * invH, edgeSoftY);
      const a = fadeX * fadeY;
      if (a < 0.05) return;
      ctx.globalAlpha = a;
      roundCapsule(ctx, x - (thick >> 1), y0, thick, hh);
    };

    for (let y = 0; y < h; y += 1) {
      if (coverage[y * stripWidth + sampleX]! > 0) {
        if (run < 0) run = y;
      } else {
        flush(y);
      }
    }
    flush(h);
  }

  ctx.globalAlpha = 1;
}

export function paintSplash(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  timeSec = 0,
): void {
  drawSplashStripes(ctx, buildSplashMask(width, height), timeSec);
}
