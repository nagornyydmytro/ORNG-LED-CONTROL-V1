<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { SceneInfo } from "../../vite-env";

const props = defineProps<{
  sceneId: string;
  sceneName: string;
  scenes: SceneInfo[];
  disabled?: boolean;
  busy?: boolean;
}>();

const emit = defineEmits<{
  switch: [sceneId: string];
}>();

/** Local select value — reset if parent cancels / rejects switch. */
const draftId = ref(props.sceneId);

watch(
  () => props.sceneId,
  (id) => {
    draftId.value = id;
  },
);

watch(
  () => props.busy,
  (busy, wasBusy) => {
    if (wasBusy && !busy && draftId.value !== props.sceneId) {
      draftId.value = props.sceneId;
    }
  },
);

const selectable = computed(() =>
  props.scenes.filter((s) => !s.error).length > 0
    ? props.scenes.filter((s) => !s.error)
    : [{ scene_id: props.sceneId, name: props.sceneName, active: true }],
);

function onChange(event: Event) {
  const next = (event.target as HTMLSelectElement).value;
  if (!next || next === props.sceneId) {
    draftId.value = props.sceneId;
    return;
  }
  // Sync draft so a cancelled confirm can re-bind :value back to the active scene.
  draftId.value = next;
  const label =
    selectable.value.find((s) => s.scene_id === next)?.name ?? next;
  const confirmed = window.confirm(
    `Перемкнути сцену на «${label}»?\n\n` +
      "Вивід перейде в Blackout і буде Disarm.\n" +
      "Поточні утримання (strobe/sweep) скинуться.\n\n" +
      "Продовжити?",
  );
  if (!confirmed) {
    draftId.value = props.sceneId;
    return;
  }
  emit("switch", next);
}
</script>

<template>
  <label class="scene-selector">
    <span class="scene-selector__k">Сцена</span>
    <select
      class="scene-selector__select"
      :value="draftId"
      :disabled="disabled || busy"
      :aria-label="`Активна сцена: ${sceneName}`"
      :title="sceneName"
      @change="onChange"
    >
      <option
        v-for="scene in selectable"
        :key="scene.scene_id"
        :value="scene.scene_id"
      >
        {{ scene.name }}
      </option>
    </select>
  </label>
</template>
