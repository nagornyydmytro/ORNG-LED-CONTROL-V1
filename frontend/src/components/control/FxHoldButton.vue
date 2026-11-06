<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = withDefaults(
  defineProps<{
    label: string;
    hint?: string;
    active?: boolean;
    disabled?: boolean;
    speed: number;
    showSpeed?: boolean;
  }>(),
  { showSpeed: true },
);

const showSpeed = computed(() => props.showSpeed);

const emit = defineEmits<{
  press: [];
  release: [];
  speed: [value: number];
}>();

const DEAD_ZONE_PX = 16;
const PIXELS_PER_FULL_SCALE = 180;
const SPEED_MIN = 0.05;
const SPEED_MAX = 1;
const SEND_INTERVAL_MS = 80;

const overlay = ref<{ left: number; top: number; speed: number } | null>(null);
const dragging = ref(false);

let pointerId: number | null = null;
let startY = 0;
let startSpeed = 0;
let latest = 0;
let dragged = false;
let lastSentAt = 0;
let released = true;

function clampSpeed(value: number): number {
  return Math.min(SPEED_MAX, Math.max(SPEED_MIN, value));
}

function placeOverlay(clientX: number, clientY: number, speed: number) {
  const boxW = 56;
  const boxH = 120;
  let left = clientX + 24;
  if (left + boxW > window.innerWidth - 8) left = Math.max(8, clientX - boxW - 16);
  let top = clientY - boxH / 2;
  top = Math.max(8, Math.min(window.innerHeight - boxH - 8, top));
  overlay.value = { left, top, speed };
}

function emitSpeed(value: number, force = false) {
  const now = performance.now();
  if (!force && now - lastSentAt < SEND_INTERVAL_MS) return;
  lastSentAt = now;
  emit("speed", value);
}

function onWindowPointerEnd(event: PointerEvent) {
  if (pointerId === null || pointerId < 0) return;
  if (event.pointerId !== pointerId) return;
  finish(event, true);
}

function bindWindowRelease() {
  window.addEventListener("pointerup", onWindowPointerEnd);
  window.addEventListener("pointercancel", onWindowPointerEnd);
}

function unbindWindowRelease() {
  window.removeEventListener("pointerup", onWindowPointerEnd);
  window.removeEventListener("pointercancel", onWindowPointerEnd);
}

function onPointerDown(event: PointerEvent) {
  if (props.disabled || pointerId !== null) return;
  if (event.pointerType === "mouse" && event.button !== 0) return;
  const el = event.currentTarget as HTMLElement;
  pointerId = event.pointerId;
  startY = event.clientY;
  startSpeed = Number(props.speed) || SPEED_MIN;
  latest = startSpeed;
  dragged = false;
  released = false;
  dragging.value = true;
  lastSentAt = 0;
  bindWindowRelease();
  try {
    if (typeof el.setPointerCapture === "function") el.setPointerCapture(event.pointerId);
  } catch {
    // Capture can fail; the window pointerup listener still ends the gesture.
  }
  if (showSpeed.value) placeOverlay(event.clientX, event.clientY, startSpeed);
  emit("press");
}

function onPointerMove(event: PointerEvent) {
  if (!showSpeed.value || event.pointerId !== pointerId) return;
  const dy = event.clientY - startY;
  if (!dragged && Math.abs(dy) < DEAD_ZONE_PX) return;
  dragged = true;
  latest = clampSpeed(startSpeed - dy / PIXELS_PER_FULL_SCALE);
  placeOverlay(event.clientX, event.clientY, latest);
  emitSpeed(latest);
  event.preventDefault();
}

function finish(event: PointerEvent | null, sendFinal: boolean) {
  if (pointerId === null) return;
  if (event && event.pointerId !== pointerId) return;
  pointerId = null;
  dragging.value = false;
  overlay.value = null;
  unbindWindowRelease();
  if (!released) {
    released = true;
    if (sendFinal && dragged) emitSpeed(latest, true);
    emit("release");
  }
}

function onPointerUp(event: PointerEvent) {
  finish(event, true);
}

function onPointerCancel(event: PointerEvent) {
  finish(event, true);
}

function onLostCapture(event: PointerEvent) {
  if (event.buttons !== 0) return;
  finish(event, true);
}

function onKeyDown(event: KeyboardEvent) {
  if (props.disabled || event.repeat || pointerId !== null) return;
  if (event.key !== " " && event.key !== "Enter") return;
  event.preventDefault();
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  pointerId = -1;
  startSpeed = Number(props.speed) || SPEED_MIN;
  latest = startSpeed;
  dragged = false;
  released = false;
  dragging.value = true;
  placeOverlay(rect.left + rect.width / 2, rect.top + rect.height / 2, startSpeed);
  emit("press");
}

function onKeyUp(event: KeyboardEvent) {
  if (pointerId !== -1) return;
  if (event.key !== " " && event.key !== "Enter") return;
  event.preventDefault();
  finish(null, true);
}

function onBlur() {
  if (pointerId !== null) finish(null, true);
}

function onVisibility() {
  if (document.visibilityState === "hidden" && pointerId !== null) finish(null, true);
}

function onPageHide() {
  if (pointerId !== null) finish(null, true);
}

function onContextMenu(event: Event) {
  event.preventDefault();
}

watch(
  () => props.disabled,
  (disabled) => {
    if (disabled && pointerId !== null) finish(null, true);
  },
);

onMounted(() => {
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pagehide", onPageHide);
});

onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", onVisibility);
  window.removeEventListener("pagehide", onPageHide);
  unbindWindowRelease();
  if (pointerId !== null) finish(null, false);
});
</script>

<template>
  <button
    type="button"
    class="fx-btn fx-hold"
    :class="{ active, 'fx-hold--drag': dragging }"
    :disabled="disabled"
    :aria-pressed="active"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerCancel"
    @lostpointercapture="onLostCapture"
    @keydown="onKeyDown"
    @keyup="onKeyUp"
    @blur="onBlur"
    @contextmenu="onContextMenu"
  >
    <span class="fx-btn__label">{{ label }}</span>
    <span
      v-if="hint"
      class="fx-btn__hint"
    >{{ hint }}</span>
    <Teleport to="body">
      <span
        v-if="overlay && showSpeed"
        class="fx-speed-overlay"
        :style="{ left: `${overlay.left}px`, top: `${overlay.top}px` }"
      >
        <span
          class="fx-speed-overlay__thumb"
          :style="{ height: `${Math.round(overlay.speed * 100)}%` }"
        />
        <span class="fx-speed-overlay__value">{{ Math.round(overlay.speed * 100) }}%</span>
      </span>
    </Teleport>
  </button>
</template>
