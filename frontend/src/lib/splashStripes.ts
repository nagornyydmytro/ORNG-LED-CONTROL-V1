/**
 * Full-viewport Klickpin slit splash for ORNG / HOTBOX.
 *
 * Locked orange vertical hairlines across the whole screen. Brand text sits
 * behind that grating (same orange) and morphs in place — like the reference
 * kinetic type, not a cropped portrait video and not a side-scroll off-screen.
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

function fontFor(size: number): string {
  return `900 ${size}px "Arial Black",Impact,Arial,sans-serif`;
}

export type SplashPainter = {
  width: number;
  height: number;
  paint: (ctx: CanvasRenderingContext2D, timeSec: number) => void;
  resize: (width: number, height: number) => void;
};

/**
 * Create a painter that owns offscreen text buffers and draws the splash.
 */
export function createSplashPainter(
  width: number,
  height: number,
): SplashPainter {
  let w = Math.max(1, Math.floor(width));
  let h = Math.max(1, Math.floor(height));
  const text = document.createElement("canvas");
  const textCtx = text.getContext("2d", { willReadFrequently: true });

  const resize = (nw: number, nh: number) => {
    w = Math.max(1, Math.floor(nw));
    h = Math.max(1, Math.floor(nh));
    text.width = w;
    text.height = h;
  };
  resize(w, h);

  const paintText = (timeSec: number) => {
    if (!textCtx) return;
    textCtx.setTransform(1, 0, 0, 1, 0, 0);
    textCtx.globalAlpha = 1;
    textCtx.fillStyle = "#000000";
    textCtx.fillRect(0, 0, w, h);

    // Reference motion: text stays centered, COM oscillates ~±9% width, ~2s period.
    const drift = Math.sin(timeSec * Math.PI) * w * 0.09;
    const scale = 1 + Math.sin(timeSec * 2.15) * 0.07;
    const shear = Math.sin(timeSec * 1.55) * 0.14;

    textCtx.save();
    textCtx.translate(w * 0.5 + drift, h * 0.36);
    textCtx.transform(scale, 0, shear * scale, scale, 0, 0);

    textCtx.fillStyle = "#ffffff";
    textCtx.strokeStyle = "#ffffff";
    textCtx.textAlign = "center";
    textCtx.textBaseline = "middle";
    textCtx.lineJoin = "round";

    const maxTextW = w * 0.88;
    let fontPx = Math.min(w * 0.34, h * 0.145);
    textCtx.font = fontFor(fontPx);
    while (fontPx > 28 && textCtx.measureText("HOTBOX").width > maxTextW) {
      fontPx -= 2;
      textCtx.font = fontFor(fontPx);
    }
    textCtx.lineWidth = Math.max(4, fontPx * 0.11);
    const gap = fontPx * 0.08;
    textCtx.strokeText("ORNG", 0, -fontPx * 0.55 - gap * 0.5);
    textCtx.fillText("ORNG", 0, -fontPx * 0.55 - gap * 0.5);
    textCtx.strokeText("HOTBOX", 0, fontPx * 0.55 + gap * 0.5);
    textCtx.fillText("HOTBOX", 0, fontPx * 0.55 + gap * 0.5);
    textCtx.restore();
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
    if (!textCtx || w < 8 || h < 8) return;

    paintText(timeSec);
    const img = textCtx.getImageData(0, 0, w, h);
    const d = img.data;

    // Reference geometry @720 → ~14px pitch; lock for wide screens too.
    const pitch = Math.max(5, Math.min(14, Math.round(w / 70)));
    const thin = Math.max(1, Math.round(pitch * 0.16));
    const thick = Math.max(thin + 3, Math.round(pitch * 0.78));
    const { r, g, b } = SPLASH_ORANGE_RGB;
    const invW = 1 / Math.max(1, w - 1);
    const invH = 1 / Math.max(1, h - 1);
    const edgeSoftX = 0.09;
    const edgeSoftY = 0.11;
    const band = Math.max(4, (h / 45) | 0);

    ctx.fillStyle = `rgb(${r},${g},${b})`;

    for (let i = 0; i < Math.ceil(w / pitch) + 1; i += 1) {
      const x = Math.floor(i * pitch + pitch * 0.5);
      if (x < 0 || x >= w) continue;
      const fadeX = edgeFade(x * invW, edgeSoftX);
      if (fadeX < 0.04) continue;

      // Thin field — full viewport, same orange as letters.
      for (let y0 = 0; y0 < h; y0 += band) {
        const fadeY = edgeFade((y0 + band * 0.5) * invH, edgeSoftY);
        const a = fadeX * fadeY;
        if (a < 0.05) continue;
        ctx.globalAlpha = a;
        ctx.fillRect(x - (thin >> 1), y0, thin, Math.min(band + 1, h - y0));
      }

      // Thick capsules where the morphing text sits behind this slit.
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
        const on = d[(y * w + x) * 4]! > 40;
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
