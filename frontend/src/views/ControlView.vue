<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import BlackoutButton from "../components/control/BlackoutButton.vue";
import BpmModePicker from "../components/control/BpmModePicker.vue";
import LiveFxPanel from "../components/control/LiveFxPanel.vue";
import PresetPad from "../components/control/PresetPad.vue";
import StageSimulator from "../components/simulator/StageSimulator.vue";
import { fetchStageLayout } from "../api/client";
import { getPreset, type EpisodeCard, type PresetDocument } from "../api/presets";
import { isCloudAdminBuild } from "../cloud/mode";
import { APP_STATE_KEY } from "../composables/appStateKey";
import { acceptedBpmValue, bpmDeskReason } from "../lib/acceptedBpm";
import { bpmDesk, choiceKeyFor } from "../lib/bpmIntent";
import { BPM_INTENSITIES, bpmControlsLocked, hasBpmSync } from "../lib/bpmPalettes";
import { presetButtonLabel } from "../lib/presetLabel";
import { REMOTE_PREVIEW_LIVE_MS } from "../lib/previewFreshness";
import type { StageLayout } from "../vite-env";

const cloudScene = isCloudAdminBuild();
const api = inject(APP_STATE_KEY);
if (!api) {
  throw new Error("App state is not provided");
}

const {
  state,
  engine,
  availableIds,
  availablePresets,
  connection,
  loading,
  liveView,
  liveFrame,
  selectPreset,
  nudgeEpisode,
  strobePress,
  strobeRelease,
  toggleBlackout,
  setFace,
  setPreviewSpeed,
  setMasterBrightness,
  nextAutoLook,
  setBpmSync,
  sweepPress,
  sweepRelease,
  verticalSweepPress,
  verticalSweepRelease,
  setLiveFxSpeeds,
  lastError,
} = api;

const route = useRoute();
const layout = ref<StageLayout | null>(null);
const offline = computed(() => connection.value === "offline" || connection.value === "reconnecting");
const activePresetId = computed(() => engine.value?.preset_id ?? null);
const highlightId = computed(() => {
  const q = route.query.highlight;
  return typeof q === "string" && q.length ? q : null;
});
const padPresetIds = computed(() => state.value?.pad_presets ?? []);
const strobeSpeed = computed(() => state.value?.strobe_speed ?? engine.value?.strobe_speed ?? 0.10);
const sweepSpeed = computed(() => state.value?.sweep_speed ?? engine.value?.sweep_speed ?? 0.10);

const presetDoc = ref<PresetDocument | null>(null);
const loadError = ref<string | null>(null);
const episodes = computed<EpisodeCard[]>(() => presetDoc.value?.episodes ?? []);

const episodeCount = computed(
  () => episodes.value.length || engine.value?.episode_count || 0,
);

watch(
  activePresetId,
  async (id) => {
    if (!id || id === "NONE") {
      presetDoc.value = null;
      return;
    }
    try {
      presetDoc.value = await getPreset(id);
    } catch (err) {
      presetDoc.value = null;
      loadError.value = err instanceof Error ? err.message : "Пресет не прочитано";
    }
  },
  { immediate: true },
);

onMounted(async () => {
  try {
    layout.value = await fetchStageLayout();
  } catch (err) {
    layout.value = null;
    loadError.value = err instanceof Error ? err.message : "Макет не прочитано";
  }
});

const lookBusy = ref(false);

async function onNextLook() {
  if (lookBusy.value || !autoDesk.value) return;
  lookBusy.value = true;
  try {
    await nextAutoLook();
  } catch {
    // The command layer already shows the refusal.
  } finally {
    window.setTimeout(() => {
      lookBusy.value = false;
    }, 700);
  }
}

const playingTitle = computed(() => {
  const bpm = state.value?.bpm;
  if (bpm?.enabled && bpm.scope === "auto") {
    return String(bpm.look_title || bpm.look || "Auto");
  }
  const id = shownPreset.value;
  if (!id || id === "NONE") return "NONE";
  const item = availablePresets.value.find((preset) => preset.id === id);
  return presetButtonLabel(id, item?.label ?? id);
});
const remoteDesk = computed(
  () => cloudScene || state.value?.desk_role === "raspberry",
);
const executorDown = computed(
  () => remoteDesk.value && state.value?.executor_online === false && !offline.value,
);
const previewClock = ref(Date.now());
let previewTimer: number | null = null;
onMounted(() => {
  previewTimer = window.setInterval(() => {
    previewClock.value = Date.now();
  }, 250);
});
onBeforeUnmount(() => {
  if (previewTimer != null) window.clearInterval(previewTimer);
});
const executorFrame = computed(() => {
  if (state.value?.executor_online === false) return false;
  if (remoteDesk.value) {
    const stamped = state.value?.preview_at;
    return typeof stamped === "number" && previewClock.value - stamped <= REMOTE_PREVIEW_LIVE_MS;
  }
  const view = state.value?.simulator;
  const fixtures = view
    ? view.pars.length + view.bars.length + view.beams.length + view.faces.length
    : 0;
  return fixtures > 0;
});
const playingSeconds = computed(() => {
  if (remoteDesk.value && !executorFrame.value) return null;
  return engine.value?.preset_time_s ?? 0;
});
const playingMeta = computed(() => {
  const count = episodeCount.value || engine.value?.episode_count || 0;
  const index = episodeShown.value + 1;
  const seconds = playingSeconds.value == null ? "—" : playingSeconds.value.toFixed(1);
  const clock = !count ? `${seconds} с` : `Епізод ${index}/${count} · ${seconds} с`;
  return `${clock} · ${frameNote.value}`;
});
const frameNote = computed(() => {
  const view = state.value?.simulator;
  const fixtures = view
    ? view.pars.length + view.bars.length + view.beams.length + view.faces.length
    : 0;
  if (cloudScene || state.value?.desk_role === "raspberry") {
    if (!executorFrame.value) return "немає кадру виконавця";
    const bpm = state.value?.bpm;
    if (bpm?.look) return String(bpm.look);
    if (bpm?.program === "beat" || bpm?.program === "smooth") return String(bpm.program);
    return fixtures > 0 ? "кадр виконавця" : "ефект виконавця";
  }
  return "кадр цього комп'ютера";
});

const presetHold = ref<string | null>(null);
const stageOpen = ref(window.matchMedia("(min-width: 861px)").matches);
const blackoutHold = ref<boolean | null>(null);
const episodeHold = ref<number | null>(null);
const faceHold = ref<boolean | null>(null);
let holdTicket = 0;
let holdTimer: number | null = null;

function clearOptimistic() {
  presetHold.value = null;
  episodeHold.value = null;
  blackoutHold.value = null;
  faceHold.value = null;
}

function beginHold(): number {
  const mine = ++holdTicket;
  if (holdTimer !== null) window.clearTimeout(holdTimer);
  holdTimer = window.setTimeout(() => {
    if (holdTicket === mine) clearOptimistic();
  }, 4000);
  return mine;
}

function finishHold(mine: number) {
  if (holdTicket !== mine) return;
  if (holdTimer !== null) {
    window.clearTimeout(holdTimer);
    holdTimer = null;
  }
  clearOptimistic();
}

watch(
  () => `${connection.value}|${state.value?.scene_id ?? ""}`,
  (_next, prev) => {
    if (prev === undefined) return;
    holdTicket += 1;
    clearOptimistic();
  },
);

onBeforeUnmount(() => {
  if (holdTimer !== null) window.clearTimeout(holdTimer);
});
const faceLevel = ref(1);
const strobeUi = ref<boolean | null>(null);
const sweepUi = ref<boolean | null>(null);
const sweepVUi = ref<boolean | null>(null);
const shownPreset = computed(() => presetHold.value ?? engine.value?.preset_id ?? null);
const blackoutShown = computed(() => blackoutHold.value ?? Boolean(engine.value?.blackout));
const episodeShown = computed(() => episodeHold.value ?? engine.value?.episode_index ?? 0);
const faceShown = computed(() => faceHold.value ?? Boolean(engine.value?.face_on));
const strobeShown = computed(() => strobeUi.value ?? Boolean(engine.value?.strobe_held));
const sweepShown = computed(() => sweepUi.value ?? Boolean(engine.value?.sweep_active));
const sweepVShown = computed(() => sweepVUi.value ?? Boolean(engine.value?.vertical_sweep_active));

watch(
  () => engine.value?.preset_id,
  (id) => {
    if (presetHold.value !== null && id === presetHold.value) presetHold.value = null;
  },
);
watch(
  () => engine.value?.blackout,
  (value) => {
    if (blackoutHold.value !== null && value === blackoutHold.value) blackoutHold.value = null;
  },
);
watch(
  () => engine.value?.episode_index,
  (value) => {
    if (episodeHold.value !== null && value === episodeHold.value) episodeHold.value = null;
  },
);
watch(
  () => engine.value?.face_on,
  (on) => {
    if (faceHold.value !== null && on === faceHold.value) faceHold.value = null;
  },
);
watch(
  () => engine.value?.face_brightness,
  (value) => {
    if (typeof value === "number") faceLevel.value = value;
  },
  { immediate: true },
);

function onSelectPreset(id: string) {
  if (autoDesk.value) return;
  const mine = beginHold();
  presetHold.value = id;
  episodeHold.value = 0;
  void Promise.resolve(selectPreset(id))
    .finally(() => finishHold(mine))
    .catch(() => undefined);
}

function onBlackout() {
  const next = !blackoutShown.value;
  const mine = beginHold();
  blackoutHold.value = next;
  void Promise.resolve(toggleBlackout())
    .finally(() => finishHold(mine))
    .catch(() => undefined);
}

function onStrobePress() {
  strobeUi.value = true;
  void Promise.resolve(strobePress()).catch(() => {
    strobeUi.value = null;
  });
}

function onStrobeRelease() {
  strobeUi.value = false;
  void Promise.resolve(strobeRelease()).finally(() => {
    strobeUi.value = null;
  });
}

function onSweepPress() {
  sweepUi.value = true;
  void Promise.resolve(sweepPress("horizontal")).catch(() => {
    sweepUi.value = null;
  });
}

function onSweepRelease() {
  sweepUi.value = false;
  void Promise.resolve(sweepRelease("horizontal")).finally(() => {
    sweepUi.value = null;
  });
}

function onVerticalPress() {
  sweepVUi.value = true;
  void Promise.resolve(verticalSweepPress()).catch(() => {
    sweepVUi.value = null;
  });
}

function onVerticalRelease() {
  sweepVUi.value = false;
  void Promise.resolve(verticalSweepRelease()).finally(() => {
    sweepVUi.value = null;
  });
}

function onFaceBrightness(value: number) {
  faceLevel.value = value;
  const mine = beginHold();
  faceHold.value = true;
  void Promise.resolve(setFace(true, value))
    .finally(() => finishHold(mine))
    .catch(() => undefined);
}

function onToggleFace() {
  const next = !faceShown.value;
  const mine = beginHold();
  faceHold.value = next;
  void Promise.resolve(setFace(next, faceLevel.value))
    .finally(() => finishHold(mine))
    .catch(() => undefined);
}

const bpmLocked = computed(() => bpmControlsLocked(state.value?.bpm));
const speedUi = ref(1);

watch(
  () => state.value?.preview_speed,
  (value) => {
    if (typeof value === "number") speedUi.value = value;
  },
  { immediate: true },
);

function onProgramSpeed(value: number) {
  speedUi.value = value;
  if (bpmLocked.value) return;
  void setPreviewSpeed(value);
}

const brightnessUi = ref(100);
const brightnessHold = ref(false);

watch(
  () => engine.value?.master_brightness,
  (value) => {
    if (brightnessHold.value || typeof value !== "number") return;
    brightnessUi.value = Math.round(Math.min(1, Math.max(0, value)) * 100);
  },
  { immediate: true },
);

function onMasterBrightness(value: number) {
  brightnessHold.value = true;
  brightnessUi.value = value;
  void setMasterBrightness(value / 100);
}

function releaseMasterBrightness() {
  brightnessHold.value = false;
}

const bpmCapable = computed(() => hasBpmSync(state.value?.capabilities));
const bpmChoice = computed(() =>
  bpmDesk(
    choiceKeyFor({ locationId: state.value?.location_id, sceneId: state.value?.scene_id }),
    state.value?.bpm,
  ),
);
const bpmOn = computed(() => bpmCapable.value && bpmChoice.value.enabled);
const autoDesk = computed(() => bpmOn.value && bpmChoice.value.choice.scope === "auto");
const bpmAccepted = computed(() => acceptedBpmValue(state.value?.bpm ?? null));
const bpmStatus = computed(() => bpmDeskReason(state.value?.bpm ?? null));
const confirmedLive = computed(() => {
  if (offline.value) return false;
  if (state.value?.executor_online === false) return false;
  return executorFrame.value;
});
const stageNote = computed(() => {
  if (!confirmedLive.value) return "Немає живого кадру";
  if (blackoutShown.value) return "Blackout · на екрані план, вихід темний";
  const bpm = state.value?.bpm;
  if (bpm?.enabled && bpm.scope === "auto") {
    const program = bpm.program === "beat" ? "бітова програма" : "плавна програма";
    const cycle = typeof bpm.picture_cycle === "number" ? ` · ${bpm.picture_cycle}` : "";
    const names: Record<string, string> = { beam: "Голови", par: "PAR", bar: "Бари" };
    const groups = Array.isArray(bpm.groups) ? bpm.groups : [];
    const cast = groups.length ? groups.map((id: string) => names[id] ?? id).join(", ") : "порожній склад";
    const intensity = BPM_INTENSITIES.find((item) => item.id === bpm.intensity)?.label;
    const motion = groups.includes("beam") && typeof bpm.motion_beats === "number"
      ? ` · рух голів ${bpm.motion_beats}`
      : "";
    const blend = bpm.blend_from ? " · перехід" : "";
    return `План виконавця · ${program}${cycle} · ${cast}${intensity ? ` · ${intensity}` : ""}${motion}${blend}`;
  }
  return frameNote.value;
});

function stageView() {
  if (!confirmedLive.value) return null;
  return liveView();
}

function toggleBpm() {
  void setBpmSync({ enabled: !bpmOn.value });
}

function shiftEpisode(delta: number) {
  const count = episodeCount.value || engine.value?.episode_count || 0;
  const current = episodeShown.value;
  const next = Math.max(0, count > 0 ? Math.min(count - 1, current + delta) : current + delta);
  const mine = beginHold();
  episodeHold.value = next;
  void Promise.resolve(nudgeEpisode(delta))
    .finally(() => finishHold(mine))
    .catch(() => undefined);
}

function goPrevEpisode() {
  shiftEpisode(-1);
}

function goNextEpisode() {
  shiftEpisode(1);
}
</script>

<template>
  <div
    class="control-view"
    data-light-pad
  >
    <p
      v-if="loading"
      class="banner"
    >
      Завантаження стану…
    </p>
    <p
      v-else-if="offline"
      class="banner banner--warn"
      role="status"
    >
      Немає зв'язку з backend. Керування обмежене.
    </p>
    <p
      v-else-if="executorDown"
      class="banner banner--warn"
      role="status"
    >
      Виконавець не на зв'язку. Керування чекає, поки зал відновить канал.
    </p>
    <p
      v-if="loadError"
      class="banner banner--warn"
      role="alert"
    >
      {{ loadError }}
    </p>
    <p
      v-else-if="lastError && !offline"
      class="banner banner--warn"
      role="alert"
    >
      {{ lastError }}
    </p>

    <div class="control-grid">
      <div class="control-grid__stage">
        <section class="stage-card">
          <button
            type="button"
            class="stage-card__toggle"
            :aria-expanded="stageOpen"
            @click="stageOpen = !stageOpen"
          >
            <span class="stage-card__copy">
              <strong>{{ playingTitle }}</strong>
              <button
                v-if="autoDesk"
                type="button"
                class="look-next"
                :disabled="offline || lookBusy || (remoteDesk && !executorFrame)"
                aria-label="Змінити ефект"
                title="Змінити ефект"
                @click.stop="onNextLook"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    d="M7 7h11l-2.2-2.2 1.4-1.4L22 8l-4.8 4.6-1.4-1.4L18 9H7a4 4 0 0 0 0 8h2v2H7a6 6 0 0 1 0-12zm10 10H6l2.2 2.2-1.4 1.4L2 16l4.8-4.6 1.4 1.4L6 15h11a4 4 0 0 0 0-8h-2V5h2a6 6 0 0 1 0 12z"
                    fill="currentColor"
                  />
                </svg>
              </button>
              <span>{{ playingMeta }}</span>
            </span>
            <span
              class="stage-card__chevron"
              :class="{ 'is-open': stageOpen }"
            />
          </button>
          <div
            v-show="stageOpen"
            class="stage-card__body"
          >
            <p
              class="hint"
              data-stage-note
            >
              {{ stageNote }}
            </p>
            <StageSimulator
              plain
              :show-cables="false"
              :layout="layout"
              :view="stageView"
              :frame="liveFrame"
              :nonzero-channels="cloudScene ? undefined : (state?.simulator?.nonzero_channels ?? 0)"
              :blackout="blackoutShown"
              :disabled="offline || !confirmedLive"
              :show-inspector="false"
            />
          </div>
        </section>
      </div>

      <aside class="control-grid__side">
        <BlackoutButton
          :active="blackoutShown"
          :disabled="offline"
          @toggle="onBlackout"
        />

        <PresetPad
          :active-id="shownPreset"
          :available-ids="availableIds"
          :presets="availablePresets"
          :pad-preset-ids="padPresetIds"
          :highlight-id="highlightId"
          :disabled="offline || autoDesk"
          :episode-disabled="offline || autoDesk || shownPreset === 'NONE' || !shownPreset || episodeCount <= 0"
          @select="onSelectPreset"
          @prev-episode="goPrevEpisode"
          @next-episode="goNextEpisode"
        />

        <LiveFxPanel
          :strobe-held="strobeShown"
          :sweep-active="sweepShown"
          :vertical-sweep-active="sweepVShown"
          :color-hit-active="(engine?.color_hit_active ?? false) && !autoDesk"
          :face-on="faceShown"
          :strobe-speed="strobeSpeed"
          :sweep-speed="sweepSpeed"
          :live-effects="state?.live_effects ?? null"
          :lock-tempo="false"
          :disabled="offline"
          @strobe-press="onStrobePress"
          @strobe-release="onStrobeRelease"
          @sweep-press="onSweepPress"
          @sweep-release="onSweepRelease"
          @vertical-sweep-press="onVerticalPress"
          @vertical-sweep-release="onVerticalRelease"
          @face="onToggleFace"
          @update:strobe-speed="(v) => setLiveFxSpeeds({ strobe_speed: v })"
          @update:sweep-speed="(v) => setLiveFxSpeeds({ sweep_speed: v })"
        />

        <label
          class="meter"
          aria-label="speed"
        >
          <span class="meter__name">speed</span>
          <input
            type="range"
            min="0.5"
            max="5"
            step="0.25"
            :value="speedUi"
            :disabled="offline || bpmLocked || autoDesk"
            @input="onProgramSpeed(Number(($event.target as HTMLInputElement).value))"
          >
          <span class="meter__value">{{ speedUi.toFixed(2) }}</span>
        </label>
        <label
          class="meter"
          aria-label="brightness"
        >
          <span class="meter__name">brightness</span>
          <input
            type="range"
            min="0"
            max="100"
            step="1"
            :value="brightnessUi"
            :disabled="offline"
            @input="onMasterBrightness(Number(($event.target as HTMLInputElement).value))"
            @change="releaseMasterBrightness"
          >
          <span class="meter__value">{{ brightnessUi }}</span>
        </label>
        <label
          v-if="faceShown"
          class="meter"
          aria-label="face"
        >
          <span class="meter__name">face</span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            :value="faceLevel"
            :disabled="offline"
            @input="onFaceBrightness(Number(($event.target as HTMLInputElement).value))"
          >
          <span class="meter__value">{{ faceLevel.toFixed(2) }}</span>
        </label>
        <div
          v-if="bpmCapable"
          class="bpm-row"
        >
          <span class="meter__name">Музика</span>
          <button
            type="button"
            class="theme-slider"
            :class="{ 'is-dark': bpmOn }"
            role="switch"
            :aria-checked="bpmOn"
            aria-label="Музика"
            :disabled="offline"
            @click="toggleBpm"
          >
            <span class="theme-slider__thumb" />
          </button>
          <span
            v-if="bpmChoice.pending || bpmChoice.error"
            class="bpm-row__reason"
            role="status"
          >
            {{ bpmChoice.error || "Ще застосовується" }}
          </span>
          <div
            v-if="bpmOn"
            class="bpm-row__reading"
          >
            <span
              class="meter__value"
              data-accepted-bpm
            >{{ bpmAccepted }}</span>
            <span
              v-if="bpmStatus"
              class="bpm-row__reason"
            >{{ bpmStatus }}</span>
          </div>
        </div>
        <BpmModePicker v-if="bpmCapable && bpmOn" />
      </aside>
    </div>
  </div>
</template>
