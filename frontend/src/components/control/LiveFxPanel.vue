<script setup lang="ts">
import { computed } from "vue";
import FxHoldButton from "./FxHoldButton.vue";
import type { LiveEffectsState } from "../../vite-env";

const props = defineProps<{
  strobeHeld: boolean;
  sweepActive: boolean;
  verticalSweepActive: boolean;
  colorHitActive: boolean;
  strobeSpeed: number;
  sweepSpeed: number;
  liveEffects?: LiveEffectsState | null;
  disabled?: boolean;
  showColorHit?: boolean;
  faceOn?: boolean;
  lockTempo?: boolean;
}>();

const emit = defineEmits<{
  strobePress: [];
  strobeRelease: [];
  sweepPress: [];
  sweepRelease: [];
  verticalSweepPress: [];
  verticalSweepRelease: [];
  colorHit: [];
  "update:strobeSpeed": [value: number];
  "update:sweepSpeed": [value: number];
  face: [];
}>();

const warnings = computed(() => props.liveEffects?.warnings ?? []);
</script>

<template>
  <section
    class="fx-panel"
    aria-label="Ефекти"
  >
    <div class="fx-grid">
      <FxHoldButton
        label="STROBE"
        :active="strobeHeld"
        :disabled="disabled"
        :show-speed="!lockTempo"
        :speed="strobeSpeed"
        @press="emit('strobePress')"
        @release="emit('strobeRelease')"
        @speed="emit('update:strobeSpeed', $event)"
      />
      <FxHoldButton
        label="SWEEP H"
        :active="sweepActive"
        :disabled="disabled"
        :show-speed="!lockTempo"
        :speed="sweepSpeed"
        @press="emit('sweepPress')"
        @release="emit('sweepRelease')"
        @speed="emit('update:sweepSpeed', $event)"
      />
      <FxHoldButton
        label="SWEEP V"
        :active="verticalSweepActive"
        :disabled="disabled"
        :show-speed="!lockTempo"
        :speed="sweepSpeed"
        @press="emit('verticalSweepPress')"
        @release="emit('verticalSweepRelease')"
        @speed="emit('update:sweepSpeed', $event)"
      />
      <button
        type="button"
        class="fx-btn"
        :class="{ active: faceOn }"
        :aria-pressed="faceOn ?? false"
        :disabled="disabled"
        @click="emit('face')"
      >
        <span class="fx-btn__label">FACE</span>
      </button>
    </div>

    <div
      v-if="warnings.length"
      class="banner banner--warn fx-warnings"
      role="status"
    >
      <p
        v-for="(w, i) in warnings"
        :key="i"
      >
        {{ w }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.fx-speeds {
  display: grid;
  gap: 0.75rem;
  margin-top: 0.85rem;
}
</style>
