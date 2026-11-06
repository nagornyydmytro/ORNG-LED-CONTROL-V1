<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  adminAuthed,
  adminAuthChecked,
  endAdminSession,
  markAdminAuthed,
} from "../cloud/adminSession";
import {
  AdminAuthError,
  adminApprovePair,
  adminAssignScene,
  adminDeviceCommand,
  adminListDevices,
  adminListScenes,
  adminLogin,
  adminLogout,
  adminMe,
  adminRevokeDevice,
  type AdminDevice,
  type AdminScene,
} from "../api/adminCloud";
import { createCloudDeviceTransport } from "../cloud/transports/cloudDevice";
import type { CloudControlTransport, CloudDeviceLiveState } from "../cloud/transport";
import { useTheme } from "../composables/useTheme";

const emit = defineEmits<{ back: [] }>();
const { isLight, setTheme } = useTheme();

type Screen = "login" | "list" | "device" | "pair";

const authChecked = adminAuthChecked;
const authed = adminAuthed;
const loginUser = ref("");
const loginPass = ref("");
const loginBusy = ref(false);

const screen = ref<Screen>("login");
const devices = ref<AdminDevice[]>([]);
const scenes = ref<AdminScene[]>([]);
const selectedId = ref<string | null>(null);
const live = ref<CloudDeviceLiveState | null>(null);
const transport = ref<CloudControlTransport | null>(null);

const busy = ref(false);
const actionBusy = ref(false);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);

const pairId = ref("");
const pairCode = ref("");
const pairName = ref("");

const activeDevices = computed(() =>
  devices.value.filter((d) => !d.revoked),
);

const onlineDevices = computed(() =>
  activeDevices.value.filter((d) => d.online).sort((a, b) => displayName(a).localeCompare(displayName(b))),
);

const offlineDevices = computed(() =>
  activeDevices.value.filter((d) => !d.online).sort((a, b) => displayName(a).localeCompare(displayName(b))),
);

const selected = computed(
  () => devices.value.find((d) => d.id === selectedId.value) ?? null,
);

const title = computed(() => (selected.value ? displayName(selected.value) : "Локація"));

function displayName(d: AdminDevice): string {
  const raw = (d.name || "").trim();
  if (!raw) return "Локація";
  return raw.replace(/\s*Raspberry\s*$/i, "").trim() || raw;
}

function sceneLine(d: AdminDevice): string {
  if (d.current_scene_id === "orng-bar") return "ORNG BAR";
  return d.current_scene_id || "Без сцени";
}

const statusRows = computed(() => {
  const d = selected.value;
  const s = live.value;
  const online = Boolean(s?.online ?? d?.online);
  const blackout = Boolean(s?.blackout ?? d?.blackout ?? true);
  const armed = Boolean(s?.armed ?? d?.armed);
  const kb = s?.keyboard_connected;
  const eth = s?.ethernet_link;
  const cloud = s?.cloud_connected;
  const artnet = s?.artnet_route_valid;
  return [
    { label: "Зв’язок", ok: online, text: online ? "Онлайн" : "Офлайн" },
    {
      label: "Світло",
      ok: online && armed && !blackout,
      warn: online && blackout,
      text: !online ? "—" : blackout ? "Blackout" : armed ? "Працює" : "Немає виходу",
    },
    {
      label: "Клавіатура",
      ok: kb === true,
      warn: kb === false,
      text: kb == null ? "—" : kb ? "Підключена" : "Немає",
    },
    {
      label: "Мережа залу",
      ok: eth === true && artnet !== false,
      warn: eth === false || artnet === false,
      text:
        eth == null && artnet == null
          ? "—"
          : eth === false
            ? "Немає лінку"
            : artnet === false
              ? "Art-Net маршрут"
              : "OK",
    },
    {
      label: "Хмара",
      ok: cloud === true,
      warn: cloud === false,
      text: cloud == null ? "—" : cloud ? "OK" : "Немає",
    },
  ];
});

const sceneLabel = computed(() => {
  const name = live.value?.scene_name;
  const id = live.value?.current_scene_id ?? selected.value?.current_scene_id;
  if (name) return name;
  if (id === "orng-bar") return "ORNG BAR";
  return id || "—";
});

const appLabel = computed(() => {
  const v = live.value?.app_version ?? selected.value?.app_version;
  return v || "—";
});

function markUnauthed() {
  endAdminSession("expired");
  disposeTransport();
  screen.value = "login";
}

function disposeTransport() {
  transport.value?.dispose();
  transport.value = null;
  live.value = null;
}

async function checkSession() {
  try {
    const me = await adminMe();
    if (me?.ok) markAdminAuthed(me.username);
  } catch (err) {
    if (!(err instanceof AdminAuthError)) {
      error.value = err instanceof Error ? err.message : "Сесію не перевірено";
    }
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
    markAdminAuthed(res.username);
    loginPass.value = "";
    await enterService();
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
    error.value = "Вихід на сервері не підтверджено";
  }
  endAdminSession("logout");
  disposeTransport();
  devices.value = [];
  selectedId.value = null;
  screen.value = "login";
}

async function enterService() {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await refreshDevices();
    try {
      const sc = await adminListScenes();
      scenes.value = sc.scenes ?? [];
    } catch (err) {
      scenes.value = [];
      if (err instanceof AdminAuthError) {
        markUnauthed();
        error.value = err.message;
        return;
      }
      error.value = err instanceof Error ? err.message : "Сцени не прочитано";
    }
    screen.value = "list";
  } catch (e) {
    if (e instanceof AdminAuthError) {
      markUnauthed();
      error.value = e.message;
      return;
    }
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function openDevice(id: string) {
  busy.value = true;
  error.value = null;
  notice.value = null;
  disposeTransport();
  selectedId.value = id;
  try {
    const t = createCloudDeviceTransport({ installationId: id });
    t.onLiveState((s) => {
      live.value = { ...(live.value ?? { online: false, armed: false, blackout: true }), ...s };
    });
    const d = devices.value.find((x) => x.id === id);
    live.value = {
      online: Boolean(d?.online),
      armed: Boolean(d?.armed),
      blackout: Boolean(d?.blackout ?? true),
      current_scene_id: d?.current_scene_id,
      app_version: d?.app_version,
    };
    await t.connect();
    transport.value = t;
    screen.value = "device";
    void adminDeviceCommand(id, "request_diagnostics").catch(() => {
      /* optional */
    });
  } catch (e) {
    if (e instanceof AdminAuthError) {
      markUnauthed();
      error.value = e.message;
      return;
    }
    error.value = e instanceof Error ? e.message : String(e);
    screen.value = "list";
  } finally {
    busy.value = false;
  }
}

function backToList() {
  disposeTransport();
  selectedId.value = null;
  screen.value = "list";
}

async function runAction(label: string, fn: () => Promise<void>) {
  if (!selectedId.value) return;
  actionBusy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await fn();
    notice.value = label;
    await refreshDevices();
  } catch (e) {
    if (e instanceof AdminAuthError) {
      markUnauthed();
      error.value = e.message;
      return;
    }
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    actionBusy.value = false;
  }
}

function restartRuntime() {
  if (!window.confirm("Перезапустити програму на Raspberry? (не весь комп’ютер)")) return;
  void runAction("Перезапуск надіслано", async () => {
    await adminDeviceCommand(selectedId.value!, "restart_runtime");
  });
}

function forceBlackout() {
  void runAction("Blackout увімкнено", async () => {
    await adminDeviceCommand(selectedId.value!, "blackout", { enabled: true });
  });
}

function checkUpdate() {
  void runAction("Перевірка оновлення", async () => {
    await adminDeviceCommand(selectedId.value!, "check_app_update");
  });
}

function syncScene() {
  const sceneId = live.value?.current_scene_id ?? selected.value?.current_scene_id;
  if (!sceneId) {
    error.value = "Немає активної сцени";
    return;
  }
  void runAction("Синхронізація сцени", () =>
    transport.value!.syncSceneRelease(sceneId),
  );
}

async function assignDefaultScene() {
  if (!selectedId.value) return;
  const sceneId =
    selected.value?.current_scene_id ||
    scenes.value.find((s) => s.scene_id === "orng-bar")?.scene_id ||
    scenes.value[0]?.scene_id;
  if (!sceneId) {
    error.value = "Немає сцен у каталозі";
    return;
  }
  void runAction("Сцену призначено", async () => {
    await adminAssignScene(selectedId.value!, {
      scene_id: sceneId,
      track_latest: true,
    });
  });
}

async function removeOffline(d: AdminDevice, ev: Event) {
  ev.preventDefault();
  ev.stopPropagation();
  if (d.online) {
    error.value = "Не можна прибрати онлайн-локацію";
    return;
  }
  if (!window.confirm(`Прибрати «${displayName(d)}» зі списку? Це старий офлайн-запис.`)) {
    return;
  }
  actionBusy.value = true;
  error.value = null;
  notice.value = null;
  try {
    await adminRevokeDevice(d.id);
    notice.value = "Офлайн-запис прибрано";
    await refreshDevices();
  } catch (e) {
    if (e instanceof AdminAuthError) {
      markUnauthed();
      error.value = e.message;
      return;
    }
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    actionBusy.value = false;
  }
}

async function onPair() {
  actionBusy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const res = await adminApprovePair({
      pairing_id: pairId.value.trim(),
      code: pairCode.value.trim(),
      name: pairName.value.trim() || "Локація",
      platform: "raspberry",
    });
    notice.value = "Пристрій додано";
    pairId.value = "";
    pairCode.value = "";
    pairName.value = "";
    await refreshDevices();
    if (res.installation_id) {
      await openDevice(res.installation_id);
    } else {
      screen.value = "list";
    }
  } catch (e) {
    if (e instanceof AdminAuthError) {
      markUnauthed();
      error.value = e.message;
      return;
    }
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    actionBusy.value = false;
  }
}

function pillClass(row: { ok?: boolean; warn?: boolean }) {
  if (row.ok) return "ui-pill--ok";
  if (row.warn) return "ui-pill--warn";
  return "ui-pill--off";
}

onMounted(async () => {
  await checkSession();
  if (authed.value) await enterService();
});

onUnmounted(() => {
  disposeTransport();
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
        <strong>Сервіс</strong>
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
        <button
          type="button"
          class="ui-link"
          @click="emit('back')"
        >
          ← Пульт
        </button>
        <button
          v-if="authed && screen === 'device'"
          type="button"
          class="ui-link"
          @click="backToList"
        >
          Локації
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
      v-if="notice"
      class="ui-alert ui-alert--ok"
      role="status"
    >
      {{ notice }}
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

    <!-- LIST -->
    <section
      v-else-if="authed && screen === 'list'"
      class="ui-stack"
    >
      <div class="ui-head">
        <h1>Локації</h1>
        <button
          type="button"
          class="ui-btn ui-btn--ghost"
          :disabled="busy"
          @click="enterService"
        >
          Оновити
        </button>
      </div>

      <p
        v-if="busy"
        class="ui-muted"
      >
        Завантаження…
      </p>
      <p
        v-else-if="!activeDevices.length"
        class="ui-alert ui-alert--warn"
      >
        Немає пристроїв. Додай Raspberry через пару.
      </p>

      <button
        v-for="d in onlineDevices"
        :key="d.id"
        type="button"
        class="ui-loc"
        @click="openDevice(d.id)"
      >
        <div class="ui-loc__head">
          <strong>{{ displayName(d) }}</strong>
          <span class="ui-pill ui-pill--ok">Online</span>
        </div>
        <div class="ui-loc__meta">
          <span>{{ sceneLine(d) }}</span>
          <span>{{ d.blackout ? "Blackout" : "Світло" }}</span>
        </div>
      </button>

      <template v-if="offlineDevices.length">
        <p class="ui-section-label">
          Старі / офлайн
        </p>
        <div
          v-for="d in offlineDevices"
          :key="d.id"
          class="ui-loc ui-loc--offline"
        >
          <button
            type="button"
            class="ui-loc__main"
            @click="openDevice(d.id)"
          >
            <div class="ui-loc__head">
              <strong>{{ displayName(d) }}</strong>
              <span class="ui-pill ui-pill--off">Offline</span>
            </div>
            <div class="ui-loc__meta">
              <span>{{ sceneLine(d) }}</span>
              <span>Не підключений</span>
            </div>
          </button>
          <button
            type="button"
            class="ui-btn ui-btn--danger ui-btn--slim"
            :disabled="actionBusy"
            @click="removeOffline(d, $event)"
          >
            Прибрати
          </button>
        </div>
      </template>

      <button
        type="button"
        class="ui-btn ui-btn--block"
        @click="screen = 'pair'"
      >
        + Додати пристрій
      </button>
    </section>

    <!-- PAIR -->
    <section
      v-else-if="authed && screen === 'pair'"
      class="ui-stack"
    >
      <div class="ui-head">
        <h1>Новий пристрій</h1>
        <button
          type="button"
          class="ui-link"
          @click="screen = 'list'"
        >
          ← Назад
        </button>
      </div>
      <p class="ui-muted">
        Код і ID беруться з екрана pairing на Raspberry.
      </p>
      <form
        class="ui-form ui-card"
        @submit.prevent="onPair"
      >
        <label>
          Назва локації
          <input
            v-model="pairName"
            placeholder="ORNG BAR"
            required
          >
        </label>
        <label>
          Pairing ID
          <input
            v-model="pairId"
            autocomplete="off"
            required
          >
        </label>
        <label>
          Код
          <input
            v-model="pairCode"
            autocomplete="one-time-code"
            required
          >
        </label>
        <button
          type="submit"
          class="ui-btn ui-btn--primary"
          :disabled="actionBusy"
        >
          Підтвердити
        </button>
      </form>
    </section>

    <!-- DEVICE -->
    <section
      v-else-if="authed && screen === 'device'"
      class="ui-stack"
    >
      <header class="ui-device-head">
        <h1>{{ title }}</h1>
        <p class="ui-muted">
          {{ sceneLabel }} · app {{ appLabel }}
        </p>
      </header>

      <div class="ui-status">
        <div
          v-for="row in statusRows"
          :key="row.label"
          class="ui-status__row"
        >
          <span>{{ row.label }}</span>
          <span
            class="ui-pill"
            :class="pillClass(row)"
          >{{ row.text }}</span>
        </div>
      </div>

      <div class="ui-stack">
        <button
          type="button"
          class="ui-btn ui-btn--danger"
          :disabled="actionBusy || !selected?.online"
          @click="forceBlackout"
        >
          Увімкнути Blackout
        </button>
        <button
          type="button"
          class="ui-btn"
          :disabled="actionBusy || !selected?.online"
          @click="restartRuntime"
        >
          Перезапуск програми
        </button>
        <button
          type="button"
          class="ui-btn"
          :disabled="actionBusy || !selected?.online"
          @click="syncScene"
        >
          Синхронізувати сцену
        </button>
        <button
          type="button"
          class="ui-btn"
          :disabled="actionBusy"
          @click="assignDefaultScene"
        >
          Призначити сцену
        </button>
        <button
          type="button"
          class="ui-btn"
          :disabled="actionBusy || !selected?.online"
          @click="checkUpdate"
        >
          Перевірити оновлення
        </button>
      </div>
    </section>
  </div>
</template>
