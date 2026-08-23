<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";

const props = defineProps<{
  label: string;
  hint?: string;
  active: boolean;
  disabled?: boolean;
  variant?: "danger" | "accent" | "neutral";
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
  activePointerId = event.pointerId;
  try {
    buttonRef.value?.setPointerCapture(event.pointerId);
  } catch {
    // Capture can fail on some synthetic events — still emit press.
  }
  emit("press");
}

function stop(event?: PointerEvent) {
  if (!pressing.value && !props.active) return;
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
  if (!pressing.value && !props.active) return;
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

// Releasing on unmount / hidden tab is a safety requirement, not a nicety.
onBeforeUnmount(() => stop());
</script>

<template>
  <button
    ref="buttonRef"
    type="button"
    class="fx-btn fx-btn--momentary"
    :class="[`fx-btn--${variant ?? 'neutral'}`, { active: active || pressing }]"
    :disabled="disabled"
    :aria-pressed="active || pressing"
    @pointerdown="start"
    @pointerup="stop"
    @pointercancel="stop"
    @lostpointercapture="stop"
    @keydown="onKeyDown"
    @keyup="onKeyUp"
  >
    <span class="fx-btn__label">{{ label }}</span>
    <span
      v-if="hint"
      class="fx-btn__hint"
    >{{ hint }}</span>
  </button>
</template>
