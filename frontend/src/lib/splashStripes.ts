/**
 * Klickpin slit-scan splash — fixed vertical slits + kinetic typography.
 *
 * Reference: locked hairline field; letterforms thicken those slits and morph
 * in place over time. Orange replaces reference red. Soft edge dissolve.
 */

export const SPLASH_MIN_MS = 2800;

/** Hot orange (reference red → brand orange). */
export const SPLASH_ORANGE = "#ff4d00";
export const SPLASH_ORANGE_RGB = { r: 255, g: 77, b: 0 };

export type SplashMask = {
  width: number;
  height: number;
  /** 0/255 coverage, row-major, length = width * height */
  coverage: Uint8Array;
};

/** @deprecated alias kept for SplashScreen imports */
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

/**
 * Binary glyph mask for ORNG / HOTBOX.
 * Block height ≈ 30% of the viewport; centered horizontally, upper-mid.
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

  const maxTextW = w * 0.92;
  let fontPx = Math.min(w * 0.36, h * 0.14);
  ctx.font = fontFor(fontPx);
  while (fontPx > 28 && ctx.measureText("HOTBOX").width > maxTextW) {
    fontPx -= 2;
    ctx.font = fontFor(fontPx);
  }

  const gap = fontPx * 0.05;
  const blockH = fontPx * 2 + gap;
  const textTop = h * 0.36 - blockH * 0.5;
  const cx = w * 0.5;
  ctx.lineWidth = Math.max(4, fontPx * 0.12);

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

export function buildSplashGlyph(width: number, height: number): SplashMask {
  return buildSplashMask(width, height);
}

/**
 * Paint fixed thin slits, then kinetic thick glyph bars.
 * timeSec drives morph / assemble / wave (reference text animation).
 */
export function drawSplashStripes(
  ctx: CanvasRenderingContext2D,
  mask: SplashMask,
  timeSec = 0,
): void {
  const { width: w, height: h, coverage } = mask;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
  if (w < 8 || h < 8) return;

  // Reference: slits stay locked (~14px pitch @ 720).
  const pitch = Math.max(5, Math.min(14, Math.round(w / 70)));
  const thin = Math.max(1, Math.round(pitch * 0.18));
  const thickBase = Math.max(thin + 3, Math.round(pitch * 0.78));
  const { r, g, b } = SPLASH_ORANGE_RGB;
  const invW = 1 / Math.max(1, w - 1);
  const invH = 1 / Math.max(1, h - 1);
  const edgeSoftX = 0.1;
  const edgeSoftY = 0.14;

  // Intro: bars scatter then lock onto glyphs (~0.7s).
  const assemble = smoothstep(0, 0.7, timeSec);
  // Ongoing morph — COM in the reference oscillates ~2s period.
  const morph = timeSec * Math.PI; // period ≈ 2s
  // Thickness breath + field flicker.
  const breath = 0.72 + 0.28 * Math.sin(timeSec * 4.2);
  const flicker = 0.88 + 0.12 * Math.sin(timeSec * 37.0);
  const thick = Math.max(thin + 2, Math.round(thickBase * breath));

  const band = Math.max(3, (h / 50) | 0);
  const slitCount = Math.ceil(w / pitch) + 1;

  for (let i = 0; i < slitCount; i += 1) {
    const x = Math.floor(i * pitch + pitch * 0.5);
    if (x < 0 || x >= w) continue;
    const fadeX = edgeFade(x * invW, edgeSoftX);
    if (fadeX < 0.04) continue;

    // --- Fixed thin field (always visible) ---
    for (let y0 = 0; y0 < h; y0 += band) {
      const fadeY = edgeFade((y0 + band * 0.5) * invH, edgeSoftY);
      const a = fadeX * fadeY * flicker;
      if (a < 0.04) continue;
      ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
      ctx.fillRect(x - (thin >> 1), y0, thin, Math.min(band + 1, h - y0));
    }

    // Sample glyph with per-slit morph offset (text morphs in place; slits fixed).
    const sampleShift = Math.round(
      Math.sin(morph + i * 0.37) * pitch * 3.2 * (0.35 + 0.65 * assemble),
    );
    const sx = Math.max(0, Math.min(w - 1, x + sampleShift));

    // Vertical wave + intro scatter along each slit.
    const yWave = Math.sin(timeSec * 3.1 + i * 0.51) * h * 0.018;
    const scatter =
      (1 - assemble) *
      Math.sin(i * 12.989 + 1.7) *
      h *
      0.16;

    let run = -1;
    const flush = (yEnd: number) => {
      if (run < 0) return;
      const y0 = run;
      const hh = yEnd - y0;
      run = -1;
      if (hh < 2) return;

      const drawY = Math.round(y0 + yWave + scatter);
      if (drawY >= h || drawY + hh <= 0) return;
      const clippedY = Math.max(0, drawY);
      const clippedH = Math.min(h, drawY + hh) - clippedY;
      if (clippedH < 2) return;

      const fadeY = edgeFade((clippedY + clippedH * 0.5) * invH, edgeSoftY);
      const a = fadeX * fadeY * (0.55 + 0.45 * assemble);
      if (a < 0.05) return;
      ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
      ctx.fillRect(x - (thick >> 1), clippedY, thick, clippedH);
    };

    for (let y = 0; y < h; y += 1) {
      if (coverage[y * w + sx]! > 0) {
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
