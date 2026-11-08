<script setup lang="ts">
import { computed, inject, ref, watch } from "vue";
import { APP_STATE_KEY } from "../../composables/appStateKey";
import {
  BPM_CASTS,
  BPM_GROUPS,
  BPM_CHARACTERS,
  BPM_INTENSITIES,
  BPM_LEGACY_PALETTES,
  BPM_PALETTES,
  BPM_PERIODS,
  BPM_PRESET_MODES,
  PERIODS_FOR_CHARACTER,
  bpmChoiceKey,
  bpmChoiceRevision,
  hasAutoDirector,
  normalizeGroups,
  readBpmChoice,
  writeBpmChoice,
  type BpmCastId,
  type BpmGroupId,
  type BpmCharacterId,
  type BpmChoice,
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
  bpmChoiceKey({
    locationId: api?.state.value?.location_id,
    sceneId: api?.state.value?.scene_id,
  }),
);
const director = computed(() => hasAutoDirector(api?.state.value?.capabilities));
const saved = computed(() => {
  bpmChoiceRevision.value;
  return readBpmChoice(choiceKey.value);
});
const draft = ref<BpmChoice | null>(null);
const applied = computed<BpmChoice | null>(() => {
  const bpm = api?.state.value?.bpm;
  if (!bpm?.enabled || (bpm.scope !== "auto" && bpm.scope !== "preset")) return null;
  const nextMode = BPM_PRESET_MODES.find((item) => item.id === bpm.mode)?.id ?? saved.value.mode;
  const knownPalette = BPM_PALETTES.find((item) => item.id === bpm.palette)?.id;
  const nextPalette =
    knownPalette ??
    (bpm.palette === "heads" ? "white" : bpm.palette === "bars" ? "orange" : null);
  if (!nextMode || !nextPalette || !bpm.scope) return null;
  const nextCast =
    BPM_CASTS.find((item) => item.id === bpm.cast)?.id ??
    (bpm.palette === "bars" || bpm.palette === "heads" ? bpm.palette : saved.value.cast);
  const nextCharacter =
    BPM_CHARACTERS.find((item) => item.id === bpm.character)?.id ?? saved.value.character;
  const nextIntensity =
    BPM_INTENSITIES.find((item) => item.id === bpm.intensity)?.id ?? saved.value.intensity;
  const allowed = PERIODS_FOR_CHARACTER[nextCharacter];
  const rawPeriod = bpm.period;
  const parsed =
    rawPeriod === "auto" || rawPeriod == null
      ? "auto"
      : BPM_PERIODS.some((item) => item.id !== "auto" && Number(item.id) === Number(rawPeriod))
        ? (Number(rawPeriod) as BpmPeriodId)
        : saved.value.period;
  const nextPeriod = allowed.includes(parsed) ? parsed : "auto";
  return {
    scope: bpm.scope,
    mode: nextMode,
    palette: nextPalette,
    cast: nextCast,
    character: nextCharacter,
    intensity: nextIntensity,
    period: nextPeriod,
    groups: normalizeGroups(bpm.groups, nextCast),
    excluded: Array.isArray(bpm.excluded)
      ? bpm.excluded.filter((item: unknown): item is string => typeof item === "string")
      : saved.value.excluded,
    parRhythm: bpm.par_rhythm === true,
    accent: typeof bpm.accent === "number" ? bpm.accent : saved.value.accent,
  };
});
const shown = computed(() => draft.value ?? applied.value ?? saved.value);
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

function sameChoice(left: BpmChoice, right: BpmChoice): boolean {
  return (
    left.scope === right.scope &&
    left.mode === right.mode &&
    left.palette === right.palette &&
    left.groups.join() === right.groups.join() &&
    left.excluded.join() === right.excluded.join() &&
    left.character === right.character &&
    left.intensity === right.intensity &&
    left.period === right.period &&
    left.parRhythm === right.parRhythm &&
    left.accent === right.accent
  );
}

watch(applied, (value) => {
  if (draft.value && value && sameChoice(draft.value, value)) draft.value = null;
});

async function commit(next: BpmChoice, patch: SyncPatch) {
  const previous = shown.value;
  const key = choiceKey.value;
  draft.value = next;
  const ok = await api?.setBpmSync(patch);
  if (!ok) {
    draft.value = previous;
    return;
  }
  writeBpmChoice(next, key);
}

function setScope(next: BpmSyncScope) {
  const base = shown.value;
  void commit({ ...base, scope: next }, { scope: next });
}

function setMode(next: BpmSyncMode) {
  const base = shown.value;
  void commit({ ...base, mode: next }, { mode: next });
}

function setPalette(next: BpmPaletteId) {
  const base = shown.value;
  void commit({ ...base, palette: next }, { palette: next });
}

function toggleGroup(id: BpmGroupId) {
  const base = shown.value;
  const nextGroups = base.groups.includes(id)
    ? base.groups.filter((item) => item !== id)
    : [...base.groups, id];
  void commit({ ...base, groups: nextGroups }, { groups: nextGroups });
}

function setCharacter(next: BpmCharacterId) {
  const base = shown.value;
  const nextPeriod = PERIODS_FOR_CHARACTER[next].includes(base.period) ? base.period : "auto";
  void commit(
    { ...base, character: next, period: nextPeriod },
    nextPeriod === base.period ? { character: next } : { character: next, period: nextPeriod },
  );
}

function setParRhythm(next: boolean) {
  const base = shown.value;
  void commit({ ...base, parRhythm: next }, { parRhythm: next });
}

function setAccent(next: number) {
  const base = shown.value;
  const amount = Math.max(0, Math.min(1, next));
  void commit({ ...base, accent: amount }, { accent: amount });
}

function setIntensity(next: BpmIntensityId) {
  const base = shown.value;
  void commit({ ...base, intensity: next }, { intensity: next });
}

function setPeriod(next: BpmPeriodId) {
  if (!PERIODS_FOR_CHARACTER[character.value].includes(next)) return;
  const base = shown.value;
  void commit({ ...base, period: next }, { period: next });
}

function paletteOf(id: string) {
  return BPM_PALETTES.find((item) => item.id === id);
}
</script>

<template>
  <div
    v-if="api && api.state.value?.bpm?.enabled"
    class="choice-group bpm-picker"
  >
    <span class="choice-group__label">{{ scope === "auto" ? "Авто · вихід у Пресет" : "Робота" }}</span>
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
                @change="setAccent(Number(($event.target as HTMLInputElement).value))"
              >
              <span class="hint">{{ accent <= 0 ? "Вимкнено" : "Виразніше" }}</span>
            </label>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
