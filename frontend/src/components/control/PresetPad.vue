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
</script>

<template>
  <section
    class="preset-pad"
    aria-label="Пресет"
  >
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
