<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import {
  SPLASH_MIN_MS,
  buildSplashMask,
  drawSplashStripes,
  type SplashMask,
} from "../lib/splashStripes";

const visible = ref(true);
const fading = ref(false);
const canvasRef = ref<HTMLCanvasElement | null>(null);

let raf = 0;
let hideTimer = 0;
let cssW = 0;
let cssH = 0;
let mask: SplashMask | null = null;
const splashT0 = (window as unknown as { __ORNG_SPLASH_T0?: number }).__ORNG_SPLASH_T0;
const t0 = typeof splashT0 === "number" ? splashT0 : performance.now();

function ensureSize(canvas: HTMLCanvasElement): void {
  const nextW = Math.max(1, Math.floor(window.innerWidth));
  const nextH = Math.max(1, Math.floor(window.innerHeight));
  if (nextW === cssW && nextH === cssH && mask) return;
  cssW = nextW;
  cssH = nextH;
  canvas.width = nextW;
  canvas.height = nextH;
  canvas.style.width = `${nextW}px`;
  canvas.style.height = `${nextH}px`;
  mask = buildSplashMask(nextW, nextH);
}

function frame(now: number) {
  const canvas = canvasRef.value;
  if (!canvas || !visible.value) return;
  const ctx = canvas.getContext("2d", { alpha: false });
  if (!ctx) return;
  ensureSize(canvas);
  if (mask) drawSplashStripes(ctx, mask, (now - t0) / 1000);
  raf = window.requestAnimationFrame(frame);
}

function dismiss() {
  if (fading.value || !visible.value) return;
  fading.value = true;
  window.setTimeout(() => {
    visible.value = false;
    if (raf) window.cancelAnimationFrame(raf);
    raf = 0;
  }, 420);
}

onMounted(() => {
  document.getElementById("boot-splash")?.remove();
  raf = window.requestAnimationFrame(frame);
  const remaining = Math.max(0, SPLASH_MIN_MS - (performance.now() - t0));
  hideTimer = window.setTimeout(dismiss, remaining);
});

onBeforeUnmount(() => {
  if (raf) window.cancelAnimationFrame(raf);
  if (hideTimer) window.clearTimeout(hideTimer);
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="splash"
      :class="{ 'splash--out': fading }"
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
  z-index: 100000;
  background: #000;
  pointer-events: all;
  opacity: 1;
  transition: opacity 0.4s ease;
}

.splash--out {
  opacity: 0;
  pointer-events: none;
}

.splash__canvas {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
