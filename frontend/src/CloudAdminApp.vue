<script setup lang="ts">
import { computed, onMounted, onUnmounted, provide, ref, shallowRef } from "vue";
import {
  AdminAuthError,
  adminListLocations,
  adminLogin,
  adminLogout,
  adminMe,
  type AdminLocation,
} from "./api/adminCloud";
import AppShell from "./components/AppShell.vue";
import { APP_STATE_KEY, type AppStateApi } from "./composables/appStateKey";
import { useCloudAppState } from "./composables/useCloudAppState";
import { provideToasts } from "./composables/useToasts";
import {
  adminAuthChecked,
  adminAuthed,
  endAdminSession,
  markAdminAuthed,
} from "./cloud/adminSession";
import type { CloudControlTransport, CloudDeviceLiveState } from "./cloud/transport";
import { createCloudDeviceTransport } from "./cloud/transports/cloudDevice";
import { createCloudScenePort } from "./runtime/cloudScenePort";
import {
  installScenePort,
  locationStatusLabel,
  sceneSyncPhase,
  useCloudSceneBackend,
} from "./runtime/scenePort";

useCloudSceneBackend();
const authChecked = adminAuthChecked;
const authed = adminAuthed;

const STORAGE_KEY = "orng-operator-location-id";

const toasts = provideToasts();
const loginUser = ref("");
const loginPass = ref("");
const loginBusy = ref(false);
const error = ref<string | null>(null);
const locations = ref<AdminLocation[]>([]);
const locationId = ref<string | null>(null);
const accountId = ref("session");
const transport = shallowRef<CloudControlTransport | null>(null);
const syncSnap = ref<CloudDeviceLiveState>({
  online: false,
  armed: false,
  blackout: true,
});
let stopSync: (() => void) | null = null;

const selected = computed(
  () => locations.value.find((item) => item.id === locationId.value) ?? null,
);
const location = computed(() => {
  const item = selected.value;
  if (!item) return null;
  return {
    id: item.id,
    name: item.name,
    sceneId: item.scene_id,
    sceneName: item.name,
  };
});

const appState = useCloudAppState(transport, toasts, location);
provide(APP_STATE_KEY, appState as unknown as AppStateApi);

const syncLabel = computed(() => locationStatusLabel(sceneSyncPhase.value));

function rememberedLocation(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function openLocation(item: AdminLocation) {
  stopSync?.();
  stopSync = null;
  const previous = transport.value;
  transport.value = null;
  previous?.dispose();
  syncSnap.value = { online: false, armed: false, blackout: true };
  installScenePort(
    createCloudScenePort({
      installationId: item.installation_id,
      locationId: item.id,
      sceneId: item.scene_id,
      accountId: accountId.value,
      live: () => syncSnap.value,
    }),
  );
  locationId.value = item.id;
  error.value = item.installation_id ? null : "Виконавця немає. Пресети й налаштування доступні.";
  try {
    localStorage.setItem(STORAGE_KEY, item.id);
  } catch {
    /* ignore */
  }
  if (!item.installation_id) {
    transport.value = null;
    return;
  }
  const next = createCloudDeviceTransport({ installationId: item.installation_id });
  stopSync = next.onLiveState((state) => {
    syncSnap.value = { ...syncSnap.value, ...state };
  });
  transport.value = next;
}

async function enter() {
  const res = await adminListLocations();
  locations.value = res.locations ?? [];
  const open = locations.value;
  if (open.length === 0) {
    error.value = "Немає локації";
    return;
  }
  const remembered = rememberedLocation();
  const pick =
    open.find(
      (item) =>
        item.id === remembered ||
        item.installation_id === remembered ||
        item.installations.some((member) => member.id === remembered),
    ) ?? open[0]!;
  openLocation(pick);
}

async function checkSession() {
  try {
    const me = await adminMe();
    if (!me?.ok) return;
    if (me.username) accountId.value = me.username;
    await enter();
    markAdminAuthed(me.username);
  } catch (err) {
    if (!(err instanceof AdminAuthError)) {
      error.value = err instanceof Error ? err.message : "Сесію не перевірено";
    }
  } finally {
    authChecked.value = true;
  }
}

async function onLogin() {
  loginBusy.value = true;
  error.value = null;
  try {
    await adminLogin(loginUser.value.trim(), loginPass.value);
    accountId.value = loginUser.value.trim() || accountId.value;
    loginPass.value = "";
    await enter();
    markAdminAuthed(accountId.value);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Вхід не вдався";
  } finally {
    loginBusy.value = false;
  }
}

function onSignedOut(event: Event) {
  stopSync?.();
  stopSync = null;
  const previous = transport.value;
  transport.value = null;
  previous?.dispose();
  installScenePort(null);
  locationId.value = null;
  const reason = (event as CustomEvent<string>).detail;
  if (reason === "expired" && !error.value) {
    error.value = "Сесія застаріла — увійдіть знову";
  }
}

async function onLogout() {
  error.value = null;
  try {
    await adminLogout();
  } catch {
    error.value = "Вихід на сервері не підтверджено";
  }
  endAdminSession("logout");
}

function onSwitchLocation(id: string) {
  const item = locations.value.find((entry) => entry.id === id);
  if (item) openLocation(item);
}

onMounted(() => {
  window.addEventListener("orng-admin-signed-out", onSignedOut);
  void checkSession();
});

onUnmounted(() => {
  window.removeEventListener("orng-admin-signed-out", onSignedOut);
});
</script>

<template>
  <section
    v-if="!authChecked"
    class="ui-page"
  >
    <p class="ui-muted">
      Перевірка сесії…
    </p>
  </section>
  <section
    v-else-if="!authed"
    class="ui-page"
  >
    <div class="ui-card">
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
        <p
          v-if="error"
          class="ui-alert"
          role="alert"
        >
          {{ error }}
        </p>
        <button
          type="submit"
          class="ui-btn ui-btn--primary"
          :disabled="loginBusy"
        >
          Увійти
        </button>
      </form>
    </div>
  </section>
  <AppShell
    v-else
    :connection="appState.connection.value"
    transport="cloud"
    :armed="false"
    :output-error="error"
    :blackout="appState.engine.value?.blackout ?? true"
    :scene-id="appState.sceneId.value"
    :scene-name="appState.sceneName.value"
    :scenes="appState.availableScenes.value"
    :scene-switching="appState.sceneSwitching.value"
    :location-name="selected?.name ?? ''"
    :location-id="locationId"
    :locations="locations.map((item) => ({ id: item.id, name: item.name }))"
    :sync-label="syncLabel"
    show-logout
    @switch-scene="appState.switchScene"
    @switch-location="onSwitchLocation"
    @logout="onLogout"
  />
</template>
