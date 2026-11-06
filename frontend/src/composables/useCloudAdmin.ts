import { computed, onUnmounted, ref, shallowRef } from "vue";
import {
  adminAuthChecked,
  adminAuthed,
  adminSessionEpoch,
  adminUsername,
  endAdminSession,
  markAdminAuthed,
} from "../cloud/adminSession";
import {
  AdminAuthError,
  adminAssignAppRelease,
  adminAssignScene,
  adminApprovePair,
  adminBulkAppRollout,
  adminDeviceCommand,
  adminGetControlSurface,
  adminGetDevice,
  adminListAppEvents,
  adminListAppReleases,
  adminListAudit,
  adminListCommands,
  adminListDevices,
  adminListReleases,
  adminListScenes,
  adminLogin,
  adminLogout,
  adminMe,
  adminPublishAppRelease,
  adminRevokeDevice,
  adminRollbackAppAssignment,
  adminRotateDevice,
  type AdminAppAssignment,
  type AdminAppRelease,
  type AdminAppUpdateEvent,
  type AdminControlSurface,
  type AdminDevice,
  type AdminRelease,
  type AdminScene,
  type AuditEvent,
  type CommandLogRow,
} from "../api/adminCloud";
import { applyConfirmText, selectionUnchanged } from "../runtime/appUpdateActions";
import { defaultAccessEmail, defaultAdminBase } from "../cloud/mode";
import { createCloudDeviceTransport } from "../cloud/transports/cloudDevice";
import type { CloudControlTransport, CloudDeviceLiveState } from "../cloud/transport";

function deviceArch(device: AdminDevice | null | undefined): string | null {
  if (!device) return null;
  const caps = device.capabilities;
  if (caps && typeof caps === "object" && "arch" in caps) {
    const arch = (caps as { arch?: unknown }).arch;
    if (typeof arch === "string" && arch) return arch;
  }
  return null;
}

export function useCloudAdmin() {
  const adminBase = ref(defaultAdminBase());
  const accessEmail = ref(defaultAccessEmail());
  const authed = adminAuthed;
  const authUsername = adminUsername;
  const authChecked = adminAuthChecked;
  const devices = ref<AdminDevice[]>([]);
  const selectedId = ref<string | null>(null);
  const selectedIds = ref<string[]>([]);
  const live = shallowRef<CloudDeviceLiveState | null>(null);
  const scenes = ref<AdminScene[]>([]);
  const releases = ref<AdminRelease[]>([]);
  const appReleases = ref<AdminAppRelease[]>([]);
  const appAssignment = ref<AdminAppAssignment | null>(null);
  const appEvents = ref<AdminAppUpdateEvent[]>([]);
  const audit = ref<AuditEvent[]>([]);
  const commands = ref<CommandLogRow[]>([]);
  const busy = ref(false);
  const error = ref<string | null>(null);
  const message = ref<string | null>(null);
  const transport = shallowRef<CloudControlTransport | null>(null);
  const controlSurface = shallowRef<AdminControlSurface | null>(null);

  function markUnauthed() {
    endAdminSession("expired");
    transport.value?.dispose();
    transport.value = null;
    live.value = null;
  }

  function onSignedOut() {
    transport.value?.dispose();
    transport.value = null;
    live.value = null;
  }

  window.addEventListener("orng-admin-signed-out", onSignedOut);

  function handleAuthError(e: unknown): boolean {
    if (e instanceof AdminAuthError || (e instanceof Error && /Login required/i.test(e.message))) {
      markUnauthed();
      return true;
    }
    return false;
  }

  const selected = computed(
    () => devices.value.find((d) => d.id === selectedId.value) ?? null,
  );

  const selectedInstallMode = computed(() => live.value?.install_mode ?? null);

  const isDeveloperMode = computed(() => {
    const mode = (selectedInstallMode.value ?? "").toLowerCase();
    return mode === "developer" || mode === "dev";
  });

  const developerUpdateReason = computed(() =>
    isDeveloperMode.value
      ? "Developer checkout: remote self-update disabled (developer_checkout)"
      : null,
  );

  const selectedArch = computed(
    () => live.value?.arch ?? deviceArch(selected.value) ?? null,
  );

  const clientOpts = () => ({
    adminBase: adminBase.value,
    accessEmail: accessEmail.value,
  });

  function persistSettings() {
    try {
      localStorage.setItem("orng-cloud-admin", adminBase.value);
      localStorage.setItem("orng-cloud-access-email", accessEmail.value);
    } catch {
      /* ignore */
    }
  }

  async function checkSession() {
    error.value = null;
    try {
      const me = await adminMe(clientOpts());
      if (me?.ok) markAdminAuthed(me.username);
    } catch (err) {
      if (!(err instanceof AdminAuthError)) {
        error.value = err instanceof Error ? err.message : "Сесію не перевірено";
      }
    } finally {
      authChecked.value = true;
    }
  }

  async function login(username: string, password: string) {
    busy.value = true;
    error.value = null;
    try {
      const res = await adminLogin(username, password, clientOpts());
      markAdminAuthed(res.username);
      message.value = null;
      await refreshDevices();
      await refreshScenes();
      await refreshAppReleases();
      await refreshAppEvents();
      await refreshAudit();
    } catch (e) {
      markUnauthed();
      error.value = e instanceof Error ? e.message : String(e);
      throw e;
    } finally {
      busy.value = false;
    }
  }

  async function logout() {
    busy.value = true;
    try {
      await adminLogout(clientOpts());
    } catch {
      error.value = "Вихід на сервері не підтверджено";
    } finally {
      endAdminSession("logout");
      devices.value = [];
      busy.value = false;
    }
  }

  async function refreshDevices() {
    error.value = null;
    persistSettings();
    const epoch = adminSessionEpoch.value;
    const wasAuthed = authed.value;
    try {
      const res = await adminListDevices(clientOpts());
      if (!wasAuthed || epoch !== adminSessionEpoch.value || !authed.value) return;
      devices.value = res.devices ?? [];
      const valid = new Set(devices.value.map((d) => d.id));
      selectedIds.value = selectedIds.value.filter((id) => valid.has(id));
    } catch (e) {
      if (!wasAuthed || epoch !== adminSessionEpoch.value) return;
      if (handleAuthError(e)) return;
      throw e;
    }
  }

  async function refreshScenes() {
    const res = await adminListScenes(clientOpts());
    scenes.value = res.scenes ?? [];
  }

  async function refreshReleases(sceneId: string) {
    if (!sceneId) {
      releases.value = [];
      return;
    }
    const res = await adminListReleases(sceneId, clientOpts());
    releases.value = res.releases ?? [];
  }

  async function refreshAppReleases(query?: {
    platform?: string;
    arch?: string;
    channel?: string;
  }) {
    const res = await adminListAppReleases(query, clientOpts());
    appReleases.value = res.releases ?? [];
  }

  async function refreshAppEvents(installationId?: string) {
    const res = await adminListAppEvents(
      { limit: 40, installation_id: installationId || undefined },
      clientOpts(),
    );
    appEvents.value = res.events ?? [];
  }

  async function refreshAudit(target?: string) {
    const res = await adminListAudit(
      { limit: 60, target: target || undefined },
      clientOpts(),
    );
    audit.value = res.events ?? [];
  }

  async function refreshCommands(installationId: string) {
    const res = await adminListCommands(installationId, { limit: 40 }, clientOpts());
    commands.value = res.commands ?? [];
  }

  function disposeTransport() {
    transport.value?.dispose();
    transport.value = null;
    live.value = null;
  }

  function toggleSelectedId(id: string, checked: boolean) {
    if (checked) {
      if (!selectedIds.value.includes(id)) selectedIds.value = [...selectedIds.value, id];
      return;
    }
    selectedIds.value = selectedIds.value.filter((x) => x !== id);
  }

  function selectAllDevices(checked: boolean) {
    selectedIds.value = checked ? devices.value.map((d) => d.id) : [];
  }

  async function selectDevice(id: string | null) {
    disposeTransport();
    controlSurface.value = null;
    selectedId.value = id;
    appAssignment.value = null;
    if (!id) return;
    busy.value = true;
    error.value = null;
    try {
      const [detail, surface] = await Promise.all([
        adminGetDevice(id, clientOpts()),
        adminGetControlSurface(id, clientOpts()).catch(() => null),
      ]);
      controlSurface.value = surface;
      const idx = devices.value.findIndex((d) => d.id === id);
      if (idx >= 0) devices.value[idx] = detail.device;
      live.value = {
        online: Boolean(detail.device.online),
        armed: Boolean(detail.device.armed),
        blackout: Boolean(detail.device.blackout),
        transport: detail.device.transport ?? null,
        current_scene_id: detail.device.current_scene_id,
        current_release_version: detail.device.current_release_version,
        current_content_hash: detail.device.current_content_hash ?? null,
        last_synced_hash: detail.device.last_synced_hash ?? null,
        last_error: detail.device.last_error ?? null,
        app_version: detail.device.app_version ?? null,
        build_id: detail.device.build_id ?? null,
        platform: detail.device.platform ?? null,
        arch: deviceArch(detail.device),
      };
      const t = createCloudDeviceTransport({
        installationId: id,
        ...clientOpts(),
      });
      t.onLiveState((s) => {
        live.value = { ...(live.value ?? { online: false, armed: false, blackout: true }), ...s };
      });
      t.onCommandAck(() => {
        void refreshCommands(id);
        void refreshAppEvents(id);
      });
      await t.connect();
      transport.value = t;
      await refreshCommands(id);
      await refreshAppEvents(id);
      if (detail.device.current_scene_id) {
        await refreshReleases(detail.device.current_scene_id);
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  async function approvePair(body: {
    pairing_id: string;
    code: string;
    name: string;
    platform: string;
  }) {
    busy.value = true;
    error.value = null;
    try {
      const res = await adminApprovePair(body, clientOpts());
      message.value = `Схвалено: ${res.installation_id} (токен лише на пристрої)`;
      await refreshDevices();
      await refreshAudit();
      return res;
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
      throw e;
    } finally {
      busy.value = false;
    }
  }

  async function revokeSelected() {
    if (!selectedId.value) return;
    if (!window.confirm("Відкликати пристрій? Токен стане недійсним.")) return;
    busy.value = true;
    try {
      await adminRevokeDevice(selectedId.value, clientOpts());
      message.value = "Пристрій відкликано";
      disposeTransport();
      selectedId.value = null;
      await refreshDevices();
      await refreshAudit();
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  async function rotateSelected() {
    if (!selectedId.value) return;
    if (
      !window.confirm(
        "Ініціювати безпечну ротацію? Токен не показується в браузері; пристрій має підготувати ключ і ACK.",
      )
    ) {
      return;
    }
    busy.value = true;
    try {
      const res = await adminRotateDevice(selectedId.value, clientOpts());
      message.value = res.note ?? "Ротація очікує ACK пристрою";
      await refreshDevices();
      await refreshAudit(selectedId.value);
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  async function assignScene(sceneId: string, version: number | null, trackLatest: boolean) {
    if (!selectedId.value) return;
    busy.value = true;
    try {
      await adminAssignScene(
        selectedId.value,
        {
          scene_id: sceneId,
          desired_version: version,
          track_latest: trackLatest,
        },
        clientOpts(),
      );
      message.value = "Сцену призначено · sync надіслано";
      await refreshDevices();
      await selectDevice(selectedId.value);
      await refreshAudit(selectedId.value);
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  async function syncSelected() {
    if (!selectedId.value || !transport.value) return;
    const sceneId = selected.value?.current_scene_id ?? live.value?.current_scene_id;
    if (!sceneId) {
      error.value = "Немає призначеної сцени";
      return;
    }
    const version = selected.value?.current_release_version ?? live.value?.current_release_version ?? null;
    busy.value = true;
    try {
      await transport.value.syncSceneRelease(sceneId, version);
      message.value = "sync_scene_release надіслано";
      await refreshCommands(selectedId.value);
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  async function activateSelectedScene() {
    if (!selectedId.value || !transport.value) return;
    const sceneId = selected.value?.current_scene_id ?? live.value?.current_scene_id;
    if (!sceneId) {
      error.value = "Немає сцени для activate";
      return;
    }
    busy.value = true;
    try {
      await transport.value.activateScene(sceneId);
      message.value = "activate_scene надіслано";
      await refreshCommands(selectedId.value);
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  async function publishAppRelease(input: {
    packageFile: File;
    manifestJson: string;
    version?: string;
    build_id?: string;
    platform?: string;
    arch?: string;
    channel?: string;
    signature_b64?: string;
    key_id?: string;
    notes?: string | null;
  }) {
    busy.value = true;
    error.value = null;
    try {
      const res = await adminPublishAppRelease(input, clientOpts());
      message.value = res.idempotent
        ? `App release already published (${res.release.version}/${res.release.build_id})`
        : `Published app ${res.release.version} · ${res.release.build_id}`;
      await refreshAppReleases();
      await refreshAudit();
      return res;
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
      throw e;
    } finally {
      busy.value = false;
    }
  }

  async function assignAppRelease(body: {
    desired_version?: string | null;
    desired_build_id?: string | null;
    channel?: string;
    track_latest?: boolean;
    auto_update?: boolean;
  }) {
    if (!selectedId.value) return;
    if (isDeveloperMode.value) {
      error.value = developerUpdateReason.value;
      return;
    }
    busy.value = true;
    error.value = null;
    try {
      const res = await adminAssignAppRelease(selectedId.value, body, clientOpts());
      appAssignment.value = res.assignment;
      message.value = `App assigned ${res.assignment.desired_version ?? "—"} / ${res.assignment.desired_build_id ?? "—"}`;
      await refreshDevices();
      await refreshCommands(selectedId.value);
      await refreshAppEvents(selectedId.value);
      await refreshAudit(selectedId.value);
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  async function bulkAppRollout(body: {
    desired_version: string;
    desired_build_id: string;
    channel?: string;
    track_latest?: boolean;
    auto_update?: boolean;
  }) {
    if (!selectedIds.value.length) {
      error.value = "Оберіть пристрої для bulk rollout";
      return;
    }
    busy.value = true;
    error.value = null;
    try {
      const res = await adminBulkAppRollout(
        {
          installation_ids: selectedIds.value,
          ...body,
        },
        clientOpts(),
      );
      const failNote = res.errors?.length ? ` · ${res.errors.length} error(s)` : "";
      message.value = `Bulk rollout: ${res.updated.length} device(s) → ${body.desired_version}/${body.desired_build_id}${failNote}`;
      await refreshDevices();
      await refreshAudit();
      if (selectedId.value) {
        await refreshCommands(selectedId.value);
        await refreshAppEvents(selectedId.value);
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  async function sendAppCommand(
    type:
      | "check_app_update"
      | "stage_app_update"
      | "apply_app_update"
      | "cancel_staged_update"
      | "rollback_app_update"
      | "request_diagnostics"
      | "restart_runtime"
      | "blackout"
      | "disarm",
    payload: Record<string, unknown> = {},
    capturedInstallationId?: string | null,
  ) {
    const installationId = capturedInstallationId ?? selectedId.value;
    if (!installationId) return;
    if (
      type !== "request_diagnostics"
      && type !== "blackout"
      && type !== "disarm"
      && isDeveloperMode.value
    ) {
      error.value = developerUpdateReason.value;
      return;
    }
    if (type === "apply_app_update") {
      const version = String(
        payload.version ?? live.value?.staged_app_version ?? appAssignment.value?.desired_version ?? "",
      );
      const buildId = String(
        payload.build_id ?? live.value?.staged_build_id ?? appAssignment.value?.desired_build_id ?? "",
      );
      if (
        !window.confirm(
          applyConfirmText({ installationId, version, buildId }),
        )
      ) {
        return;
      }
      if (!selectionUnchanged(installationId, selectedId.value)) {
        error.value = "Вибір установки змінився. Команду не надіслано.";
        return;
      }
      payload = { ...payload, force: true, version, build_id: buildId };
    }
    if (type === "restart_runtime") {
      if (
        !window.confirm(
          `Перезапуск runtime ${installationId}. Це не перезавантаження Raspberry OS.`,
        )
      ) {
        return;
      }
      if (!selectionUnchanged(installationId, selectedId.value)) {
        error.value = "Вибір установки змінився. Команду не надіслано.";
        return;
      }
    }
    busy.value = true;
    error.value = null;
    try {
      await adminDeviceCommand(installationId, type, payload, clientOpts());
      message.value = `${type} надіслано`;
      await refreshCommands(installationId);
      await refreshAppEvents(installationId);
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  async function rollbackAppAssignment(body: {
    desired_version: string;
    desired_build_id: string;
    channel?: string;
  }) {
    if (!selectedId.value) return;
    if (isDeveloperMode.value) {
      error.value = developerUpdateReason.value;
      return;
    }
    busy.value = true;
    error.value = null;
    try {
      const res = await adminRollbackAppAssignment(selectedId.value, body, clientOpts());
      appAssignment.value = res.assignment;
      message.value = `App rollback assigned → ${body.desired_version}/${body.desired_build_id}`;
      await refreshCommands(selectedId.value);
      await refreshAppEvents(selectedId.value);
      await refreshAudit(selectedId.value);
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      busy.value = false;
    }
  }

  const padPresetIds = computed(() => {
    const livePads = live.value?.pad_presets ?? [];
    if (livePads.length > 0) return livePads.slice(0, 9);
    const slots = controlSurface.value?.pad_presets ?? [];
    return slots.length > 0 ? slots.slice(0, 9) : [];
  });
  const availableIds = computed(() => new Set(["NONE", ...padPresetIds.value]));

  onUnmounted(() => {
    window.removeEventListener("orng-admin-signed-out", onSignedOut);
    disposeTransport();
  });

  return {
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
    selectedInstallMode,
    deviceArch,
    persistSettings,
    refreshDevices,
    refreshScenes,
    refreshReleases,
    refreshAppReleases,
    refreshAppEvents,
    refreshAudit,
    refreshCommands,
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
  };
}

export type CloudAdminApi = ReturnType<typeof useCloudAdmin>;
