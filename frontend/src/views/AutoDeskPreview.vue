<script setup lang="ts">
import { computed, provide, ref } from "vue";
import { useRoute } from "vue-router";
import { acceptedBpmValue, bpmDeskReason } from "../lib/acceptedBpm";
import BlackoutButton from "../components/control/BlackoutButton.vue";
import BpmModePicker from "../components/control/BpmModePicker.vue";
import LiveFxPanel from "../components/control/LiveFxPanel.vue";
import PresetPad from "../components/control/PresetPad.vue";
import { APP_STATE_KEY, type AppStateApi } from "../composables/appStateKey";
import type { AppState } from "../vite-env";

const route = useRoute();
const extra = computed(() => route.query.extra === "1");
const sync = computed(() => String(route.query.sync || "searching"));
const director = computed(() => route.query.legacy !== "1");

const state = ref<AppState>({
  engine: {
    preset_id: "P04",
    preset_time_s: 12,
    episode_index: 0,
    episode_time_s: 0,
    episode_count: 4,
    cycle_duration_s: 180,
    blackout: false,
    face_on: false,
    face_brightness: 1,
    strobe_held: false,
    white_hit_active: false,
    master_brightness: 1,
    time_s: 12,
    color_hit_active: false,
    sweep_active: false,
    vertical_sweep_active: false,
  },
  output: {
    transport: "mock",
    preferred_transport: "mock",
    armed: false,
    last_error: null,
    frames_sent: 0,
    network_allowed: false,
    udp_active: false,
    target_ip: null,
    universe: 0,
    frame_sum: 0,
    nonzero_channels: 0,
  },
  presets: ["P01", "P02", "P03", "P04", "NONE"],
  fixture_ids: [],
  frame: [],
  sequence: 1,
  preview_speed: 1,
  pad_presets: ["P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09"],
  simulator: {
    pars: [],
    bars: [],
    beams: [],
    faces: [],
    nonzero_channels: 0,
    blackout_visual: false,
  },
  scene_id: "orng-bar",
  scene_name: "ORNG BAR",
  location_id: "lab",
  capabilities: director.value ? ["bpm_sync", "auto_director", "blackout"] : ["bpm_sync"],
  bpm: {
    enabled: true,
    scope: "auto",
    mode: "smooth",
    palette: "contrast",
    cast: "all",
    character: "rhythmic",
    intensity: "full",
    period: "auto",
    bpm: sync.value === "locked" ? 128 : null,
    locked: sync.value === "locked",
    signal: sync.value !== "waiting" && sync.value !== "no_input",
    capture: sync.value === "no_input" ? "no_device" : "running",
    device: sync.value === "no_input" ? null : "Mock USB",
    sync: sync.value as NonNullable<AppState["bpm"]>["sync"],
    section: "groove",
  },
});

provide(APP_STATE_KEY, {
  state,
  setBpmSync: async () => true,
} as unknown as AppStateApi);

const availableIds = new Set(["P01", "P02", "P03", "P04", "NONE"]);
const bpmAccepted = computed(() => acceptedBpmValue(state.value.bpm ?? null));
const bpmStatus = computed(() => bpmDeskReason(state.value.bpm ?? null));
</script>

<template>
  <div
    class="control-view"
    data-preview-auto-desk
  >
    <div class="control-grid">
      <aside class="control-grid__side">
        <BlackoutButton :active="false" />
        <PresetPad
          active-id="P04"
          :available-ids="availableIds"
          :presets="[
            { id: 'P01', label: 'P01', builtin: true },
            { id: 'P02', label: 'P02', builtin: true },
            { id: 'P03', label: 'P03', builtin: true },
            { id: 'P04', label: 'P04', builtin: true },
          ]"
          :pad-preset-ids="state.pad_presets ?? []"
          disabled
          episode-disabled
        />
        <LiveFxPanel
          :strobe-held="false"
          :sweep-active="false"
          :vertical-sweep-active="false"
          :color-hit-active="false"
          :strobe-speed="0.1"
          :sweep-speed="0.1"
          disabled
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
            value="1"
            disabled
          >
          <span class="meter__value">1.00</span>
        </label>
        <div class="bpm-row">
          <span class="meter__name">Музика</span>
          <button
            type="button"
            class="theme-slider is-dark"
            role="switch"
            aria-checked="true"
            aria-label="Музика"
          >
            <span class="theme-slider__thumb" />
          </button>
          <div class="bpm-row__reading">
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
        <BpmModePicker :start-expanded="extra" />
      </aside>
    </div>
  </div>
</template>

