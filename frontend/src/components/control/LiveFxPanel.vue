<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import MomentaryButton from "./MomentaryButton.vue";
import type { LiveEffectsState } from "../../vite-env";

const props = defineProps<{
  strobeHeld: boolean;
  whiteHitActive: boolean;
  dropActive: boolean;
  colorHitActive: boolean;
  sweepActive: boolean;
  liveEffects?: LiveEffectsState | null;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  whiteHit: [];
  strobePress: [];
  strobeRelease: [];
  dropPress: [];
  dropRelease: [];
  colorHit: [];
  sweepHit: [];
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
        Самостійний шар поверх пресету. Утримання = momentary. Не залежить від того, які
        прилади вже горіли в епізоді.
      </p>
    </header>

    <div class="fx-grid">
      <MomentaryButton
        label="STROBE"
        hint="утримуйте"
        variant="danger"
        :active="strobeHeld"
        :disabled="disabled"
        @press="emit('strobePress')"
        @release="emit('strobeRelease')"
      />
      <MomentaryButton
        label="DROP"
        hint="утримуйте"
        variant="danger"
        :active="dropActive"
        :disabled="disabled"
        @press="emit('dropPress')"
        @release="emit('dropRelease')"
      />
      <button
        type="button"
        class="fx-btn fx-btn--accent"
        :class="{ active: whiteHitActive }"
        :disabled="disabled"
        @click="emit('whiteHit')"
      >
        <span class="fx-btn__label">WHITE HIT</span>
        <span class="fx-btn__hint">спалах</span>
      </button>
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
      <button
        type="button"
        class="fx-btn"
        :class="{ active: sweepActive }"
        :disabled="disabled"
        @click="emit('sweepHit')"
      >
        <span class="fx-btn__label">SWEEP HIT</span>
        <span class="fx-btn__hint">прохід зліва направо</span>
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
      <RouterLink
        class="btn btn--sm"
        to="/channels"
      >
        Відкрити налаштування каналів
      </RouterLink>
    </div>
  </section>
</template>
