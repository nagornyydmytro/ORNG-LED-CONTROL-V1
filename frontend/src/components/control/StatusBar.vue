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
  return `Епізод ${props.engine.episode_index + 1}/10 · ${formatClock(props.engine.episode_time_s)}`;
});

const cycleLabel = computed(() => {
  if (!props.engine) return "—";
  return `Цикл ${formatClock(props.engine.preset_time_s)} / 03:00`;
});

const outputLabel = computed(() => {
  if (!props.output) return "невідомо";
  const mode = props.output.transport.toUpperCase();
  if (props.output.armed) return `${mode} · armed`;
  return `${mode} · не активний`;
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
      <span class="k">Output</span>
      <strong>{{ outputLabel }}</strong>
    </div>
  </section>
</template>
