<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from "vue";
import BlackoutButton from "../components/control/BlackoutButton.vue";
import LiveFxPanel from "../components/control/LiveFxPanel.vue";
import PresetPad from "../components/control/PresetPad.vue";
import StatusBar from "../components/control/StatusBar.vue";
import StageSimulator from "../components/simulator/StageSimulator.vue";
import { fetchStageLayout } from "../api/client";
import { getPreset, type EpisodeCard, type PresetDocument } from "../api/presets";
import { APP_STATE_KEY } from "../composables/appStateKey";
import { formatClock, paletteCss } from "../lib/presets";
import type { StageLayout } from "../vite-env";

const api = inject(APP_STATE_KEY);
if (!api) {
  throw new Error("App state is not provided");
}

const {
  state,
  engine,
  output,
  availableIds,
  connection,
  loading,
  liveView,
  liveFrame,
  selectPreset,
  seekEpisode,
  whiteHit,
  strobePress,
  strobeRelease,
  toggleBlackout,
  setFace,
  setMasterBrightness,
  setPreviewSpeed,
  dropPress,
  dropRelease,
  colorHit,
  sweepHit,
} = api;

const layout = ref<StageLayout | null>(null);
const offline = computed(() => connection.value === "offline");
const activePresetId = computed(() => engine.value?.preset_id ?? null);
const isNonePreset = computed(() => !activePresetId.value || activePresetId.value === "NONE");

const presetDoc = ref<PresetDocument | null>(null);
const episodes = computed<EpisodeCard[]>(() => presetDoc.value?.episodes ?? []);

const currentEpisode = computed(() => {
  const index = engine.value?.episode_index ?? 0;
  return episodes.value[index] ?? null;
});

const episodeTimeLabel = computed(() => {
  const t = engine.value?.episode_time_s ?? 0;
  return formatClock(t);
});

const currentEpisodeTitle = computed(() => {
  const index = engine.value?.episode_index ?? 0;
  const ep = currentEpisode.value;
  if (!ep) return `Епізод ${index + 1}`;
  return `Епізод ${index + 1} · ${ep.effect} · ${ep.palette}`;
});

watch(
  activePresetId,
  async (id) => {
    if (!id || id === "NONE") {
      presetDoc.value = null;
      return;
    }
    try {
      presetDoc.value = await getPreset(id);
    } catch {
      presetDoc.value = null;
    }
  },
  { immediate: true },
);

onMounted(async () => {
  try {
    layout.value = await fetchStageLayout();
  } catch {
    layout.value = null;
  }
});

function onFaceBrightness(value: number) {
  void setFace(engine.value?.face_on ?? true, value);
}

function onToggleFace() {
  void setFace(!(engine.value?.face_on ?? false), engine.value?.face_brightness);
}

function goPrevEpisode() {
  const index = engine.value?.episode_index ?? 0;
  if (index <= 0) return;
  void seekEpisode(index - 1);
}

function goNextEpisode() {
  const index = engine.value?.episode_index ?? 0;
  const count = episodes.value.length || engine.value?.episode_count || 0;
  if (index >= count - 1) return;
  void seekEpisode(index + 1);
}
</script>

<template>
  <div class="control-view">
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

    <div class="control-grid">
      <div class="control-grid__stage">
        <StatusBar
          :engine="engine"
          :output="output"
        />

        <StageSimulator
          :layout="layout"
          :view="liveView"
          :frame="liveFrame"
          :nonzero-channels="state?.simulator?.nonzero_channels ?? 0"
          :engine-preset-id="engine?.preset_id ?? '—'"
          :preset-time-s="engine?.preset_time_s ?? 0"
          :episode-index="engine?.episode_index ?? 0"
          :episode-count="engine?.episode_count ?? 10"
          :cycle-duration-s="engine?.cycle_duration_s ?? 180"
          :blackout="engine?.blackout ?? false"
          :strobe-held="engine?.strobe_held ?? false"
          :white-hit-active="engine?.white_hit_active ?? false"
          :drop-active="engine?.drop_active ?? false"
          :color-hit-active="engine?.color_hit_active ?? false"
          :sweep-active="engine?.sweep_active ?? false"
          :face-on="engine?.face_on ?? false"
          :preview-speed="state?.preview_speed ?? 1"
          :disabled="offline"
          title="Жива сцена"
          badge="Фактичний кадр виводу"
          @update:preview-speed="setPreviewSpeed"
        />
      </div>

      <aside class="control-grid__side">
        <BlackoutButton
          :active="engine?.blackout ?? false"
          :disabled="offline"
          @toggle="toggleBlackout"
        />

        <section
          class="card"
          aria-label="Пресет"
        >
          <header class="card__head">
            <h2>Пресети</h2>
            <p class="card__sub">
              10 епізодів × 18 с = 180 с
            </p>
          </header>
          <PresetPad
            :active-id="engine?.preset_id ?? null"
            :available-ids="availableIds"
            :disabled="offline"
            @select="selectPreset"
          />
        </section>

        <section
          class="card"
          aria-label="Епізоди"
        >
          <header class="card__head">
            <h2>Епізоди</h2>
            <p
              v-if="isNonePreset"
              class="card__sub"
            >
              Пресет не вибрано
            </p>
            <p
              v-else
              class="card__sub"
            >
              {{ currentEpisodeTitle }} · {{ episodeTimeLabel }}
            </p>
          </header>

          <template v-if="!isNonePreset">
            <div class="episode-nav">
              <button
                type="button"
                class="action-btn"
                :disabled="offline || (engine?.episode_index ?? 0) <= 0"
                @click="goPrevEpisode"
              >
                ← Попередній
              </button>
              <button
                type="button"
                class="action-btn"
                :disabled="
                  offline ||
                    (engine?.episode_index ?? 0) >=
                    (episodes.length || engine?.episode_count || 1) - 1
                "
                @click="goNextEpisode"
              >
                Наступний →
              </button>
            </div>
            <div
              v-if="episodes.length"
              class="episode-strip"
            >
              <button
                v-for="(ep, index) in episodes"
                :key="ep.id"
                type="button"
                class="episode-chip"
                :class="{ active: index === (engine?.episode_index ?? 0) }"
                :style="{ '--chip': paletteCss(ep.palette) }"
                :disabled="offline"
                @click="seekEpisode(index)"
              >
                <span class="episode-chip__n">{{ index + 1 }}</span>
                <span class="episode-chip__palette">{{ ep.palette }}</span>
              </button>
            </div>
          </template>
        </section>

        <LiveFxPanel
          :strobe-held="engine?.strobe_held ?? false"
          :white-hit-active="engine?.white_hit_active ?? false"
          :drop-active="engine?.drop_active ?? false"
          :color-hit-active="engine?.color_hit_active ?? false"
          :sweep-active="engine?.sweep_active ?? false"
          :disabled="offline"
          @white-hit="whiteHit"
          @strobe-press="strobePress"
          @strobe-release="strobeRelease"
          @drop-press="dropPress"
          @drop-release="dropRelease"
          @color-hit="() => colorHit()"
          @sweep-hit="() => sweepHit()"
        />

        <section
          class="card"
          aria-label="Рівні"
        >
          <header class="card__head">
            <h2>Рівні</h2>
          </header>
          <button
            type="button"
            class="fx-btn"
            :class="{ active: engine?.face_on }"
            :aria-pressed="engine?.face_on ?? false"
            :disabled="offline"
            @click="onToggleFace"
          >
            <span class="fx-btn__label">DJ FACE</span>
            <span class="fx-btn__hint">світло на обличчя</span>
          </button>
          <label class="field">
            <span>Яскравість Face</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              :value="engine?.face_brightness ?? 0.55"
              :disabled="offline || !engine?.face_on"
              @input="onFaceBrightness(Number(($event.target as HTMLInputElement).value))"
            >
          </label>
          <label class="field">
            <span>Master</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              :value="engine?.master_brightness ?? 1"
              :disabled="offline"
              @input="setMasterBrightness(Number(($event.target as HTMLInputElement).value))"
            >
          </label>
        </section>
      </aside>
    </div>
  </div>
</template>
