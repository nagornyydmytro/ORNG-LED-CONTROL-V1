<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import CloudRemotePad from "../components/cloud/CloudRemotePad.vue";
import { useCloudAdmin } from "../composables/useCloudAdmin";
import { useTheme } from "../composables/useTheme";
import { orngAppVersion } from "../lib/appVersion";
import type { AdminAppRelease, AdminDevice } from "../api/adminCloud";
import { appUpdateButtonState, updateBadges } from "../runtime/appUpdateActions";

const {
  adminBase,
  accessEmail,
  authed,
  authUsername,
  authChecked,
  checkSession,
  login,
  logout,
  devices,
  selectedId,
  selectedIds,
  selected,
  live,
  scenes,
  releases,
  appReleases,
  appAssignment,
  appEvents,
  audit,
  commands,
  busy,
  error,
  message,
  transport,
  padPresetIds,
  availableIds,
  isDeveloperMode,
  developerUpdateReason,
  selectedArch,
  deviceArch,
  refreshDevices,
  refreshScenes,
  refreshReleases,
  refreshAppReleases,
  refreshAppEvents,
  refreshAudit,
  selectDevice,
  toggleSelectedId,
  selectAllDevices,
  approvePair,
  revokeSelected,
  rotateSelected,
  assignScene,
  syncSelected,
  activateSelectedScene,
  publishAppRelease,
  assignAppRelease,
  bulkAppRollout,
  sendAppCommand,
  rollbackAppAssignment,
} = useCloudAdmin();

const loginUser = ref("");
const loginPass = ref("");
const loginBusy = ref(false);

async function onLogin() {
  loginBusy.value = true;
  try {
    await login(loginUser.value.trim(), loginPass.value);
    loginPass.value = "";
  } catch {
    /* error surfaced via composable */
  } finally {
    loginBusy.value = false;
  }
}

const { isLight, setTheme } = useTheme();
const uiVersion = orngAppVersion();

const appBadges = computed(() =>
  updateBadges({
    updateState: live.value?.update_state,
    runningBuild: live.value?.build_id ?? selected.value?.build_id,
    desiredBuild: live.value?.desired_build_id ?? appAssignment.value?.desired_build_id,
    stagedBuild: live.value?.staged_build_id,
    lastError: live.value?.last_update_error,
  }),
);

const appButtons = computed(() =>
  appUpdateButtonState({
    hasInstallation: Boolean(selectedId.value),
    installMode: live.value?.install_mode,
    devicePlatform: live.value?.platform ?? selected.value?.platform,
    deviceArch: selectedArch.value,
    releasePlatform: selectedAppRelease.value?.platform,
    releaseArch: selectedAppRelease.value?.arch,
    updateState: live.value?.update_state,
    stagedBuild: live.value?.staged_build_id,
    targetBuild: live.value?.desired_build_id ?? appAssignment.value?.desired_build_id,
    busy: busy.value,
  }),
);

function venueBool(v: boolean | null | undefined): string {
  if (v === true) return "OK";
  if (v === false) return "WARN";
  return "—";
}

function shortHash(v: string | null | undefined): string {
  if (!v) return "—";
  return v.length > 12 ? `${v.slice(0, 12)}…` : v;
}

const pairId = ref("");
const pairCode = ref("");
const pairName = ref("ORNG Device");
const pairPlatform = ref("raspberry");
const assignSceneId = ref("");
const assignVersion = ref<number | "latest">("latest");
const trackLatest = ref(true);

const appChannel = ref("stable");
const appTrackLatest = ref(true);
const appAutoUpdate = ref(false);
const selectedAppReleaseKey = ref("");
const packageFile = ref<File | null>(null);
const manifestFile = ref<File | null>(null);
const publishNotes = ref("");

const allDevicesSelected = computed(
  () => devices.value.length > 0 && selectedIds.value.length === devices.value.length,
);

const selectedAppRelease = computed(() => {
  const key = selectedAppReleaseKey.value;
  if (!key) return null;
  return appReleases.value.find((r) => releaseKey(r) === key) ?? null;
});

function releaseKey(r: AdminAppRelease): string {
  return `${r.platform}|${r.arch}|${r.channel}|${r.version}|${r.build_id}`;
}

function formatRelease(r: AdminAppRelease): string {
  return `${r.version} / ${r.build_id} · ${r.platform}/${r.arch} · ${r.channel}`;
}

function deviceModeLabel(d: AdminDevice): string {
  if (selectedId.value === d.id && live.value?.install_mode) {
    return live.value.install_mode;
  }
  return "—";
}

function deviceUpdateState(d: AdminDevice): string {
  if (selectedId.value === d.id && live.value?.update_state) {
    return live.value.update_state;
  }
  return "—";
}

function deviceProgress(d: AdminDevice): string {
  if (selectedId.value !== d.id) return "—";
  const p = live.value?.update_progress;
  if (p == null || Number.isNaN(p)) return "—";
  return `${Math.round(p * 100)}%`;
}

function deviceLastUpdateError(d: AdminDevice): string {
  if (selectedId.value === d.id && live.value?.last_update_error) {
    return live.value.last_update_error;
  }
  return d.last_error ?? "—";
}

onMounted(async () => {
  try {
    await checkSession();
    if (!authed.value) return;
    await refreshDevices();
    await refreshScenes();
    await refreshAppReleases();
    await refreshAppEvents();
    await refreshAudit();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
});

watch(
  () => selected.value?.current_scene_id,
  (sceneId) => {
    if (sceneId) {
      assignSceneId.value = sceneId;
      void refreshReleases(sceneId);
    }
  },
);

watch(assignSceneId, (id) => {
  if (id) void refreshReleases(id);
});

watch(
  () => live.value?.desired_app_version,
  (v) => {
    if (v && live.value?.desired_build_id) {
      const match = appReleases.value.find(
        (r) => r.version === v && r.build_id === live.value?.desired_build_id,
      );
      if (match) selectedAppReleaseKey.value = releaseKey(match);
    }
  },
);

function onThemeToggle(event: Event) {
  setTheme((event.target as HTMLInputElement).checked ? "light" : "dark");
}

async function onApprove() {
  await approvePair({
    pairing_id: pairId.value.trim(),
    code: pairCode.value.trim().toUpperCase(),
    name: pairName.value.trim() || "ORNG Device",
    platform: pairPlatform.value,
  });
  pairId.value = "";
  pairCode.value = "";
}

async function onAssign() {
  const version = assignVersion.value === "latest" ? null : Number(assignVersion.value);
  await assignScene(assignSceneId.value, version, trackLatest.value);
}

async function onPublishApp() {
  if (!packageFile.value || !manifestFile.value) {
    error.value = "Потрібні package file і manifest JSON";
    return;
  }
  const manifestJson = await manifestFile.value.text();
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(manifestJson) as Record<string, unknown>;
  } catch {
    error.value = "manifest must be valid JSON";
    return;
  }
  await publishAppRelease({
    packageFile: packageFile.value,
    manifestJson,
    version: typeof parsed.version === "string" ? parsed.version : undefined,
    build_id: typeof parsed.build_id === "string" ? parsed.build_id : undefined,
    platform: typeof parsed.platform === "string" ? parsed.platform : undefined,
    arch: typeof parsed.arch === "string" ? parsed.arch : undefined,
    channel:
      typeof parsed.channel === "string" ? parsed.channel : appChannel.value || "stable",
    signature_b64:
      typeof parsed.signature_b64 === "string"
        ? parsed.signature_b64
        : typeof parsed.signature === "string"
          ? parsed.signature
          : undefined,
    key_id: typeof parsed.key_id === "string" ? parsed.key_id : undefined,
    notes: publishNotes.value.trim() || null,
  });
  packageFile.value = null;
  manifestFile.value = null;
  publishNotes.value = "";
}

async function onAssignApp() {
  const rel = selectedAppRelease.value;
  await assignAppRelease({
    desired_version: appTrackLatest.value ? null : (rel?.version ?? null),
    desired_build_id: appTrackLatest.value ? null : (rel?.build_id ?? null),
    channel: appChannel.value || rel?.channel || "stable",
    track_latest: appTrackLatest.value,
    auto_update: appAutoUpdate.value,
  });
}

async function onBulkRollout() {
  const rel = selectedAppRelease.value;
  if (!rel) {
    error.value = "Оберіть app release для rollout";
    return;
  }
  if (
    !window.confirm(
      `Assign ${rel.version}/${rel.build_id} to ${selectedIds.value.length} device(s)?`,
    )
  ) {
    return;
  }
  await bulkAppRollout({
    desired_version: rel.version,
    desired_build_id: rel.build_id,
    channel: appChannel.value || rel.channel,
    track_latest: appTrackLatest.value,
    auto_update: appAutoUpdate.value,
  });
}

async function onRollbackAssign() {
  const rel = selectedAppRelease.value;
  if (!rel) {
    error.value = "Оберіть release для rollback assign";
    return;
  }
  await rollbackAppAssignment({
    desired_version: rel.version,
    desired_build_id: rel.build_id,
    channel: appChannel.value || rel.channel,
  });
}

function onPackageChange(ev: Event) {
  const input = ev.target as HTMLInputElement;
  packageFile.value = input.files?.[0] ?? null;
}

function onManifestChange(ev: Event) {
  const input = ev.target as HTMLInputElement;
  manifestFile.value = input.files?.[0] ?? null;
}
</script>

<template>
  <div class="admin-shell">
    <header class="admin-shell__top">
      <div class="admin-shell__brand">
        <img
          src="/logo.svg"
          alt=""
          width="28"
          height="28"
        >
        <div>
          <strong>ORNG</strong>
          <span class="muted"> Cloud Admin</span>
          <span class="muted"> · UI {{ uiVersion }}</span>
        </div>
      </div>
      <div class="admin-shell__top-actions">
        <span
          v-if="authed && authUsername"
          class="muted"
        >{{ authUsername }}</span>
        <button
          v-if="authed"
          type="button"
          class="btn btn--ghost"
          :disabled="busy"
          @click="logout"
        >
          Вийти
        </button>
        <label class="theme-toggle">
          <input
            type="checkbox"
            :checked="isLight"
            @change="onThemeToggle"
          >
          Світла
        </label>
      </div>
    </header>

    <section
      v-if="authChecked && !authed"
      class="login-panel"
    >
      <h1>Вхід</h1>
      <p class="muted">Тільки для операторів. Пристрої підключаються окремо через pairing.</p>
      <form
        class="login-form"
        @submit.prevent="onLogin"
      >
        <label>
          Логін
          <input
            v-model="loginUser"
            type="text"
            name="username"
            autocomplete="username"
            required
          >
        </label>
        <label>
          Пароль
          <input
            v-model="loginPass"
            type="password"
            name="password"
            autocomplete="current-password"
            required
          >
        </label>
        <button
          type="submit"
          class="btn"
          :disabled="loginBusy || busy"
        >
          Увійти
        </button>
      </form>
      <p
        v-if="error"
        class="error"
      >
        {{ error }}
      </p>
    </section>

    <template v-else-if="authed">
    <div class="admin-grid">
      <aside class="admin-col">
        <section class="card">
          <header class="card__head">
            <h2>Пристрої</h2>
            <label class="check">
              <input
                type="checkbox"
                :checked="allDevicesSelected"
                @change="selectAllDevices(($event.target as HTMLInputElement).checked)"
              >
              all
            </label>
          </header>
          <div class="cloud-panel__row">
            <label>
              Admin base
              <input
                v-model="adminBase"
                type="url"
              >
            </label>
            <label>
              Dev email
              <input
                v-model="accessEmail"
                type="email"
              >
            </label>
            <button
              type="button"
              :disabled="busy"
              @click="refreshDevices()"
            >
              Оновити
            </button>
          </div>
          <ul class="device-list">
            <li
              v-for="d in devices"
              :key="d.id"
            >
              <div class="device-list__row">
                <input
                  type="checkbox"
                  class="device-list__check"
                  :checked="selectedIds.includes(d.id)"
                  :aria-label="`Select ${d.name}`"
                  @change="toggleSelectedId(d.id, ($event.target as HTMLInputElement).checked)"
                >
                <button
                  type="button"
                  class="device-list__btn"
                  :class="{ active: d.id === selectedId }"
                  @click="selectDevice(d.id)"
                >
                  <span class="device-list__name">{{ d.name }}</span>
                  <span
                    class="dot"
                    :class="d.online ? 'ok' : 'off'"
                    :title="d.online ? 'DeviceDO online' : 'офлайн'"
                  />
                  <span class="muted">{{ d.platform }}{{ deviceArch(d) ? `/${deviceArch(d)}` : "" }}</span>
                  <span class="muted">
                    app {{ d.app_version ?? "—" }} / {{ d.build_id ?? "—" }}
                  </span>
                  <span class="muted">mode {{ deviceModeLabel(d) }}</span>
                  <span class="muted">upd {{ deviceUpdateState(d) }} · {{ deviceProgress(d) }}</span>
                  <span
                    v-if="deviceLastUpdateError(d) !== '—'"
                    class="err"
                  >{{ deviceLastUpdateError(d) }}</span>
                  <span
                    v-if="d.revoked"
                    class="err"
                  >revoked</span>
                  <span
                    v-if="d.rotation_pending"
                    class="warn"
                  >rotate…</span>
                </button>
              </div>
            </li>
          </ul>
          <p
            v-if="!devices.length"
            class="muted"
          >
            Немає пристроїв.
          </p>
          <p
            v-else
            class="muted"
          >
            Selected for rollout: {{ selectedIds.length }}
          </p>
        </section>

        <section class="card">
          <header class="card__head">
            <h2>Парування</h2>
            <p class="card__sub">
              Код з пристрою · токен ніколи не показується тут
            </p>
          </header>
          <div class="cloud-panel__row">
            <input
              v-model="pairId"
              placeholder="pairing_id"
            >
            <input
              v-model="pairCode"
              placeholder="КОД"
            >
            <input
              v-model="pairName"
              placeholder="Назва"
            >
            <select v-model="pairPlatform">
              <option value="raspberry">
                raspberry
              </option>
              <option value="desktop_windows">
                desktop_windows
              </option>
              <option value="desktop_macos">
                desktop_macos
              </option>
              <option value="development">
                development
              </option>
            </select>
            <button
              type="button"
              :disabled="busy || !pairId || !pairCode"
              @click="onApprove"
            >
              Схвалити
            </button>
          </div>
        </section>
      </aside>

      <main class="admin-col admin-col--main">
        <template v-if="selected">
          <section class="card">
            <header class="card__head">
              <h2>{{ selected.name }}</h2>
              <p class="card__sub">
                <span :class="live?.online || selected.online ? 'ok' : 'off'">
                  {{ live?.online || selected.online ? "онлайн" : "офлайн" }}
                </span>
                · {{ selected.id }}
                · last_seen {{ selected.last_seen_at ?? "—" }}
              </p>
            </header>
            <div class="cloud-panel__row">
              <button
                type="button"
                :disabled="busy"
                @click="syncSelected()"
              >
                Sync assigned release
              </button>
              <button
                type="button"
                :disabled="busy"
                @click="activateSelectedScene()"
              >
                Activate scene
              </button>
              <button
                type="button"
                :disabled="busy"
                @click="rotateSelected()"
              >
                Safe rotate
              </button>
              <button
                type="button"
                class="danger-btn"
                :disabled="busy"
                @click="revokeSelected()"
              >
                Revoke
              </button>
            </div>

            <div class="cloud-panel__row">
              <label>
                Сцена
                <select v-model="assignSceneId">
                  <option
                    v-for="s in scenes"
                    :key="s.scene_id"
                    :value="s.scene_id"
                  >
                    {{ s.name }} ({{ s.scene_id }})
                  </option>
                </select>
              </label>
              <label>
                Версія
                <select v-model="assignVersion">
                  <option value="latest">
                    latest (track)
                  </option>
                  <option
                    v-for="r in releases"
                    :key="r.version"
                    :value="r.version"
                  >
                    v{{ r.version }}
                  </option>
                </select>
              </label>
              <label class="check">
                <input
                  v-model="trackLatest"
                  type="checkbox"
                >
                track_latest
              </label>
              <button
                type="button"
                :disabled="busy || !assignSceneId"
                @click="onAssign"
              >
                Assign
              </button>
            </div>
            <p class="muted">
              Поточна: {{ selected.current_scene_id ?? "—" }}
              v{{ live?.current_release_version ?? selected.current_release_version ?? "—" }}
              · armed {{ live?.armed ?? selected.armed }}
              · blackout {{ live?.blackout ?? selected.blackout }}
              · {{ live?.transport ?? selected.transport ?? "—" }}
            </p>
            <p class="muted">
              Scene sync:
              <strong>{{ live?.sync_status ?? "—" }}</strong>
              · assigned v{{ live?.desired_scene_version ?? "—" }}
              · applied v{{ live?.current_release_version ?? selected.current_release_version ?? "—" }}
              · synced {{ shortHash(live?.last_synced_hash ?? selected.last_synced_hash) }}
              · content {{ shortHash(live?.current_content_hash ?? selected.current_content_hash) }}
              <template
                v-if="
                  (live?.current_content_hash || selected.current_content_hash) &&
                    (live?.last_synced_hash || selected.last_synced_hash) &&
                    (live?.current_content_hash ?? selected.current_content_hash) !==
                      (live?.last_synced_hash ?? selected.last_synced_hash)
                "
              >
                · <span class="err">diverged</span>
              </template>
              <template
                v-else-if="live?.desired_content_hash && live?.current_content_hash && live.desired_content_hash !== live.current_content_hash"
              >
                · <span class="err">assigned≠applied</span>
              </template>
              <template v-else-if="live?.sync_status === 'synced'">
                · match
              </template>
            </p>
            <p class="muted">
              App {{ live?.app_version ?? selected.app_version ?? "—" }}
              / {{ live?.build_id ?? selected.build_id ?? "—" }}
              · {{ live?.install_mode ?? "—" }}
              · {{ live?.platform ?? selected.platform }}{{ selectedArch ? `/${selectedArch}` : "" }}
              · upd {{ live?.update_state ?? "—" }}
              <template v-if="live?.update_progress != null">
                · {{ Math.round((live.update_progress ?? 0) * 100) }}%
              </template>
              <span
                v-if="live?.last_update_error"
                class="err"
              > · {{ live.last_update_error }}</span>
            </p>
            <p class="muted">
              HW acceptance:
              <strong>{{ live?.hardware_acceptance ?? "pending" }}</strong>
              · Wi‑Fi {{ venueBool(live?.wifi_connected) }}
              · cloud {{ venueBool(live?.cloud_connected) }}
              · eth {{ venueBool(live?.ethernet_link) }}
              · Art‑Net route {{ venueBool(live?.artnet_route_valid) }}
              · kb {{ live?.keyboard_connected ? `yes×${live?.keyboard_count ?? 1}` : venueBool(live?.keyboard_connected) }}
              <template v-if="live?.cpu_temp_c != null">
                · {{ live.cpu_temp_c.toFixed(0) }}°C
              </template>
            </p>
          </section>

          <CloudRemotePad
            :transport="transport"
            :live="live"
            :pad-preset-ids="padPresetIds"
            :available-ids="availableIds"
          />

          <section class="card">
            <header class="card__head">
              <h2>Команди</h2>
              <p class="card__sub">
                pending / applied / failed
              </p>
            </header>
            <ul class="log-list">
              <li
                v-for="c in commands"
                :key="c.command_id"
              >
                <span
                  class="status"
                  :data-status="c.status"
                >{{ c.status }}</span>
                {{ c.type }}
                <span class="muted">{{ c.created_at }}</span>
                <span
                  v-if="c.error_message"
                  class="err"
                >{{ c.error_message }}</span>
              </li>
            </ul>
            <p
              v-if="!commands.length"
              class="muted"
            >
              Порожньо.
            </p>
          </section>
        </template>
        <p
          v-else
          class="muted"
        >
          Оберіть пристрій для пульта й керування сценою.
        </p>
      </main>

      <aside class="admin-col">
        <section class="card">
          <header class="card__head">
            <h2>Audit</h2>
            <button
              type="button"
              :disabled="busy"
              @click="refreshAudit(selectedId ?? undefined)"
            >
              Оновити
            </button>
          </header>
          <ul class="log-list">
            <li
              v-for="e in audit"
              :key="e.id"
            >
              <strong>{{ e.action }}</strong>
              <span class="muted">{{ e.actor }}</span>
              <span
                v-if="e.target"
                class="muted"
              >→ {{ e.target }}</span>
              <div class="muted">
                {{ e.created_at }}
              </div>
            </li>
          </ul>
        </section>
      </aside>
    </div>

    <!-- Phase 3: Application releases (separate from scenes) -->
    <section class="card app-releases">
      <header class="card__head">
        <h2>Application releases</h2>
        <p class="card__sub">
          Signed packages · login session required · device command channel for stage/apply
        </p>
        <button
          type="button"
          :disabled="busy"
          @click="refreshAppReleases(); refreshAppEvents(selectedId ?? undefined)"
        >
          Оновити
        </button>
      </header>

      <p
        v-if="isDeveloperMode && selected"
        class="warn-banner"
        role="status"
      >
        {{ developerUpdateReason }}
      </p>

      <div class="app-releases__grid">
        <div>
          <h3>Publish</h3>
          <div class="cloud-panel__row">
            <label>
              Package
              <input
                type="file"
                accept=".tar.gz,.tgz,application/gzip"
                @change="onPackageChange"
              >
            </label>
            <label>
              Manifest JSON
              <input
                type="file"
                accept=".json,application/json"
                @change="onManifestChange"
              >
            </label>
            <label>
              Notes
              <input
                v-model="publishNotes"
                type="text"
                placeholder="optional"
              >
            </label>
            <button
              type="button"
              :disabled="busy || !packageFile || !manifestFile"
              @click="onPublishApp"
            >
              Upload / publish
            </button>
          </div>
          <p class="muted">
            Manifest must include signature fields; Worker verifies before store.
          </p>
        </div>

        <div>
          <h3>Releases</h3>
          <ul class="log-list">
            <li
              v-for="r in appReleases"
              :key="releaseKey(r)"
            >
              <label class="check release-pick">
                <input
                  v-model="selectedAppReleaseKey"
                  type="radio"
                  name="app-release"
                  :value="releaseKey(r)"
                >
                <span>{{ formatRelease(r) }}</span>
              </label>
              <span class="muted">{{ r.published_at }}</span>
              <span
                v-if="r.notes"
                class="muted"
              >· {{ r.notes }}</span>
            </li>
          </ul>
          <p
            v-if="!appReleases.length"
            class="muted"
          >
            Немає app releases.
          </p>
        </div>
      </div>

      <div class="cloud-panel__row app-releases__actions">
        <label>
          Channel
          <input
            v-model="appChannel"
            type="text"
          >
        </label>
        <label class="check">
          <input
            v-model="appTrackLatest"
            type="checkbox"
            :disabled="isDeveloperMode"
          >
          track_latest
        </label>
        <label class="check">
          <input
            v-model="appAutoUpdate"
            type="checkbox"
            :disabled="isDeveloperMode"
            title="Лише stage. Застосування лишається окремою командою."
          >
          auto stage
        </label>
        <button
          type="button"
          :disabled="busy || !selectedId || isDeveloperMode"
          :title="developerUpdateReason ?? undefined"
          @click="onAssignApp"
        >
          Assign to selected
        </button>
        <button
          type="button"
          :disabled="busy || !selectedIds.length || !selectedAppRelease || isDeveloperMode"
          :title="developerUpdateReason ?? undefined"
          @click="onBulkRollout"
        >
          Bulk rollout ({{ selectedIds.length }})
        </button>
        <button
          type="button"
          :disabled="busy || !selectedId || !selectedAppRelease || isDeveloperMode"
          :title="developerUpdateReason ?? undefined"
          @click="onRollbackAssign"
        >
          Rollback assign
        </button>
      </div>

      <p class="muted">
        Доступно <strong>{{ appBadges.available ? "так" : "ні" }}</strong>
        · Скачано <strong>{{ appBadges.downloaded ? "так" : "ні" }}</strong>
        · Очікує <strong>{{ appBadges.waiting ? "так" : "ні" }}</strong>
        · Застосовано <strong>{{ appBadges.applied ? "так" : "ні" }}</strong>
        · Помилка <strong>{{ appBadges.error ? "так" : "ні" }}</strong>
      </p>

      <div class="cloud-panel__row app-releases__actions">
        <button
          type="button"
          :disabled="!appButtons.check"
          :title="appButtons.reason ?? developerUpdateReason ?? undefined"
          @click="sendAppCommand('check_app_update', {}, selectedId)"
        >
          Check
        </button>
        <button
          type="button"
          :disabled="!appButtons.stage"
          :title="appButtons.reason ?? developerUpdateReason ?? undefined"
          @click="
            sendAppCommand(
              'stage_app_update',
              {
                version: selectedAppRelease?.version,
                build_id: selectedAppRelease?.build_id,
              },
              selectedId,
            )
          "
        >
          Stage
        </button>
        <button
          type="button"
          class="warn-btn"
          :disabled="!appButtons.apply"
          :title="appButtons.reason ?? developerUpdateReason ?? undefined"
          @click="
            sendAppCommand(
              'apply_app_update',
              {
                version: live?.staged_app_version ?? appAssignment?.desired_version,
                build_id: live?.staged_build_id ?? appAssignment?.desired_build_id,
              },
              selectedId,
            )
          "
        >
          Apply now
        </button>
        <button
          type="button"
          :disabled="!appButtons.cancel"
          :title="appButtons.reason ?? undefined"
          @click="sendAppCommand('cancel_staged_update', {}, selectedId)"
        >
          Cancel staged
        </button>
        <button
          type="button"
          :disabled="!appButtons.rollback"
          :title="appButtons.reason ?? developerUpdateReason ?? undefined"
          @click="sendAppCommand('rollback_app_update', {}, selectedId)"
        >
          Rollback (device)
        </button>
      </div>

      <p
        v-if="appAssignment"
        class="muted"
      >
        Last assignment: {{ appAssignment.desired_version }} /
        {{ appAssignment.desired_build_id }}
        · track {{ Boolean(appAssignment.track_latest) }}
        · auto {{ Boolean(appAssignment.auto_update) }}
      </p>

      <h3>Update events</h3>
      <ul class="log-list">
        <li
          v-for="ev in appEvents"
          :key="ev.id"
        >
          <strong>{{ ev.event_type }}</strong>
          <span class="muted">{{ ev.installation_id }}</span>
          <span class="muted">{{ ev.created_at }}</span>
        </li>
      </ul>
      <p
        v-if="!appEvents.length"
        class="muted"
      >
        Немає подій оновлення.
      </p>
    </section>

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
    </template>
  </div>
</template>

<style scoped>
.admin-shell {
  max-width: 1200px;
  margin: 0 auto;
  padding: 1rem 1.25rem 3rem;
  display: grid;
  gap: 1rem;
}
.admin-shell__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem 1rem;
  flex-wrap: wrap;
}
.admin-shell__top-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.login-panel {
  max-width: 22rem;
  margin: 2rem auto;
  display: grid;
  gap: 1rem;
}
.login-panel h1 {
  margin: 0;
  font-size: 1.5rem;
}
.login-form {
  display: grid;
  gap: 0.75rem;
}
.login-form label {
  display: grid;
  gap: 0.35rem;
  font-size: 0.9rem;
}
.login-form input {
  padding: 0.55rem 0.65rem;
  border: 1px solid var(--line, #333);
  border-radius: 8px;
  background: transparent;
  color: inherit;
}
.login-panel .error {
  color: #e85d4c;
  margin: 0;
}
.admin-shell__brand {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  font-size: 1.15rem;
  min-width: min(100%, 16rem);
}
.admin-shell__hint {
  opacity: 0.75;
  margin: 0;
  font-size: 0.9rem;
}
.admin-grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: minmax(16rem, 22rem) minmax(0, 1fr) minmax(14rem, 18rem);
}
@media (max-width: 960px) {
  .admin-grid {
    grid-template-columns: 1fr;
  }
}
.admin-col {
  display: grid;
  gap: 1rem;
  align-content: start;
}
.device-list {
  list-style: none;
  margin: 0.75rem 0 0;
  padding: 0;
  display: grid;
  gap: 0.35rem;
}
.device-list__row {
  display: flex;
  gap: 0.35rem;
  align-items: flex-start;
}
.device-list__check {
  margin-top: 0.85rem;
  flex-shrink: 0;
}
.device-list__btn {
  width: 100%;
  text-align: left;
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  align-items: center;
  padding: 0.5rem 0.65rem;
  border: 1px solid var(--line, #333);
  background: transparent;
  color: inherit;
  border-radius: 8px;
  cursor: pointer;
}
.device-list__btn.active {
  border-color: var(--orng, #ff6a00);
}
.device-list__name {
  font-weight: 600;
}
.dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  display: inline-block;
  background: #666;
}
.dot.ok {
  background: #2d8a4e;
}
.ok {
  color: #2d8a4e;
}
.off {
  opacity: 0.65;
}
.warn {
  color: #c90;
}
.warn-banner {
  color: #c90;
  margin: 0.5rem 0;
  font-size: 0.9rem;
}
.err,
.cloud-panel__err {
  color: #c44;
}
.cloud-panel__msg {
  color: #2d8a4e;
}
.muted {
  opacity: 0.7;
  font-size: 0.85rem;
}
.cloud-panel__row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: end;
}
.cloud-panel__row label {
  display: grid;
  gap: 0.2rem;
  font-size: 0.8rem;
}
.cloud-panel__row input,
.cloud-panel__row select {
  padding: 0.35rem 0.5rem;
}
.log-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.45rem;
  font-size: 0.85rem;
  max-height: 28rem;
  overflow: auto;
}
.status {
  font-size: 0.7rem;
  text-transform: uppercase;
  padding: 0.1rem 0.35rem;
  border-radius: 4px;
  background: color-mix(in srgb, CanvasText 12%, transparent);
  margin-right: 0.35rem;
}
.status[data-status="applied"],
.status[data-status="accepted"] {
  background: color-mix(in srgb, #2d8a4e 35%, transparent);
}
.status[data-status="failed"] {
  background: color-mix(in srgb, #b33 40%, transparent);
}
.status[data-status="pending"] {
  background: color-mix(in srgb, #c90 40%, transparent);
}
.danger-btn {
  color: #c44;
}
.warn-btn {
  color: #c90;
  font-weight: 600;
}
.check {
  display: flex !important;
  flex-direction: row !important;
  align-items: center;
  gap: 0.35rem;
}
.app-releases {
  margin-top: 0.5rem;
}
.app-releases h3 {
  margin: 0.75rem 0 0.4rem;
  font-size: 0.95rem;
}
.app-releases__grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: 1fr 1fr;
}
@media (max-width: 720px) {
  .app-releases__grid {
    grid-template-columns: 1fr;
  }
}
.app-releases__actions {
  margin-top: 0.75rem;
}
.release-pick {
  width: 100%;
}
</style>
