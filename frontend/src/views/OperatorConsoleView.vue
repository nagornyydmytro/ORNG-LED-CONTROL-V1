<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  AdminAuthError,
  adminGetControlSurface,
  adminListDevices,
  adminLogin,
  adminLogout,
  adminMe,
  type AdminControlSurface,
  type AdminDevice,
} from "../api/adminCloud";
import { createCloudDeviceTransport } from "../cloud/transports/cloudDevice";
import type { CloudControlTransport, CloudDeviceLiveState } from "../cloud/transport";
import { useTheme } from "../composables/useTheme";

const STORAGE_KEY = "orng-operator-location-id";
const PROGRAM_SPEED_MIN = 0.5;
const PROGRAM_SPEED_MAX = 5;
const LIVE_FX_SPEED_MIN = 0.05;
const LIVE_FX_SPEED_MAX = 1;

type Screen = "login" | "locations" | "pad" | "service";
type HoldFx = "strobe" | "sweep-h" | "sweep-v";

const emit = defineEmits<{ openService: [] }>();
const { isLight, setTheme } = useTheme();

const authChecked = ref(false);
const authed = ref(false);
const authUsername = ref<string | null>(null);
const loginUser = ref("");
const loginPass = ref("");
const loginBusy = ref(false);
const busy = ref(false);
const error = ref<string | null>(null);
const screen = ref<Screen>("login");

const devices = ref<AdminDevice[]>([]);
const selectedId = ref<string | null>(null);
const surface = ref<AdminControlSurface | null>(null);
const live = ref<CloudDeviceLiveState | null>(null);
const transport = ref<CloudControlTransport | null>(null);
const cmdError = ref<string | null>(null);

/** Instant local UI — never disable the whole pad while a command flies. */
const pressedId = ref<string | null>(null);
const optimisticPreset = ref<string | null>(null);
const holdingFx = ref<HoldFx | null>(null);
const programSpeed = ref(1);
const liveFxSpeed = ref(0.35);
let lastProgramSent = 1;
let lastLiveFxSent = 0.35;
let lastPointerBtn: string | null = null;

const operatorLocations = computed(() =>
  devices.value.filter((d) => !d.revoked && d.online),
);

const selected = computed(
  () => devices.value.find((d) => d.id === selectedId.value) ?? null,
);

const sceneTitle = computed(() => {
  const name = live.value?.scene_name || surface.value?.scene_name;
  if (name) return name;
  const id = live.value?.current_scene_id ?? selected.value?.current_scene_id;
  if (id === "orng-bar") return "ORNG BAR";
  return id || "—";
});

const padSlots = computed(() => {
  const fromLive = live.value?.pad_presets ?? [];
  if (fromLive.length > 0) return fromLive.slice(0, 9);
  const fromSurface = surface.value?.pad_presets ?? [];
  if (fromSurface.length > 0) return fromSurface.slice(0, 9);
  return [];
});

const online = computed(() => Boolean(live.value?.online ?? selected.value?.online));
const blackout = computed(() => Boolean(live.value?.blackout ?? selected.value?.blackout ?? true));
const activePreset = computed(
  () => optimisticPreset.value ?? live.value?.preset_id ?? null,
);
const faceOn = computed(() => Boolean(live.value?.face_on));

function markUnauthed() {
  authed.value = false;
  authUsername.value = null;
  disposeTransport();
  screen.value = "login";
}

function disposeTransport() {
  transport.value?.dispose();
  transport.value = null;
  live.value = null;
  holdingFx.value = null;
  optimisticPreset.value = null;
}

function rememberLocation(id: string | null) {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

function rememberedLocation(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

async function checkSession() {
  try {
    const me = await adminMe();
    if (me?.ok) {
      authed.value = true;
      authUsername.value = me.username;
    } else {
      markUnauthed();
    }
  } catch {
    markUnauthed();
  } finally {
    authChecked.value = true;
  }
}

async function refreshDevices() {
  const res = await adminListDevices();
  devices.value = res.devices ?? [];
}

async function onLogin() {
  loginBusy.value = true;
  error.value = null;
  try {
    const res = await adminLogin(loginUser.value.trim(), loginPass.value);
    authed.value = true;
    authUsername.value = res.username;
    loginPass.value = "";
    await enterOperator();
  } catch (e) {
    markUnauthed();
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loginBusy.value = false;
  }
}

async function onLogout() {
  try {
    await adminLogout();
  } catch {
    /* ignore */
  }
  markUnauthed();
  devices.value = [];
  selectedId.value = null;
  surface.value = null;
}

async function enterOperator() {
  busy.value = true;
  error.value = null;
  try {
    await refreshDevices();
    const onlineIds = operatorLocations.value.map((d) => d.id);
    const remembered = rememberedLocation();
    if (onlineIds.length === 1) {
      await openPad(onlineIds[0]!);
      return;
    }
    if (remembered && onlineIds.includes(remembered)) {
      await openPad(remembered);
      return;
    }
    screen.value = "locations";
  } catch (e) {
    if (e instanceof AdminAuthError) {
      markUnauthed();
      error.value = e.message;
      return;
    }
    error.value = e instanceof Error ? e.message : String(e);
    screen.value = "locations";
  } finally {
    busy.value = false;
  }
}

async function openPad(id: string) {
  busy.value = true;
  cmdError.value = null;
  error.value = null;
  disposeTransport();
  selectedId.value = id;
  rememberLocation(id);
  try {
    surface.value = await adminGetControlSurface(id);
    const t = createCloudDeviceTransport({ installationId: id });
    t.onLiveState((s) => {
      live.value = { ...(live.value ?? { online: false, armed: false, blackout: true }), ...s };
      // Clear optimism only when device confirms the same preset (not stale heartbeat).
      if (
        optimisticPreset.value != null &&
        s.preset_id != null &&
        s.preset_id === optimisticPreset.value
      ) {
        optimisticPreset.value = null;
      }
    });
    t.onCommandAck((ack) => {
      if (ack.type === "select_preset" && (ack.status === "failed" || ack.status === "timeout")) {
        optimisticPreset.value = null;
        cmdError.value = ack.error
          ? `Пресет: ${ack.error}`
          : "Пресет: команда не підтверджена";
      }
    });
    live.value = {
      online: Boolean(surface.value.online),
      armed: Boolean(surface.value.armed),
      blackout: Boolean(surface.value.blackout),
      transport: surface.value.transport,
      current_scene_id: surface.value.scene_id,
    };
    await t.connect();
    transport.value = t;
    screen.value = "pad";
  } catch (e) {
    if (e instanceof AdminAuthError) {
      markUnauthed();
      error.value = e.message;
      return;
    }
    error.value = e instanceof Error ? e.message : String(e);
    screen.value = "locations";
  } finally {
    busy.value = false;
  }
}

function backToLocations() {
  disposeTransport();
  selectedId.value = null;
  surface.value = null;
  screen.value = "locations";
}

/** Fire-and-forget — never locks the pad UI. */
function fire(label: string, fn: () => Promise<void>) {
  if (!transport.value || !online.value) {
    cmdError.value = "Пристрій офлайн";
    return;
  }
  cmdError.value = null;
  void fn().catch((e) => {
    if (e instanceof AdminAuthError) {
      markUnauthed();
      error.value = e.message;
      return;
    }
    const raw = e instanceof Error ? e.message : String(e);
    cmdError.value = friendlyError(label, raw);
  });
}

function friendlyError(label: string, raw: string): string {
  if (/csrf|сесія застаріла|Login required|Потрібен вхід/i.test(raw)) {
    return "Сесія застаріла — увійдіть знову";
  }
  if (/offline|not open|websocket/i.test(raw)) {
    return "Немає зв’язку з пристроєм";
  }
  if (raw.length > 120 || raw.trim().startsWith("{")) {
    return `${label}: команда не виконана`;
  }
  return `${label}: ${raw}`;
}

function onTapDown(ev: PointerEvent, id: string) {
  if (!ev.isPrimary) return;
  ev.preventDefault();
  ev.stopPropagation();
  (ev.currentTarget as HTMLElement | null)?.setPointerCapture?.(ev.pointerId);
  lastPointerBtn = id;
  pressedId.value = id;
}

function onPrimaryTap(ev: PointerEvent, id: string, fn: () => void) {
  if (!ev.isPrimary) return;
  if (lastPointerBtn != null && lastPointerBtn !== id) {
    pressedId.value = null;
    return;
  }
  ev.preventDefault();
  ev.stopPropagation();
  lastPointerBtn = null;
  pressedId.value = null;
  fn();
}

function onTapCancel() {
  lastPointerBtn = null;
  pressedId.value = null;
}

function selectPreset(id: string) {
  optimisticPreset.value = id;
  fire("Пресет", () => transport.value!.selectPreset(id));
}

function toggleBlackout() {
  const next = !blackout.value;
  if (live.value) live.value = { ...live.value, blackout: next };
  fire("Blackout", () => transport.value!.blackout(next));
}

function faceToggle() {
  if (live.value) live.value = { ...live.value, face_on: !faceOn.value };
  fire("Face", () => transport.value!.faceToggle());
}

function onHoldStart(ev: PointerEvent, id: HoldFx, start: () => void) {
  if (!ev.isPrimary) return;
  ev.preventDefault();
  ev.stopPropagation();
  (ev.currentTarget as HTMLElement | null)?.setPointerCapture?.(ev.pointerId);
  lastPointerBtn = id;
  pressedId.value = id;
  holdingFx.value = id;
  start();
}

function onHoldEnd(ev: PointerEvent, end: () => void) {
  if (!ev.isPrimary) return;
  ev.preventDefault();
  ev.stopPropagation();
  try {
    (ev.currentTarget as HTMLElement | null)?.releasePointerCapture?.(ev.pointerId);
  } catch {
    /* ignore */
  }
  lastPointerBtn = null;
  pressedId.value = null;
  holdingFx.value = null;
  end();
}

function strobe(edge: "press" | "release") {
  fire("Strobe", () => transport.value!.strobe(edge));
}

function sweep(mode: "horizontal" | "vertical", edge: "press" | "release") {
  fire("Sweep", () => transport.value!.sweep(edge, mode));
}

function episode(delta: number) {
  fire("Episode", () => transport.value!.episodeNudge(delta));
}

function onProgramSpeedInput(ev: Event) {
  const v = Number((ev.target as HTMLInputElement).value);
  programSpeed.value = v;
  const delta = Math.round((v - lastProgramSent) * 100) / 100;
  if (Math.abs(delta) < 0.01) return;
  lastProgramSent = v;
  fire("Speed", () => transport.value!.speed("program", delta));
}

function onLiveFxSpeedInput(ev: Event) {
  const v = Number((ev.target as HTMLInputElement).value);
  liveFxSpeed.value = v;
  const delta = Math.round((v - lastLiveFxSent) * 1000) / 1000;
  if (Math.abs(delta) < 0.005) return;
  lastLiveFxSent = v;
  fire("FX Speed", () => transport.value!.speed("live_fx", delta));
}

onMounted(async () => {
  await checkSession();
  if (authed.value) await enterOperator();
});

onUnmounted(() => {
  disposeTransport();
});

watch(online, (ok) => {
  if (!ok && screen.value === "pad") {
    cmdError.value = "Пристрій офлайн";
  }
});
</script>

<template>
  <div class="ui-page">
    <header class="ui-top">
      <div class="ui-brand">
        <img
          src="/logo.svg"
          alt=""
          width="28"
          height="28"
        >
        <strong>ORNG</strong>
      </div>
      <div class="ui-top-actions">
        <div class="ui-theme">
          <button
            type="button"
            :aria-pressed="!isLight"
            @click="setTheme('dark')"
          >
            Темна
          </button>
          <button
            type="button"
            :aria-pressed="isLight"
            @click="setTheme('light')"
          >
            Світла
          </button>
        </div>
        <span
          v-if="authed && authUsername"
          class="ui-user"
        >{{ authUsername }}</span>
        <button
          v-if="authed && screen === 'pad'"
          type="button"
          class="ui-link"
          @click="backToLocations"
        >
          ← Локації
        </button>
        <button
          v-if="authed"
          type="button"
          class="ui-link"
          @click="emit('openService')"
        >
          Сервіс
        </button>
        <button
          v-if="authed"
          type="button"
          class="ui-link"
          @click="onLogout"
        >
          Вийти
        </button>
      </div>
    </header>

    <p
      v-if="error"
      class="ui-alert"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-if="cmdError && screen === 'pad'"
      class="ui-alert"
      role="alert"
    >
      {{ cmdError }}
    </p>

    <!-- LOGIN -->
    <section
      v-if="authChecked && !authed"
      class="ui-card"
    >
      <h1>Вхід</h1>
      <form
        class="ui-form"
        @submit.prevent="onLogin"
      >
        <label>
          Логін
          <input
            v-model="loginUser"
            autocomplete="username"
            required
          >
        </label>
        <label>
          Пароль
          <input
            v-model="loginPass"
            type="password"
            autocomplete="current-password"
            required
          >
        </label>
        <button
          type="submit"
          class="ui-btn ui-btn--primary"
          :disabled="loginBusy"
        >
          Увійти
        </button>
      </form>
    </section>

    <!-- LOCATIONS -->
    <section
      v-else-if="authed && screen === 'locations'"
      class="ui-stack"
    >
      <h1>Виберіть локацію</h1>
      <p
        v-if="busy"
        class="ui-muted"
      >
        Завантаження…
      </p>
      <p
        v-else-if="!operatorLocations.length"
        class="ui-alert ui-alert--warn"
      >
        Немає онлайн-локацій. Перевірте Raspberry у розділі «Сервіс».
      </p>
      <article
        v-for="d in operatorLocations"
        :key="d.id"
        class="ui-loc"
      >
        <div class="ui-loc__head">
          <h2>{{ d.name || "Локація" }}</h2>
          <span
            class="ui-pill"
            :class="d.online ? 'ui-pill--ok' : 'ui-pill--off'"
          >{{ d.online ? "Online" : "Offline" }}</span>
        </div>
        <button
          type="button"
          class="ui-btn ui-btn--primary ui-btn--block"
          :disabled="busy || !d.online"
          @click="openPad(d.id)"
        >
          Відкрити пульт
        </button>
      </article>
    </section>

    <!-- PAD -->
    <section
      v-else-if="authed && screen === 'pad'"
      class="ui-stack"
    >
      <header class="ui-device-head">
        <h1>{{ sceneTitle }}</h1>
        <p
          v-if="!online"
          class="ui-sub"
        >
          Offline
        </p>
      </header>

      <button
        type="button"
        class="ui-blackout"
        :class="{
          'ui-blackout--on': blackout,
          'ui-btn--flash': pressedId === 'blackout',
        }"
        @pointerdown="onTapDown($event, 'blackout')"
        @pointerup="onPrimaryTap($event, 'blackout', toggleBlackout)"
        @pointercancel="onTapCancel"
      >
        BLACKOUT
      </button>

      <div class="ui-grid-3">
        <button
          v-for="id in padSlots"
          :key="id"
          type="button"
          class="ui-preset"
          :class="{
            'ui-preset--active': activePreset === id,
            'ui-btn--flash': pressedId === `preset-${id}`,
          }"
          @pointerdown="onTapDown($event, `preset-${id}`)"
          @pointerup="onPrimaryTap($event, `preset-${id}`, () => selectPreset(id))"
          @pointercancel="onTapCancel"
        >
          <span class="ui-preset__id">{{ id }}</span>
        </button>
      </div>

      <button
        type="button"
        class="ui-btn ui-btn--block"
        :class="{
          'ui-preset--active': activePreset === 'NONE',
          'ui-btn--flash': pressedId === 'none',
        }"
        @pointerdown="onTapDown($event, 'none')"
        @pointerup="onPrimaryTap($event, 'none', () => selectPreset('NONE'))"
        @pointercancel="onTapCancel"
      >
        NONE
      </button>

      <div
        class="ui-fx-row"
        :class="{ 'ui-fx-row--holding': holdingFx }"
      >
        <div class="ui-grid-2">
          <button
            type="button"
            class="ui-btn"
            :class="{
              'ui-preset--active': faceOn,
              'ui-btn--flash': pressedId === 'face',
            }"
            @pointerdown="onTapDown($event, 'face')"
            @pointerup="onPrimaryTap($event, 'face', faceToggle)"
            @pointercancel="onTapCancel"
          >
            Face
          </button>
          <button
            type="button"
            class="ui-btn"
            :class="{
              'ui-btn--held': holdingFx === 'strobe',
              'ui-btn--flash': pressedId === 'strobe',
            }"
            @pointerdown="onHoldStart($event, 'strobe', () => strobe('press'))"
            @pointerup="onHoldEnd($event, () => strobe('release'))"
            @pointercancel="onHoldEnd($event, () => strobe('release'))"
          >
            Strobe
          </button>
          <button
            type="button"
            class="ui-btn"
            :class="{
              'ui-btn--held': holdingFx === 'sweep-h',
              'ui-btn--flash': pressedId === 'sweep-h',
            }"
            @pointerdown="onHoldStart($event, 'sweep-h', () => sweep('horizontal', 'press'))"
            @pointerup="onHoldEnd($event, () => sweep('horizontal', 'release'))"
            @pointercancel="onHoldEnd($event, () => sweep('horizontal', 'release'))"
          >
            Sweep H
          </button>
          <button
            type="button"
            class="ui-btn"
            :class="{
              'ui-btn--held': holdingFx === 'sweep-v',
              'ui-btn--flash': pressedId === 'sweep-v',
            }"
            @pointerdown="onHoldStart($event, 'sweep-v', () => sweep('vertical', 'press'))"
            @pointerup="onHoldEnd($event, () => sweep('vertical', 'release'))"
            @pointercancel="onHoldEnd($event, () => sweep('vertical', 'release'))"
          >
            Sweep V
          </button>
        </div>

        <div
          v-if="holdingFx"
          class="ui-live-speed"
        >
          <input
           
            type="range"
            orient="vertical"
            :min="LIVE_FX_SPEED_MIN"
            :max="LIVE_FX_SPEED_MAX"
            step="0.05"
            :value="liveFxSpeed"
            @input="onLiveFxSpeedInput"
          >
          <span>FX</span>
        </div>
      </div>

      <label class="ui-range">
        <span>Speed {{ programSpeed.toFixed(2) }}×</span>
        <input
          type="range"
          :min="PROGRAM_SPEED_MIN"
          :max="PROGRAM_SPEED_MAX"
          step="0.25"
          :value="programSpeed"
          @input="onProgramSpeedInput"
        >
      </label>

      <div class="ui-grid-2">
        <button
          type="button"
          class="ui-btn"
          :class="{ 'ui-btn--flash': pressedId === 'ep-' }"
          @pointerdown="onTapDown($event, 'ep-')"
          @pointerup="onPrimaryTap($event, 'ep-', () => episode(-1))"
          @pointercancel="onTapCancel"
        >
          Episode −
        </button>
        <button
          type="button"
          class="ui-btn"
          :class="{ 'ui-btn--flash': pressedId === 'ep+' }"
          @pointerdown="onTapDown($event, 'ep+')"
          @pointerup="onPrimaryTap($event, 'ep+', () => episode(1))"
          @pointercancel="onTapCancel"
        >
          Episode +
        </button>
      </div>
    </section>
  </div>
</template>
