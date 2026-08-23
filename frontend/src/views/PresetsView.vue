<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  EFFECTS,
  GROUPS,
  PALETTES,
  TRANSITIONS,
  createCustomPreset,
  deletePreset,
  duplicatePreset,
  getPreset,
  listPresets,
  newEpisode,
  renamePreset,
  savePreset,
  startEditorEpisodePreview,
  stopEditorEpisodePreview,
  suggestCustomPresetId,
  type EpisodeCard,
  type PresetDocument,
} from "../api/presets";
import { fetchStageLayout } from "../api/client";
import BlackoutButton from "../components/control/BlackoutButton.vue";
import StageSimulator from "../components/simulator/StageSimulator.vue";
import { APP_STATE_KEY } from "../composables/appStateKey";
import { usePreviewClip } from "../composables/usePreviewClip";
import type { PresetInfo, StageLayout } from "../vite-env";
import { PRESET_CATALOG, formatClock, paletteCss } from "../lib/presets";

const api = inject(APP_STATE_KEY);
if (!api) {
  throw new Error("App state is not provided");
}

const {
  connection,
  engine,
  output,
  state,
  refreshRest,
  toggleBlackout,
  setPadPresets,
} = api;

const padDraft = ref<string[]>([]);
const padSaving = ref(false);

watch(
  () => state.value?.pad_presets,
  (ids) => {
    if (ids?.length === 9) padDraft.value = [...ids];
  },
  { immediate: true },
);

function setPadSlot(index: number, presetId: string) {
  const next = [...padDraft.value];
  while (next.length < 9) next.push(`P0${next.length + 1}`);
  next[index] = presetId;
  padDraft.value = next.slice(0, 9);
}

async function savePadSlots() {
  if (padDraft.value.length !== 9) return;
  padSaving.value = true;
  try {
    await setPadPresets(padDraft.value);
    setStatus("Склад пульта збережено");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося зберегти пульт");
  } finally {
    padSaving.value = false;
  }
}

const router = useRouter();

const summaries = ref<PresetInfo[]>([]);
const editing = ref<PresetDocument | null>(null);
const layout = ref<StageLayout | null>(null);
const loading = ref(true);
const saving = ref(false);
const message = ref<string | null>(null);
const error = ref<string | null>(null);
const savedHintId = ref<string | null>(null);
const newId = ref("C01");
const newLabel = ref("Мій пресет");
const renameLabel = ref("");
const dragIndex = ref<number | null>(null);
const selectedId = ref<string | null>(null);

const preview = usePreviewClip();

const totalDuration = computed(() =>
  editing.value ? editing.value.episodes.reduce((sum, ep) => sum + Number(ep.duration_s), 0) : 0,
);

const isBuiltinEditing = computed(() => editing.value?.builtin === true);
const offline = computed(() => connection.value === "offline");
const selected = computed(() => summaries.value.find((p) => p.id === selectedId.value) ?? null);
const editorPreview = computed(() => state.value?.preset_editor_preview ?? null);
const hardwareTestActive = computed(() => editorPreview.value?.active === true);

async function reloadList() {
  summaries.value = await listPresets();
  await refreshRest();
  newId.value = suggestCustomPresetId(summaries.value.map((p) => p.id));
}

function setStatus(ok: string | null, err: string | null = null) {
  message.value = ok;
  error.value = err;
}

async function openEditor(id: string) {
  error.value = null;
  editing.value = await getPreset(id);
  renameLabel.value = editing.value.label;
  savedHintId.value = null;
}

const episodeDuration = computed(() => {
  const info = selected.value;
  const count = info?.episode_count ?? 0;
  if (!info || count === 0) return 18;
  return (info.total_duration_s ?? 180) / count;
});

const previewEpisode = computed(() =>
  Math.min(
    (selected.value?.episode_count ?? 10) - 1,
    Math.floor(preview.timeS.value / Math.max(1, episodeDuration.value)),
  ),
);

async function choose(id: string) {
  selectedId.value = id;
  await preview.load(id, 12, 15);
}

async function previewFrom(episodeIndex: number) {
  if (!selectedId.value) return;
  await preview.load(selectedId.value, 12, 15, episodeIndex * episodeDuration.value);
}

async function onCreate() {
  saving.value = true;
  try {
    const id = newId.value.trim() || suggestCustomPresetId(summaries.value.map((p) => p.id));
    newId.value = id;
    await createCustomPreset(id, newLabel.value.trim() || "Мій пресет");
    await reloadList();
    await openEditor(id);
    setStatus(`Створено ${id}. Натисніть «Зберегти пресет», щоб він з’явився на пульті.`);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка створення");
  } finally {
    saving.value = false;
  }
}

async function onSave() {
  if (!editing.value) return;
  saving.value = true;
  try {
    await savePreset(editing.value.id, editing.value);
    await reloadList();
    savedHintId.value = editing.value.id;
    setStatus(`Пресет ${editing.value.id} збережено і доступний на пульті`);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка збереження");
  } finally {
    saving.value = false;
  }
}

async function openOnPad() {
  const id = savedHintId.value ?? editing.value?.id;
  if (!id) return;
  await router.push({ name: "control", query: { highlight: id } });
}

async function onRename() {
  if (!editing.value) return;
  saving.value = true;
  try {
    await renamePreset(editing.value.id, renameLabel.value.trim());
    editing.value = { ...editing.value, label: renameLabel.value.trim() };
    await reloadList();
    setStatus("Назву оновлено на пульті без дубліката");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка перейменування");
  } finally {
    saving.value = false;
  }
}

async function onDuplicate(id: string) {
  const nextId =
    suggestCustomPresetId(summaries.value.map((p) => p.id)) ||
    `${id}_copy`.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 28) ||
    "Copy1";
  saving.value = true;
  try {
    await duplicatePreset(id, nextId);
    await reloadList();
    await openEditor(nextId);
    setStatus(`Дубльовано як ${nextId}`);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка дублювання");
  } finally {
    saving.value = false;
  }
}

async function onDelete(id: string, builtin?: boolean) {
  if (builtin) {
    setStatus(null, "Вбудований пресет не можна видалити");
    return;
  }
  saving.value = true;
  try {
    if (hardwareTestActive.value && editorPreview.value?.preset_id === id) {
      await stopEditorEpisodePreview();
    }
    await deletePreset(id);
    if (editing.value?.id === id) editing.value = null;
    if (savedHintId.value === id) savedHintId.value = null;
    await reloadList();
    setStatus(`Видалено ${id}`);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка видалення");
  } finally {
    saving.value = false;
  }
}

/** Virtual-scene only — does not enable Art-Net / Arm / clear Blackout. */
async function onApplyVisualization(id: string) {
  selectedId.value = id;
  await preview.load(id, 12, 15);
  setStatus(`${id} застосовано до візуалізації (Mock-кліп)`);
}

async function playEpisodeOnHardware(index: number) {
  if (!editing.value) return;
  const ep = editing.value.episodes[index];
  if (!ep) return;
  saving.value = true;
  try {
    await startEditorEpisodePreview({
      preset_id: editing.value.id,
      preset_label: editing.value.label,
      episode_index: index,
      episode: ep,
    });
    await refreshRest();
    setStatus(`Тест епізоду ${index + 1} на обладнанні`);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка тесту епізоду");
  } finally {
    saving.value = false;
  }
}

async function stopHardwareTest() {
  try {
    await stopEditorEpisodePreview();
    await refreshRest();
    setStatus("Тест на обладнанні зупинено");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося зупинити тест");
  }
}

function addEpisode() {
  if (!editing.value || isBuiltinEditing.value) return;
  editing.value = {
    ...editing.value,
    episodes: [...editing.value.episodes, newEpisode(editing.value.episodes.length + 1)],
  };
}

async function removeEpisode(index: number) {
  if (!editing.value || isBuiltinEditing.value || editing.value.episodes.length <= 1) return;
  const removed = editing.value.episodes[index];
  if (
    hardwareTestActive.value &&
    editorPreview.value?.preset_id === editing.value.id &&
    (editorPreview.value.episode_id === removed?.id ||
      editorPreview.value.episode_index === index)
  ) {
    await stopHardwareTest();
  }
  const episodes = editing.value.episodes.filter((_, i) => i !== index);
  editing.value = { ...editing.value, episodes };
}

function moveEpisode(from: number, to: number) {
  if (!editing.value) return;
  if (to < 0 || to >= editing.value.episodes.length) return;
  const episodes = [...editing.value.episodes];
  const [item] = episodes.splice(from, 1);
  episodes.splice(to, 0, item);
  editing.value = { ...editing.value, episodes };
}

function onDragStart(index: number) {
  dragIndex.value = index;
}

function onDrop(index: number) {
  if (dragIndex.value === null || !editing.value) return;
  moveEpisode(dragIndex.value, index);
  dragIndex.value = null;
}

function updateEpisode(index: number, patch: Partial<EpisodeCard>) {
  if (!editing.value) return;
  if (isBuiltinEditing.value && patch.duration_s !== undefined) return;
  const episodes = editing.value.episodes.map((ep, i) => (i === index ? { ...ep, ...patch } : ep));
  editing.value = { ...editing.value, episodes };
}

function toggleGroup(index: number, group: string) {
  if (!editing.value) return;
  const ep = editing.value.episodes[index];
  const groups = ep.groups.includes(group)
    ? ep.groups.filter((g) => g !== group)
    : [...ep.groups, group];
  if (groups.length === 0) return;
  updateEpisode(index, { groups });
}

watch(
  () => editing.value?.id,
  async (id, prev) => {
    if (prev && hardwareTestActive.value && editorPreview.value?.preset_id === prev && id !== prev) {
      await stopHardwareTest();
    }
  },
);

onMounted(async () => {
  try {
    await reloadList();
    layout.value = await fetchStageLayout().catch(() => null);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка завантаження");
  } finally {
    loading.value = false;
  }
  const first = summaries.value[0];
  if (first) void choose(engine.value?.preset_id ?? first.id);
});

onBeforeUnmount(() => {
  if (hardwareTestActive.value) {
    void stopEditorEpisodePreview().then(() => refreshRest());
  }
});
</script>

<template>
  <div class="presets-view">
    <header class="card">
      <h1>Пресети</h1>
      <p class="card__sub">
        Три окремі дії: візуалізація · тест епізоду на обладнанні · збереження на пульт.
      </p>
    </header>

    <p
      v-if="loading"
      class="banner"
    >
      Завантаження…
    </p>
    <p
      v-else-if="error"
      class="banner banner--warn"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-else-if="message"
      class="banner"
      role="status"
    >
      {{ message }}
      <button
        v-if="savedHintId"
        type="button"
        class="btn btn--sm btn--primary"
        style="margin-left: 12px"
        @click="openOnPad"
      >
        Відкрити на пульті
      </button>
    </p>

    <section
      v-if="hardwareTestActive"
      class="card editor-hw-test"
      aria-label="Тест на обладнанні"
    >
      <header class="card__head">
        <div>
          <h2>Тест на обладнанні активний</h2>
          <p class="card__sub">
            {{ editorPreview?.preset_label }} ·
            {{ editorPreview?.episode_title }}
          </p>
        </div>
        <div class="row-actions">
          <BlackoutButton
            :active="engine?.blackout ?? false"
            :disabled="offline"
            @toggle="toggleBlackout"
          />
          <button
            type="button"
            class="btn btn--danger"
            @click="stopHardwareTest"
          >
            Зупинити тест
          </button>
        </div>
      </header>
      <div class="form-grid">
        <div class="field">
          <span>Час циклу епізоду</span>
          <strong>{{ formatClock(editorPreview?.elapsed_s ?? 0) }} /
            {{ formatClock(editorPreview?.episode_duration_s ?? 0) }}</strong>
        </div>
        <div class="field">
          <span>Art-Net / UDP / Arm</span>
          <strong>
            {{ output?.transport ?? "—" }} /
            {{ output?.udp_active ? "UDP on" : "UDP off" }} /
            {{ output?.armed ? "Armed" : "Disarmed" }}
          </strong>
        </div>
        <div class="field">
          <span>Blackout</span>
          <strong>{{ engine?.blackout ? "увімкнено" : "вимкнено" }}</strong>
        </div>
        <div class="field">
          <span>DMX source</span>
          <strong>{{ output?.source_owner ?? "—" }}</strong>
        </div>
        <div class="field">
          <span>Source NZ / Wire NZ</span>
          <strong>
            {{ editorPreview?.source_nonzero_channels ?? output?.source_nonzero_channels ?? 0 }}
            /
            {{ editorPreview?.wire_nonzero_channels ?? output?.wire_nonzero_channels ?? 0 }}
          </strong>
        </div>
      </div>
      <ul
        v-if="(editorPreview?.blockers?.length ?? 0) > 0"
        class="banner banner--warn"
      >
        <li
          v-for="(b, i) in editorPreview?.blockers ?? []"
          :key="i"
        >
          {{ b }}
        </li>
      </ul>
    </section>

    <section
      class="card"
      aria-label="Пульт — 9 пресетів"
    >
      <header class="card__head">
        <h2>Склад пульта</h2>
        <p class="card__sub">
          На головній завжди NONE (клавіша P), далі рівно 9 обраних (клавіші Q–O).
          NONE тут не налаштовується.
        </p>
      </header>
      <div class="form-grid">
        <label
          v-for="(_, index) in 9"
          :key="index"
          class="field"
        >
          <span>Слот {{ index + 1 }}</span>
          <select
            :value="padDraft[index] ?? ''"
            :disabled="offline || padSaving"
            @change="setPadSlot(index, ($event.target as HTMLSelectElement).value)"
          >
            <option
              v-for="preset in summaries"
              :key="preset.id"
              :value="preset.id"
              :disabled="padDraft.includes(preset.id) && padDraft[index] !== preset.id"
            >
              {{ preset.id }} · {{ preset.label }}
            </option>
          </select>
        </label>
      </div>
      <div class="row-actions">
        <button
          type="button"
          class="btn"
          :disabled="offline || padSaving || padDraft.length !== 9"
          @click="savePadSlots"
        >
          Зберегти склад пульта
        </button>
      </div>
    </section>

    <div class="presets-grid">
      <section
        class="card"
        aria-label="Список пресетів"
      >
        <header class="card__head">
          <h2>Каталог</h2>
          <p class="card__sub">
            Вбудовані та власні пресети
          </p>
        </header>

        <div class="preset-cards">
          <button
            v-for="preset in summaries"
            :key="preset.id"
            type="button"
            class="preset-card"
            :class="{ active: selectedId === preset.id }"
            :data-id="preset.id"
            @click="choose(preset.id)"
          >
            <span class="preset-card__top">
              <span class="preset-card__id">{{ preset.id }}</span>
              <span class="badge badge--muted">{{
                preset.builtin ? "вбудований" : "власний"
              }}</span>
            </span>
            <span class="preset-card__label">{{ preset.label }}</span>
            <span class="palette">
              <i
                v-for="(palette, idx) in preset.palettes ?? []"
                :key="idx"
                :style="{ background: paletteCss(palette) }"
                :title="palette"
              />
            </span>
            <span class="intensity-bar"><i
              :style="{ width: `${Math.round((preset.avg_intensity ?? 0) * 100)}%` }"
            /></span>
            <span class="preset-card__meta">
              <span>{{ preset.episode_count ?? "—" }} еп. ·
                {{ (preset.total_duration_s ?? 0).toFixed(0) }} с</span>
              <span>темп {{ Math.round((preset.avg_speed ?? 0) * 100) }}%</span>
            </span>
          </button>

          <div
            v-for="slot in PRESET_CATALOG.filter((p) => !summaries.some((s) => s.id === p.id))"
            :key="`slot-${slot.id}`"
            class="preset-card"
          >
            <span class="preset-card__id">{{ slot.id }}</span>
            <span class="preset-card__label">Ще не завантажено</span>
          </div>
        </div>

        <div class="row-actions">
          <button
            type="button"
            class="btn btn--primary"
            :disabled="offline || !selectedId"
            @click="selectedId && onApplyVisualization(selectedId)"
          >
            Застосувати до візуалізації
          </button>
          <button
            type="button"
            class="btn"
            :disabled="!selectedId"
            @click="selectedId && openEditor(selectedId)"
          >
            Редагувати
          </button>
          <button
            type="button"
            class="btn"
            :disabled="saving || !selectedId"
            @click="selectedId && onDuplicate(selectedId)"
          >
            Дублювати
          </button>
          <button
            type="button"
            class="btn btn--danger"
            :disabled="saving || !selected || selected.builtin"
            @click="selected && onDelete(selected.id, selected.builtin)"
          >
            Видалити
          </button>
        </div>
      </section>

      <div class="preview-column">
        <StageSimulator
          :layout="layout"
          :view="preview.view"
          :engine-preset-id="preview.presetId.value ?? '—'"
          :preset-time-s="preview.timeS.value"
          :episode-index="previewEpisode"
          :episode-count="selected?.episode_count ?? 10"
          :cycle-duration-s="selected?.total_duration_s ?? 180"
          :nonzero-channels="1"
          :show-inspector="false"
          title="Безпечний Mock-перегляд"
          badge="Не надсилається у transport"
        />

        <section
          v-if="selected"
          class="card"
          aria-label="Епізоди пресету"
        >
          <header class="card__head">
            <h2>Епізоди</h2>
            <p class="card__sub">
              {{ selected.episode_count }} × {{ episodeDuration.toFixed(0) }} с =
              {{ (selected.total_duration_s ?? 180).toFixed(0) }} с
            </p>
          </header>
          <div class="episode-strip">
            <button
              v-for="(palette, index) in selected.palettes"
              :key="`${selected.id}-${index}`"
              type="button"
              class="episode-chip"
              :class="{ active: index === previewEpisode }"
              :style="{ '--chip': paletteCss(palette) }"
              @click="previewFrom(index)"
            >
              <span class="episode-chip__n">{{ index + 1 }}</span>
              <span class="episode-chip__palette">{{ palette }}</span>
            </button>
          </div>
          <p class="card__sub">
            Натисніть епізод для Mock-візуалізації. Для реального виводу використайте
            «Відтворити на обладнанні» в редакторі.
          </p>
        </section>
      </div>
    </div>

    <section
      class="card"
      aria-label="Створити пресет"
    >
      <header class="card__head">
        <h2>Створити власний пресет</h2>
        <p class="card__sub">
          ID стійкий і не залежить від назви (наприклад C01)
        </p>
      </header>
      <div class="form-grid">
        <label class="field">
          <span>ID</span>
          <input
            v-model="newId"
            type="text"
            maxlength="32"
          >
        </label>
        <label class="field">
          <span>Назва</span>
          <input
            v-model="newLabel"
            type="text"
            maxlength="80"
          >
        </label>
      </div>
      <div class="row-actions">
        <button
          type="button"
          class="btn"
          :disabled="saving || offline"
          @click="onCreate"
        >
          Створити
        </button>
      </div>
    </section>

    <section
      v-if="editing"
      class="card"
      aria-label="Редактор епізодів"
    >
      <header class="card__head">
        <div>
          <h2>Редактор · {{ editing.id }}</h2>
          <p class="card__sub">
            Σ {{ totalDuration.toFixed(1) }} с · {{ editing.episodes.length }} епізодів ·
            hardware_tuned=false
            <span v-if="isBuiltinEditing"> · структура 10 × 18 с зафіксована</span>
          </p>
          <p class="card__sub">
            Після збереження пресет з’явиться на пульті
          </p>
        </div>
        <div class="row-actions">
          <label class="field field--inline">
            <span>Назва</span>
            <input
              v-model="renameLabel"
              type="text"
            >
          </label>
          <button
            type="button"
            class="btn btn--sm"
            :disabled="saving"
            @click="onRename"
          >
            Зберегти назву
          </button>
          <button
            type="button"
            class="btn btn--sm btn--primary"
            :disabled="saving"
            @click="onSave"
          >
            Зберегти пресет
          </button>
          <button
            type="button"
            class="btn btn--sm"
            :disabled="isBuiltinEditing"
            @click="addEpisode"
          >
            + Епізод
          </button>
        </div>
      </header>

      <div
        v-for="(ep, index) in editing.episodes"
        :key="ep.id"
        class="card episode-card"
        draggable="true"
        @dragstart="onDragStart(index)"
        @dragover.prevent
        @drop="onDrop(index)"
      >
        <div class="card__head">
          <strong>Епізод {{ index + 1 }} · {{ ep.id }}</strong>
          <div class="row-actions">
            <button
              type="button"
              class="btn btn--sm btn--primary"
              :disabled="offline || saving"
              @click="playEpisodeOnHardware(index)"
            >
              Відтворити на обладнанні
            </button>
            <button
              type="button"
              class="btn btn--sm"
              :disabled="index === 0"
              @click="moveEpisode(index, index - 1)"
            >
              ↑
            </button>
            <button
              type="button"
              class="btn btn--sm"
              :disabled="index >= editing.episodes.length - 1"
              @click="moveEpisode(index, index + 1)"
            >
              ↓
            </button>
            <button
              type="button"
              class="btn btn--sm btn--danger"
              :disabled="isBuiltinEditing || editing.episodes.length <= 1"
              @click="removeEpisode(index)"
            >
              Видалити
            </button>
          </div>
        </div>

        <div class="form-grid">
          <label class="field">
            <span>Тривалість (с)</span>
            <input
              type="number"
              min="0.1"
              max="180"
              step="0.1"
              :value="ep.duration_s"
              :disabled="isBuiltinEditing"
              @input="updateEpisode(index, { duration_s: Number(($event.target as HTMLInputElement).value) })"
            >
          </label>
          <label class="field">
            <span>Палітра</span>
            <select
              :value="ep.palette"
              @change="updateEpisode(index, { palette: ($event.target as HTMLSelectElement).value })"
            >
              <option
                v-for="p in PALETTES"
                :key="p"
                :value="p"
              >
                {{ p }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>Ефект</span>
            <select
              :value="ep.effect"
              @change="updateEpisode(index, { effect: ($event.target as HTMLSelectElement).value })"
            >
              <option
                v-for="fx in EFFECTS"
                :key="fx"
                :value="fx"
              >
                {{ fx }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>Перехід</span>
            <select
              :value="ep.transition"
              @change="updateEpisode(index, { transition: ($event.target as HTMLSelectElement).value })"
            >
              <option
                v-for="tr in TRANSITIONS"
                :key="tr"
                :value="tr"
              >
                {{ tr }}
              </option>
            </select>
          </label>
          <p class="card__sub">
            Ефекти й переходи пишуть лише в канали з призначеною роллю в «Налаштування каналів».
            Канали unused / без мапінгу завжди 0.
          </p>
          <label class="field">
            <span>Швидкість {{ ep.speed.toFixed(2) }}</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              :value="ep.speed"
              @input="updateEpisode(index, { speed: Number(($event.target as HTMLInputElement).value) })"
            >
          </label>
          <label class="field">
            <span>Інтенсивність {{ ep.intensity.toFixed(2) }}</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              :value="ep.intensity"
              @input="updateEpisode(index, { intensity: Number(($event.target as HTMLInputElement).value) })"
            >
          </label>
        </div>

        <div class="chips">
          <button
            v-for="group in GROUPS"
            :key="group"
            type="button"
            class="chip"
            :class="{ on: ep.groups.includes(group) }"
            @click="toggleGroup(index, group)"
          >
            {{ group }}
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
