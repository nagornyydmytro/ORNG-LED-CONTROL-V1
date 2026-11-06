<script setup lang="ts">
import { computed, ref } from "vue";
import BlackoutButton from "../control/BlackoutButton.vue";
import LiveFxPanel from "../control/LiveFxPanel.vue";
import PresetPad from "../control/PresetPad.vue";
import type { CloudControlTransport, CloudDeviceLiveState } from "../../cloud/transport";
import { bpmControlsLocked } from "../../lib/bpmPalettes";
import type { PresetInfo } from "../../vite-env";

const props = defineProps<{
  transport: CloudControlTransport | null;
  live: CloudDeviceLiveState | null;
  padPresetIds: string[];
  availableIds: Set<string>;
  disabled?: boolean;
}>();

const error = ref<string | null>(null);
const strobeSpeed = ref(0.1);
const sweepSpeed = ref(0.1);
let strobeOrigin = 0.1;
let sweepOrigin = 0.1;
const offline = computed(() => props.disabled || !props.transport || !props.live?.online);
const autoOn = computed(
  () => props.live?.bpm?.enabled === true && props.live.bpm.scope === "auto",
);
const programLocked = computed(() => autoOn.value || bpmControlsLocked(props.live?.bpm ?? null));
const presetLocked = computed(() => offline.value || autoOn.value);
const liveFxLocked = computed(() => offline.value);

const presets = computed<PresetInfo[]>(() =>
  props.padPresetIds.map((id) => ({ id, label: id, builtin: true })),
);

async function run(label: string, fn: () => Promise<void>) {
  error.value = null;
  try {
    await fn();
    return true;
  } catch (e) {
    error.value = `${label}: ${e instanceof Error ? e.message : String(e)}`;
    return false;
  }
}

function selectPreset(id: string) {
  if (!props.transport) return;
  void run("preset", () => props.transport!.selectPreset(id));
}

function toggleBlackout() {
  if (!props.transport || !props.live) return;
  void run("blackout", () => props.transport!.blackout(!props.live!.blackout));
}

function strobePress() {
  if (!props.transport) return;
  strobeOrigin = strobeSpeed.value;
  void run("strobe", () => props.transport!.strobe("press"));
}

function strobeRelease() {
  strobeSpeed.value = strobeOrigin;
  if (!props.transport) return;
  void run("strobe", () => props.transport!.strobe("release"));
}

function sweepPress(mode: "horizontal" | "vertical") {
  if (!props.transport) return;
  sweepOrigin = sweepSpeed.value;
  void run("sweep", () => props.transport!.sweep("press", mode));
}

function sweepRelease(mode: "horizontal" | "vertical") {
  sweepSpeed.value = sweepOrigin;
  if (!props.transport) return;
  void run("sweep", () => props.transport!.sweep("release", mode));
}

function faceToggle() {
  if (!props.transport) return;
  void run("face", () => props.transport!.faceToggle());
}

function episode(delta: number) {
  if (!props.transport) return;
  void run("zoom/episode", () => props.transport!.episodeNudge(delta));
}

function nudgeFxSpeed(delta: number) {
  if (!props.transport) return;
  void run("speed", () => props.transport!.speed("live_fx", delta));
}

function nudgeProgramSpeed(delta: number) {
  if (!props.transport) return;
  void run("speed", () => props.transport!.speed("program", delta));
}

function onStrobeSpeed(value: number) {
  const prev = strobeSpeed.value;
  strobeSpeed.value = value;
  if (!props.transport || liveFxLocked.value) {
    strobeSpeed.value = prev;
    return;
  }
  const delta = value - prev;
  if (Math.abs(delta) < 0.001) return;
  void run("strobe-speed", () => props.transport!.speed("live_fx", delta)).then((ok) => {
    if (!ok) strobeSpeed.value = prev;
  });
}

function onSweepSpeed(value: number) {
  const prev = sweepSpeed.value;
  sweepSpeed.value = value;
  if (!props.transport || liveFxLocked.value) {
    sweepSpeed.value = prev;
    return;
  }
  const delta = value - prev;
  if (Math.abs(delta) < 0.001) return;
  void run("sweep-speed", () => props.transport!.speed("live_fx", delta)).then((ok) => {
    if (!ok) sweepSpeed.value = prev;
  });
}
</script>

<template>
  <section
    class="cloud-pad"
    aria-label="Віддалений пульт"
  >
    <header class="cloud-pad__head">
      <h2>Пульт</h2>
      <p class="muted">
        Momentary через admin WSS. Джерело:
        <code>{{ transport?.sourceId ?? "—" }}</code>
      </p>
      <p
        v-if="live"
        class="cloud-pad__live"
      >
        <span :class="live.online ? 'ok' : 'off'">{{ live.online ? "online" : "offline" }}</span>
        · {{ live.current_scene_id || "—" }}
        · {{ live.blackout ? "blackout" : "lit" }}
        · {{ live.armed ? "armed" : "disarmed" }}
        · preset {{ live.preset_id || "—" }}
      </p>
    </header>

    <p
      v-if="offline"
      class="banner banner--warn"
      role="status"
    >
      Пристрій офлайн або WSS не підключено — керування обмежене.
    </p>

    <div class="cloud-pad__row">
      <BlackoutButton
        :active="live?.blackout ?? false"
        :disabled="offline"
        @toggle="toggleBlackout"
      />
      <button
        type="button"
        class="action-btn"
        :disabled="liveFxLocked"
        @click="faceToggle"
      >
        Face
      </button>
    </div>

    <div class="cloud-pad__row">
      <button
        type="button"
        class="action-btn"
        :disabled="offline || programLocked"
        @click="nudgeProgramSpeed(-0.25)"
      >
        Speed −
      </button>
      <button
        type="button"
        class="action-btn"
        :disabled="offline || programLocked"
        @click="nudgeProgramSpeed(0.25)"
      >
        Speed +
      </button>
      <button
        type="button"
        class="action-btn"
        :disabled="liveFxLocked"
        @click="nudgeFxSpeed(-0.1)"
      >
        FX speed −
      </button>
      <button
        type="button"
        class="action-btn"
        :disabled="liveFxLocked"
        @click="nudgeFxSpeed(0.1)"
      >
        FX speed +
      </button>
    </div>

    <PresetPad
      :active-id="live?.preset_id ?? null"
      :available-ids="availableIds"
      :presets="presets"
      :pad-preset-ids="padPresetIds"
      :disabled="presetLocked"
      :episode-disabled="presetLocked || !live?.preset_id || live?.preset_id === 'NONE'"
      @select="selectPreset"
      @prev-episode="episode(-1)"
      @next-episode="episode(1)"
    />

    <LiveFxPanel
      :strobe-held="live?.strobe_held ?? false"
      :sweep-active="live?.sweep_active ?? false"
      :vertical-sweep-active="live?.vertical_sweep_active ?? false"
      :color-hit-active="false"
      :strobe-speed="strobeSpeed"
      :sweep-speed="sweepSpeed"
      :disabled="liveFxLocked"
      @strobe-press="strobePress"
      @strobe-release="strobeRelease"
      @sweep-press="() => sweepPress('horizontal')"
      @sweep-release="() => sweepRelease('horizontal')"
      @vertical-sweep-press="() => sweepPress('vertical')"
      @vertical-sweep-release="() => sweepRelease('vertical')"
      @update:strobe-speed="onStrobeSpeed"
      @update:sweep-speed="onSweepSpeed"
    />

    <p
      v-if="error"
      class="cloud-panel__err"
      role="alert"
    >
      {{ error }}
    </p>
  </section>
</template>

<style scoped>
.cloud-pad {
  display: grid;
  gap: 1rem;
}
.cloud-pad__head h2 {
  margin: 0;
  font-size: 1.05rem;
}
.cloud-pad__live {
  margin: 0.35rem 0 0;
  font-size: 0.85rem;
}
.cloud-pad__live .ok {
  color: #3dd68c;
}
.cloud-pad__live .off {
  color: #ff8b7b;
}
.cloud-pad__row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}
.muted {
  opacity: 0.75;
  font-size: 0.85rem;
  margin: 0.25rem 0 0;
}
.muted code {
  font-size: 0.8rem;
}
.banner {
  margin: 0;
  padding: 0.65rem 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.9rem;
}
.banner--warn {
  background: color-mix(in srgb, #c47a20 22%, transparent);
  border: 1px solid color-mix(in srgb, #c47a20 50%, transparent);
}
.action-btn {
  min-height: 2.5rem;
  padding: 0.45rem 0.75rem;
  border-radius: 0.55rem;
  border: 1px solid color-mix(in srgb, currentColor 28%, transparent);
  background: color-mix(in srgb, currentColor 8%, transparent);
  color: inherit;
  font: inherit;
}
.action-btn:disabled {
  opacity: 0.45;
}
.cloud-panel__err {
  color: #ff8b7b;
  margin: 0;
}
</style>
