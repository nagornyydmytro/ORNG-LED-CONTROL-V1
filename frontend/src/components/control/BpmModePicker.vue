<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from "vue";
import { APP_STATE_KEY } from "../../composables/appStateKey";
import { beginBpmChange, bpmDesk, choiceKeyFor, finishBpmChange } from "../../lib/bpmIntent";
import {
  BPM_GROUPS,
  BPM_CHARACTERS,
  BPM_INTENSITIES,
  BPM_LEGACY_PALETTES,
  BPM_PALETTES,
  BPM_PERIODS,
  BPM_PRESET_MODES,
  PERIODS_FOR_CHARACTER,
  hasAutoDirector,
  type BpmCastId,
  type BpmGroupId,
  type BpmCharacterId,
  type BpmIntensityId,
  type BpmPaletteId,
  type BpmPeriodId,
} from "../../lib/bpmPalettes";
import type { BpmSyncMode, BpmSyncScope } from "../../vite-env";

const props = defineProps<{
  startExpanded?: boolean;
}>();

const api = inject(APP_STATE_KEY, null);
const extraOpen = ref(props.startExpanded === true);

function toggleExtra() {
  extraOpen.value = !extraOpen.value;
}

watch(
  () => props.startExpanded,
  (value) => {
    if (value) extraOpen.value = true;
  },
);
const choiceKey = computed(() =>
  choiceKeyFor({
    locationId: api?.state.value?.location_id,
    sceneId: api?.state.value?.scene_id,
  }),
);
const director = computed(() => hasAutoDirector(api?.state.value?.capabilities));
const desk = computed(() => bpmDesk(choiceKey.value, api?.state.value?.bpm));
const shown = computed(() => desk.value.choice);
const scope = computed(() => shown.value.scope);
const mode = computed(() => shown.value.mode);
const palette = computed(() => shown.value.palette);
const groups = computed(() => shown.value.groups);
const character = computed(() => shown.value.character);
const intensity = computed(() => shown.value.intensity);
const period = computed(() => shown.value.period);
const parRhythm = computed(() => shown.value.parRhythm);
const accent = computed(() => shown.value.accent);
const periodChoices = computed(() =>
  BPM_PERIODS.filter((item) => PERIODS_FOR_CHARACTER[character.value].includes(item.id)),
);
const palettes = computed(() => (director.value ? BPM_PALETTES : BPM_LEGACY_PALETTES));

type SyncPatch = {
  scope?: BpmSyncScope;
  mode?: BpmSyncMode;
  palette?: BpmPaletteId;
  cast?: BpmCastId;
  groups?: BpmGroupId[];
  excluded?: string[];
  character?: BpmCharacterId;
  intensity?: BpmIntensityId;
  period?: BpmPeriodId;
  parRhythm?: boolean;
  accent?: number;
};

let accentSend: number | null = null;
let accentFlight: { key: string; rev: string } | null = null;

function dropAccent(reason: string) {
  if (accentSend !== null) {
    window.clearTimeout(accentSend);
    accentSend = null;
  }
  const flight = accentFlight;
  accentFlight = null;
  if (!flight) return;
  finishBpmChange(flight.key, { accent: flight.rev }, false, reason);
}

function seedFromState() {
  const bpm = api?.state.value?.bpm;
  return {
    enabled: bpm?.enabled === true,
    mode: bpm?.mode,
    scope: bpm?.scope,
    palette: bpm?.palette,
    cast: bpm?.cast,
    groups: bpm?.groups,
    excluded: bpm?.excluded,
    character: bpm?.character,
    intensity: bpm?.intensity,
    period: bpm?.period,
    parRhythm: bpm?.par_rhythm,
    accent: bpm?.accent,
    revision: bpm?.revision,
    session: bpm?.session,
    epoch: bpm?.epoch,
  };
}

function commit(patch: SyncPatch) {
  beginBpmChange(choiceKey.value, patch, seedFromState());
  void api?.setBpmSync(patch);
}

function setScope(next: BpmSyncScope) {
  commit({ scope: next });
}

function setMode(next: BpmSyncMode) {
  commit({ mode: next });
}

function setPalette(next: BpmPaletteId) {
  commit({ palette: next });
}

function toggleGroup(id: BpmGroupId) {
  const base = shown.value;
  const nextGroups = base.groups.includes(id)
    ? base.groups.filter((item) => item !== id)
    : [...base.groups, id];
  commit({ groups: nextGroups });
}

function setCharacter(next: BpmCharacterId) {
  const base = shown.value;
  const nextPeriod = PERIODS_FOR_CHARACTER[next].includes(base.period) ? base.period : "auto";
  commit(nextPeriod === base.period ? { character: next } : { character: next, period: nextPeriod });
}

function setParRhythm(next: boolean) {
  commit({ parRhythm: next });
}

function setAccent(next: number) {
  const amount = Math.max(0, Math.min(1, next));
  const key = choiceKey.value;
  const staged = beginBpmChange(key, { accent: amount }, seedFromState());
  if (accentSend !== null) window.clearTimeout(accentSend);
  accentFlight = { key, rev: staged.fieldRevs.accent ?? staged.rev };
  accentSend = window.setTimeout(() => {
    const flight = accentFlight;
    accentSend = null;
    accentFlight = null;
    if (!flight || flight.key !== choiceKey.value) {
      if (flight) finishBpmChange(flight.key, { accent: flight.rev }, false, "Локацію змінено до відправки");
      return;
    }
    void api?.setBpmSync({ accent: amount });
  }, 40);
}

watch(choiceKey, (next, previous) => {
  if (previous && next !== previous) dropAccent("Локацію змінено до відправки");
});

onBeforeUnmount(() => dropAccent("Пульт закрито до відправки"));

function setIntensity(next: BpmIntensityId) {
  commit({ intensity: next });
}

function setPeriod(next: BpmPeriodId) {
  if (!PERIODS_FOR_CHARACTER[character.value].includes(next)) return;
  commit({ period: next });
}

function paletteOf(id: string) {
  return BPM_PALETTES.find((item) => item.id === id);
}
</script>

<template>
  <div
    v-if="api && desk.enabled"
    class="choice-group bpm-picker"
  >
    <span class="choice-group__label">{{ scope === "auto" ? "Авто · вихід у Пресет" : "Робота" }}</span>
    <p
      v-if="desk.error || desk.pending"
      class="hint"
      role="status"
    >
      {{ desk.error || "Ще застосовується" }}
    </p>
    <div
      class="bpm-modes"
      role="group"
      aria-label="Режим роботи"
    >
      <button
        type="button"
        class="action-btn"
        :class="{ active: scope === 'auto' }"
        @click="setScope('auto')"
      >
        Авто
      </button>
      <button
        type="button"
        class="action-btn"
        :class="{ active: scope === 'preset', 'action-btn--exit': scope === 'auto' }"
        @click="setScope('preset')"
      >
        Пресет
      </button>
    </div>
    <template v-if="scope === 'preset'">
      <span class="bpm-picker__label">Хід пресета</span>
      <div
        class="bpm-modes"
        role="group"
        aria-label="Хід пресета"
      >
        <button
          v-for="item in BPM_PRESET_MODES"
          :key="item.id"
          type="button"
          class="action-btn"
          :class="{ active: mode === item.id }"
          @click="setMode(item.id)"
        >
          {{ item.label }}
        </button>
      </div>
    </template>
    <div
      v-else
      class="bpm-auto"
    >
      <span class="bpm-picker__label">Палітра</span>
      <div
        class="bpm-swatches"
        role="group"
        aria-label="Палітра"
      >
        <button
          v-for="item in palettes"
          :key="item.id"
          type="button"
          class="palette-swatch"
          :class="{ active: palette === item.id }"
          :title="paletteOf(item.id)?.note || item.label"
          @click="setPalette(item.id)"
        >
          <span class="palette-swatch__dots">
            <i
              v-for="(tone, index) in paletteOf(item.id)?.swatches ?? ['#ff6b00']"
              :key="`${item.id}-${index}`"
              :style="{ background: tone }"
            />
          </span>
          <span class="palette-swatch__name">{{ item.label }}</span>
        </button>
      </div>
      <template v-if="director">
        <span class="bpm-picker__label">Характер</span>
        <div
          class="bpm-modes"
          role="group"
          aria-label="Характер Авто"
        >
          <button
            v-for="item in BPM_CHARACTERS"
            :key="item.id"
            type="button"
            class="action-btn"
            :class="{ active: character === item.id }"
            @click="setCharacter(item.id)"
          >
            {{ item.label }}
          </button>
        </div>
        <span class="bpm-picker__label">Інтенсивність</span>
        <div
          class="bpm-modes"
          role="group"
          aria-label="Інтенсивність"
        >
          <button
            v-for="item in BPM_INTENSITIES"
            :key="item.id"
            type="button"
            class="action-btn"
            :class="{ active: intensity === item.id }"
            @click="setIntensity(item.id)"
          >
            {{ item.label }}
          </button>
        </div>
        <span class="bpm-picker__label">Склад</span>
        <div
          class="bpm-modes"
          role="group"
          aria-label="Склад приладів"
        >
          <button
            v-for="item in BPM_GROUPS"
            :key="item.id"
            type="button"
            class="action-btn"
            :class="{ active: groups.includes(item.id) }"
            :aria-pressed="groups.includes(item.id)"
            @click="toggleGroup(item.id)"
          >
            {{ item.label }}
          </button>
        </div>
        <div class="bpm-extra">
          <button
            type="button"
            class="action-btn bpm-extra__toggle"
            :aria-expanded="extraOpen"
            aria-controls="bpm-extra-panel"
            @click="toggleExtra"
          >
            <span>{{ extraOpen ? "Згорнути" : "Ще" }}</span>
            <svg
              class="bpm-extra__arrow"
              :class="{ 'is-open': extraOpen }"
              viewBox="0 0 16 16"
              aria-hidden="true"
              focusable="false"
            >
              <path d="M3.2 5.4 8 10.2l4.8-4.8" />
            </svg>
          </button>
          <div
            v-if="extraOpen"
            id="bpm-extra-panel"
            class="bpm-extra__panel"
          >
            <span class="bpm-picker__label">Музичний період</span>
            <div
              class="bpm-modes"
              role="group"
              aria-label="Музичний період"
            >
              <button
                v-for="item in periodChoices"
                :key="String(item.id)"
                type="button"
                class="action-btn"
                :class="{ active: period === item.id }"
                @click="setPeriod(item.id)"
              >
                {{ item.label }}
              </button>
            </div>
            <span class="bpm-picker__label">Окремий ритм PAR</span>
            <div
              class="bpm-modes"
              role="group"
              aria-label="Окремий ритм PAR"
            >
              <button
                type="button"
                class="action-btn"
                :class="{ active: parRhythm }"
                @click="setParRhythm(!parRhythm)"
              >
                {{ parRhythm ? "Увімкнено" : "Вимкнено" }}
              </button>
            </div>
            <p
              v-if="parRhythm"
              class="hint"
            >
              PAR — плавний фон
            </p>
            <label class="field">
              <span>Додаткові акценти</span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                :value="accent"
                aria-label="Додаткові акценти"
                @input="setAccent(Number(($event.target as HTMLInputElement).value))"
              >
              <span class="hint">{{ accent <= 0 ? "Вимкнено" : "Виразніше" }}</span>
            </label>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
