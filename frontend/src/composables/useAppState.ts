import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from "vue";
import {
  createCommandId,
  fetchPresets,
  fetchState,
  postCommand,
} from "../api/client";
import {
  BUTTON_BLACKOUT,
  BUTTON_FACE,
  BUTTON_STROBE,
  postInputAction,
  postInputButton,
} from "../api/input";
import { activateScene as postActivateScene, fetchScenes } from "../api/scenes";
import { bpmChoiceKey, bpmControlsLocked, readBpmChoice } from "../lib/bpmPalettes";
import { getWebSessionSourceId } from "../lib/sessionSourceId";
import type { AppState, PresetInfo, SceneInfo } from "../vite-env";
import { useKeyboardPad } from "./useKeyboardPad";
import type { ToastApi } from "./useToasts";

export type ConnectionStatus = "connecting" | "online" | "offline" | "reconnecting";

function wsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const sourceId = encodeURIComponent(getWebSessionSourceId());
  return `${protocol}//${window.location.host}/api/ws?source_id=${sourceId}`;
}

/** UI text refresh rate; the canvas keeps rendering every frame regardless. */
const REACTIVE_PUBLISH_MS = 110;

function controlSignature(state: AppState): string {
  const e = state.engine;
  const o = state.output;
  return [
    e.preset_id,
    e.episode_index,
    e.blackout,
    e.strobe_held,
    e.white_hit_active,
    e.face_on,
    e.drop_active,
    e.color_hit_active,
    e.sweep_active,
    e.vertical_sweep_active,
    e.strobe_speed,
    e.sweep_speed,
    state.pad_presets?.join(","),
    state.strobe_speed,
    state.sweep_speed,
    o.transport,
    o.preferred_transport,
    o.armed,
    o.udp_active,
    o.network_allowed,
    o.frame_sum,
    o.nonzero_channels,
    o.source_frame_sum,
    o.source_nonzero_channels,
    o.wire_frame_sum,
    o.wire_nonzero_channels,
    o.source_owner,
    o.last_error,
    state.preview_speed,
    state.raw_tester?.active,
    state.raw_tester?.nonzero_channels,
    JSON.stringify(state.raw_tester?.prepared_channels ?? []),
    state.preset_editor_preview?.active,
    state.preset_editor_preview?.episode_id,
    state.preset_editor_preview?.elapsed_s,
    JSON.stringify(state.preset_editor_preview?.blockers ?? []),
    state.live_effects?.active,
    JSON.stringify(state.live_effects?.active_ids ?? []),
    JSON.stringify(state.live_effects?.warnings ?? []),
    state.scene_id,
    state.scene_name,
  ].join("|");
}

export function useAppState(toasts: ToastApi) {
  const state = shallowRef<AppState | null>(null);
  const availablePresets = ref<PresetInfo[]>([]);
  const availableScenes = ref<SceneInfo[]>([]);
  const connection = ref<ConnectionStatus>("connecting");
  const loading = ref(true);
  const lastError = ref<string | null>(null);
  const sceneSwitching = ref(false);
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let disposed = false;
  let intentionalClose = false;

  // Newest frame lives outside Vue reactivity: 30 fps of 512 channels must not
  // re-render the component tree. The canvas reads it from its own rAF loop.
  const live: { state: AppState | null } = { state: null };
  let lastPublishAt = 0;
  let publishTimer: number | null = null;
  let lastSignature = "";

  const engine = computed(() => state.value?.engine ?? null);
  const output = computed(() => state.value?.output ?? null);
  const sceneId = computed(() => state.value?.scene_id ?? "orng-bar");
  const sceneName = computed(() => state.value?.scene_name ?? "ORNG BAR");
  const availableIds = computed(() => {
    const ids = new Set(state.value?.presets ?? []);
    ids.add("NONE");
    return ids;
  });

  function publish() {
    if (publishTimer !== null) {
      window.clearTimeout(publishTimer);
      publishTimer = null;
    }
    lastPublishAt = Date.now();
    state.value = live.state;
    lastError.value = live.state?.output.last_error ?? null;
    if (live.state?.desk_role === "raspberry" && live.state.executor_online === false) {
      connection.value = "offline";
    }
    loading.value = false;
  }

  function applyState(next: AppState) {
    live.state = next;
    const signature = controlSignature(next);
    const changed = signature !== lastSignature;
    lastSignature = signature;
    const elapsed = Date.now() - lastPublishAt;
    if (changed || state.value === null || elapsed >= REACTIVE_PUBLISH_MS) {
      publish();
      return;
    }
    if (publishTimer === null) {
      publishTimer = window.setTimeout(publish, REACTIVE_PUBLISH_MS - elapsed);
    }
  }

  /** Latest simulator view without reactivity — for the canvas render loop. */
  function liveView() {
    return live.state?.simulator ?? null;
  }

  /** Latest raw frame without reactivity — for the throttled inspector. */
  function liveFrame(): number[] {
    return live.state?.frame ?? [];
  }

  async function refreshScenes() {
    try {
      const body = await fetchScenes();
      availableScenes.value = body.scenes;
    } catch {
      // keep last known list when offline
    }
  }

  async function refreshRest() {
    const [nextState, presets] = await Promise.all([fetchState(), fetchPresets()]);
    applyState(nextState);
    availablePresets.value = presets;
    await refreshScenes();
  }

  function scheduleReconnect() {
    if (disposed || reconnectTimer !== null) return;
    connection.value = "reconnecting";
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      connectWs();
    }, 1200);
  }

  function connectWs() {
    if (disposed) return;
    intentionalClose = false;
    connection.value = connection.value === "online" ? "online" : "connecting";
    const ws = new WebSocket(wsUrl());
    socket = ws;

    ws.addEventListener("open", () => {
      if (state.value?.desk_role === "raspberry" && state.value.executor_online === false) {
        connection.value = "offline";
        return;
      }
      connection.value = "online";
    });

    ws.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(String(event.data)) as {
          type: string;
          state?: AppState;
          detail?: string;
        };
        if ((payload.type === "hello" || payload.type === "state") && payload.state) {
          applyState(payload.state);
        } else if (payload.type === "error" && payload.detail) {
          toasts.push(payload.detail, "error");
        }
      } catch (err) {
        toasts.push("Помилка розбору realtime-стану", "error");
        console.error(err);
      }
    });

    ws.addEventListener("close", () => {
      socket = null;
      if (disposed || intentionalClose) return;
      connection.value = "offline";
      scheduleReconnect();
    });

    ws.addEventListener("error", () => {
      // close handler performs reconnect
    });
  }

  async function runCommand(
    path: string,
    payload: Record<string, unknown> = {},
    successMessage?: string,
  ) {
    try {
      const ack = await postCommand(path, {
        ...payload,
        source_id:
          typeof payload.source_id === "string" ? payload.source_id : getWebSessionSourceId(),
        client_command_id: createCommandId(path),
      });
      applyState(ack.state);
      if (successMessage) toasts.push(successMessage, "success");
      return ack;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Помилка команди";
      toasts.push(message, "error");
      throw err;
    }
  }

  async function dispatchPad(
    buttonId: number,
    edge: "press" | "release" | "pulse" = "pulse",
    successMessage?: string,
  ) {
    try {
      const ack = await postInputButton(
        buttonId,
        edge,
        "ui",
        getWebSessionSourceId(),
      );
      if (ack.accepted) {
        applyState(ack.state);
        if (successMessage) toasts.push(successMessage, "success");
      }
      return ack;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Помилка input";
      toasts.push(message, "error");
      throw err;
    }
  }

  async function switchScene(nextSceneId: string) {
    if (!nextSceneId || nextSceneId === sceneId.value) return;
    sceneSwitching.value = true;
    try {
      const ack = await postActivateScene(nextSceneId);
      applyState(ack.state);
      await refreshRest();
      toasts.push(`Сцена: ${ack.state.scene_name ?? nextSceneId}`, "success");
      return ack;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не вдалося змінити сцену";
      toasts.push(message, "error");
      throw err;
    } finally {
      sceneSwitching.value = false;
    }
  }

  async function selectPreset(presetId: string) {
    if (presetId !== "NONE" && !availableIds.value.has(presetId)) {
      toasts.push(`Пресет ${presetId} ще не завантажено на сервері`, "error");
      return;
    }
    await runCommand("select-preset", { preset_id: presetId, reset_clock: true });
  }

  async function seekEpisode(episodeIndex: number) {
    await runCommand("seek-episode", { episode_index: episodeIndex });
  }

  async function nudgeEpisode(delta: number) {
    const count = engine.value?.episode_count ?? 0;
    if (count <= 0) return;
    const index = engine.value?.episode_index ?? 0;
    const next = (index + (delta >= 0 ? 1 : -1) + count) % count;
    await seekEpisode(next);
  }

  /** Serialize STROBE press/release so pointerup cannot race ahead of press. */
  let strobeChain: Promise<void> = Promise.resolve();

  function strobePress() {
    strobeChain = strobeChain
      .then(() => dispatchPad(BUTTON_STROBE, "press"))
      .then(() => undefined)
      .catch(() => undefined);
    return strobeChain;
  }

  function strobeRelease() {
    strobeChain = strobeChain
      .then(() => dispatchPad(BUTTON_STROBE, "release"))
      .then(() => undefined)
      .catch(() => undefined);
    return strobeChain;
  }

  async function setBlackout(enabled: boolean) {
    // Pad contract is toggle; keep explicit set via command for API completeness.
    await runCommand("blackout", { enabled });
  }

  async function toggleBlackout() {
    await dispatchPad(BUTTON_BLACKOUT);
  }

  async function setFace(enabled: boolean, brightness?: number) {
    if (brightness !== undefined) {
      await runCommand("face", { enabled, brightness });
      return;
    }
    const current = state.value?.engine.face_on ?? false;
    if (enabled !== current) {
      await dispatchPad(BUTTON_FACE);
    }
  }

  async function setMasterBrightness(value: number) {
    await runCommand("master-brightness", { value });
  }

  async function setPreviewSpeed(value: number) {
    if (bpmControlsLocked(state.value?.bpm)) return;
    await runCommand("preview-speed", { value });
  }

  async function nextAutoLook(): Promise<void> {
    await runCommand("auto-next-look", {});
  }

  async function setBpmSync(patch: {
    enabled?: boolean;
    mode?: "smooth" | "bar" | "beat";
    scope?: "preset" | "auto";
    palette?: import("../vite-env").BpmPaletteId;
    cast?: "all" | "bars" | "heads";
    character?: "smooth" | "rhythmic" | "impact";
    intensity?: "soft" | "normal" | "full";
    period?: "auto" | 0.5 | 1 | 2 | 4 | 8;
    parRhythm?: boolean;
    accent?: number;
  }): Promise<boolean> {
    const current = state.value?.bpm;
    const saved = readBpmChoice(
      bpmChoiceKey({ locationId: state.value?.location_id, sceneId: state.value?.scene_id }),
    );
    try {
      await runCommand("bpm-sync", {
        enabled: patch.enabled ?? current?.enabled ?? false,
        mode: patch.mode ?? current?.mode ?? saved.mode,
        scope: patch.scope ?? current?.scope ?? saved.scope,
        palette: patch.palette ?? current?.palette ?? saved.palette,
        cast: patch.cast ?? current?.cast ?? saved.cast,
        character: patch.character ?? current?.character ?? saved.character,
        intensity: patch.intensity ?? current?.intensity ?? saved.intensity,
        period: patch.period ?? current?.period ?? saved.period,
        par_rhythm: patch.parRhythm ?? current?.par_rhythm ?? saved.parRhythm,
        accent: patch.accent ?? current?.accent ?? saved.accent,
      });
      return true;
    } catch {
      return false;
    }
  }

  async function colorHit(rgb?: { r: number; g: number; b: number }) {
    await runCommand("color-hit", rgb ? { ...rgb } : {});
  }

  /**
   * Per-mode sweep chains: horizontal and vertical can be held together by the
   * same tab source_id. Release must always match the mode that was pressed —
   * never a shared lastSweepMode (that mis-releases the other axis).
   */
  const sweepChains: Record<"horizontal" | "vertical", Promise<void>> = {
    horizontal: Promise.resolve(),
    vertical: Promise.resolve(),
  };

  async function dispatchSweepAction(
    action: "sweep_press" | "sweep_release" | "vertical_sweep_press" | "vertical_sweep_release",
    edge: "press" | "release",
  ) {
    const ack = await postInputAction({ action, edge });
    if (ack.accepted) {
      applyState(ack.state);
    }
    return ack;
  }

  function sweepPress(mode: "horizontal" | "vertical" = "horizontal") {
    const action = mode === "vertical" ? "vertical_sweep_press" : "sweep_press";
    sweepChains[mode] = sweepChains[mode]
      .then(() => dispatchSweepAction(action, "press"))
      .then(() => undefined)
      .catch(() => undefined);
    return sweepChains[mode];
  }

  function sweepRelease(mode: "horizontal" | "vertical" = "horizontal") {
    const action = mode === "vertical" ? "vertical_sweep_release" : "sweep_release";
    sweepChains[mode] = sweepChains[mode]
      .then(() => dispatchSweepAction(action, "release"))
      .then(() => undefined)
      .catch(() => undefined);
    return sweepChains[mode];
  }

  function verticalSweepPress() {
    return sweepPress("vertical");
  }

  function verticalSweepRelease() {
    return sweepRelease("vertical");
  }

  function releaseAllSweeps() {
    return Promise.all([sweepRelease("horizontal"), sweepRelease("vertical")]);
  }

  async function setLiveFxSpeeds(payload: {
    strobe_speed?: number;
    sweep_speed?: number;
  }) {
    await runCommand("live-fx-speed", { ...payload, persist: true });
  }

  async function setPadPresets(presetIds: string[]) {
    await runCommand("pad-presets", { preset_ids: presetIds });
  }

  async function notifyFocusLoss() {
    const source_id = getWebSessionSourceId();
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "focus_loss" }));
    }
    await runCommand("focus-loss", { source_id });
  }

  async function notifyVisibilityHidden() {
    const source_id = getWebSessionSourceId();
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "visibility_hidden" }));
    }
    await runCommand("visibility-hidden", { source_id });
  }

  function onVisibility() {
    if (document.visibilityState === "hidden") {
      void notifyVisibilityHidden();
      void strobeRelease();
      void releaseAllSweeps();
    }
  }

  function onBlur() {
    void notifyFocusLoss();
    void strobeRelease();
    void releaseAllSweeps();
  }

  watch(connection, (value) => {
    if (value !== "online" && value !== "reconnecting") {
      window.dispatchEvent(new CustomEvent("orng-release-holds"));
    }
  });

  useKeyboardPad({
    enabled: () => connection.value === "online" || connection.value === "reconnecting",
    onState: (next) => applyState(next),
  });

  onMounted(async () => {
    try {
      await refreshRest();
      connection.value = "online";
    } catch (err) {
      connection.value = "offline";
      lastError.value = err instanceof Error ? err.message : "Немає зв'язку";
      toasts.push(lastError.value, "error");
    } finally {
      loading.value = false;
      connectWs();
      window.addEventListener("blur", onBlur);
      document.addEventListener("visibilitychange", onVisibility);
    }
  });

  onUnmounted(() => {
    disposed = true;
    intentionalClose = true;
    window.removeEventListener("blur", onBlur);
    document.removeEventListener("visibilitychange", onVisibility);
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    if (publishTimer !== null) window.clearTimeout(publishTimer);
    socket?.close();
  });

  return {
    state,
    engine,
    output,
    sceneId,
    sceneName,
    availableScenes,
    availablePresets,
    availableIds,
    connection,
    loading,
    lastError,
    sceneSwitching,
    liveView,
    liveFrame,
    selectPreset,
    seekEpisode,
    nudgeEpisode,
    strobePress,
    strobeRelease,
    setBlackout,
    toggleBlackout,
    setFace,
    setMasterBrightness,
    setPreviewSpeed,
    nextAutoLook,
    setBpmSync,
    colorHit,
    sweepPress,
    sweepRelease,
    verticalSweepPress,
    verticalSweepRelease,
    releaseAllSweeps,
    setLiveFxSpeeds,
    setPadPresets,
    switchScene,
    refreshRest,
  };
}
