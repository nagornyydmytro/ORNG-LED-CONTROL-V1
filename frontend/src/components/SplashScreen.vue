<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { SPLASH_MIN_MS, createSplashPainter } from "../lib/splashStripes";

const visible = ref(true);
const canvasRef = ref<HTMLCanvasElement | null>(null);

let raf = 0;
let hideTimer = 0;
let painter: ReturnType<typeof createSplashPainter> | null = null;
let painted = false;

const splashT0 = (window as unknown as { __ORNG_SPLASH_T0?: number }).__ORNG_SPLASH_T0;
const t0 = typeof splashT0 === "number" ? splashT0 : performance.now();
const hold = new URLSearchParams(location.search).has("splashHold");

function lockApp(): void {
  document.documentElement.classList.add("splash-active");
  document.body.style.overflow = "hidden";
}

function unlockApp(): void {
  document.documentElement.classList.remove("splash-active");
  document.body.style.overflow = "";
  document.getElementById("boot-splash")?.remove();
}

function ensurePainter(w: number, h: number): void {
  if (!painter) {
    painter = createSplashPainter(w, h);
    return;
  }
  if (painter.width !== w || painter.height !== h) painter.resize(w, h);
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
  ensurePainter(w, h);
  painter?.paint(ctx, (now - t0) / 1000);

  if (!painted) {
    painted = true;
    document.getElementById("boot-splash")?.classList.add("boot-splash--done");
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

onMounted(() => {
  lockApp();
  raf = window.requestAnimationFrame(frame);
  if (!hold) {
    const remaining = Math.max(0, SPLASH_MIN_MS - (performance.now() - t0));
    hideTimer = window.setTimeout(dismiss, remaining);
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
  overflow: hidden;
}

.splash__canvas {
  display: block;
  width: 100%;
  height: 100%;
  background: #000000;
}
</style>
