<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { AUTO_GROUP_LABEL, fetchAutoCatalog } from "../../api/auto";
import { useAutoPreview } from "../../composables/useAutoPreview";
import { BPM_INTENSITIES, BPM_PALETTES, BPM_PERIODS } from "../../lib/bpmPalettes";
import StageSimulator from "../simulator/StageSimulator.vue";
import type { AutoCatalog, StageLayout } from "../../vite-env";

const props = defineProps<{
  layout: StageLayout | null;
  sceneId?: string | null;
  locationId?: string | null;
  sceneRevision?: string | null;
  sceneReady: boolean;
  sceneError?: string | null;
}>();

const catalog = ref<AutoCatalog | null>(null);
const catalogError = ref<string | null>(null);
const catalogLoading = ref(true);
const search = ref("");
const family = ref("all");
const program = ref("all");
const selectedId = ref("vertical_wave");
const cycleBeats = ref(4);
const palette = ref("orange");
const intensity = ref("full");
const bpm = ref(120);
const wide = ref(false);
const listEl = ref<HTMLElement | null>(null);
const preview = useAutoPreview();
const {
  loading: previewLoading,
  error: previewError,
  compatible,
  reason,
  beat,
  playing,
  seconds,
  view,
  play,
  pause,
  restart,
  setBpm,
  invalidate,
  canonical,
} = preview;

const families = computed(() => catalog.value?.families ?? []);
const compositions = computed(() => catalog.value?.compositions ?? []);
const availableRows = computed(() =>
  (catalog.value?.available ?? []).filter((row) => row.look === selectedId.value),
);
const periods = computed(() => {
  const all = BPM_PERIODS.filter((item) => item.id !== "auto");
  const cycles = new Set(availableRows.value.map((row) => Number(row.cycle_beats)));
  if (!cycles.size) return all;
  return all.filter((item) => cycles.has(Number(item.id)));
});

const filtered = computed(() => {
  const query = search.value.trim().toLowerCase();
  return compositions.value.filter((item) => {
    if (family.value !== "all" && item.family !== family.value) return false;
    if (program.value === "smooth" && item.program !== "smooth") return false;
    if (program.value === "beat" && item.program !== "beat") return false;
    if (!query) return true;
    const hay = [item.id, item.title, item.family, item.family_title, item.motion, item.notes]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return hay.includes(query);
  });
});

const selected = computed(
  () =>
    compositions.value.find((item) => item.id === selectedId.value) ??
    filtered.value[0] ??
    null,
);

const palettes = computed(() => {
  const dumped = new Set(availableRows.value.map((row) => row.palette));
  const allowed = dumped.size ? dumped : new Set(selected.value?.palettes ?? []);
  const known = BPM_PALETTES.filter((item) => allowed.size === 0 || allowed.has(item.id));
  return known.length ? known : BPM_PALETTES;
});

const intensities = computed(() => {
  const dumped = new Set(availableRows.value.map((row) => row.intensity));
  if (!dumped.size) return BPM_INTENSITIES;
  const known = BPM_INTENSITIES.filter((item) => dumped.has(item.id));
  return known.length ? known : BPM_INTENSITIES;
});

const beatMarks = computed(() => {
  const count = Math.max(1, Math.round(cycleBeats.value * 2) / 2);
  const marks: number[] = [];
  for (let step = 0; step < count; step += 1) marks.push(step);
  return marks;
});

const summary = computed(() => {
  if (!selected.value) return "Оберіть композицію";
  return `${selected.value.title} · ${beat.value.toFixed(2)} / ${cycleBeats.value} долі · ${seconds.value.toFixed(2)} с`;
});

function groupLabel(id: string): string {
  return AUTO_GROUP_LABEL[id] ?? id;
}

async function reloadCatalog() {
  catalogLoading.value = true;
  catalogError.value = null;
  try {
    catalog.value = await fetchAutoCatalog();
    const fallback = catalog.value.default_look || "vertical_wave";
    if (!compositions.value.some((item) => item.id === selectedId.value)) {
      selectedId.value = fallback;
    }
    snapAvailable(selectedId.value);
  } catch (err) {
    catalog.value = null;
    catalogError.value = err instanceof Error ? err.message : "Каталог не прочитано";
  } finally {
    catalogLoading.value = false;
  }
}

async function revealSelected() {
  await nextTick();
  const root = listEl.value;
  const node = root?.querySelector(`[data-look="${selectedId.value}"]`);
  if (!(node instanceof HTMLElement) || !root) return;
  const nodeRect = node.getBoundingClientRect();
  const rootRect = root.getBoundingClientRect();
  if (nodeRect.top < rootRect.top || nodeRect.bottom > rootRect.bottom) {
    root.scrollTop += nodeRect.top - rootRect.top - 8;
  }
}

function snapAvailable(look: string) {
  const rows = (catalog.value?.available ?? []).filter((row) => row.look === look);
  if (!rows.length) return;
  const exact = rows.find(
    (row) =>
      Number(row.cycle_beats) === Number(cycleBeats.value) &&
      row.palette === palette.value &&
      row.intensity === intensity.value,
  );
  if (exact) return;
  const samePalette =
    rows.find((row) => row.palette === palette.value && row.intensity === intensity.value) ??
    rows.find((row) => row.palette === palette.value) ??
    rows[0];
  if (!samePalette) return;
  cycleBeats.value = Number(samePalette.cycle_beats);
  palette.value = samePalette.palette;
  intensity.value = samePalette.intensity;
}

function choose(id: string) {
  selectedId.value = id;
  const item = compositions.value.find((row) => row.id === id);
  if (item?.palettes?.length && !item.palettes.includes(palette.value)) {
    palette.value = item.palettes.includes("orange") ? "orange" : item.palettes[0];
  }
  snapAvailable(id);
}

function step(delta: number) {
  const rows = filtered.value;
  if (!rows.length) return;
  const index = Math.max(0, rows.findIndex((item) => item.id === selectedId.value));
  const next = rows[(index + delta + rows.length) % rows.length];
  if (next) choose(next.id);
}

function sceneKey(): string {
  return [
    props.locationId ?? "local",
    props.sceneId ?? "",
    props.sceneRevision ?? "",
  ].join(":");
}

async function refreshPreview() {
  if (!props.sceneReady) {
    invalidate();
    return;
  }
  if (!selectedId.value) return;
  await preview.load(
    {
      look: selectedId.value,
      cycleBeats: cycleBeats.value,
      palette: palette.value,
      intensity: intensity.value,
      bpm: bpm.value,
    },
    sceneKey(),
    props.sceneId ?? null,
  );
}

watch(
  () => [
    selectedId.value,
    cycleBeats.value,
    palette.value,
    intensity.value,
    props.sceneId,
    props.locationId,
    props.sceneRevision,
    props.sceneReady,
  ],
  () => {
    void refreshPreview();
  },
);

watch(
  () => bpm.value,
  (value) => setBpm(Number(value) || 120),
);

watch(selectedId, () => {
  void revealSelected();
});

onMounted(async () => {
  await reloadCatalog();
  await refreshPreview();
  await revealSelected();
});
</script>

<template>
  <div
    class="auto-library"
    :class="{ 'auto-library--wide': wide }"
  >
    <div
      class="preview-column auto-preview"
      :class="{ 'is-paused': !playing }"
    >
      <header class="card__head auto-preview__head">
        <h2>Перегляд</h2>
        <span
          v-if="selected"
          class="auto-preview__now"
        >{{ selected.title }}</span>
        <span class="auto-preview__state">{{ playing ? "Грає" : "Пауза" }}</span>
      </header>
      <p class="hint">
        Зал з цієї сторінки не керується.
      </p>
      <div class="auto-preview__toolbar">
        <button
          type="button"
          class="btn btn--sm"
          :disabled="!compatible || !canonical || previewLoading"
          @click="playing ? pause() : play()"
        >
          {{ playing ? "Пауза" : "Грати" }}
        </button>
        <button
          type="button"
          class="btn btn--sm"
          @click="restart()"
        >
          Спочатку
        </button>
        <button
          type="button"
          class="btn btn--sm"
          @click="step(-1)"
        >
          Попередня
        </button>
        <button
          type="button"
          class="btn btn--sm"
          @click="step(1)"
        >
          Наступна
        </button>
        <label class="field field--inline">
          <span>Темп</span>
          <input
            v-model.number="bpm"
            type="number"
            min="40"
            max="220"
            step="1"
          >
        </label>
        <label class="field field--inline">
          <span>Період</span>
          <select v-model.number="cycleBeats">
            <option
              v-for="item in periods"
              :key="String(item.id)"
              :value="item.id"
            >
              {{ item.label }}
            </option>
          </select>
        </label>
        <label class="field field--inline">
          <span>Палітра</span>
          <select v-model="palette">
            <option
              v-for="item in palettes"
              :key="item.id"
              :value="item.id"
            >
              {{ item.label }}
            </option>
          </select>
        </label>
        <label class="field field--inline">
          <span>Інтенсивність</span>
          <select v-model="intensity">
            <option
              v-for="item in intensities"
              :key="item.id"
              :value="item.id"
            >
              {{ item.label }}
            </option>
          </select>
        </label>
        <button
          type="button"
          class="btn btn--sm"
          @click="wide = !wide"
        >
          {{ wide ? "Вийти" : "На весь екран" }}
        </button>
      </div>
      <p
        v-if="!sceneReady"
        class="banner banner--warn"
        role="alert"
      >
        {{ sceneError || "Сцену ще не завантажено" }}
      </p>
      <p
        v-else-if="previewLoading"
        class="banner"
      >
        Будую кадри…
      </p>
      <p
        v-else-if="previewError"
        class="banner banner--warn"
        role="alert"
      >
        {{ previewError }}
      </p>
      <p
        v-else-if="reason && (!compatible || !canonical)"
        class="banner banner--warn"
        role="alert"
      >
        {{ reason }}
      </p>
      <StageSimulator
        plain
        :layout="layout"
        :view="view"
        :summary="summary"
        :engine-preset-id="selected?.id ?? '—'"
        :preset-time-s="beat"
        :episode-index="0"
        :episode-count="1"
        :cycle-duration-s="seconds"
        :nonzero-channels="1"
        :blackout="false"
        :show-inspector="false"
      />
      <div
        class="auto-beats"
        aria-label="Маркери часток"
      >
        <i
          v-for="mark in beatMarks"
          :key="mark"
          :class="{ on: beat >= mark && beat < mark + 1 }"
        />
      </div>
    </div>

    <section
      class="card auto-library__list"
      aria-label="Каталог авто-епізодів"
    >
      <header class="card__head">
        <h2>Авто-епізоди</h2>
        <span
          v-if="catalog"
          class="preset-card__meta"
        >{{ catalog.count }}</span>
      </header>
      <div class="form-grid">
        <label class="field">
          <span>Пошук</span>
          <input
            v-model="search"
            type="search"
            placeholder="vertical_wave, хвиля…"
          >
        </label>
        <label class="field">
          <span>Тип</span>
          <select
            v-model="program"
            aria-label="Тип композиції"
          >
            <option value="all">
              Усі
            </option>
            <option value="smooth">
              Плавні
            </option>
            <option value="beat">
              Під біт
            </option>
          </select>
        </label>
        <label class="field">
          <span>Родина</span>
          <select v-model="family">
            <option value="all">
              Усі
            </option>
            <option
              v-for="item in families"
              :key="item.id"
              :value="item.id"
            >
              {{ item.title }}
            </option>
          </select>
        </label>
      </div>
      <p
        v-if="catalogLoading"
        class="hint"
      >
        Завантаження каталогу…
      </p>
      <p
        v-else-if="catalogError"
        class="hint"
        role="alert"
      >
        {{ catalogError }}
      </p>
      <div
        v-else
        ref="listEl"
        class="preset-cards auto-cards"
      >
        <button
          v-for="item in filtered"
          :key="item.id"
          type="button"
          class="preset-card"
          :class="{ active: selectedId === item.id }"
          :data-look="item.id"
          :aria-pressed="selectedId === item.id"
          @click="choose(item.id)"
        >
          <span class="preset-card__label">{{ item.title }}</span>
          <span class="preset-card__meta">{{ item.family_title || item.family }}</span>
          <span class="hint">{{ item.motion || item.notes }}</span>
          <span class="preset-card__meta">{{ item.groups.map(groupLabel).join(" · ") }}</span>
        </button>
      </div>
    </section>
  </div>
</template>
