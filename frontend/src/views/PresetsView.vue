<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  EFFECTS,
  GROUPS,
  PALETTES,
  TRANSITION_LABELS,
  TRANSITIONS,
  cloneEpisode,
  createCustomPreset,
  effectUsesSpeed,
  freshLookId,
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
  type LookAction,
  type PresetDocument,
} from "../api/presets";
import { fetchStageLayout } from "../api/client";
import { fetchPatch, fetchProfiles } from "../api/setup";
import { lookCaption, matchesGroups, offeredGroups } from "../runtime/sceneValidate";
import { presetButtonLabel } from "../lib/presetLabel";
import BlackoutButton from "../components/control/BlackoutButton.vue";
import IconButton from "../components/IconButton.vue";
import StageSimulator from "../components/simulator/StageSimulator.vue";
import { APP_STATE_KEY } from "../composables/appStateKey";
import { activeScenePort, publishLocationDraft } from "../runtime/scenePort";
import { usePreviewClip } from "../composables/usePreviewClip";
import type { PresetInfo, StageLayout } from "../vite-env";
import { episodeIndexAt, episodeStartS } from "../lib/episodeClock";
import { formatClock, paletteCss } from "../lib/presets";
import AutoCatalogPanel from "../components/presets/AutoCatalogPanel.vue";

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
const route = useRoute();
const catalogKind = ref<"presets" | "auto">(route.query.catalog === "auto" ? "auto" : "presets");

const summaries = ref<PresetInfo[]>([]);
const editing = ref<PresetDocument | null>(null);
const layout = ref<StageLayout | null>(null);
const rigFixtures = ref<Array<Record<string, unknown>> | null>(null);
const rigProfiles = ref<Record<string, Record<string, unknown>>>({});
const sceneRevision = computed(() =>
  JSON.stringify({
    scene: state.value?.scene_id ?? null,
    location: state.value?.location_id ?? null,
    layout: layout.value,
    fixtures: rigFixtures.value,
    profiles: rigProfiles.value,
    presets: summaries.value.map((item) => [
      item.id,
      item.hardware_tuned ?? false,
      item.source ?? "",
      item.effects ?? [],
      item.avg_speed ?? null,
      item.episode_count ?? null,
      item.total_duration_s ?? null,
    ]),
    calibration: state.value?.beam_calibration ?? null,
  }),
);

function groupsFor(episode: EpisodeCard): readonly string[] {
  if (!rigFixtures.value) return GROUPS;
  return offeredGroups(rigFixtures.value, episode.groups);
}

function episodeNotes(episode: EpisodeCard): string[] {
  if (!rigFixtures.value) return [];
  const looks = episode.actions?.length
    ? episode.actions.map((action) => ({
        groups: action.groups?.length ? action.groups : episode.groups,
        effect: action.effect,
      }))
    : [{ groups: episode.groups, effect: episode.effect }];
  return [
    ...new Set(
      looks
        .map((look) => lookCaption(rigFixtures.value ?? [], rigProfiles.value, look.groups, look.effect))
        .filter(Boolean),
    ),
  ];
}

function effectDisabled(groups: string[], current: string, effect: string): boolean {
  if (!rigFixtures.value || effect === current) return false;
  return lookCaption(rigFixtures.value, rigProfiles.value, groups, effect) !== "";
}
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
const selectedDoc = ref<PresetDocument | null>(null);

const preview = usePreviewClip();

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
  try {
    const doc = await getPreset(id);
    editing.value = doc;
    renameLabel.value = doc.label;
  } catch (err) {
    editing.value = null;
    setStatus(null, err instanceof Error ? err.message : "Пресет не прочитано");
    return;
  }
  savedHintId.value = null;
}

const selectedDurations = computed(() =>
  (selectedDoc.value?.episodes ?? []).map((episode) => Number(episode.duration_s) || 0),
);
const previewCount = computed(
  () => selectedDurations.value.length || selected.value?.episode_count || 0,
);

const previewEpisode = computed(() => {
  if (!selectedDurations.value.length) return 0;
  return episodeIndexAt(selectedDurations.value, preview.timeS.value);
});

async function choose(id: string) {
  if (!id || id.toUpperCase() === "NONE") return;
  selectedId.value = id;
  try {
    selectedDoc.value = await getPreset(id);
  } catch (err) {
    selectedDoc.value = null;
    setStatus(null, err instanceof Error ? err.message : "Пресет не прочитано");
  }
  await preview.load(id, 12, 15);
}

async function previewFrom(episodeIndex: number) {
  if (!selectedId.value) return;
  const start = episodeStartS(selectedDurations.value, episodeIndex);
  await preview.load(selectedId.value, 12, 15, start);
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
    try {
      await publishLocationDraft();
      setStatus(`Пресет ${editing.value.id} збережено для локації`);
    } catch (err) {
      setStatus(
        null,
        `Чернетку збережено. ${err instanceof Error ? err.message : "Публікацію відхилено"}`,
      );
    }
    await reloadList();
    savedHintId.value = editing.value.id;
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

const actionCursor = ref<Record<string, number>>({});
const hardwarePreviewAllowed = computed(() => activeScenePort()?.hardwarePreview !== false);

function actionAt(episode: EpisodeCard): number {
  const count = episode.actions?.length ?? 0;
  if (count < 1) return 0;
  return Math.min(actionCursor.value[episode.id] ?? 0, count - 1);
}

function selectAction(episodeId: string, index: number) {
  actionCursor.value = { ...actionCursor.value, [episodeId]: index };
}

function lookGroups(groups: string[]): readonly string[] {
  if (!rigFixtures.value) return GROUPS;
  return offeredGroups(rigFixtures.value, groups);
}

function actionFromEpisode(episode: EpisodeCard): LookAction {
  return {
    id: episode.id,
    groups: [...episode.groups],
    palette: episode.palette,
    effect: episode.effect,
    speed: episode.speed,
    intensity: episode.intensity,
    cycle_beats: episode.cycle_beats ?? null,
  };
}

function addLook(episodeIndex: number) {
  if (!editing.value || isBuiltinEditing.value) return;
  const episode = editing.value.episodes[episodeIndex];
  if (!episode) return;
  const actions = episode.actions?.length
    ? episode.actions.map((action) => ({ ...action, groups: [...action.groups] }))
    : [actionFromEpisode(episode)];
  const previous = actions[actions.length - 1];
  actions.push({ ...previous, id: freshLookId("a"), groups: [...previous.groups] });
  updateEpisode(episodeIndex, { actions });
  selectAction(episode.id, actions.length - 1);
}

function copyLook(episodeIndex: number, actionIndex: number) {
  if (!editing.value || isBuiltinEditing.value) return;
  const episode = editing.value.episodes[episodeIndex];
  const existing = episode?.actions;
  const source = existing?.[actionIndex];
  if (!episode || !existing || !source) return;
  const actions = existing.map((action) => ({ ...action, groups: [...action.groups] }));
  actions.splice(actionIndex + 1, 0, { ...source, id: freshLookId("a"), groups: [...source.groups] });
  updateEpisode(episodeIndex, { actions });
  selectAction(episode.id, actionIndex + 1);
}

function removeLook(episodeIndex: number, actionIndex: number) {
  if (!editing.value || isBuiltinEditing.value) return;
  const episode = editing.value.episodes[episodeIndex];
  if (!episode?.actions?.length) return;
  const actions = episode.actions.filter((_, index) => index !== actionIndex);
  if (actions.length <= 1) {
    const only = actions[0] ?? actionFromEpisode(episode);
    updateEpisode(episodeIndex, {
      groups: [...only.groups],
      palette: only.palette || episode.palette,
      effect: only.effect,
      speed: only.speed,
      intensity: only.intensity,
      cycle_beats: only.cycle_beats ?? null,
      actions: [],
    });
    return;
  }
  updateEpisode(episodeIndex, { actions });
  selectAction(episode.id, Math.max(0, actionIndex - 1));
}

function moveLook(episodeIndex: number, from: number, to: number) {
  if (!editing.value) return;
  const episode = editing.value.episodes[episodeIndex];
  if (!episode?.actions || to < 0 || to >= episode.actions.length) return;
  const actions = episode.actions.map((action) => ({ ...action, groups: [...action.groups] }));
  const [item] = actions.splice(from, 1);
  actions.splice(to, 0, item);
  updateEpisode(episodeIndex, { actions });
  selectAction(episode.id, to);
}

function copyEpisodeCard(index: number) {
  if (!editing.value || isBuiltinEditing.value) return;
  const episodes = [...editing.value.episodes];
  const source = episodes[index];
  if (!source) return;
  episodes.splice(index + 1, 0, cloneEpisode(source));
  editing.value = { ...editing.value, episodes };
}

function toggleActionGroup(episodeIndex: number, actionIndex: number, group: string) {
  if (!editing.value) return;
  const action = editing.value.episodes[episodeIndex]?.actions?.[actionIndex];
  if (!action) return;
  const groups = action.groups.includes(group)
    ? action.groups.filter((item) => item !== group)
    : [...action.groups, group];
  if (groups.length === 0) return;
  updateAction(episodeIndex, actionIndex, { groups });
}

function overlapNote(episode: EpisodeCard, actionIndex: number): string {
  const actions = episode.actions ?? [];
  const fixtures = rigFixtures.value;
  if (!fixtures || actions.length < 2) return "";
  const idsFor = (groups: string[]) =>
    fixtures
      .filter((fixture) => {
        const kind = String(fixture.kind ?? "");
        const groupsOf = Array.isArray(fixture.groups) ? fixture.groups.map(String) : [];
        return kind !== "face_par" && matchesGroups(groupsOf, groups);
      })
      .map((fixture) => String(fixture.id));
  const current = new Set(idsFor(actions[actionIndex]?.groups ?? []));
  const replaced = actions
    .slice(actionIndex + 1)
    .some((action) => idsFor(action.groups).some((id) => current.has(id)));
  return replaced ? "Пізніша дія замінює цей прилад цілком" : "";
}

function updateAction(episodeIndex: number, actionIndex: number, patch: Partial<NonNullable<EpisodeCard["actions"]>[number]>) {
  if (!editing.value) return;
  const episode = editing.value.episodes[episodeIndex];
  if (!episode?.actions?.[actionIndex]) return;
  const actions = episode.actions.map((action, index) =>
    index === actionIndex ? { ...action, ...patch } : action,
  );
  updateEpisode(episodeIndex, { actions });
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

async function retryLoad() {
  loading.value = true;
  error.value = null;
  try {
    await reloadList();
    layout.value = await fetchStageLayout();
    try {
      const [patch, profiles] = await Promise.all([fetchPatch(), fetchProfiles()]);
      rigFixtures.value = Array.isArray(patch.fixtures)
        ? (patch.fixtures as Array<Record<string, unknown>>)
        : null;
      rigProfiles.value = profiles ?? {};
    } catch {
      rigFixtures.value = null;
    }
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка завантаження");
  } finally {
    loading.value = false;
  }
}

watch(catalogKind, (kind) => {
  if (kind === "auto") preview.clear();
});

onMounted(async () => {
  await retryLoad();
  if (catalogKind.value === "auto") return;
  const active = engine.value?.preset_id;
  const pick =
    summaries.value.find((item) => item.id === active && item.id.toUpperCase() !== "NONE") ??
    summaries.value.find((item) => item.id.toUpperCase() !== "NONE");
  if (pick && !error.value) void choose(pick.id);
});

onBeforeUnmount(() => {
  if (hardwareTestActive.value) {
    void stopEditorEpisodePreview().then(() => refreshRest());
  }
});
</script>

<template>
  <div class="presets-view">
    <header class="presets-head">
      <h1>Пресети</h1>
      <div
        class="chips catalog-switch"
        role="tablist"
        aria-label="Каталог"
      >
      <button
        type="button"
        class="chip"
        :class="{ on: catalogKind === 'presets' }"
        role="tab"
        :aria-selected="catalogKind === 'presets'"
        @click="catalogKind = 'presets'"
      >
        Пресети
      </button>
      <button
        type="button"
        class="chip"
        :class="{ on: catalogKind === 'auto' }"
        role="tab"
        :aria-selected="catalogKind === 'auto'"
        data-catalog="auto"
        @click="catalogKind = 'auto'"
      >
        Авто-епізоди
      </button>
      </div>
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
      <button
        type="button"
        class="btn btn--sm"
        style="margin-left: 12px"
        @click="retryLoad"
      >
        Спробувати знову
      </button>
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
          <h2>{{ editorPreview?.preset_label || "Тест" }}</h2>
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
        <div
          v-if="false"
          class="field"
        >
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
        <div
          v-if="false"
          class="field"
        >
          <span>DMX source</span>
          <strong>{{ output?.source_owner ?? "—" }}</strong>
        </div>
        <div
          v-if="false"
          class="field"
        >
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

    <AutoCatalogPanel
      v-if="catalogKind === 'auto'"
      :layout="layout"
      :scene-id="state?.scene_id ?? null"
      :location-id="state?.location_id ?? null"
      :scene-revision="sceneRevision"
      :scene-ready="Boolean(layout) && !error"
      :scene-error="error"
    />

    <div
      v-else
      class="presets-grid"
    >
      <section
        class="card"
        aria-label="Список пресетів"
      >
        <header class="card__head">
          <h2>Каталог</h2>
          <div class="row-actions catalog-actions">
            <button
              type="button"
              class="btn btn--primary"
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
              v-if="selected && !selected.builtin"
              type="button"
              class="btn btn--danger"
              :disabled="saving"
              @click="onDelete(selected.id, selected.builtin)"
            >
              Видалити
            </button>
          </div>
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
            <span class="preset-card__label">{{ presetButtonLabel(preset.id, preset.label) }}</span>
            <span
              v-if="preset.palettes?.length"
              class="palette"
            >
              <i
                v-for="(palette, idx) in preset.palettes"
                :key="idx"
                :style="{ background: paletteCss(palette) }"
              />
            </span>
          </button>
        </div>

      </section>

      <div class="preview-column">
        <StageSimulator
          plain
          :layout="layout"
          :view="preview.view"
          :summary="preview.paletteSample.value ? 'Зразок палітри, не програвання епізоду' : undefined"
          :engine-preset-id="preview.presetId.value ?? '—'"
          :preset-time-s="preview.timeS.value"
          :episode-index="previewEpisode"
          :episode-count="previewCount"
          :cycle-duration-s="selected?.total_duration_s ?? 180"
          :nonzero-channels="1"
          :show-inspector="false"
        />

        <section
          v-if="selected"
          class="card"
          aria-label="Епізоди пресету"
        >
          <header class="card__head">
            <h2>Епізоди</h2>
          </header>
          <div class="episode-strip">
            <button
              v-for="n in previewCount"
              :key="`${selected.id}-${n}`"
              type="button"
              class="episode-chip"
              :class="{ active: n - 1 === previewEpisode }"
              @click="previewFrom(n - 1)"
            >
              <span class="episode-chip__n">{{ n }}</span>
            </button>
          </div>
        </section>
      </div>
    </div>

    <section
      v-if="catalogKind === 'presets'"
      class="card"
      aria-label="Пульт — 9 пресетів"
    >
      <header class="card__head">
        <h2>Пульт</h2>
      </header>
      <div class="form-grid pad-slots">
        <label
          v-for="(_, index) in 9"
          :key="index"
          class="field"
        >
          <span>{{ index + 1 }}</span>
          <select
            :value="padDraft[index] ?? ''"
            :aria-label="`Слот ${index + 1}`"
            :disabled="padSaving"
            @change="setPadSlot(index, ($event.target as HTMLSelectElement).value)"
          >
            <option
              v-for="preset in summaries"
              :key="preset.id"
              :value="preset.id"
              :disabled="padDraft.includes(preset.id) && padDraft[index] !== preset.id"
            >
              {{ presetButtonLabel(preset.id, preset.label) }}
            </option>
          </select>
        </label>
      </div>
      <div class="row-actions">
        <button
          type="button"
          class="btn"
          :disabled="padSaving || padDraft.length !== 9"
          @click="savePadSlots"
        >
          Зберегти склад пульта
        </button>
      </div>
    </section>

    <section
      v-if="catalogKind === 'presets'"
      class="card"
      aria-label="Створити пресет"
    >
      <header class="card__head">
        <h2>Новий пресет</h2>
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
          :disabled="saving"
          @click="onCreate"
        >
          Створити
        </button>
      </div>
    </section>

    <section
      v-if="catalogKind === 'presets' && editing"
      class="card"
      aria-label="Редактор епізодів"
    >
      <header class="card__head">
        <div>
          <h2>Редактор</h2>
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
            Зберегти для локації
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
          <strong>Епізод {{ index + 1 }}</strong>
          <div class="row-actions">
            <button
              type="button"
              class="action-btn"
              :disabled="offline || saving || !hardwarePreviewAllowed"
              @click="playEpisodeOnHardware(index)"
            >
              Відтворити на обладнанні
            </button>
            <IconButton
              icon="up"
              label="Вище"
              :disabled="index === 0"
              @click="moveEpisode(index, index - 1)"
            />
            <IconButton
              icon="down"
              label="Нижче"
              :disabled="index >= editing.episodes.length - 1"
              @click="moveEpisode(index, index + 1)"
            />
            <button
              type="button"
              class="action-btn"
              :disabled="isBuiltinEditing"
              @click="copyEpisodeCard(index)"
            >
              Копія
            </button>
            <button
              type="button"
              class="action-btn action-btn--danger"
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
          <label
            v-if="!ep.actions?.length"
            class="field"
          >
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
                {{ p.replaceAll("_", " ") }}
              </option>
            </select>
          </label>
          <label
            v-if="!ep.actions?.length"
            class="field"
          >
            <span>Ефект</span>
            <select
              :value="ep.effect"
              @change="updateEpisode(index, { effect: ($event.target as HTMLSelectElement).value })"
            >
              <option
                v-for="fx in EFFECTS"
                :key="fx"
                :value="fx"
                :disabled="effectDisabled(ep.groups, ep.effect, fx)"
              >
                {{ fx.replaceAll("_", " ") }}
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
                {{ TRANSITION_LABELS[tr] }}
              </option>
            </select>
          </label>
          <label
            v-if="!ep.actions?.length && effectUsesSpeed(ep.effect)"
            class="field"
          >
            <span class="meter-line"><span>Швидкість</span><span>{{ ep.speed.toFixed(2) }}</span></span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              :value="ep.speed"
              @input="updateEpisode(index, { speed: Number(($event.target as HTMLInputElement).value) })"
            >
          </label>
          <label
            v-if="!ep.actions?.length"
            class="field"
          >
            <span class="meter-line"><span>Інтенсивність</span><span>{{ ep.intensity.toFixed(2) }}</span></span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              :value="ep.intensity"
              @input="updateEpisode(index, { intensity: Number(($event.target as HTMLInputElement).value) })"
            >
          </label>
          <label
            v-if="!ep.actions?.length && editing.musical_version && effectUsesSpeed(ep.effect)"
            class="field"
          >
            <span>Цикл, долі</span>
            <select
              :value="ep.cycle_beats ?? ''"
              @change="updateEpisode(index, { cycle_beats: Number(($event.target as HTMLSelectElement).value) as 1 | 2 | 4 | 8 })"
            >
              <option
                v-for="beats in [1, 2, 4, 8]"
                :key="beats"
                :value="beats"
              >
                {{ beats }}
              </option>
            </select>
          </label>
        </div>

        <div
          v-if="ep.actions?.length"
          class="action-list"
        >
          <div class="episode-strip">
            <button
              v-for="(action, actionIndex) in ep.actions"
              :key="action.id || actionIndex"
              type="button"
              class="episode-chip"
              :class="{ active: actionIndex === actionAt(ep) }"
              @click="selectAction(ep.id, actionIndex)"
            >
              <span class="episode-chip__n">{{ actionIndex + 1 }}</span>
            </button>
            <button
              type="button"
              class="action-btn"
              :disabled="isBuiltinEditing"
              @click="addLook(index)"
            >
              Ще дія
            </button>
          </div>
          <p
            v-if="overlapNote(ep, actionAt(ep))"
            class="hint"
          >
            {{ overlapNote(ep, actionAt(ep)) }}
          </p>
          <div
            v-for="(action, actionIndex) in ep.actions"
            v-show="actionIndex === actionAt(ep)"
            :key="`${action.id || actionIndex}-fields`"
            class="form-grid"
          >
            <label class="field">
              <span>Ефект</span>
              <select
                :value="action.effect"
                @change="updateAction(index, actionIndex, { effect: ($event.target as HTMLSelectElement).value })"
              >
                <option
                  v-for="fx in EFFECTS"
                  :key="fx"
                  :value="fx"
                  :disabled="effectDisabled(action.groups, action.effect, fx)"
                >
                  {{ fx.replaceAll("_", " ") }}
                </option>
              </select>
            </label>
            <label class="field">
              <span>Палітра</span>
              <select
                :value="action.palette || ep.palette"
                @change="updateAction(index, actionIndex, { palette: ($event.target as HTMLSelectElement).value })"
              >
                <option
                  v-for="p in PALETTES"
                  :key="p"
                  :value="p"
                >
                  {{ p.replaceAll("_", " ") }}
                </option>
              </select>
            </label>
            <label
              v-if="effectUsesSpeed(action.effect)"
              class="field"
            >
              <span class="meter-line"><span>Швидкість</span><span>{{ Number(action.speed ?? 0).toFixed(2) }}</span></span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                :value="action.speed"
                @input="updateAction(index, actionIndex, { speed: Number(($event.target as HTMLInputElement).value) })"
              >
            </label>
            <label class="field">
              <span class="meter-line"><span>Інтенсивність</span><span>{{ Number(action.intensity ?? 0).toFixed(2) }}</span></span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                :value="action.intensity"
                @input="updateAction(index, actionIndex, { intensity: Number(($event.target as HTMLInputElement).value) })"
              >
            </label>
            <label
              v-if="editing.musical_version && effectUsesSpeed(action.effect)"
              class="field"
            >
              <span>Цикл, долі</span>
              <select
                :value="action.cycle_beats ?? ''"
                @change="updateAction(index, actionIndex, { cycle_beats: Number(($event.target as HTMLSelectElement).value) as 1 | 2 | 4 | 8 })"
              >
                <option
                  v-for="beats in [1, 2, 4, 8]"
                  :key="beats"
                  :value="beats"
                >
                  {{ beats }}
                </option>
              </select>
            </label>
          </div>
          <div class="row-actions">
            <IconButton
              icon="up"
              label="Вище"
              :disabled="actionAt(ep) === 0"
              @click="moveLook(index, actionAt(ep), actionAt(ep) - 1)"
            />
            <IconButton
              icon="down"
              label="Нижче"
              :disabled="actionAt(ep) >= ep.actions.length - 1"
              @click="moveLook(index, actionAt(ep), actionAt(ep) + 1)"
            />
            <button
              type="button"
              class="action-btn"
              :disabled="isBuiltinEditing"
              @click="copyLook(index, actionAt(ep))"
            >
              Копія
            </button>
            <button
              type="button"
              class="action-btn"
              :disabled="isBuiltinEditing"
              @click="removeLook(index, actionAt(ep))"
            >
              Прибрати дію
            </button>
          </div>
          <div class="chips">
            <button
              v-for="group in lookGroups(ep.actions[actionAt(ep)]?.groups ?? [])"
              :key="group"
              type="button"
              class="chip"
              :class="{ on: ep.actions[actionAt(ep)]?.groups.includes(group) }"
              @click="toggleActionGroup(index, actionAt(ep), group)"
            >
              {{ group.replaceAll("_", " ") }}
            </button>
          </div>
        </div>

        <div
          v-if="!ep.actions?.length"
          class="chips"
        >
          <button
            v-for="group in groupsFor(ep)"
            :key="group"
            type="button"
            class="chip"
            :class="{ on: ep.groups.includes(group) }"
            @click="toggleGroup(index, group)"
          >
            {{ group.replaceAll("_", " ") }}
          </button>
        </div>
        <div
          v-if="!ep.actions?.length"
          class="row-actions"
        >
          <button
            type="button"
            class="action-btn"
            :disabled="isBuiltinEditing"
            @click="addLook(index)"
          >
            Ще дія
          </button>
        </div>
        <p
          v-for="note in episodeNotes(ep)"
          :key="note"
          class="hint"
        >
          {{ note }}
        </p>
      </div>
    </section>
  </div>
</template>
