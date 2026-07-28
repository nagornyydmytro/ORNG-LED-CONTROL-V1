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

function start(event: Event) {
  if (props.disabled) return;
  event.preventDefault();
  if (pressing.value) return;
  pressing.value = true;
  emit("press");
}

function stop() {
  if (!pressing.value && !props.active) return;
  pressing.value = false;
  emit("release");
}

function onKeyDown(event: KeyboardEvent) {
  if (event.repeat) return;
  if (event.key === " " || event.key === "Enter") start(event);
}

function onKeyUp(event: KeyboardEvent) {
  if (event.key === " " || event.key === "Enter") stop();
}

// Releasing on unmount / hidden tab is a safety requirement, not a nicety.
onBeforeUnmount(stop);
</script>

<template>
  <button
    type="button"
    class="fx-btn fx-btn--momentary"
    :class="[`fx-btn--${variant ?? 'neutral'}`, { active: active || pressing }]"
    :disabled="disabled"
    :aria-pressed="active || pressing"
    @pointerdown="start"
    @pointerup="stop"
    @pointercancel="stop"
    @pointerleave="stop"
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
