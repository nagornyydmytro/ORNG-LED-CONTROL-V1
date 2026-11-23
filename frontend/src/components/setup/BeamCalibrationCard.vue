<script setup lang="ts">
import type { BeamCalibrationBeamView, BeamCalibrationSessionView } from "../../vite-env";
import { BEAM_NUMERIC_FIELDS, type BeamDraft } from "./beamDraft";

interface BeamPatchFixture {
  id: string;
  label: string;
}

const props = defineProps<{
  fixture: BeamPatchFixture;
  draft: BeamDraft;
  errors?: Record<string, string>;
  beamState: BeamCalibrationBeamView | null;
  session: BeamCalibrationSessionView | null;
  playing?: boolean;
  busy?: boolean;
  blackout?: boolean;
}>();

const emit = defineEmits<{
  edit: [draft: BeamDraft];
  play: [];
  stop: [];
}>();

function update(partial: Partial<BeamDraft>) {
  emit("edit", { ...props.draft, ...partial });
}

function sliderValue(raw: string, min: number): number {
  const value = Number(raw);
  return Number.isFinite(value) ? value : min;
}
</script>

<template>
  <article class="card beam-card">
    <div class="card__head">
      <h3>{{ fixture.label }}</h3>
      <button
        v-if="playing"
        type="button"
        class="action-btn"
        :disabled="busy"
        @click="emit('stop')"
      >
        Зупинити
      </button>
      <button
        v-else
        type="button"
        class="action-btn"
        :disabled="busy || !blackout"
        title="Доступно лише під blackout"
        @click="emit('play')"
      >
        Відтворити на обладнанні
      </button>
    </div>

    <div class="row-actions">
      <button
        type="button"
        class="action-btn"
        :class="{ active: draft.pan_invert }"
        :aria-pressed="draft.pan_invert"
        @click="update({ pan_invert: !draft.pan_invert })"
      >
        Pan інверт
      </button>
      <button
        type="button"
        class="action-btn"
        :class="{ active: draft.tilt_invert }"
        :aria-pressed="draft.tilt_invert"
        @click="update({ tilt_invert: !draft.tilt_invert })"
      >
        Tilt інверт
      </button>
    </div>

    <div class="form-grid">
      <div
        v-for="fdef in BEAM_NUMERIC_FIELDS"
        :key="fdef.key"
        class="field beam-card__slider"
      >
        <span>{{ fdef.label }}</span>
        <input
          type="range"
          :min="fdef.min"
          :max="fdef.max"
          :step="fdef.step"
          :value="sliderValue(draft[fdef.key], fdef.min)"
          @input="update({ [fdef.key]: ($event.target as HTMLInputElement).value })"
        >
        <input
          type="text"
          inputmode="decimal"
          :value="draft[fdef.key]"
          :aria-label="fdef.label"
          @input="update({ [fdef.key]: ($event.target as HTMLInputElement).value })"
        >
        <p
          v-if="errors?.[fdef.key]"
          class="hint beam-card__status--error"
          role="alert"
        >
          {{ errors[fdef.key] }}
        </p>
      </div>
    </div>
  </article>
</template>
