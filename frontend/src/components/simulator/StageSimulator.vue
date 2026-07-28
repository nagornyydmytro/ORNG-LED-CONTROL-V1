<script setup lang="ts">
import type { SimulatorView } from "../../vite-env";

defineProps<{
  simulator: SimulatorView | null;
  frame: number[];
  enginePresetId: string;
  presetTimeS: number;
  episodeIndex: number;
  episodeCount?: number;
  cycleDurationS?: number;
  blackout: boolean;
  strobeHeld: boolean;
  whiteHitActive: boolean;
  faceOn: boolean;
  previewSpeed: number;
  disabled?: boolean;
  compact?: boolean;
}>();

const emit = defineEmits<{
  "update:previewSpeed": [value: number];
}>();

const SPEED_OPTIONS = [1, 5, 10, 30, 60];

function parStyle(par: {
  r: number;
  g: number;
  b: number;
  w: number;
  intensity: number;
}): Record<string, string> {
  const mix = Math.min(1, par.w * 0.65);
  const r = Math.round((par.r * (1 - mix) + mix) * 255 * par.intensity);
  const g = Math.round((par.g * (1 - mix) + mix) * 255 * par.intensity);
  const b = Math.round((par.b * (1 - mix) + mix) * 255 * par.intensity);
  const glow = Math.max(0.12, par.intensity);
  return {
    background: `rgb(${r} ${g} ${b})`,
    boxShadow: `0 0 ${1.4 * glow}rem rgb(${r} ${g} ${b} / ${0.45 * glow})`,
    opacity: String(0.35 + 0.65 * par.intensity),
  };
}

function segmentStyle(level: number, dimmer: number): Record<string, string> {
  const v = level * dimmer;
  return {
    opacity: String(0.15 + 0.85 * v),
    background: `linear-gradient(90deg, #ff6a00, #ffd28a)`,
    filter: `brightness(${0.45 + 0.9 * v})`,
  };
}

function beamTransform(pan: number, tilt: number): string {
  const yaw = (pan - 0.5) * 70;
  const pitch = (tilt - 0.5) * 40;
  return `rotate(${yaw}deg) skewY(${pitch * 0.15}deg)`;
}

function sortedByOrder<T extends { order?: number | null; side: string; id: string }>(
  items: T[],
): T[] {
  return [...items].sort((a, b) => {
    const oa = a.order ?? 999;
    const ob = b.order ?? 999;
    if (oa !== ob) return oa - ob;
    return a.id.localeCompare(b.id);
  });
}

function sortedBeams<T extends { order?: number | null; side: string; id: string }>(
  items: T[],
): T[] {
  // Audience left/right: Beam Left must render in the left column.
  const sideRank = (side: string) => (side === "left" ? 0 : side === "right" ? 1 : 2);
  return [...items].sort((a, b) => {
    const sideDiff = sideRank(a.side) - sideRank(b.side);
    if (sideDiff !== 0) return sideDiff;
    return a.id.localeCompare(b.id);
  });
}

function formatCycle(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60)
    .toString()
    .padStart(2, "0");
  const s = (total % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function onSpeed(value: string) {
  emit("update:previewSpeed", Number(value));
}
</script>

<template>
  <section
    class="simulator"
    aria-label="Симулятор сцени"
  >
    <header class="sim-head">
      <div>
        <h2>Симулятор</h2>
        <p class="sim-meta">
          {{ enginePresetId }} · епізод {{ episodeIndex + 1 }}/{{ episodeCount ?? 10 }} ·
          {{ presetTimeS.toFixed(1) }} с / {{ formatCycle(cycleDurationS ?? 180) }} · ×{{ previewSpeed }}
        </p>
      </div>
      <label class="speed">
        Швидкість preview
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
    </header>

    <div
      class="overlays"
      aria-live="polite"
    >
      <span
        class="chip"
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
        :class="{ on: faceOn }"
      >Face</span>
      <span
        v-if="simulator?.blackout_visual"
        class="chip on"
      >Кадр = 0</span>
    </div>

    <div
      class="stage"
      :class="{ blackout: blackout || simulator?.blackout_visual }"
    >
      <div class="row faces">
        <div
          v-for="face in sortedByOrder(simulator?.faces ?? [])"
          :key="face.id"
          class="fixture face"
          :data-id="face.id"
          :title="face.label"
        >
          <div
            class="lamp"
            :style="parStyle(face)"
          />
          <span>{{ face.label }}</span>
        </div>
      </div>

      <div class="row bars">
        <div
          v-for="bar in sortedByOrder(simulator?.bars ?? [])"
          :key="bar.id"
          class="fixture bar"
          :data-id="bar.id"
          :data-side="bar.side"
          :title="bar.label"
        >
          <div class="segments">
            <i
              v-for="(seg, idx) in bar.segments"
              :key="idx"
              :style="segmentStyle(seg, bar.dimmer)"
            />
          </div>
          <span>{{ bar.label }} · {{ bar.side }}</span>
        </div>
      </div>

      <div class="row pars">
        <div
          v-for="par in sortedByOrder(simulator?.pars ?? [])"
          :key="par.id"
          class="fixture par"
          :data-id="par.id"
          :data-side="par.side"
          :data-ring="par.ring"
          :title="`${par.label} (${par.side}/${par.ring})`"
        >
          <div
            class="lamp"
            :style="parStyle(par)"
          />
          <span>{{ par.label }}</span>
        </div>
      </div>

      <div class="row beams">
        <div
          v-for="beam in sortedBeams(simulator?.beams ?? [])"
          :key="beam.id"
          class="fixture beam"
          :data-id="beam.id"
          :data-side="beam.side"
          :title="beam.label"
        >
          <div
            class="beam-head"
            :style="{
              opacity: String(0.25 + 0.75 * beam.dimmer),
              transform: beamTransform(beam.pan, beam.tilt),
            }"
          >
            <div class="beam-ray" />
          </div>
          <span>{{ beam.label }} · {{ beam.side }}</span>
        </div>
      </div>
    </div>

    <details class="inspector">
      <summary>
        Інспектор каналів (512) · ненульових {{ simulator?.nonzero_channels ?? 0 }}
      </summary>
      <div
        class="channels"
        data-testid="channel-inspector"
      >
        <div
          v-for="(value, index) in frame"
          :key="index"
          class="ch"
          :class="{ lit: value > 0 }"
          :title="`Ch ${index + 1}: ${value}`"
        >
          <span class="n">{{ index + 1 }}</span>
          <span class="v">{{ value }}</span>
        </div>
      </div>
    </details>
  </section>
</template>
