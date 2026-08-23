<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";

const props = defineProps<{
  held: boolean;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  press: [];
  release: [];
}>();

const pressing = ref(false);
const buttonRef = ref<HTMLButtonElement | null>(null);
let activePointerId: number | null = null;

function start(event: PointerEvent) {
  if (props.disabled) return;
  if (pressing.value) return;
  event.preventDefault();
  pressing.value = true;
  activePointerId = typeof event.pointerId === "number" ? event.pointerId : 1;
  try {
    if (typeof buttonRef.value?.setPointerCapture === "function") {
      buttonRef.value.setPointerCapture(activePointerId);
    }
  } catch {
    // Capture can fail on synthetic events — still emit press.
  }
  emit("press");
}

function stop(event?: PointerEvent) {
  if (!pressing.value && !props.held) return;
  if (
    event &&
    activePointerId !== null &&
    typeof event.pointerId === "number" &&
    event.pointerId !== activePointerId &&
    event.type !== "lostpointercapture"
  ) {
    return;
  }
  const el = buttonRef.value;
  if (
    activePointerId !== null &&
    el &&
    typeof el.hasPointerCapture === "function" &&
    el.hasPointerCapture(activePointerId)
  ) {
    try {
      el.releasePointerCapture(activePointerId);
    } catch {
      // already released
    }
  }
  activePointerId = null;
  if (!pressing.value && !props.held) return;
  pressing.value = false;
  emit("release");
}

function onKeyDown(event: KeyboardEvent) {
  if (event.repeat) return;
  if (event.key === " " || event.key === "Enter") {
    event.preventDefault();
    if (pressing.value) return;
    pressing.value = true;
    emit("press");
  }
}

function onKeyUp(event: KeyboardEvent) {
  if (event.key === " " || event.key === "Enter") stop();
}

onBeforeUnmount(() => {
  stop();
});
</script>

<template>
  <button
    ref="buttonRef"
    type="button"
    class="strobe-btn"
    :class="{ held: held || pressing }"
    :disabled="disabled"
    :aria-pressed="held || pressing"
    @pointerdown="start"
    @pointerup="stop"
    @pointercancel="stop"
    @lostpointercapture="stop"
    @keydown="onKeyDown"
    @keyup="onKeyUp"
  >
    STROBE
    <span class="hint">утримуйте</span>
  </button>
</template>
