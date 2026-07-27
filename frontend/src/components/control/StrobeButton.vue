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

function start(event: Event) {
  if (props.disabled) return;
  event.preventDefault();
  if (pressing.value) return;
  pressing.value = true;
  emit("press");
}

function stop() {
  if (!pressing.value && !props.held) return;
  pressing.value = false;
  emit("release");
}

function onKeyDown(event: KeyboardEvent) {
  if (event.repeat) return;
  if (event.key === " " || event.key === "Enter") {
    start(event);
  }
}

function onKeyUp(event: KeyboardEvent) {
  if (event.key === " " || event.key === "Enter") {
    stop();
  }
}

onBeforeUnmount(() => {
  stop();
});
</script>

<template>
  <button
    type="button"
    class="strobe-btn"
    :class="{ held: held || pressing }"
    :disabled="disabled"
    :aria-pressed="held || pressing"
    @pointerdown="start"
    @pointerup="stop"
    @pointercancel="stop"
    @pointerleave="stop"
    @keydown="onKeyDown"
    @keyup="onKeyUp"
  >
    STROBE
    <span class="hint">утримуйте</span>
  </button>
</template>
