<script setup lang="ts">
import { computed, inject, onBeforeUnmount, reactive, ref } from "vue";
import {
  beamCalibrationBegin,
  beamCalibrationEnd,
  beamCalibrationHome,
  beamCalibrationSave,
  beamCalibrationSet,
  beamCalibrationVisible,
} from "../../api/setup";
import { disarmOutput } from "../../api/client";
import { APP_STATE_KEY } from "../../composables/appStateKey";
import type { BeamCalibrationBeamView, BeamCalibrationSessionView } from "../../vite-env";

interface BeamPatchFixture {
  id: string;
  label: string;
  spatial?: Record<string, unknown>;
}

const props = defineProps<{
  fixture: BeamPatchFixture;
  beamState: BeamCalibrationBeamView | null;
  session: BeamCalibrationSessionView | null;
}>();

const appState = inject(APP_STATE_KEY, null);

type DraftKey =
  | "pan_offset"
  | "tilt_offset"
  | "pan_min"
  | "pan_max"
  | "tilt_min"
  | "tilt_max"
  | "home_pan"
  | "home_tilt"
  | "max_pan_speed"
  | "max_tilt_speed";

interface NumericFieldDef {
  key: DraftKey;
  label: string;
  min: number;
  max: number;
  step: number;
}

const NUMERIC_FIELDS: NumericFieldDef[] = [
  { key: "pan_offset", label: "Pan offset", min: -0.5, max: 0.5, step: 0.01 },
  { key: "tilt_offset", label: "Tilt offset", min: -0.5, max: 0.5, step: 0.01 },
  { key: "pan_min", label: "Pan min", min: 0, max: 1, step: 0.01 },
  { key: "pan_max", label: "Pan max", min: 0, max: 1, step: 0.01 },
  { key: "tilt_min", label: "Tilt min", min: 0, max: 1, step: 0.01 },
  { key: "tilt_max", label: "Tilt max", min: 0, max: 1, step: 0.01 },
  { key: "home_pan", label: "Home Pan", min: 0, max: 1, step: 0.01 },
  { key: "home_tilt", label: "Home Tilt", min: 0, max: 1, step: 0.01 },
  { key: "max_pan_speed", label: "Max Pan Speed", min: 0.01, max: 2, step: 0.01 },
  { key: "max_tilt_speed", label: "Max Tilt Speed", min: 0.01, max: 2, step: 0.01 },
];

function draftFromSpatial(spatial: Record<string, unknown> | undefined) {
  return {
    pan_invert: Boolean(spatial?.pan_invert),
    tilt_invert: Boolean(spatial?.tilt_invert),
    pan_offset: Number(spatial?.pan_offset ?? 0),
    tilt_offset: Number(spatial?.tilt_offset ?? 0),
    pan_min: Number(spatial?.pan_min ?? 0),
    pan_max: Number(spatial?.pan_max ?? 1),
    tilt_min: Number(spatial?.tilt_min ?? 0),
    tilt_max: Number(spatial?.tilt_max ?? 1),
    home_pan: Number(spatial?.home_pan ?? 0.5),
    home_tilt: Number(spatial?.home_tilt ?? 0.5),
    max_pan_speed: Number(spatial?.max_pan_speed ?? 0.35),
    max_tilt_speed: Number(spatial?.max_tilt_speed ?? 0.25),
    beam_calibration_confirmed: Boolean(spatial?.beam_calibration_confirmed),
    beam_calibration_notes: String(spatial?.beam_calibration_notes ?? ""),
  };
}

function draftFromBeamState(beamState: BeamCalibrationBeamView | null) {
  if (!beamState) return draftFromSpatial(props.fixture.spatial);
  return {
    pan_invert: Boolean(beamState.pan_invert),
    tilt_invert: Boolean(beamState.tilt_invert),
    pan_offset: Number(beamState.pan_offset ?? 0),
    tilt_offset: Number(beamState.tilt_offset ?? 0),
    pan_min: Number(beamState.pan_min ?? 0),
    pan_max: Number(beamState.pan_max ?? 1),
    tilt_min: Number(beamState.tilt_min ?? 0),
    tilt_max: Number(beamState.tilt_max ?? 1),
    home_pan: Number(beamState.home_pan ?? 0.5),
    home_tilt: Number(beamState.home_tilt ?? 0.5),
    max_pan_speed: Number(beamState.max_pan_speed ?? 0.35),
    max_tilt_speed: Number(beamState.max_tilt_speed ?? 0.25),
    beam_calibration_confirmed: Boolean(beamState.calibration_confirmed),
    beam_calibration_notes: String(beamState.notes ?? ""),
  };
}

const draft = reactive(draftFromSpatial(props.fixture.spatial));

const saving = ref(false);
const busy = ref(false);
const errorMessage = ref<string | null>(null);
const statusMessage = ref<string | null>(null);

function setDraft(key: DraftKey, value: number) {
  draft[key] = value;
}

function setStatus(ok: string | null, err: string | null = null) {
  statusMessage.value = ok;
  errorMessage.value = err;
}

const sideLabel = computed(() => {
  const side = String(props.beamState?.side ?? props.fixture.spatial?.side ?? "");
  if (side === "left") return "Ліва (з боку глядача)";
  if (side === "right") return "Права (з боку глядача)";
  return side || "—";
});

const isActiveSession = computed(
  () => Boolean(props.session?.active) && props.session?.fixture_id === props.fixture.id,
);

const sessionPan = computed(() => Number(props.session?.semantic_pan ?? 0.5));
const sessionTilt = computed(() => Number(props.session?.semantic_tilt ?? 0.5));

const rangesValid = computed(
  () => draft.pan_min < draft.pan_max && draft.tilt_min < draft.tilt_max,
);

const visibleOn = computed(() => Boolean(props.session?.visible_beam_on));

function globalOf(local: number | null | undefined): number | null {
  if (local === null || local === undefined || !props.beamState) return null;
  return Number(props.beamState.start_address) + Number(local) - 1;
}

const encoded = computed(
  () =>
    props.beamState?.encoded ?? {
      pan_coarse: 0,
      pan_fine: null,
      tilt_coarse: 0,
      tilt_fine: null,
      pan_16bit: null,
      tilt_16bit: null,
    },
);

const panNormalized = computed(() => Number(props.beamState?.physical_pan ?? 0));
const tiltNormalized = computed(() => Number(props.beamState?.physical_tilt ?? 0));
const panPercent = computed(() => Math.round(panNormalized.value * 100));
const tiltPercent = computed(() => Math.round(tiltNormalized.value * 100));

async function withBusy(action: () => Promise<void>) {
  busy.value = true;
  errorMessage.value = null;
  try {
    await action();
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : "Помилка калібрування";
  } finally {
    busy.value = false;
  }
}

async function saveCalibration() {
  saving.value = true;
  errorMessage.value = null;
  try {
    await beamCalibrationSave(props.fixture.id, {
      pan_invert: draft.pan_invert,
      tilt_invert: draft.tilt_invert,
      pan_offset: draft.pan_offset,
      tilt_offset: draft.tilt_offset,
      pan_min: draft.pan_min,
      pan_max: draft.pan_max,
      tilt_min: draft.tilt_min,
      tilt_max: draft.tilt_max,
      home_pan: draft.home_pan,
      home_tilt: draft.home_tilt,
      max_pan_speed: draft.max_pan_speed,
      max_tilt_speed: draft.max_tilt_speed,
      beam_calibration_confirmed: draft.beam_calibration_confirmed,
      beam_calibration_notes: draft.beam_calibration_notes,
    });
    await appState?.refreshRest?.();
    Object.assign(draft, draftFromBeamState(props.beamState));
    setStatus(`Калібрування ${props.fixture.label} збережено`);
  } catch (err) {
    setStatus(null, err instanceof Error ? err.message : "Не вдалося зберегти калібрування");
  } finally {
    saving.value = false;
  }
}

async function startCalibration() {
  const confirmed = window.confirm(
    "Почати калібрування руху?\n\n" +
      "Напрямок Pan/Tilt цієї голови ще невідомий (не відкалібровано) — " +
      "рух може статись у будь-якому напрямку. Переконайтеся, що зона під головою вільна.\n\n" +
      "Продовжити?",
  );
  if (!confirmed) {
    setStatus("Початок калібрування скасовано");
    return;
  }
  await withBusy(async () => {
    // Defensive: end any other active session first so switching heads is safe.
    await beamCalibrationEnd().catch(() => undefined);
    await beamCalibrationBegin(props.fixture.id, true);
    await appState?.refreshRest?.();
    setStatus(`Калібрування руху ${props.fixture.label} активне`);
  });
}

async function stopCalibration() {
  await withBusy(async () => {
    await beamCalibrationEnd();
    await appState?.refreshRest?.();
    setStatus("Калібрування руху зупинено");
  });
}

async function setPan(value: number) {
  await withBusy(async () => {
    await beamCalibrationSet({ pan: value });
    await appState?.refreshRest?.();
  });
}

async function setTilt(value: number) {
  await withBusy(async () => {
    await beamCalibrationSet({ tilt: value });
    await appState?.refreshRest?.();
  });
}

function nudgePan(delta: number) {
  void setPan(Math.max(0, Math.min(1, sessionPan.value + delta)));
}

function nudgeTilt(delta: number) {
  void setTilt(Math.max(0, Math.min(1, sessionTilt.value + delta)));
}

async function goHome() {
  await withBusy(async () => {
    await beamCalibrationHome();
    await appState?.refreshRest?.();
    setStatus("Позицію Home застосовано");
  });
}

function saveCurrentAsHome() {
  draft.home_pan = sessionPan.value;
  draft.home_tilt = sessionTilt.value;
  setStatus("Поточну позицію збережено як Home (у чернетці, натисніть «Зберегти калібрування»)");
}

function useCurrentAsPanMin() {
  draft.pan_min = sessionPan.value;
}

function useCurrentAsPanMax() {
  draft.pan_max = sessionPan.value;
}

function useCurrentAsTiltMin() {
  draft.tilt_min = sessionTilt.value;
}

function useCurrentAsTiltMax() {
  draft.tilt_max = sessionTilt.value;
}

async function goSemantic(pan: number, tilt: number) {
  await withBusy(async () => {
    await beamCalibrationSet({ pan, tilt });
    await appState?.refreshRest?.();
  });
}

async function toggleVisible(enabled: boolean) {
  if (enabled) {
    const confirmed = window.confirm(
      "Показати тестовий промінь на мінімальній яскравості?\n\n" +
        "Переконайтеся, що це безпечно для присутніх у залі.\n\nПродовжити?",
    );
    if (!confirmed) return;
  }
  await withBusy(async () => {
    await beamCalibrationVisible(enabled, enabled);
    await appState?.refreshRest?.();
  });
}

async function disarmNow() {
  await withBusy(async () => {
    await disarmOutput();
    await appState?.refreshRest?.();
    setStatus("DISARM виконано");
  });
}

onBeforeUnmount(() => {
  if (isActiveSession.value) {
    void beamCalibrationEnd().catch(() => undefined);
  }
});

defineExpose({ isActiveSession });
</script>

<template>
  <article class="card beam-card">
    <div class="card__head">
      <h3>{{ fixture.label }}</h3>
      <span class="card__sub">{{ fixture.id }} · {{ sideLabel }}</span>
    </div>

    <p class="hint">
      Підвіс: стельовий, голова встановлена догори основою
    </p>
    <p class="hint">
      Start address: {{ beamState?.start_address ?? "—" }}
    </p>

    <ul class="kv beam-card__roles">
      <li>
        <span>Pan Coarse</span>
        <strong>
          local {{ beamState?.roles?.pan_coarse ?? "—" }} →
          DMX {{ globalOf(beamState?.roles?.pan_coarse ?? null) ?? "—" }}
        </strong>
      </li>
      <li v-if="beamState?.roles?.pan_fine != null">
        <span>Pan Fine</span>
        <strong>
          local {{ beamState.roles.pan_fine }} →
          DMX {{ globalOf(beamState.roles.pan_fine) }}
        </strong>
      </li>
      <li>
        <span>Tilt Coarse</span>
        <strong>
          local {{ beamState?.roles?.tilt_coarse ?? "—" }} →
          DMX {{ globalOf(beamState?.roles?.tilt_coarse ?? null) ?? "—" }}
        </strong>
      </li>
      <li v-if="beamState?.roles?.tilt_fine != null">
        <span>Tilt Fine</span>
        <strong>
          local {{ beamState.roles.tilt_fine }} →
          DMX {{ globalOf(beamState.roles.tilt_fine) }}
        </strong>
      </li>
    </ul>

    <p
      class="hint"
      :class="{ 'beam-card__status--ok': beamState?.calibration_confirmed }"
    >
      {{ beamState?.calibration_confirmed ? "Калібрування підтверджено" : "Не відкалібровано" }}
    </p>

    <div class="row-actions">
      <label class="check">
        <input
          v-model="draft.pan_invert"
          type="checkbox"
        >
        pan_invert
      </label>
      <label class="check">
        <input
          v-model="draft.tilt_invert"
          type="checkbox"
        >
        tilt_invert
      </label>
    </div>

    <div class="form-grid">
      <div
        v-for="fdef in NUMERIC_FIELDS"
        :key="fdef.key"
        class="field beam-card__slider"
      >
        <span>{{ fdef.label }}</span>
        <input
          type="range"
          :min="fdef.min"
          :max="fdef.max"
          :step="fdef.step"
          :value="draft[fdef.key]"
          @input="setDraft(fdef.key, Number(($event.target as HTMLInputElement).value))"
        >
        <input
          type="number"
          :min="fdef.min"
          :max="fdef.max"
          :step="fdef.step"
          :value="draft[fdef.key]"
          @change="setDraft(fdef.key, Number(($event.target as HTMLInputElement).value))"
        >
      </div>
    </div>

    <label class="field">
      Нотатки калібрування
      <textarea
        v-model="draft.beam_calibration_notes"
        rows="2"
      />
    </label>

    <label class="check">
      <input
        v-model="draft.beam_calibration_confirmed"
        type="checkbox"
      >
      Калібрування фізично перевірено
    </label>

    <button
      type="button"
      class="action-btn"
      :disabled="saving"
      @click="saveCalibration"
    >
      Зберегти калібрування
    </button>

    <p
      v-if="statusMessage"
      class="hint"
    >
      {{ statusMessage }}
    </p>
    <p
      v-if="errorMessage"
      class="hint beam-card__status--error"
      role="alert"
    >
      {{ errorMessage }}
    </p>

    <section class="beam-card__test">
      <h4>Тест руху</h4>

      <button
        v-if="!isActiveSession"
        type="button"
        class="action-btn"
        :disabled="busy"
        @click="startCalibration"
      >
        Почати калібрування руху
      </button>

      <template v-else>
        <div class="field beam-card__slider">
          <span>Pan (тест)</span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            :value="sessionPan"
            @input="setPan(Number(($event.target as HTMLInputElement).value))"
          >
          <input
            type="number"
            min="0"
            max="1"
            step="0.01"
            :value="sessionPan"
            @change="setPan(Number(($event.target as HTMLInputElement).value))"
          >
          <button
            type="button"
            class="action-btn"
            @click="nudgePan(-0.01)"
          >
            −
          </button>
          <button
            type="button"
            class="action-btn"
            @click="nudgePan(0.01)"
          >
            +
          </button>
        </div>
        <div class="field beam-card__slider">
          <span>Tilt (тест)</span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            :value="sessionTilt"
            @input="setTilt(Number(($event.target as HTMLInputElement).value))"
          >
          <input
            type="number"
            min="0"
            max="1"
            step="0.01"
            :value="sessionTilt"
            @change="setTilt(Number(($event.target as HTMLInputElement).value))"
          >
          <button
            type="button"
            class="action-btn"
            @click="nudgeTilt(-0.01)"
          >
            −
          </button>
          <button
            type="button"
            class="action-btn"
            @click="nudgeTilt(0.01)"
          >
            +
          </button>
        </div>

        <div class="row-actions">
          <button
            type="button"
            class="action-btn"
            @click="goHome"
          >
            Перейти в Home
          </button>
          <button
            type="button"
            class="action-btn"
            @click="saveCurrentAsHome"
          >
            Зберегти поточну позицію як Home
          </button>
        </div>
        <div class="row-actions">
          <button
            type="button"
            class="action-btn"
            @click="useCurrentAsPanMin"
          >
            Поточне → Pan min
          </button>
          <button
            type="button"
            class="action-btn"
            @click="useCurrentAsPanMax"
          >
            Поточне → Pan max
          </button>
          <button
            type="button"
            class="action-btn"
            @click="useCurrentAsTiltMin"
          >
            Поточне → Tilt min
          </button>
          <button
            type="button"
            class="action-btn"
            @click="useCurrentAsTiltMax"
          >
            Поточне → Tilt max
          </button>
        </div>
        <div class="row-actions">
          <button
            type="button"
            class="action-btn"
            :disabled="!rangesValid"
            @click="goSemantic(0.5, 0.5)"
          >
            Центр
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="!rangesValid"
            @click="goSemantic(0, 0.5)"
          >
            Ліво
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="!rangesValid"
            @click="goSemantic(1, 0.5)"
          >
            Право
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="!rangesValid"
            @click="goSemantic(0.5, 1)"
          >
            Верх
          </button>
          <button
            type="button"
            class="action-btn"
            :disabled="!rangesValid"
            @click="goSemantic(0.5, 0)"
          >
            Низ
          </button>
        </div>

        <label class="check">
          <input
            type="checkbox"
            :checked="visibleOn"
            @change="toggleVisible(($event.target as HTMLInputElement).checked)"
          >
          Показати промінь на мінімальній яскравості
        </label>

        <button
          type="button"
          class="action-btn action-btn--danger"
          :disabled="busy"
          @click="stopCalibration"
        >
          Зупинити калібрування
        </button>
      </template>

      <ul class="kv beam-card__dmx">
        <li>
          <span>Pan normalized</span>
          <strong>{{ panNormalized.toFixed(3) }} ({{ panPercent }}%)</strong>
        </li>
        <li>
          <span>Pan coarse / fine / 16bit</span>
          <strong>
            {{ encoded.pan_coarse }} / {{ encoded.pan_fine ?? "—" }} /
            {{ encoded.pan_16bit ?? "—" }}
          </strong>
        </li>
        <li>
          <span>Tilt normalized</span>
          <strong>{{ tiltNormalized.toFixed(3) }} ({{ tiltPercent }}%)</strong>
        </li>
        <li>
          <span>Tilt coarse / fine / 16bit</span>
          <strong>
            {{ encoded.tilt_coarse }} / {{ encoded.tilt_fine ?? "—" }} /
            {{ encoded.tilt_16bit ?? "—" }}
          </strong>
        </li>
        <li>
          <span>Global DMX канали</span>
          <strong>
            Pan {{ globalOf(beamState?.roles?.pan_coarse ?? null) ?? "—" }}
            <template v-if="beamState?.roles?.pan_fine != null">
              /{{ globalOf(beamState.roles.pan_fine) }}
            </template>
            · Tilt {{ globalOf(beamState?.roles?.tilt_coarse ?? null) ?? "—" }}
            <template v-if="beamState?.roles?.tilt_fine != null">
              /{{ globalOf(beamState.roles.tilt_fine) }}
            </template>
          </strong>
        </li>
      </ul>

      <button
        type="button"
        class="action-btn action-btn--danger"
        @click="disarmNow"
      >
        DISARM
      </button>
    </section>
  </article>
</template>
