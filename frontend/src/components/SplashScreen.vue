<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { SPLASH_MIN_MS, SPLASH_TEXT_BOTTOM, SPLASH_TEXT_TOP } from "../lib/splashBrand";

const visible = ref(true);
let hideTimer = 0;

const splashT0 = (window as unknown as { __ORNG_SPLASH_T0?: number }).__ORNG_SPLASH_T0;
const t0 = typeof splashT0 === "number" ? splashT0 : performance.now();
const hold = new URLSearchParams(location.search).has("splashHold");

/** Two identical halves → seamless translateX(-50%) loop. */
const TOP_HALF = Array.from({ length: 6 }, () => SPLASH_TEXT_TOP);
const BOTTOM_HALF = Array.from({ length: 4 }, () => SPLASH_TEXT_BOTTOM);

function lockApp(): void {
  document.documentElement.classList.add("splash-active");
  document.body.style.overflow = "hidden";
}

function unlockApp(): void {
  document.documentElement.classList.remove("splash-active");
  document.body.style.overflow = "";
  document.getElementById("boot-splash")?.remove();
}

function dismiss(): void {
  if (!visible.value) return;
  visible.value = false;
  unlockApp();
}

onMounted(() => {
  lockApp();
  window.requestAnimationFrame(() => {
    document.getElementById("boot-splash")?.classList.add("boot-splash--done");
  });
  if (!hold) {
    const remaining = Math.max(0, SPLASH_MIN_MS - (performance.now() - t0));
    hideTimer = window.setTimeout(dismiss, remaining);
  }
});

onBeforeUnmount(() => {
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
      :aria-label="`${SPLASH_TEXT_TOP} ${SPLASH_TEXT_BOTTOM}`"
    >
      <div class="marquee" aria-hidden="true">
        <div class="marquee__track">
          <span v-for="(word, i) in TOP_HALF" :key="`t1-${i}`" class="marquee__word">{{ word }}</span>
          <span v-for="(word, i) in TOP_HALF" :key="`t2-${i}`" class="marquee__word">{{ word }}</span>
        </div>
      </div>
      <div class="marquee" aria-hidden="true">
        <div class="marquee__track marquee__track--reverse">
          <span v-for="(word, i) in BOTTOM_HALF" :key="`b1-${i}`" class="marquee__word">{{ word }}</span>
          <span v-for="(word, i) in BOTTOM_HALF" :key="`b2-${i}`" class="marquee__word">{{ word }}</span>
        </div>
      </div>
      <span class="visually-hidden">{{ SPLASH_TEXT_TOP }} {{ SPLASH_TEXT_BOTTOM }}</span>
    </div>
  </Teleport>
</template>

<style scoped>
.splash {
  --splash-orange: #ff4d00;
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
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: center;
  gap: clamp(0.35rem, 1.2vh, 1rem);
}

.marquee {
  position: relative;
  width: 100%;
  height: 1.1em;
  font-size: clamp(3.5rem, 15vh, 10rem);
  overflow: hidden;
}

.marquee__track {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 0.6em;
  width: max-content;
  will-change: transform;
  animation: orng-marquee 12s linear infinite;
}

.marquee__track--reverse {
  animation-name: orng-marquee-reverse;
  animation-duration: 16s;
}

.marquee__word {
  flex: 0 0 auto;
  color: var(--splash-orange);
  font-weight: 900;
  font-family: Impact, "Arial Black", Arial, sans-serif;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  line-height: 1;
  white-space: nowrap;
}

@keyframes orng-marquee {
  from {
    transform: translateX(0);
  }
  to {
    transform: translateX(-50%);
  }
}

@keyframes orng-marquee-reverse {
  from {
    transform: translateX(-50%);
  }
  to {
    transform: translateX(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .marquee__track {
    animation: none;
    transform: translateX(-25%);
  }
}
</style>
