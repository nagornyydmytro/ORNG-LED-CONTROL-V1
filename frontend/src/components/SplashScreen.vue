<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

/** Full reference sequence length (see public/splash-frames/manifest.json). */
const FRAME_COUNT = 125;
const FPS = 30;
const SPLASH_MIN_MS = Math.ceil((FRAME_COUNT / FPS) * 1000);

const visible = ref(true);
const canvasRef = ref<HTMLCanvasElement | null>(null);

let raf = 0;
let hideTimer = 0;
let frames: HTMLImageElement[] = [];
let ready = false;
let painted = false;

const splashT0 = (window as unknown as { __ORNG_SPLASH_T0?: number }).__ORNG_SPLASH_T0;
const t0 = typeof splashT0 === "number" ? splashT0 : performance.now();

function frameUrl(i: number): string {
  return `/splash-frames/${String(i).padStart(3, "0")}.jpg`;
}

function lockApp(): void {
  document.documentElement.classList.add("splash-active");
  document.body.style.overflow = "hidden";
}

function unlockApp(): void {
  document.documentElement.classList.remove("splash-active");
  document.body.style.overflow = "";
  document.getElementById("boot-splash")?.remove();
}

function preloadFrames(): Promise<void> {
  frames = Array.from({ length: FRAME_COUNT }, (_, i) => {
    const img = new Image();
    img.decoding = "async";
    img.src = frameUrl(i);
    return img;
  });
  return Promise.all(
    frames.map((img) =>
      img.decode().catch(() => undefined),
    ),
  ).then(() => {
    ready = frames.some((img) => img.naturalWidth > 0);
  });
}

function drawCover(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  w: number,
  h: number,
): void {
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
  if (!img.naturalWidth || !img.naturalHeight) return;
  const scale = Math.max(w / img.naturalWidth, h / img.naturalHeight);
  const dw = img.naturalWidth * scale;
  const dh = img.naturalHeight * scale;
  ctx.drawImage(img, (w - dw) * 0.5, (h - dh) * 0.5, dw, dh);
}

function frame(now: number) {
  const canvas = canvasRef.value;
  if (!canvas || !visible.value) return;
  const ctx = canvas.getContext("2d", { alpha: false });
  if (!ctx) return;

  const w = Math.max(1, Math.floor(window.innerWidth));
  const h = Math.max(1, Math.floor(window.innerHeight));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }

  const elapsed = now - t0;
  const idx = Math.min(
    FRAME_COUNT - 1,
    Math.max(0, Math.floor((elapsed / 1000) * FPS)),
  );
  const img = frames[idx];
  if (img && img.naturalWidth > 0) {
    drawCover(ctx, img, w, h);
  } else {
    ctx.fillStyle = "#000000";
    ctx.fillRect(0, 0, w, h);
  }

  if (!painted) {
    painted = true;
    document.getElementById("boot-splash")?.classList.add("boot-splash--done");
  }

  const hold = new URLSearchParams(location.search).has("splashHold");
  if (!hold && ready && elapsed >= SPLASH_MIN_MS && idx >= FRAME_COUNT - 1) {
    dismiss();
    return;
  }

  raf = window.requestAnimationFrame(frame);
}

function dismiss() {
  if (!visible.value) return;
  visible.value = false;
  if (raf) window.cancelAnimationFrame(raf);
  raf = 0;
  unlockApp();
}

onMounted(async () => {
  lockApp();
  await preloadFrames();
  raf = window.requestAnimationFrame(frame);
  if (!new URLSearchParams(location.search).has("splashHold")) {
    // Failsafe if decode stalls — never block the app forever.
    hideTimer = window.setTimeout(dismiss, SPLASH_MIN_MS + 1500);
  }
});

onBeforeUnmount(() => {
  if (raf) window.cancelAnimationFrame(raf);
  if (hideTimer) window.clearTimeout(hideTimer);
  unlockApp();
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="splash"
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label="ORNG HOTBOX"
    >
      <canvas
        ref="canvasRef"
        class="splash__canvas"
      />
      <span class="visually-hidden">ORNG HOTBOX</span>
    </div>
  </Teleport>
</template>

<style scoped>
.splash {
  position: fixed;
  inset: 0;
  z-index: 2147483646;
  width: 100vw;
  height: 100vh;
  margin: 0;
  padding: 0;
  background: #000000;
  pointer-events: all;
  display: block;
  overflow: hidden;
}

.splash__canvas {
  display: block;
  width: 100%;
  height: 100%;
  background: #000000;
}
</style>
