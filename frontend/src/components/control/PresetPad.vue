<script setup lang="ts">
import { computed } from "vue";
import { presetButtonLabel } from "../../lib/presetLabel";
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
  episodeDisabled?: boolean;
}>();

const emit = defineEmits<{
  select: [id: string];
  prevEpisode: [];
  nextEpisode: [];
}>();

const NONE_PRESET = { id: "NONE", label: "NONE" } as const;

const padPresets = computed(() => {
  const fromApi = props.presets ?? [];
  const byId = new Map(fromApi.map((p) => [p.id, p]));
  for (const id of props.availableIds) {
    if (id === "NONE" || byId.has(id)) continue;
    byId.set(id, { id, label: id, builtin: !id.startsWith("C") });
  }

  const slots = (props.padPresetIds ?? []).slice(0, 9);
  return slots.map((id) => {
    const item = byId.get(id);
    return {
      id,
      label: presetButtonLabel(id, item?.label ?? id),
      builtin: item?.builtin ?? true,
    };
  });
});
</script>

<template>
  <section aria-label="Пресет">
    <div class="preset-pad">
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
        <span class="preset-id">{{ preset.label }}</span>
      </button>
    </div>
    <div class="preset-none-row">
      <button
        type="button"
        class="preset-btn preset-btn--arrow"
        aria-label="Попередній епізод"
        :disabled="disabled || episodeDisabled"
        @click="emit('prevEpisode')"
      >
        <svg class="preset-arrow" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M16.5 5.5 7.5 12 16.5 18.5" />
        </svg>
      </button>
      <button
        type="button"
        class="preset-btn preset-btn--none"
        :class="{
          active: activeId === NONE_PRESET.id,
          highlight: highlightId === NONE_PRESET.id && activeId !== NONE_PRESET.id,
          unavailable: !availableIds.has(NONE_PRESET.id),
        }"
        :disabled="disabled || !availableIds.has(NONE_PRESET.id)"
        :aria-pressed="activeId === NONE_PRESET.id"
        @click="emit('select', NONE_PRESET.id)"
      >
        <span class="preset-id">NONE</span>
      </button>
      <button
        type="button"
        class="preset-btn preset-btn--arrow"
        aria-label="Наступний епізод"
        :disabled="disabled || episodeDisabled"
        @click="emit('nextEpisode')"
      >
        <svg class="preset-arrow" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M7.5 5.5 16.5 12 7.5 18.5" />
        </svg>
      </button>
    </div>
  </section>
</template>
