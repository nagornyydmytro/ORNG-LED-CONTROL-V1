<script setup lang="ts">
import { computed } from "vue";
import { formatClock } from "../../lib/presets";
import type { EngineState, OutputState } from "../../vite-env";

const props = defineProps<{
  engine: EngineState | null;
  output: OutputState | null;
}>();

const episodeLabel = computed(() => {
  if (!props.engine) return "—";
  const count = props.engine.episode_count ?? 10;
  return `Епізод ${props.engine.episode_index + 1}/${count} · ${formatClock(props.engine.episode_time_s)}`;
});

const cycleLabel = computed(() => {
  if (!props.engine) return "—";
  const total = props.engine.cycle_duration_s ?? 180;
  return `Цикл ${formatClock(props.engine.preset_time_s)} / ${formatClock(total)}`;
});

const outputLabel = computed(() => {
  if (!props.engine) return "—";
  return props.engine.blackout ? "BLACKOUT" : "Світло";
});
</script>

<template>
  <section
    class="status-bar"
    aria-live="polite"
  >
    <div>
      <span class="k">Активний</span>
      <strong>{{ engine?.preset_id ?? "—" }}</strong>
    </div>
    <div>
      <span class="k">Час</span>
      <strong>{{ cycleLabel }}</strong>
    </div>
    <div>
      <span class="k">Епізод</span>
      <strong>{{ episodeLabel }}</strong>
    </div>
    <div>
      <span class="k">Вивід</span>
      <strong>{{ outputLabel }}</strong>
    </div>
  </section>
</template>
