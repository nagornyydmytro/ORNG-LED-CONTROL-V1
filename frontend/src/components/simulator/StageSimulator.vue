<script setup lang="ts">
import { computed, ref } from "vue";
import type { SimulatorView, StageLayout } from "../../vite-env";
import { formatClock } from "../../lib/stage";
import ChannelInspector from "./ChannelInspector.vue";
import StageCanvas from "./StageCanvas.vue";

const props = withDefaults(defineProps<{
  layout: StageLayout | null;
  /** Newest simulator view, read outside reactivity by the canvas loop. */
  view: () => SimulatorView | null;
  frame?: () => number[];
  nonzeroChannels?: number;
  enginePresetId?: string;
  presetTimeS?: number;
  episodeIndex?: number;
  episodeCount?: number;
  cycleDurationS?: number;
  blackout?: boolean;
  strobeHeld?: boolean;
  whiteHitActive?: boolean;
  dropActive?: boolean;
  colorHitActive?: boolean;
  sweepActive?: boolean;
  faceOn?: boolean;
  previewSpeed?: number;
  disabled?: boolean;
  showInspector?: boolean;
  title?: string;
  badge?: string | null;
}>(), {
  frame: undefined,
  nonzeroChannels: 0,
  enginePresetId: "—",
  presetTimeS: 0,
  episodeIndex: 0,
  episodeCount: 10,
  cycleDurationS: 180,
  previewSpeed: undefined,
  showInspector: true,
  title: "Симулятор",
  badge: null,
});

const emit = defineEmits<{
  "update:previewSpeed": [value: number];
}>();

const SPEED_OPTIONS = [1, 5, 10, 30, 60];
const showCables = ref(true);

const meta = computed(() => {
  const episode = (props.episodeIndex ?? 0) + 1;
  const count = props.episodeCount ?? 10;
  const time = (props.presetTimeS ?? 0).toFixed(1);
  const cycle = formatClock(props.cycleDurationS ?? 180);
  return `${props.enginePresetId ?? "—"} · епізод ${episode}/${count} · ${time} с / ${cycle} · ×${props.previewSpeed ?? 1}`;
});

function onSpeed(value: string) {
  emit("update:previewSpeed", Number(value));
}
</script>

<template>
  <section
    class="simulator card"
    aria-label="Симулятор сцени"
  >
    <header class="simulator__head">
      <div>
        <h2>{{ title ?? "Симулятор" }}</h2>
        <p class="simulator__meta">
          {{ meta }}
        </p>
      </div>
      <div class="simulator__tools">
        <span
          v-if="badge"
          class="badge badge--muted"
        >{{ badge }}</span>
        <label class="switch">
          <input
            v-model="showCables"
            type="checkbox"
          >
          <span>Показувати з'єднання</span>
        </label>
        <label
          v-if="previewSpeed !== undefined"
          class="field field--inline"
        >
          <span>Швидкість preview</span>
          <select
            :value="previewSpeed"
            :disabled="disabled"
            @change="onSpeed(($event.target as HTMLSelectElement).value)"
          >
            <option
              v-for="opt in SPEED_OPTIONS"
              :key="opt"
              :value="opt"
            >
              ×{{ opt }}
            </option>
          </select>
        </label>
      </div>
    </header>

    <div
      class="chips"
      aria-live="polite"
    >
      <span
        class="chip chip--danger"
        :class="{ on: blackout }"
      >Blackout</span>
      <span
        class="chip"
        :class="{ on: strobeHeld }"
      >Strobe</span>
      <span
        class="chip"
        :class="{ on: whiteHitActive }"
      >White Hit</span>
      <span
        class="chip"
        :class="{ on: dropActive }"
      >Drop</span>
      <span
        class="chip"
        :class="{ on: colorHitActive }"
      >Color Hit</span>
      <span
        class="chip"
        :class="{ on: sweepActive }"
      >Sweep</span>
      <span
        class="chip"
        :class="{ on: faceOn }"
      >Face</span>
      <span
        v-if="(nonzeroChannels ?? 0) === 0"
        class="chip on"
      >Кадр = 0</span>
    </div>

    <div
      class="stage"
      :class="{ blackout: blackout || (nonzeroChannels ?? 0) === 0 }"
    >
      <StageCanvas
        :layout="layout"
        :view="view"
        :show-cables="showCables"
      />
    </div>

    <ChannelInspector
      v-if="showInspector && frame"
      :frame="frame"
      :nonzero="nonzeroChannels ?? 0"
    />
  </section>
</template>
