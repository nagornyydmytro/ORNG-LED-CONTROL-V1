<script setup lang="ts">
import { PRESET_CATALOG } from "../../lib/presets";

defineProps<{
  activeId: string | null;
  availableIds: Set<string>;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  select: [id: string];
}>();

const NONE_PRESET = { id: "NONE", label: "Без пресету" } as const;
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
      v-for="preset in PRESET_CATALOG"
      :key="preset.id"
      type="button"
      class="preset-btn"
      :class="{
        active: activeId === preset.id,
        unavailable: !availableIds.has(preset.id),
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
