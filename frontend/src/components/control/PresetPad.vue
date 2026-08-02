<script setup lang="ts">
import { computed } from "vue";
import type { PresetInfo } from "../../vite-env";

const props = defineProps<{
  activeId: string | null;
  availableIds: Set<string>;
  /** Full catalog from backend (builtin + custom). */
  presets?: PresetInfo[];
  /** Exactly 9 preset ids shown on the home pad (after NONE). */
  padPresetIds?: string[];
  /** Visual highlight without activating (e.g. «Відкрити на пульті»). */
  highlightId?: string | null;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  select: [id: string];
}>();

const NONE_PRESET = { id: "NONE", label: "Без пресету" } as const;

const padPresets = computed(() => {
  const fromApi = props.presets ?? [];
  const byId = new Map(fromApi.map((p) => [p.id, p]));
  for (const id of props.availableIds) {
    if (id === "NONE" || byId.has(id)) continue;
    byId.set(id, { id, label: id, builtin: !id.startsWith("C") });
  }

  const slots = (props.padPresetIds ?? []).slice(0, 9);
  return slots.map((id, index) => {
    const item = byId.get(id);
    return {
      id,
      label: item?.label ?? id,
      builtin: item?.builtin ?? true,
      slot: index + 1,
    };
  });
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
      :title="'Клавіша 0'"
      @click="emit('select', NONE_PRESET.id)"
    >
      <span class="preset-id">0 · {{ NONE_PRESET.id }}</span>
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
      :title="`Клавіша ${preset.slot}`"
      @click="emit('select', preset.id)"
    >
      <span class="preset-id">{{ preset.slot }} · {{ preset.id }}</span>
      <span class="preset-label">{{ preset.label }}</span>
    </button>
  </section>
</template>
