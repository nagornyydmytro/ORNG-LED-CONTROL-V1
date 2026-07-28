<script setup lang="ts">
import { computed } from "vue";
import { PRESET_CATALOG } from "../../lib/presets";
import type { PresetInfo } from "../../vite-env";

const props = defineProps<{
  activeId: string | null;
  availableIds: Set<string>;
  /** Full catalog from backend (builtin + custom). */
  presets?: PresetInfo[];
  /** Visual highlight without activating (e.g. «Відкрити на пульті»). */
  highlightId?: string | null;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  select: [id: string];
}>();

const NONE_PRESET = { id: "NONE", label: "Без пресету" } as const;

const staffOrder = PRESET_CATALOG.map((p) => p.id);

const padPresets = computed(() => {
  const fromApi = props.presets ?? [];
  const byId = new Map(fromApi.map((p) => [p.id, p]));
  // AppState.presets may include custom ids before /api/presets labels refresh.
  for (const id of props.availableIds) {
    if (id === "NONE" || byId.has(id)) continue;
    byId.set(id, { id, label: id, builtin: !staffOrder.includes(id as never) ? false : true });
  }
  const staffFallback = PRESET_CATALOG.map((p) => ({
    id: p.id,
    label: p.label,
    builtin: true,
  }));
  const ordered: Array<{ id: string; label: string; builtin?: boolean }> = [];
  const seen = new Set<string>();

  for (const id of staffOrder) {
    const item = byId.get(id) ?? staffFallback.find((s) => s.id === id);
    if (item && item.id !== "NONE") {
      ordered.push({ id: item.id, label: item.label, builtin: item.builtin ?? true });
      seen.add(item.id);
    }
  }
  const customs = [...byId.values()]
    .filter((p) => p.id !== "NONE" && !seen.has(p.id))
    .slice()
    .sort((a, b) => a.id.localeCompare(b.id));
  for (const item of customs) {
    ordered.push({ id: item.id, label: item.label, builtin: item.builtin ?? false });
  }
  return ordered;
});
</script>

<template>
  <section
    class="preset-pad"
    aria-label="Пресет"
  >
    <button
      type="button"
      class="preset-btn"
      :class="{
        active: activeId === NONE_PRESET.id,
        highlight: highlightId === NONE_PRESET.id && activeId !== NONE_PRESET.id,
        unavailable: !availableIds.has(NONE_PRESET.id),
      }"
      :disabled="disabled || !availableIds.has(NONE_PRESET.id)"
      :aria-pressed="activeId === NONE_PRESET.id"
      @click="emit('select', NONE_PRESET.id)"
    >
      <span class="preset-id">{{ NONE_PRESET.id }}</span>
      <span class="preset-label">{{ NONE_PRESET.label }}</span>
    </button>
    <button
      v-for="preset in padPresets"
      :key="preset.id"
      type="button"
      class="preset-btn"
      :class="{
        active: activeId === preset.id,
        highlight: highlightId === preset.id && activeId !== preset.id,
        unavailable: !availableIds.has(preset.id),
        custom: preset.builtin === false,
      }"
      :disabled="disabled || !availableIds.has(preset.id)"
      :aria-pressed="activeId === preset.id"
      @click="emit('select', preset.id)"
    >
      <span class="preset-id">{{ preset.id }}</span>
      <span class="preset-label">{{ preset.label }}</span>
    </button>
  </section>
</template>
