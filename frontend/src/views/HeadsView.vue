<script setup lang="ts">
import { computed, inject, onUnmounted, ref } from "vue";
import { APP_STATE_KEY } from "../composables/appStateKey";

const app = inject(APP_STATE_KEY);
const offline = computed(
  () => app?.connection.value === "offline" || app?.connection.value === "reconnecting",
);

const colors = [
  { id: "red", label: "Красный", paint: "#e10600" },
  { id: "white", label: "Белый", paint: "#f4f4f4" },
  { id: "violet", label: "Фиолетовый", paint: "#7a2bff" },
  { id: "blue", label: "Синий", paint: "#1d4dff" },
  { id: "cyan", label: "Голубой", paint: "#1ee0e0" },
  { id: "none", label: "Никакой", paint: "transparent" },
] as const;

type ColorId = (typeof colors)[number]["id"];

const enabled = ref(false);
const light = ref(false);
const scene = ref(true);
const color = ref<ColorId>("red");
const stickX = ref(0);
const stickY = ref(0);
const ring = ref<HTMLElement | null>(null);
let drag = false;
let sendTimer: number | null = null;

function pose() {
  return {
    enabled: enabled.value,
    light: light.value && enabled.value,
    scene: scene.value,
    color: color.value,
    x: stickX.value,
    y: (stickY.value + 1) / 2,
  };
}

function deliver() {
  app?.sendHeadDesk(pose());
}

function send() {
  if (sendTimer !== null) return;
  deliver();
  sendTimer = window.setTimeout(() => {
    sendTimer = null;
  }, 40);
}

function toggleEnabled() {
  enabled.value = !enabled.value;
  if (enabled.value) {
    stickX.value = 0;
    stickY.value = 0;
  }
  deliver();
}

function toggleLight() {
  light.value = !light.value;
  if (light.value) color.value = "red";
  deliver();
}

function toggleScene() {
  scene.value = !scene.value;
  deliver();
}

function pick(next: ColorId) {
  color.value = next;
  deliver();
}

function place(event: PointerEvent) {
  const node = ring.value;
  if (!node) return;
  const rect = node.getBoundingClientRect();
  const radius = rect.width / 2;
  if (radius <= 0) return;
  let x = (event.clientX - (rect.left + radius)) / radius;
  let y = (event.clientY - (rect.top + radius)) / radius;
  const reach = Math.hypot(x, y);
  if (reach > 1) {
    x /= reach;
    y /= reach;
  }
  stickX.value = x;
  stickY.value = y;
  if (enabled.value) send();
}

function down(event: PointerEvent) {
  drag = true;
  ring.value?.setPointerCapture(event.pointerId);
  place(event);
}

function move(event: PointerEvent) {
  if (!drag) return;
  place(event);
}

function up(event: PointerEvent) {
  if (!drag) return;
  drag = false;
  place(event);
  if (enabled.value) deliver();
  ring.value?.releasePointerCapture(event.pointerId);
}

onUnmounted(() => {
  if (sendTimer !== null) window.clearTimeout(sendTimer);
  if (enabled.value) {
    app?.sendHeadDesk({ enabled: false, light: false, scene: true, color: "red", x: 0, y: 0 });
  }
});

const knobStyle = computed(() => ({
  left: `${50 + stickX.value * 42}%`,
  top: `${50 + stickY.value * 42}%`,
}));
</script>

<template>
  <section
    class="heads"
    :class="{ 'is-off': offline }"
  >
    <div class="heads__bar">
      <label class="switch">
        <span>Функция</span>
        <button
          type="button"
          class="theme-slider"
          :class="{ 'is-dark': enabled }"
          role="switch"
          :aria-checked="enabled"
          aria-label="Функция"
          :disabled="offline"
          @click="toggleEnabled"
        >
          <span class="theme-slider__thumb" />
        </button>
      </label>
      <label class="switch">
        <span>Свет</span>
        <button
          type="button"
          class="theme-slider"
          :class="{ 'is-dark': light }"
          role="switch"
          :aria-checked="light"
          aria-label="Свет"
          :disabled="offline || !enabled"
          @click="toggleLight"
        >
          <span class="theme-slider__thumb" />
        </button>
      </label>
    </div>

    <div
      v-if="enabled && light"
      class="heads__light"
    >
      <div
        class="swatches"
        role="listbox"
        aria-label="Цвет голов"
      >
        <button
          v-for="item in colors"
          :key="item.id"
          type="button"
          class="swatch"
          :class="{ 'is-on': color === item.id, 'is-none': item.id === 'none' }"
          role="option"
          :aria-selected="color === item.id"
          :aria-label="item.label"
          :style="{ background: item.paint }"
          @click="pick(item.id)"
        />
      </div>
      <label class="switch">
        <span>Сцена</span>
        <button
          type="button"
          class="theme-slider"
          :class="{ 'is-dark': scene }"
          role="switch"
          :aria-checked="scene"
          aria-label="Сцена"
          @click="toggleScene"
        >
          <span class="theme-slider__thumb" />
        </button>
      </label>
    </div>

    <div class="pad">
      <div
        ref="ring"
        class="ring"
        :class="{ 'is-live': enabled }"
        @pointerdown="down"
        @pointermove="move"
        @pointerup="up"
        @pointercancel="up"
      >
        <span
          class="knob"
          :style="knobStyle"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.heads {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: calc(100dvh - 92px);
  overflow: hidden;
}
.heads__bar,
.heads__light {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 18px 28px;
}
.switch {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
}
.swatches {
  display: flex;
  gap: 10px;
}
.swatch {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 2px solid transparent;
  padding: 0;
  cursor: pointer;
}
.swatch.is-on {
  border-color: #fff;
  box-shadow: 0 0 0 2px #000;
}
.swatch.is-none {
  background:
    linear-gradient(135deg, transparent 46%, #fff 46% 54%, transparent 54%) !important;
  border-color: #888;
}
.pad {
  flex: 1;
  min-height: 0;
  display: grid;
  place-items: center;
  container-type: size;
}
.ring {
  width: min(100cqw, 100cqh);
  height: min(100cqw, 100cqh);
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.35);
  background: radial-gradient(circle at 50% 42%, rgba(255, 255, 255, 0.08), rgba(0, 0, 0, 0.35));
  position: relative;
  touch-action: none;
  cursor: grab;
}
.ring.is-live {
  border-color: #e10600;
}
.knob {
  position: absolute;
  width: 18%;
  aspect-ratio: 1;
  border-radius: 50%;
  background: #f2f2f2;
  transform: translate(-50%, -50%);
  pointer-events: none;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
}
</style>
