<script setup lang="ts">
defineProps<{
  faceOn: boolean;
  faceBrightness: number;
  masterBrightness: number;
  whiteHitActive: boolean;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  "toggle-face": [];
  "update:faceBrightness": [value: number];
  "update:masterBrightness": [value: number];
  "white-hit": [];
}>();
</script>

<template>
  <section
    class="overlay-controls"
    aria-label="Швидкі дії"
  >
    <button
      type="button"
      class="action-btn"
      :class="{ on: faceOn }"
      :aria-pressed="faceOn"
      :disabled="disabled"
      @click="emit('toggle-face')"
    >
      DJ Face
    </button>

    <label class="slider-field">
      <span>Face brightness</span>
      <input
        type="range"
        min="0"
        max="1"
        step="0.01"
        :value="faceBrightness"
        :disabled="disabled || !faceOn"
        @input="emit('update:faceBrightness', Number(($event.target as HTMLInputElement).value))"
      >
    </label>

    <button
      type="button"
      class="action-btn hit"
      :class="{ pulse: whiteHitActive }"
      :disabled="disabled"
      @click="emit('white-hit')"
    >
      White Hit
    </button>

    <label class="slider-field">
      <span>Master brightness</span>
      <input
        type="range"
        min="0"
        max="1"
        step="0.01"
        :value="masterBrightness"
        :disabled="disabled"
        @input="emit('update:masterBrightness', Number(($event.target as HTMLInputElement).value))"
      >
    </label>
  </section>
</template>
