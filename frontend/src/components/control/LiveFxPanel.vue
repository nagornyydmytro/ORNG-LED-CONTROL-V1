<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import MomentaryButton from "./MomentaryButton.vue";
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
}>();

const warnings = computed(() => props.liveEffects?.warnings ?? []);
</script>

<template>
  <section
    class="card fx-panel"
    aria-label="Живі ефекти"
  >
    <header class="card__head">
      <h2>Живі ефекти</h2>
      <p class="card__sub">
        Утримання = momentary. Швидкість зберігається. Zoom-енкодер під час ефекту
        крутить швидкість (0 = вимкнено, макс = суцільне світло).
      </p>
    </header>

    <div class="fx-grid">
      <MomentaryButton
        label="STROBE"
        hint="білий · утримуйте · ⌫"
        variant="danger"
        :active="strobeHeld"
        :disabled="disabled"
        @press="emit('strobePress')"
        @release="emit('strobeRelease')"
      />
      <MomentaryButton
        label="SWEEP"
        hint="зліва→направо · Enter"
        :active="sweepActive"
        :disabled="disabled"
        @press="emit('sweepPress')"
        @release="emit('sweepRelease')"
      />
      <MomentaryButton
        label="V-SWEEP"
        hint="знизу→вгору · сегменти"
        :active="verticalSweepActive"
        :disabled="disabled"
        @press="emit('verticalSweepPress')"
        @release="emit('verticalSweepRelease')"
      />
      <button
        type="button"
        class="fx-btn"
        :class="{ active: colorHitActive }"
        :disabled="disabled"
        @click="emit('colorHit')"
      >
        <span class="fx-btn__label">COLOR HIT</span>
        <span class="fx-btn__hint">контрастний колір</span>
      </button>
    </div>

    <div class="fx-speeds">
      <label class="field">
        <span>Швидкість Strobe {{ Math.round(strobeSpeed * 100) }}%</span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          :value="strobeSpeed"
          :disabled="disabled"
          @input="emit('update:strobeSpeed', Number(($event.target as HTMLInputElement).value))"
        >
      </label>
      <label class="field">
        <span>Швидкість Sweep {{ Math.round(sweepSpeed * 100) }}%</span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          :value="sweepSpeed"
          :disabled="disabled"
          @input="emit('update:sweepSpeed', Number(($event.target as HTMLInputElement).value))"
        >
      </label>
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
      <RouterLink
        class="btn btn--sm"
        to="/channels"
      >
        Відкрити налаштування каналів
      </RouterLink>
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
