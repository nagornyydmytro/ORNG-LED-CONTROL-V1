<script setup lang="ts">
import { inject } from "vue";
import BlackoutButton from "../components/control/BlackoutButton.vue";
import OverlayControls from "../components/control/OverlayControls.vue";
import PresetPad from "../components/control/PresetPad.vue";
import StatusBar from "../components/control/StatusBar.vue";
import StrobeButton from "../components/control/StrobeButton.vue";
import StageSimulator from "../components/simulator/StageSimulator.vue";
import { APP_STATE_KEY } from "../composables/appStateKey";

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
  selectPreset,
  whiteHit,
  strobePress,
  strobeRelease,
  toggleBlackout,
  setFace,
  setMasterBrightness,
  setPreviewSpeed,
} = api;

function onFaceBrightness(value: number) {
  void setFace(engine.value?.face_on ?? true, value);
}

function onToggleFace() {
  void setFace(!(engine.value?.face_on ?? false), engine.value?.face_brightness);
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
      v-else-if="connection === 'offline'"
      class="banner warn"
      role="status"
    >
      Немає зв'язку з backend. Керування обмежене.
    </p>

    <StatusBar
      :engine="engine"
      :output="output"
    />

    <PresetPad
      :active-id="engine?.preset_id ?? null"
      :available-ids="availableIds"
      :disabled="connection === 'offline'"
      @select="selectPreset"
    />

    <div class="control-row">
      <OverlayControls
        :face-on="engine?.face_on ?? false"
        :face-brightness="engine?.face_brightness ?? 0.55"
        :master-brightness="engine?.master_brightness ?? 1"
        :white-hit-active="engine?.white_hit_active ?? false"
        :disabled="connection === 'offline'"
        @toggle-face="onToggleFace"
        @update:face-brightness="onFaceBrightness"
        @update:master-brightness="setMasterBrightness"
        @white-hit="whiteHit"
      />

      <div class="danger-col">
        <StrobeButton
          :held="engine?.strobe_held ?? false"
          :disabled="connection === 'offline'"
          @press="strobePress"
          @release="strobeRelease"
        />
        <BlackoutButton
          :active="engine?.blackout ?? false"
          :disabled="connection === 'offline'"
          @toggle="toggleBlackout"
        />
      </div>
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
  </div>
</template>
