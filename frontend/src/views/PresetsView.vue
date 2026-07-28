<script setup lang="ts">
import { computed, inject, onMounted, ref } from "vue";
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
  previewPreset,
  renamePreset,
  savePreset,
  type EpisodeCard,
  type PresetDocument,
} from "../api/presets";
import StageSimulator from "../components/simulator/StageSimulator.vue";
import { APP_STATE_KEY } from "../composables/appStateKey";
import type { PresetInfo } from "../vite-env";
import { PRESET_CATALOG } from "../lib/presets";

const api = inject(APP_STATE_KEY);
if (!api) {
  throw new Error("App state is not provided");
}

const {
  selectPreset,
  connection,
  engine,
  state,
  refreshRest,
  setPreviewSpeed,
} = api;

const summaries = ref<PresetInfo[]>([]);
const editing = ref<PresetDocument | null>(null);
const loading = ref(true);
const saving = ref(false);
const message = ref<string | null>(null);
const error = ref<string | null>(null);
const newId = ref("C01");
const newLabel = ref("Мій пресет");
const renameLabel = ref("");
const dragIndex = ref<number | null>(null);

const totalDuration = computed(() =>
  editing.value ? editing.value.episodes.reduce((sum, ep) => sum + Number(ep.duration_s), 0) : 0,
);

const isBuiltinEditing = computed(() => editing.value?.builtin === true);

async function reloadList() {
  summaries.value = await listPresets();
  await refreshRest();
}

function setStatus(ok: string | null, err: string | null = null) {
  message.value = ok;
  error.value = err;
}

async function openEditor(id: string) {
  error.value = null;
  editing.value = await getPreset(id);
  renameLabel.value = editing.value.label;
}

async function onCreate() {
  saving.value = true;
  try {
    await createCustomPreset(newId.value.trim(), newLabel.value.trim());
    await reloadList();
    await openEditor(newId.value.trim());
    setStatus(`Створено ${newId.value}`);
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
    setStatus("Пресет збережено (atomic YAML)");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка збереження");
  } finally {
    saving.value = false;
  }
}

async function onRename() {
  if (!editing.value) return;
  saving.value = true;
  try {
    await renamePreset(editing.value.id, renameLabel.value.trim());
    editing.value = { ...editing.value, label: renameLabel.value.trim() };
    await reloadList();
    setStatus("Назву оновлено");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка перейменування");
  } finally {
    saving.value = false;
  }
}

async function onDuplicate(id: string) {
  const nextId = `${id}_copy`.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 28) || "Copy1";
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
    await deletePreset(id);
    if (editing.value?.id === id) editing.value = null;
    await reloadList();
    setStatus(`Видалено ${id}`);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка видалення");
  } finally {
    saving.value = false;
  }
}

async function onPreview(id: string) {
  try {
    await previewPreset(id, 10);
    setStatus(`Перегляд ${id} ×10 на Mock (Art-Net не вмикається)`);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка перегляду");
  }
}

function addEpisode() {
  if (!editing.value || isBuiltinEditing.value) return;
  editing.value = {
    ...editing.value,
    episodes: [...editing.value.episodes, newEpisode(editing.value.episodes.length + 1)],
  };
}

function removeEpisode(index: number) {
  if (!editing.value || isBuiltinEditing.value || editing.value.episodes.length <= 1) return;
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
  if (isBuiltinEditing.value && patch.duration_s !== undefined) {
    return;
  }
  const episodes = editing.value.episodes.map((ep, i) =>
    i === index ? { ...ep, ...patch } : ep,
  );
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

onMounted(async () => {
  try {
    await reloadList();
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка завантаження");
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <section
    class="page presets-page"
    aria-label="Редактор пресетів"
  >
    <header class="page-head">
      <h1>Пресети</h1>
      <p>
        Картки, запуск, перейменування, дублювання, редактор епізодів і Mock-перегляд.
        Без raw DMX і без увімкнення Art-Net.
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
      class="banner warn"
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
    </p>

    <div class="create-box">
      <h2>Створити простий пресет</h2>
      <label class="field">
        ID
        <input
          v-model="newId"
          maxlength="32"
        >
      </label>
      <label class="field">
        Назва
        <input
          v-model="newLabel"
          maxlength="80"
        >
      </label>
      <button
        type="button"
        class="action-btn"
        :disabled="saving || connection === 'offline'"
        @click="onCreate"
      >
        Створити
      </button>
    </div>

    <div class="preset-cards">
      <article
        v-for="preset in summaries"
        :key="preset.id"
        class="preset-card"
        :class="{ active: engine?.preset_id === preset.id }"
      >
        <h2>{{ preset.id }} · {{ preset.label }}</h2>
        <p>
          {{ preset.episode_count ?? "—" }} епізодів ·
          {{ (preset.total_duration_s ?? 0).toFixed(0) }} с ·
          {{ preset.builtin ? "вбудований" : "власний" }}
        </p>
        <div class="card-actions">
          <button
            type="button"
            class="action-btn"
            :disabled="connection === 'offline'"
            @click="selectPreset(preset.id)"
          >
            Запуск
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="connection === 'offline'"
            @click="onPreview(preset.id)"
          >
            Перегляд ×10
          </button>
          <button
            type="button"
            class="action-btn"
            @click="openEditor(preset.id)"
          >
            Редагувати
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="saving"
            @click="onDuplicate(preset.id)"
          >
            Дублювати
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="saving || preset.builtin"
            @click="onDelete(preset.id, preset.builtin)"
          >
            Видалити
          </button>
        </div>
      </article>

      <article
        v-for="slot in PRESET_CATALOG.filter((p) => !summaries.some((s) => s.id === p.id))"
        :key="`slot-${slot.id}`"
        class="preset-card locked"
      >
        <h2>{{ slot.id }} · {{ slot.label }}</h2>
        <p>Ще не завантажено.</p>
      </article>
    </div>

    <StageSimulator
      :simulator="state?.simulator ?? null"
      :frame="state?.frame ?? []"
      :engine-preset-id="engine?.preset_id ?? '—'"
      :preset-time-s="engine?.preset_time_s ?? 0"
      :episode-index="engine?.episode_index ?? 0"
      :episode-count="engine?.episode_count ?? 10"
      :cycle-duration-s="engine?.cycle_duration_s ?? 180"
      :blackout="engine?.blackout ?? false"
      :strobe-held="engine?.strobe_held ?? false"
      :white-hit-active="engine?.white_hit_active ?? false"
      :face-on="engine?.face_on ?? false"
      :preview-speed="state?.preview_speed ?? 1"
      :disabled="connection === 'offline'"
      @update:preview-speed="setPreviewSpeed"
    />

    <section
      v-if="editing"
      class="editor"
      aria-label="Редактор епізодів"
    >
      <header class="editor-head">
        <div>
          <h2>Редактор · {{ editing.id }}</h2>
          <p>
            Σ {{ totalDuration.toFixed(1) }} с · hardware_tuned=false ·
            {{ editing.episodes.length }} епізодів
            <span v-if="isBuiltinEditing"> · структура 10×18 с зафіксована</span>
          </p>
        </div>
        <div class="row-actions">
          <label class="field inline">
            Нова назва
            <input v-model="renameLabel">
          </label>
          <button
            type="button"
            class="action-btn"
            :disabled="saving"
            @click="onRename"
          >
            Зберегти назву
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="saving"
            @click="onSave"
          >
            Зберегти YAML
          </button>
          <button
            type="button"
            class="action-btn"
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
        class="episode-card"
        draggable="true"
        @dragstart="onDragStart(index)"
        @dragover.prevent
        @drop="onDrop(index)"
      >
        <div class="episode-toolbar">
          <strong>Епізод {{ index + 1 }} · {{ ep.id }}</strong>
          <div class="row-actions">
            <button
              type="button"
              class="action-btn"
              :disabled="index === 0"
              @click="moveEpisode(index, index - 1)"
            >
              ↑
            </button>
            <button
              type="button"
              class="action-btn"
              :disabled="index >= editing.episodes.length - 1"
              @click="moveEpisode(index, index + 1)"
            >
              ↓
            </button>
            <button
              type="button"
              class="action-btn"
              :disabled="isBuiltinEditing || editing.episodes.length <= 1"
              @click="removeEpisode(index)"
            >
              Видалити
            </button>
          </div>
        </div>

        <div class="episode-grid">
          <label class="field">
            Тривалість (с)
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
            Палітра
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
            Ефект
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
            Перехід
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
          <label class="field">
            Швидкість {{ ep.speed.toFixed(2) }}
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
            Інтенсивність {{ ep.intensity.toFixed(2) }}
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

        <div class="group-chips">
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
  </section>
</template>
