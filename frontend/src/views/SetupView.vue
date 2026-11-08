<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import {
  fetchAppConfig,
  fetchPatch,
  fetchProfiles,
  fetchReadiness,
  saveAppConfig,
  savePatch,
  setupPost,
  beamCalibrationEnd,
  validatePatch,
} from "../api/setup";
import { fetchStageLayout, postCommand, activateArtNet, deactivateArtNet, armOutput, disarmOutput, fetchArmBlockers } from "../api/client";
import HardwareBadge from "../components/setup/HardwareBadge.vue";
import BeamCalibrationCard from "../components/setup/BeamCalibrationCard.vue";
import { applyBeamDraft, beamDraftFromSpatial, type BeamDraft } from "../components/setup/beamDraft";
import StageSimulator from "../components/simulator/StageSimulator.vue";
import { isCloudAdminBuild } from "../cloud/mode";
import { publishLocationDraft, savedLocationMessage, sceneSyncDetail, sceneSyncPhase } from "../runtime/scenePort";
import { validatePatchShape, type FieldIssue } from "../runtime/sceneValidate";
import { APP_STATE_KEY } from "../composables/appStateKey";
import type { BeamCalibrationBeamView, StageLayout } from "../vite-env";

const appState = inject(APP_STATE_KEY, null);
const cloudScene = isCloudAdminBuild();

const STEPS = [
  "Mock / Art-Net",
  "Мережа, IP, Universe, FPS",
  "Список приладів",
  "DMX patch",
  "Профілі / канали",
  "Raw DMX tester",
  "Розташування",
  "Калібрація Bars / Beam",
  "Тест групи / Blackout",
  "Зведення готовності",
] as const;

const step = ref(0);
const loading = ref(true);
const saving = ref(false);
const message = ref<string | null>(null);
const error = ref<string | null>(null);
const patchErrors = ref<string[]>([]);
const readiness = ref<Record<string, unknown> | null>(null);

const app = ref<Record<string, unknown> | null>(null);
const patch = ref<Record<string, unknown> | null>(null);
const profiles = ref<Record<string, Record<string, unknown>>>({});
const stageLayout = ref<StageLayout | null>(null);
const beamDrafts = reactive<Record<string, BeamDraft>>({});
const fieldIssues = ref<FieldIssue[]>([]);
const dirty = ref(false);
const baselineJson = ref("");
const sceneKey = computed(() => String(appState?.state?.value?.scene_id || "local"));

const SETUP_STASH = "orng-unsaved-setup:";

function stable(value: unknown): string {
  return JSON.stringify(value);
}

function writeStash(key: string) {
  if (!dirty.value || !patch.value) return;
  sessionStorage.setItem(
    SETUP_STASH + key,
    JSON.stringify({ patch: patch.value, beams: beamDrafts }),
  );
}

function readStash(key: string): { patch: Record<string, unknown>; beams: Record<string, BeamDraft> } | null {
  const raw = sessionStorage.getItem(SETUP_STASH + key);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { patch?: Record<string, unknown>; beams?: Record<string, BeamDraft> };
    if (!parsed.patch) return null;
    return { patch: parsed.patch, beams: parsed.beams ?? {} };
  } catch {
    return null;
  }
}

function clearStash(key: string) {
  sessionStorage.removeItem(SETUP_STASH + key);
}

function replaceDrafts(next: Record<string, BeamDraft>) {
  for (const key of Object.keys(beamDrafts)) delete beamDrafts[key];
  for (const [key, value] of Object.entries(next)) beamDrafts[key] = value;
}

function draftsFromPatch(doc: Record<string, unknown>): Record<string, BeamDraft> {
  const list = Array.isArray(doc.fixtures) ? doc.fixtures : [];
  const next: Record<string, BeamDraft> = {};
  for (const raw of list) {
    if (!raw || typeof raw !== "object") continue;
    const fixture = raw as Record<string, unknown>;
    if (fixture.kind !== "beam") continue;
    const spatial = fixture.spatial && typeof fixture.spatial === "object"
      ? (fixture.spatial as Record<string, unknown>)
      : undefined;
    next[String(fixture.id)] = beamDraftFromSpatial(spatial);
  }
  return next;
}

const rawChannel = ref(1);
const rawValue = ref(0);

const artnet = computed(() => {
  const cfg = app.value?.artnet as Record<string, unknown> | undefined;
  return cfg ?? {};
});

const fixtures = computed(() => {
  const list = patch.value?.fixtures;
  return Array.isArray(list) ? (list as Record<string, unknown>[]) : [];
});

/** Beam Left / Beam Right cards, ordered left → right (falls back to fixture id). */
const beamFixturesOrdered = computed(() => {
  const beams = fixtures.value.filter((fx) => fx.kind === "beam");
  const sideOrder = (fx: Record<string, unknown>): number => {
    const side = String((fx.spatial as Record<string, unknown> | undefined)?.side ?? "");
    if (side === "left") return 0;
    if (side === "right") return 1;
    return 2;
  };
  return [...beams].sort((a, b) => {
    const diff = sideOrder(a) - sideOrder(b);
    if (diff !== 0) return diff;
    return String(a.id).localeCompare(String(b.id));
  });
});

const fieldErrors = computed(() => {
  const map: Record<string, Record<string, string>> = {};
  for (const issue of fieldIssues.value) {
    const row = map[issue.id] ?? {};
    row[issue.field] = issue.message;
    map[issue.id] = row;
  }
  return map;
});

function mergedPatch(): Record<string, unknown> | null {
  if (!patch.value) return null;
  const list = fixtures.value.map((fx) => {
    const draft = beamDrafts[String(fx.id)];
    if (!draft) return fx;
    const spatial = applyBeamDraft(
      { ...((fx.spatial as Record<string, unknown> | undefined) ?? {}) },
      draft,
    );
    return { ...fx, spatial };
  });
  return { ...patch.value, fixtures: list };
}

function onBeamEdit(id: string, draft: BeamDraft) {
  beamDrafts[id] = draft;
  dirty.value = true;
  fieldIssues.value = fieldIssues.value.filter((issue) => issue.id !== id);
}

const beamCalibrationState = computed(() => appState?.state.value?.beam_calibration ?? null);
const beamSession = computed(() => beamCalibrationState.value?.session ?? null);

function beamStateFor(fixtureId: string): BeamCalibrationBeamView | null {
  return (
    beamCalibrationState.value?.beams.find((entry) => entry.fixture_id === fixtureId) ?? null
  );
}

async function endBeamCalibrationIfActive() {
  if (!beamSession.value?.active) return;
  try {
    await beamCalibrationEnd();
    await appState?.refreshRest?.();
  } catch (err) {
    if (!error.value) {
      error.value = err instanceof Error ? err.message : "Не вдалося завершити калібрування";
    }
  }
}

const artnetBusy = ref(false);
const armBlockers = ref<string[]>([]);

const runtimeOutput = computed(() => appState?.output.value ?? null);
const runtimeEngine = computed(() => appState?.engine.value ?? null);

/** Authoritative Raw session from backend AppState (not local-only refs). */
const rawSession = computed(() => appState?.state.value?.raw_tester ?? null);
const rawActive = computed(() => Boolean(rawSession.value?.active));
const rawNonzero = computed(() => Number(rawSession.value?.nonzero_channels ?? 0));
const rawPreparedChannels = computed(() => {
  const prepared = (
    rawSession.value as { prepared_channels?: Array<{ channel: number; value: number }> } | null
  )?.prepared_channels;
  if (Array.isArray(prepared) && prepared.length > 0) {
    return prepared;
  }
  const frame = rawSession.value?.frame;
  if (!Array.isArray(frame)) return [] as Array<{ channel: number; value: number }>;
  return frame
    .map((value, index) => ({ channel: index + 1, value: Number(value) }))
    .filter((entry) => entry.value > 0);
});

const sourceOwnerLabel = computed(() => {
  const owner = runtimeOutput.value?.source_owner ?? "none";
  if (owner === "raw_tester") return "RAW";
  if (owner === "engine") return "Engine";
  return "none";
});

const canEnableArm = computed(() => {
  const out = runtimeOutput.value;
  if (!out) return false;
  if (out.armed) return false;
  // Backend /api/output/arm-blockers is the sole safety source of truth.
  // Do not gate on wire_nonzero_channels — safe Beam Pan/Tilt may be non-zero.
  return armBlockers.value.length === 0;
});

const canDisableArm = computed(() => Boolean(runtimeOutput.value?.armed));

async function refreshArmBlockers() {
  try {
    const payload = await fetchArmBlockers();
    armBlockers.value = payload.blockers ?? [];
  } catch {
    armBlockers.value = ["Не вдалося отримати список блокувальників Arm"];
  }
}

watch(
  () => [
    runtimeOutput.value?.transport,
    runtimeOutput.value?.udp_active,
    runtimeOutput.value?.network_allowed,
    runtimeOutput.value?.armed,
    runtimeOutput.value?.wire_nonzero_channels,
    runtimeOutput.value?.source_nonzero_channels,
    runtimeEngine.value?.blackout,
  ],
  () => {
    void refreshArmBlockers();
  },
);

async function activateArtNetSafe() {
  const confirmed = window.confirm(
    "Безпечно активувати Art-Net?\n\n" +
      "Перше натискання почне РЕАЛЬНУ відправку нульових ArtDmx-пакетів " +
      "на контролер (UDP 6454). Вивід лишиться disarmed, Blackout увімкнений.\n\n" +
      "Продовжити?",
  );
  if (!confirmed) {
    setStatus("Активацію Art-Net скасовано");
    return;
  }
  artnetBusy.value = true;
  try {
    await activateArtNet(true);
    await appState?.refreshRest?.();
    readiness.value = await fetchReadiness();
    await refreshArmBlockers();
    setStatus(
      "Art-Net активовано: runtime=artnet, UDP увімкнено, armed=false, Blackout, лише нульові кадри",
    );
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося активувати Art-Net");
  } finally {
    artnetBusy.value = false;
  }
}

async function returnToMock() {
  artnetBusy.value = true;
  try {
    await deactivateArtNet();
    await appState?.refreshRest?.();
    readiness.value = await fetchReadiness();
    await refreshArmBlockers();
    setStatus("Runtime повернуто до Mock: UDP закрито, armed=false, Blackout");
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося повернути Mock");
  } finally {
    artnetBusy.value = false;
  }
}

async function enableArmSafe() {
  const confirmed = window.confirm(
    "Увімкнути Arm?\n\n" +
      "Arm підготовлює РЕАЛЬНИЙ вихід Art-Net. Blackout залишиться увімкненим — " +
      "поки Blackout увімкнений, на дріт ітимуть лише нулі.\n\n" +
      "Ненульовий кадр стане можливим лише після окремого зняття Blackout.\n\n" +
      "Продовжити?",
  );
  if (!confirmed) {
    setStatus("Увімкнення Arm скасовано");
    return;
  }
  artnetBusy.value = true;
  try {
    await armOutput(true);
    await appState?.refreshRest?.();
    readiness.value = await fetchReadiness();
    await refreshArmBlockers();
    setStatus(
      "Arm увімкнено: armed=true, Blackout лишився увімкненим, wire лише нулі",
    );
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося увімкнути Arm");
    await refreshArmBlockers();
  } finally {
    artnetBusy.value = false;
  }
}

async function disableArm() {
  artnetBusy.value = true;
  try {
    await disarmOutput();
    await appState?.refreshRest?.();
    readiness.value = await fetchReadiness();
    await refreshArmBlockers();
    setStatus(
      "Arm вимкнено: armed=false, Blackout увімкнено, wire нулі; Art-Net UDP лишився активним",
    );
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося вимкнути Arm");
  } finally {
    artnetBusy.value = false;
  }
}

function setStatus(ok: string | null, err: string | null = null) {
  message.value = ok;
  error.value = err;
}

async function loadAll(options?: { keepForm?: boolean }) {
  if (options?.keepForm && dirty.value && patch.value) {
    try {
      const remote = await fetchPatch();
      if (stable(remote) !== baselineJson.value) {
        error.value = "З’явилась новіша сцена. Несохранені правки лишились на сторінці.";
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : "Помилка завантаження";
    }
    return;
  }
  loading.value = true;
  error.value = null;
  try {
    const [appCfg, patchCfg, profileMap, ready] = await Promise.all([
      fetchAppConfig(),
      fetchPatch(),
      fetchProfiles(),
      fetchReadiness(),
    ]);
    app.value = appCfg;
    profiles.value = profileMap;
    readiness.value = ready;
    baselineJson.value = stable(patchCfg);
    const stashed = readStash(sceneKey.value);
    if (stashed) {
      patch.value = stashed.patch;
      replaceDrafts(stashed.beams);
      dirty.value = true;
    } else {
      patch.value = patchCfg;
      replaceDrafts(draftsFromPatch(patchCfg));
      dirty.value = false;
    }
    stageLayout.value = await fetchStageLayout().catch(() => null);
    await refreshArmBlockers();
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка завантаження");
  } finally {
    loading.value = false;
  }
}

async function persistApp() {
  if (!app.value) return;
  saving.value = true;
  try {
    await saveAppConfig(app.value);
    setStatus("App config збережено (Mock output, без arm)");
    readiness.value = await fetchReadiness();
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Помилка збереження app");
  } finally {
    saving.value = false;
  }
}

async function runValidatePatch() {
  const snapshot = mergedPatch();
  if (!snapshot) return;
  const local = validatePatchShape(snapshot, profiles.value);
  fieldIssues.value = local.fields;
  if (!local.ok) {
    patchErrors.value = local.errors;
    setStatus(null, local.errors[0] ?? "Patch має помилки — збереження заблоковано");
    return;
  }
  const result = await validatePatch(snapshot);
  patchErrors.value = result.errors;
  if (result.ok) setStatus("Patch валідний (перетини не знайдені)");
  else setStatus(null, result.errors[0] ?? "Patch має помилки — збереження заблоковано");
}

async function enterRaw() {
  await setupPost("raw-tester/enter");
  await appState?.refreshRest?.();
  setStatus("Raw tester активний (підготовлений source; не вмикає Art-Net / Arm)");
}

async function exitRaw() {
  await setupPost("raw-tester/exit");
  await appState?.refreshRest?.();
  setStatus("Raw tester вимкнено: Blackout + нулі, підготовлений source очищено");
}

async function rawBlackout() {
  await setupPost("raw-tester/blackout");
  await appState?.refreshRest?.();
  setStatus("Raw Blackout (канали обнулено, сесія лишилась активною)");
}

async function setRawChannel() {
  await setupPost("raw-tester/set", {
    channel: rawChannel.value,
    value: rawValue.value,
  });
  await appState?.refreshRest?.();
}

async function identifyFixture(id: string) {
  await setupPost("identify-fixture", { fixture_id: id, level: 200 });
  await appState?.refreshRest?.();
  setStatus(`Identify ${id}`);
}

async function identifyGroup(group: string) {
  await setupPost("identify-group", { group, level: 180 });
  await appState?.refreshRest?.();
  setStatus(`Identify group ${group}`);
}

async function toggleBlackout(enabled: boolean) {
  await postCommand("blackout", { enabled });
  await appState?.refreshRest?.();
  setStatus(enabled ? "Blackout увімкнено" : "Blackout вимкнено");
}

function updateFixtureAddress(index: number, value: string) {
  const list = fixtures.value;
  if (!list[index]) return;
  const text = value.trim();
  list[index].start_address = /^-?\d+$/.test(text) ? Number(text) : value;
  patch.value = { ...patch.value!, fixtures: list };
  dirty.value = true;
  const id = String(list[index].id ?? "");
  fieldIssues.value = fieldIssues.value.filter((issue) => issue.id !== id || issue.field !== "start_address");
}

function updateSpatial(fixtureId: string, field: string, value: boolean) {
  const list = fixtures.value.map((fx) => {
    if (fx.id !== fixtureId) return fx;
    const spatial = { ...(fx.spatial as Record<string, unknown>), [field]: value };
    return { ...fx, spatial };
  });
  patch.value = { ...patch.value!, fixtures: list };
  dirty.value = true;
}

function restoreSaved() {
  clearStash(sceneKey.value);
  dirty.value = false;
  fieldIssues.value = [];
  patchErrors.value = [];
  void loadAll();
}

watch(step, async (next, previous) => {
  if (previous === 7 && next !== 7) {
    await endBeamCalibrationIfActive();
  }
  if (next === 9) {
    readiness.value = await fetchReadiness();
  }
});

async function checkedSnapshot(): Promise<Record<string, unknown> | null> {
  const snapshot = mergedPatch();
  if (!snapshot) return null;
  const local = validatePatchShape(snapshot, profiles.value);
  fieldIssues.value = local.fields;
  if (!local.ok) {
    patchErrors.value = local.errors;
    setStatus(null, local.errors[0] ?? "Виправте помилки до збереження");
    return null;
  }
  const remote = await validatePatch(snapshot);
  if (!remote.ok) {
    patchErrors.value = remote.errors;
    setStatus(null, remote.errors[0] ?? "Виправте помилки до збереження");
    return null;
  }
  patchErrors.value = [];
  return mergedPatch();
}

async function saveForLocation() {
  if (!patch.value || saving.value) return;
  saving.value = true;
  try {
    const snapshot = await checkedSnapshot();
    if (!snapshot) return;
    const remotePatch = await fetchPatch();
    if (stable(remotePatch) !== baselineJson.value) {
      setStatus(null, "З’явилась новіша сцена. Правки на сторінці не замінені.");
      return;
    }
    const writing = await checkedSnapshot();
    if (!writing) return;
    await savePatch(writing);
    baselineJson.value = stable(writing);
    await publishLocationDraft();
    const latest = mergedPatch();
    if (latest && stable(latest) !== stable(writing)) {
      await savePatch(latest);
      dirty.value = true;
      writeStash(sceneKey.value);
      setStatus("Опубліковано попередні правки. Новіші лишились у чернетці.");
      return;
    }
    if (sceneSyncPhase.value === "conflict" || sceneSyncPhase.value === "failed") {
      dirty.value = true;
      setStatus(null, sceneSyncDetail.value || "Публікація не вдалася");
      return;
    }
    dirty.value = false;
    clearStash(sceneKey.value);
    patch.value = writing;
    replaceDrafts(draftsFromPatch(writing));
    setStatus(savedLocationMessage());
  } catch (err) {
    dirty.value = true;
    const failed = sceneSyncPhase.value === "conflict" || sceneSyncPhase.value === "failed";
    setStatus(null, failed
      ? (sceneSyncDetail.value || "Публікація не вдалася")
      : (err instanceof Error ? err.message : "Не вдалося зберегти"));
  } finally {
    saving.value = false;
  }
}

watch(sceneKey, async (next, prev) => {
  if (!prev || next === prev) return;
  if (dirty.value) writeStash(prev);
  dirty.value = false;
  fieldIssues.value = [];
  await loadAll();
});

onMounted(() => {
  void loadAll();
});

onBeforeUnmount(() => {
  if (dirty.value) writeStash(sceneKey.value);
  void endBeamCalibrationIfActive();
});
</script>

<template>
  <section
    class="page setup-wizard"
    aria-label="Калібрування"
  >
    <header class="page-title">
      <h1>Калібрування</h1>
      <button
        type="button"
        class="action-btn"
        :disabled="saving"
        @click="saveForLocation"
      >
        Зберегти для локації
      </button>
      <button
        v-if="dirty"
        type="button"
        class="action-btn"
        :disabled="saving"
        @click="restoreSaved"
      >
        Повернути збережене
      </button>
    </header>

    <p
      v-if="loading"
      class="banner"
    >
      Завантаження конфігурації…
    </p>
    <p
      v-else-if="error"
      class="banner warn"
      role="alert"
    >
      {{ error }}
      <button
        type="button"
        class="btn btn--sm"
        style="margin-left: 12px"
        @click="loadAll({ keepForm: true })"
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
    </p>

    <nav
      v-show="false"
      class="wizard-steps"
      aria-label="Кроки майстра"
    >
      <button
        v-for="(label, index) in STEPS"
        :key="label"
        type="button"
        class="wizard-step"
        :class="{ active: step === index }"
        :aria-current="step === index ? 'step' : undefined"
        @click="step = index"
      >
        <span class="num">{{ index + 1 }}</span>
        <span class="label">{{ label }}</span>
      </button>
    </nav>

    <div
      v-if="!loading && app && patch"
      class="wizard-panel"
    >
      <!-- 1 Transport -->
      <div v-show="false">
        <h2>1. Mock / Art-Net</h2>
        <HardwareBadge />
        <label class="field">
          Бажаний transport (YAML)
          <select v-model="app.transport">
            <option value="mock">Mock</option>
            <option value="artnet">{{ cloudScene ? "Art-Net" : "Art-Net (конфіг лише, без arm)" }}</option>
          </select>
        </label>
        <p class="hint">
          <template v-if="cloudScene">
            Збереження публікує нову ревізію сцени. Raspberry застосовує її окремо
            і не вважається синхронізованою, доки пристрій не підтвердить revision.
            Ця сторінка не знімає blackout і не змінює arm.
          </template>
          <template v-else>
            Runtime залишається на Mock і не вмикає реальну мережу, навіть якщо в YAML
            вибрано Art-Net. Збереження конфігу лише записує бажаний transport.
          </template>
        </p>
        <button
          type="button"
          class="action-btn"
          :disabled="saving"
          @click="persistApp"
        >
          Зберегти
        </button>

        <section
          v-show="false"
          class="artnet-runtime"
          aria-label="Фактичний runtime Art-Net"
        >
          <h3>Фактичний runtime</h3>
          <ul class="kv">
            <li>
              <span>Бажаний (YAML)</span>
              <strong>{{ String(app?.transport ?? "mock") }}</strong>
            </li>
            <li>
              <span>Runtime transport</span>
              <strong>{{ runtimeOutput?.transport ?? "mock" }}</strong>
            </li>
            <li>
              <span>UDP / Art-Net</span>
              <strong>{{ runtimeOutput?.udp_active ? "активний" : "вимкнено" }}</strong>
            </li>
            <li>
              <span>armed</span>
              <strong>{{ runtimeOutput?.armed ? "true" : "false" }}</strong>
            </li>
            <li>
              <span>Blackout</span>
              <strong>{{ runtimeEngine?.blackout ? "увімкнено" : "вимкнено" }}</strong>
            </li>
            <li>
              <span>Source {{ sourceOwnerLabel }}</span>
              <strong>
                Σ{{ runtimeOutput?.source_frame_sum ?? 0 }} /
                {{ runtimeOutput?.source_nonzero_channels ?? 0 }}
              </strong>
            </li>
            <li>
              <span>Wire</span>
              <strong>
                Σ{{ runtimeOutput?.wire_frame_sum ?? runtimeOutput?.frame_sum ?? 0 }} /
                {{ runtimeOutput?.wire_nonzero_channels ?? runtimeOutput?.nonzero_channels ?? 0 }}
              </strong>
            </li>
            <li>
              <span>target</span>
              <strong>
                {{ runtimeOutput?.target_ip ?? artnet.target_ip ?? "—" }}
                · U{{ runtimeOutput?.universe ?? artnet.universe ?? 0 }}
              </strong>
            </li>
          </ul>
          <p class="hint">
            Кнопка нижче вмикає реальний UDP і одразу починає слати лише нульові
            ArtDmx-кадри. Arm і зняття Blackout — окремі наступні кроки.
          </p>
          <div class="artnet-runtime__actions">
            <button
              type="button"
              class="action-btn action-btn--danger"
              :disabled="artnetBusy || runtimeOutput?.transport === 'artnet'"
              @click="activateArtNetSafe"
            >
              Безпечно активувати Art-Net
            </button>
            <button
              type="button"
              class="action-btn"
              :disabled="artnetBusy || runtimeOutput?.transport !== 'artnet'"
              @click="returnToMock"
            >
              Повернути runtime до Mock
            </button>
          </div>

          <div
            class="artnet-arm"
            aria-label="Керування Arm"
          >
            <h3>Arm</h3>
            <p class="hint">
              Arm лише готує реальний вихід. Поки Blackout увімкнений, wire-кадр
              лишається нульовим. У Mock Arm неможливий.
            </p>
            <div class="artnet-runtime__actions">
              <button
                type="button"
                class="action-btn action-btn--danger"
                :disabled="artnetBusy || !canEnableArm"
                @click="enableArmSafe"
              >
                Увімкнути Arm
              </button>
              <button
                type="button"
                class="action-btn"
                :disabled="artnetBusy || !canDisableArm"
                @click="disableArm"
              >
                Вимкнути Arm
              </button>
            </div>
            <ul
              v-if="!canEnableArm && !runtimeOutput?.armed && armBlockers.length"
              class="arm-blockers"
            >
              <li
                v-for="reason in armBlockers"
                :key="reason"
              >
                {{ reason }}
              </li>
            </ul>
          </div>
        </section>
      </div>

      <!-- 2 Network -->
      <div v-show="false">
        <h2>2. Мережа, IP, Universe, FPS</h2>
        <HardwareBadge />
        <label class="field">
          Target IP
          <input
            :value="String(artnet.target_ip ?? '')"
            placeholder="напр. 10.0.0.50"
            @input="
              (app.artnet as Record<string, unknown>).target_ip =
                ($event.target as HTMLInputElement).value || null
            "
          >
        </label>
        <label class="field">
          Universe
          <input
            type="number"
            min="0"
            max="15"
            :value="Number(artnet.universe ?? 0)"
            @input="(app.artnet as Record<string, unknown>).universe = Number(($event.target as HTMLInputElement).value)"
          >
        </label>
        <label class="field">
          FPS
          <input
            type="number"
            min="1"
            max="60"
            :value="Number(artnet.fps ?? 30)"
            @input="(app.artnet as Record<string, unknown>).fps = Number(($event.target as HTMLInputElement).value)"
          >
        </label>
        <label class="field">
          UDP port
          <input
            type="number"
            :value="Number(artnet.udp_port ?? 6454)"
            @input="(app.artnet as Record<string, unknown>).udp_port = Number(($event.target as HTMLInputElement).value)"
          >
        </label>
        <p class="hint">
          Discovery контролера не виконується. hardware_verified завжди false на HOME.
        </p>
        <button
          type="button"
          class="action-btn"
          :disabled="saving"
          @click="persistApp"
        >
          Зберегти мережеві поля
        </button>
      </div>

      <!-- 3 Fixture list -->
      <div>
        <h2>Прилади</h2>
        <ul class="fixture-list">
          <li
            v-for="fx in fixtures"
            :key="String(fx.id)"
          >
            <strong>{{ fx.label }}</strong>
          </li>
        </ul>
      </div>

      <!-- 4 Patch -->
      <div v-show="false">
        <h2>4. DMX patch</h2>
        <p class="hint">
          Помилки перетинів видно до збереження.
        </p>
        <div class="table">
          <div
            v-for="(fx, index) in fixtures"
            :key="String(fx.id)"
            class="table-row"
          >
            <span>{{ fx.label }}</span>
            <label>
              Start
              <input
                type="number"
                min="1"
                max="512"
                :value="fx.start_address ?? ''"
                @input="updateFixtureAddress(index, ($event.target as HTMLInputElement).value)"
              >
              <p
                v-if="fieldErrors[String(fx.id)]?.start_address"
                class="hint beam-card__status--error"
                role="alert"
              >
                {{ fieldErrors[String(fx.id)]?.start_address }}
              </p>
            </label>
          </div>
        </div>
        <ul
          v-if="patchErrors.length"
          class="errors"
          role="alert"
        >
          <li
            v-for="err in patchErrors"
            :key="err"
          >
            {{ err }}
          </li>
        </ul>
        <div class="row-actions">
          <button
            type="button"
            class="action-btn"
            @click="runValidatePatch"
          >
            Перевірити
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="saving || patchErrors.length > 0"
            @click="saveForLocation"
          >
            Зберегти patch
          </button>
        </div>
      </div>

      <!-- 5 Profiles → canonical channel mapper -->
      <div v-show="false">
        <h2>5. Профілі приладів / Налаштування каналів</h2>
        <HardwareBadge />
        <p class="hint">
          Призначення каналів, ползунки 0–255 і збереження мапінгу зібрані на окремій
          сторінці. Це єдиний шлях для визначення фізичних функцій PAR / LED Bar / Beam.
        </p>
        <ol class="hint">
          <li>Відкрийте «Налаштування каналів».</li>
          <li>Оберіть тип і конкретний фізичний прилад.</li>
          <li>Натисніть «Почати тестування» і рухайте ползунки (можна кілька одночасно).</li>
          <li>Призначте функцію кожного каналу та робочі значення (White, Shutter Open…).</li>
          <li>Натисніть «Зберегти мапінг».</li>
        </ol>
        <div class="row-actions">
          <RouterLink
            class="action-btn"
            to="/channels"
          >
            Відкрити налаштування каналів
          </RouterLink>
        </div>
        <p class="hint">
          Шлях у меню: <strong>Налаштування каналів</strong> (або Setup → пункт 5 → ця кнопка).
        </p>
      </div>

      <!-- 6 Raw tester -->
      <div v-show="false">
        <h2>6. Raw DMX tester</h2>
        <p class="hint">
          Підготовлений source-кадр. Не вмикає Art-Net, UDP чи Arm і не знімає
          Blackout. Сесія зберігається між кроками майстра, поки не натиснуто
          «Вийти та скинути в нулі».
        </p>
        <div class="row-actions">
          <button
            type="button"
            class="action-btn"
            :disabled="rawActive"
            @click="enterRaw"
          >
            Увійти
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="!rawActive"
            @click="exitRaw"
          >
            Вийти та скинути в нулі
          </button>
          <button
            type="button"
            class="blackout-btn"
            @click="rawBlackout"
          >
            BLACKOUT
          </button>
        </div>
        <div class="raw-edit">
          <label class="field">
            Глобальний канал
            <input
              v-model.number="rawChannel"
              type="number"
              min="1"
              max="512"
            >
          </label>
          <label class="field">
            Значення
            <input
              v-model.number="rawValue"
              type="number"
              min="0"
              max="255"
            >
          </label>
          <button
            type="button"
            class="action-btn"
            :disabled="!rawActive"
            @click="setRawChannel"
          >
            Встановити
          </button>
        </div>
        <p>
          Статус: {{ rawActive ? "активний" : "вимкнений" }} · nonzero
          {{ rawNonzero }}
        </p>
        <ul
          v-if="rawPreparedChannels.length"
          class="raw-prepared"
        >
          <li
            v-for="entry in rawPreparedChannels"
            :key="entry.channel"
          >
            CH {{ entry.channel }} = {{ entry.value }}
          </li>
        </ul>
        <p
          v-else
          class="hint"
        >
          Підготовлених ненульових каналів немає.
        </p>
      </div>

      <!-- 8 Calibration -->
      <div>
        <h2>Промені</h2>
        <div class="invert-list">
        <div
          v-for="fx in fixtures.filter((f) => f.kind === 'bar')"
          :key="String(fx.id)"
          class="invert-row"
        >
          <strong>{{ fx.label }}</strong>
          <button
            type="button"
            class="action-btn"
            :class="{ active: Boolean((fx.spatial as Record<string, unknown>).invert_segments) }"
            :aria-pressed="Boolean((fx.spatial as Record<string, unknown>).invert_segments)"
            @click="updateSpatial(String(fx.id), 'invert_segments', !Boolean((fx.spatial as Record<string, unknown>).invert_segments))"
          >
            Інверсія
          </button>
        </div>
        </div>
        <ul
          v-if="patchErrors.length"
          class="errors"
          role="alert"
        >
          <li
            v-for="err in patchErrors"
            :key="err"
          >
            {{ err }}
          </li>
        </ul>

        <h3>Голови</h3>
        <div class="beam-cal-grid">
          <template
            v-for="fx in beamFixturesOrdered"
            :key="String(fx.id)"
          >
            <BeamCalibrationCard
              v-if="beamDrafts[String(fx.id)]"
              :fixture="{ id: String(fx.id), label: String(fx.label) }"
              :draft="beamDrafts[String(fx.id)]!"
              :errors="fieldErrors[String(fx.id)]"
              :beam-state="beamStateFor(String(fx.id))"
              :session="beamSession"
              @edit="onBeamEdit(String(fx.id), $event)"
            />
          </template>
        </div>
      </div>

      <!-- 9 Tests -->
      <div v-show="false">
        <h2>9. Тест приладу / групи / Blackout</h2>
        <HardwareBadge />
        <div class="row-actions">
          <button
            type="button"
            class="action-btn"
            @click="identifyFixture('par_1')"
          >
            Тест PAR 1
          </button>
          <button
            type="button"
            class="action-btn"
            @click="identifyGroup('par')"
          >
            Група PAR
          </button>
          <button
            type="button"
            class="action-btn"
            @click="identifyGroup('bar')"
          >
            Група Bar
          </button>
          <button
            type="button"
            class="blackout-btn"
            @click="toggleBlackout(true)"
          >
            Blackout увімк.
          </button>
          <button
            type="button"
            class="action-btn"
            @click="toggleBlackout(false)"
          >
            Blackout вимк.
          </button>
          <button
            type="button"
            class="action-btn"
            @click="exitRaw"
          >
            Вийти та скинути в нулі
          </button>
        </div>
      </div>

      <!-- 10 Readiness -->
      <div v-show="false">
        <h2>10. Зведення готовності</h2>
        <HardwareBadge />
        <ul
          v-if="readiness"
          class="ready-list"
        >
          <li>Бажаний транспорт (YAML): {{ readiness.transport_preferred }}</li>
          <li>Робочий транспорт: {{ readiness.runtime_transport }}</li>
          <li>UDP / Art-Net активний: {{ readiness.udp_active }}</li>
          <li>Вивід armed: {{ readiness.output_armed }}</li>
          <li>Мережа Art-Net: {{ readiness.artnet_network_enabled }}</li>
          <li>Blackout: {{ readiness.blackout }}</li>
          <li>
            Source {{ readiness.source_owner === "raw_tester" ? "RAW" : readiness.source_owner === "engine" ? "Engine" : "none" }}:
            Σ{{ readiness.source_frame_sum ?? 0 }} /
            {{ readiness.source_nonzero_channels ?? 0 }}
          </li>
          <li>
            Wire:
            Σ{{ readiness.wire_frame_sum ?? readiness.frame_sum }} /
            {{ readiness.wire_nonzero_channels ?? readiness.nonzero_channels }}
          </li>
          <li>Patch OK: {{ readiness.patch_ok }}</li>
          <li>Приладів: {{ readiness.fixture_count }}</li>
          <li>
            Art-Net:
            <HardwareBadge :label="String(readiness.artnet_badge)" />
          </li>
        </ul>
        <p class="hint">
          Mock-проходження майстра не підтверджує апаратну готовність.
        </p>
      </div>
    </div>

    <StageSimulator
      v-if="appState"
      plain
      :layout="stageLayout"
      :view="appState.liveView"
      :frame="appState.liveFrame"
      :nonzero-channels="appState.state.value?.simulator?.nonzero_channels ?? 0"
      :engine-preset-id="appState.engine.value?.preset_id ?? '—'"
      :preset-time-s="appState.engine.value?.preset_time_s ?? 0"
      :episode-index="appState.engine.value?.episode_index ?? 0"
      :episode-count="appState.engine.value?.episode_count ?? 10"
      :cycle-duration-s="appState.engine.value?.cycle_duration_s ?? 180"
      :blackout="appState.engine.value?.blackout ?? false"
      :strobe-held="appState.engine.value?.strobe_held ?? false"
      :white-hit-active="appState.engine.value?.white_hit_active ?? false"
      :face-on="appState.engine.value?.face_on ?? false"
      :preview-speed="appState.state.value?.preview_speed ?? 1"
      :disabled="appState.connection.value === 'offline'"
      @update:preview-speed="appState.setPreviewSpeed"
    />

    <footer
      v-show="false"
      class="wizard-nav"
    >
      <button
        type="button"
        class="icon-btn"
        aria-label="Назад"
        title="Назад"
        :disabled="step === 0"
        @click="step -= 1"
      >
        <svg
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path d="M15 6 9 12l6 6" />
        </svg>
      </button>
      <span>{{ step + 1 }} / {{ STEPS.length }}</span>
      <button
        type="button"
        class="icon-btn"
        aria-label="Далі"
        title="Далі"
        :disabled="step >= STEPS.length - 1"
        @click="step += 1"
      >
        <svg
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path d="M9 6l6 6-6 6" />
        </svg>
      </button>
    </footer>
  </section>
</template>
