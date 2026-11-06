<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  appUpdateApply,
  appUpdateCancel,
  appUpdateCheck,
  appUpdateRollback,
  appUpdateStage,
  cloudPairComplete,
  cloudPairStart,
  cloudPublish,
  cloudSync,
  fetchAppUpdateStatus,
  fetchCloudStatus,
  type AppUpdateStatus,
  type CloudStatus,
  type SyncStatus,
} from "../../api/cloud";
import { adminApprovePair, adminListDevices, type AdminDevice } from "../../api/adminCloud";
import { isLocalCloudDevApproveEnabled } from "../../cloud/mode";
import { orngAppVersion } from "../../lib/appVersion";
import {
  appUpdateButtonState,
  applyConfirmText,
  updateBadges,
} from "../../runtime/appUpdateActions";

const status = ref<CloudStatus | null>(null);
const syncResult = ref<SyncStatus | null>(null);
const appUpdate = ref<AppUpdateStatus | null>(null);
const devices = ref<AdminDevice[]>([]);
const message = ref<string | null>(null);
const error = ref<string | null>(null);
const busy = ref(false);
const uiVersion = orngAppVersion();

const adminBase = ref(localStorage.getItem("orng-cloud-admin") || "http://127.0.0.1:8787");
const pairName = ref("ORNG Device");
const pairPlatform = ref("development");
const pendingPair = ref<{
  pairing_id: string;
  code: string;
  client_challenge: string;
  expires_at: string;
} | null>(null);

const showDevApprove = isLocalCloudDevApproveEnabled();
const adminApproveUrl = "https://light.orng.wtf";

const syncBadge = computed(() => {
  const s = syncResult.value?.status ?? status.value?.sync?.status ?? "unknown";
  return s;
});

const badgeLabel = computed(() => {
  switch (syncBadge.value) {
    case "synced":
      return "synced";
    case "dirty":
    case "local":
      return "dirty (локальні зміни)";
    case "update":
    case "update_available":
      return "update_available (assigned≠local)";
    case "conflict":
      return "conflict";
    case "error":
      return "error";
    case "applied":
      return "applied";
    case "downloaded":
      return "downloaded (staged)";
    default:
      return String(syncBadge.value);
  }
});

const syncDetail = computed(() => {
  const s = syncResult.value ?? status.value?.sync;
  if (!s) return null;
  return {
    assigned: s.desired_version != null ? `v${s.desired_version}` : "—",
    applied: s.applied_version != null ? `v${s.applied_version}` : "—",
    syncedHash: s.last_synced_hash ? String(s.last_synced_hash).slice(0, 12) : "—",
    localHash: s.local_hash ? String(s.local_hash).slice(0, 12) : "—",
    appliedHash: s.applied_hash ? String(s.applied_hash).slice(0, 12) : "—",
    diverged: Boolean(s.diverged),
  };
});

const isDeveloperInstall = computed(() => {
  const mode = (appUpdate.value?.install_mode ?? "").toLowerCase();
  return (
    mode === "developer" ||
    mode === "dev" ||
    appUpdate.value?.refusal_reason === "developer_checkout" ||
    appUpdate.value?.self_update_allowed === false
  );
});

const appBadges = computed(() =>
  updateBadges({
    updateState: appUpdate.value?.update_state,
    updatePhase: appUpdate.value?.update_phase,
    lastError: appUpdate.value?.last_error,
    runningBuild: appUpdate.value?.build_id,
    desiredBuild: appUpdate.value?.desired_build_id,
    stagedBuild: appUpdate.value?.staged_build_id,
  }),
);

const appButtons = computed(() =>
  appUpdateButtonState({
    hasInstallation: true,
    installMode: appUpdate.value?.install_mode,
    selfUpdateAllowed: appUpdate.value?.self_update_allowed,
    devicePlatform: appUpdate.value?.platform,
    deviceArch: appUpdate.value?.arch,
    updateState: appUpdate.value?.update_state,
    stagedBuild: appUpdate.value?.staged_build_id,
    targetBuild: appUpdate.value?.desired_build_id ?? appUpdate.value?.staged_build_id,
    busy: busy.value,
  }),
);

const developerRefusal = computed(() => {
  if (!appUpdate.value) return null;
  if (appUpdate.value.refusal_reason === "developer_checkout" || isDeveloperInstall.value) {
    return (
      appUpdate.value.refusal_reason ??
      "developer_checkout: remote self-update disabled for developer git checkouts"
    );
  }
  return null;
});

async function refresh() {
  error.value = null;
  try {
    status.value = await fetchCloudStatus();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
  try {
    appUpdate.value = await fetchAppUpdateStatus();
  } catch {
    // Updater may be unavailable — keep cloud pairing/sync usable.
  }
}

async function refreshDevices() {
  error.value = null;
  try {
    localStorage.setItem("orng-cloud-admin", adminBase.value);
    const res = await adminListDevices({ adminBase: adminBase.value });
    devices.value = res.devices ?? [];
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function startPair() {
  busy.value = true;
  error.value = null;
  message.value = null;
  try {
    pendingPair.value = await cloudPairStart();
    message.value =
      `Код: ${pendingPair.value.code}. Відкрийте ${adminApproveUrl} (або локальний admin), ` +
      `схваліть парування, потім натисніть «Завершити після approve».`;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function completeAfterApprove() {
  if (!pendingPair.value) return;
  busy.value = true;
  error.value = null;
  try {
    const done = await cloudPairComplete({
      pairing_id: pendingPair.value.pairing_id,
      client_challenge: pendingPair.value.client_challenge,
    });
    message.value = `Підключено: ${done.installation_id} · credential збережено локально`;
    pendingPair.value = null;
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

/** Local-only helper — requires VITE_ORNG_CLOUD_DEV_APPROVE=true. */
async function approvePendingDev() {
  if (!showDevApprove || !pendingPair.value) return;
  busy.value = true;
  error.value = null;
  try {
    await adminApprovePair(
      {
        pairing_id: pendingPair.value.pairing_id,
        code: pendingPair.value.code,
        name: pairName.value,
        platform: pairPlatform.value,
      },
      { adminBase: adminBase.value },
    );
    message.value = "DEV approve OK (токен не повертається). Завершіть парування…";
    await completeAfterApprove();
    await refreshDevices();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function publish() {
  busy.value = true;
  error.value = null;
  try {
    const res = await cloudPublish({ admin_url: adminBase.value });
    message.value = res.idempotent ? "Реліз уже існує (idempotent)" : "Сцену опубліковано";
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function sync(force = false) {
  busy.value = true;
  error.value = null;
  try {
    syncResult.value = await cloudSync(force);
    message.value = `Синхронізація: ${syncResult.value.status}`;
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function runAppUpdate(
  action: "check" | "stage" | "apply" | "cancel" | "rollback",
) {
  if (developerRefusal.value && action !== "check") {
    error.value = developerRefusal.value;
    return;
  }
  const captured = {
    version: appUpdate.value?.staged_version ?? appUpdate.value?.desired_version ?? "",
    build_id: appUpdate.value?.staged_build_id ?? appUpdate.value?.desired_build_id ?? "",
    content_sha256: appUpdate.value?.staged_content_sha256 ?? "",
  };
  if (action === "apply") {
    if (
      !window.confirm(
        applyConfirmText({
          installationId: "цей пристрій",
          version: captured.version,
          buildId: captured.build_id,
        }),
      )
    ) {
      return;
    }
  }
  busy.value = true;
  error.value = null;
  try {
    if (action === "check") {
      const res = await appUpdateCheck();
      if (res.status) appUpdate.value = res.status;
      message.value = res.update_available
        ? "Оновлення доступне"
        : "Немає нового assigned release";
    } else if (action === "stage") {
      await appUpdateStage();
      message.value = "Stage запущено";
    } else if (action === "apply") {
      await appUpdateApply(true, captured);
      message.value = "Apply запущено (force)";
    } else if (action === "cancel") {
      await appUpdateCancel();
      message.value = "Staged update скасовано";
    } else {
      await appUpdateRollback();
      message.value = "Rollback запущено";
    }
    appUpdate.value = await fetchAppUpdateStatus();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    try {
      appUpdate.value = await fetchAppUpdateStatus();
    } catch {
      /* ignore */
    }
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  void refresh();
});
</script>

<template>
  <section class="cloud-panel">
    <header class="cloud-panel__head">
      <h2>Хмара</h2>
      <span
        class="cloud-badge"
        :data-status="syncBadge"
      >{{ badgeLabel }}</span>
      <span class="muted">UI {{ uiVersion }}</span>
    </header>

    <p class="cloud-panel__hint">
      Локальний режим керує світлом через FastAPI. Хмара лише парує пристрій і
      синхронізує сцени / app releases. Токени ніколи не показуються.
      Віддалений пульт — на
      <a
        :href="adminApproveUrl"
        target="_blank"
        rel="noopener"
      >light.orng.wtf</a>.
    </p>

    <div class="cloud-panel__row">
      <label>
        Admin Worker
        <input
          v-model="adminBase"
          type="url"
          placeholder="http://127.0.0.1:8787"
        >
      </label>
      <button
        type="button"
        :disabled="busy"
        @click="refreshDevices"
      >
        Оновити пристрої
      </button>
      <button
        type="button"
        :disabled="busy"
        @click="refresh"
      >
        Статус агента
      </button>
    </div>

    <div
      v-if="status"
      class="cloud-panel__status"
    >
      <p>
        Агент:
        {{ status.agent.enabled ? (status.agent.connected ? "онлайн" : "офлайн") : "вимкнено" }}
        · токен: {{ status.agent.has_token ? "є" : "немає" }}
      </p>
      <p
        v-if="status.agent.last_error"
        class="cloud-panel__err"
      >
        {{ status.agent.last_error }}
      </p>
      <p class="muted">
        Локальний hash: {{ status.sync.local_hash ?? "—" }}
      </p>
      <p
        v-if="syncDetail"
        class="muted"
      >
        assigned {{ syncDetail.assigned }}
        · applied {{ syncDetail.applied }}
        · synced {{ syncDetail.syncedHash }}
        · local {{ syncDetail.localHash }}
        · applied_hash {{ syncDetail.appliedHash }}
        <template v-if="syncDetail.diverged">
          · <span class="cloud-panel__err">diverged</span>
        </template>
      </p>
    </div>

    <div class="cloud-panel__block">
      <h3>Парування</h3>
      <ol class="cloud-steps">
        <li>Натисніть «Старт коду».</li>
        <li>
          Відкрийте
          <strong>{{ adminApproveUrl }}</strong>
          (або локальний cloud admin) і схваліть код — токен у браузер не потрапляє.
        </li>
        <li>Поверніться сюди й натисніть «Завершити після approve».</li>
      </ol>
      <div class="cloud-panel__row">
        <input
          v-model="pairName"
          placeholder="Назва (для DEV approve)"
        >
        <select v-model="pairPlatform">
          <option value="development">
            development
          </option>
          <option value="raspberry">
            raspberry
          </option>
          <option value="desktop_windows">
            desktop_windows
          </option>
          <option value="desktop_macos">
            desktop_macos
          </option>
        </select>
        <button
          type="button"
          :disabled="busy"
          @click="startPair"
        >
          Старт коду
        </button>
        <button
          type="button"
          :disabled="busy || !pendingPair"
          @click="completeAfterApprove"
        >
          Завершити після approve
        </button>
        <button
          v-if="showDevApprove"
          type="button"
          :disabled="busy || !pendingPair"
          @click="approvePendingDev"
        >
          DEV: approve локально
        </button>
      </div>
      <p
        v-if="pendingPair"
        class="cloud-code"
      >
        pairing_id: <code>{{ pendingPair.pairing_id }}</code><br>
        Код: <strong>{{ pendingPair.code }}</strong>
        · до {{ pendingPair.expires_at }}
      </p>
      <p
        v-if="!showDevApprove"
        class="muted"
      >
        Локальний approve прихований. Увімкніть лише з
        <code>VITE_ORNG_CLOUD_DEV_APPROVE=true</code>.
      </p>
    </div>

    <div class="cloud-panel__block">
      <h3>Сцена</h3>
      <div class="cloud-panel__row">
        <button
          type="button"
          :disabled="busy"
          @click="publish"
        >
          Опублікувати локальну сцену
        </button>
        <button
          type="button"
          :disabled="busy"
          @click="sync(false)"
        >
          Синхронізувати
        </button>
        <button
          type="button"
          :disabled="busy"
          @click="sync(true)"
        >
          Примусово застосувати
        </button>
      </div>
    </div>

    <div class="cloud-panel__block">
      <h3>Оновлення додатку</h3>
      <template v-if="appUpdate">
        <p>
          Версія {{ appUpdate.app_version }} / {{ appUpdate.build_id }}
          · {{ appUpdate.install_mode }}
          · {{ appUpdate.platform }}/{{ appUpdate.arch }}
        </p>
        <p class="muted">
          Стан: {{ appUpdate.update_state }}
          <template v-if="appUpdate.update_progress != null">
            · {{ Math.round((appUpdate.update_progress ?? 0) * 100) }}%
          </template>
          <template v-if="appUpdate.desired_version">
            · desired {{ appUpdate.desired_version }}/{{ appUpdate.desired_build_id ?? "—" }}
          </template>
          <template v-if="appUpdate.staged_version">
            · staged {{ appUpdate.staged_version }}/{{ appUpdate.staged_build_id ?? "—" }}
          </template>
        </p>
        <p class="muted">
          Доступно {{ appBadges.available ? "так" : "ні" }}
          · Скачано {{ appBadges.downloaded ? "так" : "ні" }}
          · Очікує {{ appBadges.waiting ? "так" : "ні" }}
          · Застосовано {{ appBadges.applied ? "так" : "ні" }}
          · Помилка {{ appBadges.error ? "так" : "ні" }}
        </p>
        <p
          v-if="appUpdate.last_error"
          class="cloud-panel__err"
        >
          {{ appUpdate.last_error }}
        </p>
        <p
          v-if="developerRefusal"
          class="cloud-panel__err"
          role="status"
        >
          Відмова: {{ developerRefusal }}
        </p>
        <div class="cloud-panel__row">
          <button
            type="button"
            :disabled="!appButtons.check"
            @click="runAppUpdate('check')"
          >
            Check
          </button>
          <button
            type="button"
            :disabled="!appButtons.stage || Boolean(developerRefusal)"
            :title="developerRefusal ?? appButtons.reason ?? undefined"
            @click="runAppUpdate('stage')"
          >
            Stage
          </button>
          <button
            type="button"
            :disabled="!appButtons.apply || Boolean(developerRefusal)"
            :title="developerRefusal ?? appButtons.reason ?? undefined"
            @click="runAppUpdate('apply')"
          >
            Apply
          </button>
          <button
            type="button"
            :disabled="!appButtons.cancel || Boolean(developerRefusal)"
            :title="developerRefusal ?? appButtons.reason ?? undefined"
            @click="runAppUpdate('cancel')"
          >
            Cancel
          </button>
          <button
            type="button"
            :disabled="!appButtons.rollback || Boolean(developerRefusal)"
            :title="developerRefusal ?? appButtons.reason ?? undefined"
            @click="runAppUpdate('rollback')"
          >
            Rollback
          </button>
        </div>
      </template>
      <p
        v-else
        class="muted"
      >
        Статус оновлення ще не завантажено.
      </p>
    </div>

    <div class="cloud-panel__block">
      <h3>Пристрої (Worker)</h3>      <ul
        v-if="devices.length"
        class="cloud-devices"
      >
        <li
          v-for="d in devices"
          :key="String(d.id)"
        >
          <strong>{{ d.name }}</strong>
          <span class="muted">({{ d.platform }})</span>
          —
          <span :class="d.online ? 'ok' : 'off'">{{ d.online ? "онлайн" : "офлайн" }}</span>
          <span
            v-if="d.revoked"
            class="err"
          >відкликано</span>
          · сцена {{ d.current_scene_id ?? "—" }}
          v{{ d.current_release_version ?? "—" }}
        </li>
      </ul>
      <p
        v-else
        class="muted"
      >
        Немає пристроїв (або Worker не запущено).
      </p>
    </div>

    <p
      v-if="message"
      class="cloud-panel__msg"
      role="status"
    >
      {{ message }}
    </p>
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
.cloud-panel {
  display: grid;
  gap: 1rem;
  max-width: 52rem;
}
.cloud-panel__head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.cloud-panel__head h2 {
  margin: 0;
  font-size: 1.15rem;
}
.cloud-badge {
  font-size: 0.75rem;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  background: color-mix(in srgb, CanvasText 12%, transparent);
}
.cloud-badge[data-status="synced"],
.cloud-badge[data-status="applied"] {
  background: color-mix(in srgb, #2d8a4e 35%, transparent);
}
.cloud-badge[data-status="conflict"],
.cloud-badge[data-status="error"] {
  background: color-mix(in srgb, #b33 40%, transparent);
}
.cloud-panel__hint,
.muted {
  opacity: 0.75;
  font-size: 0.9rem;
}
.cloud-panel__row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: end;
}
.cloud-panel__row label {
  display: grid;
  gap: 0.25rem;
  font-size: 0.85rem;
  flex: 1 1 14rem;
}
.cloud-panel__row input,
.cloud-panel__row select {
  padding: 0.35rem 0.5rem;
}
.cloud-panel__block {
  border-top: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
  padding-top: 0.75rem;
}
.cloud-panel__block h3 {
  margin: 0 0 0.5rem;
  font-size: 1rem;
}
.cloud-steps {
  margin: 0 0 0.75rem;
  padding-left: 1.2rem;
  font-size: 0.9rem;
  opacity: 0.9;
}
.cloud-code {
  font-family: ui-monospace, monospace;
}
.cloud-devices {
  margin: 0;
  padding-left: 1.1rem;
}
.ok {
  color: #2d8a4e;
}
.off {
  opacity: 0.7;
}
.err,
.cloud-panel__err {
  color: #c44;
}
.cloud-panel__msg {
  color: #2d8a4e;
}
</style>
