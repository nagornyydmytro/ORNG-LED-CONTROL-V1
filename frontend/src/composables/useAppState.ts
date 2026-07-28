import { computed, onMounted, onUnmounted, ref, shallowRef } from "vue";
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
  BUTTON_WHITE_HIT,
  PRESET_BUTTONS,
  postInputButton,
} from "../api/input";
import type { AppState, PresetInfo } from "../vite-env";
import { useKeyboardPad } from "./useKeyboardPad";
import type { ToastApi } from "./useToasts";

export type ConnectionStatus = "connecting" | "online" | "offline" | "reconnecting";

function wsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/ws`;
}

export function useAppState(toasts: ToastApi) {
  const state = shallowRef<AppState | null>(null);
  const availablePresets = ref<PresetInfo[]>([]);
  const connection = ref<ConnectionStatus>("connecting");
  const loading = ref(true);
  const lastError = ref<string | null>(null);
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let disposed = false;
  let intentionalClose = false;

  const engine = computed(() => state.value?.engine ?? null);
  const output = computed(() => state.value?.output ?? null);
  const availableIds = computed(() => new Set(state.value?.presets ?? []));

  function applyState(next: AppState) {
    state.value = next;
    lastError.value = next.output.last_error;
    loading.value = false;
  }

  async function refreshRest() {
    const [nextState, presets] = await Promise.all([fetchState(), fetchPresets()]);
    applyState(nextState);
    availablePresets.value = presets;
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
      const ack = await postInputButton(buttonId, edge, "ui");
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

  async function selectPreset(presetId: string) {
    if (!availableIds.value.has(presetId)) {
      toasts.push(`Пресет ${presetId} ще не завантажено на сервері`, "error");
      return;
    }
    const buttonId = PRESET_BUTTONS[presetId];
    if (!buttonId) {
      await runCommand("select-preset", { preset_id: presetId, reset_clock: true });
      return;
    }
    await dispatchPad(buttonId);
  }

  async function whiteHit() {
    await dispatchPad(BUTTON_WHITE_HIT, "pulse", "White Hit");
  }

  async function strobePress() {
    await dispatchPad(BUTTON_STROBE, "press");
  }

  async function strobeRelease() {
    if (!state.value?.engine.strobe_held && connection.value !== "online") return;
    try {
      await dispatchPad(BUTTON_STROBE, "release");
    } catch {
      // failsafe best-effort
    }
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
    await runCommand("preview-speed", { value });
  }

  async function notifyFocusLoss() {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "focus_loss" }));
    }
    await runCommand("focus-loss");
  }

  async function notifyVisibilityHidden() {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "visibility_hidden" }));
    }
    await runCommand("visibility-hidden");
  }

  function onVisibility() {
    if (document.visibilityState === "hidden") {
      void notifyVisibilityHidden();
      void strobeRelease();
    }
  }

  function onBlur() {
    void notifyFocusLoss();
    void strobeRelease();
  }

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
    socket?.close();
  });

  return {
    state,
    engine,
    output,
    availablePresets,
    availableIds,
    connection,
    loading,
    lastError,
    selectPreset,
    whiteHit,
    strobePress,
    strobeRelease,
    setBlackout,
    toggleBlackout,
    setFace,
    setMasterBrightness,
    setPreviewSpeed,
    refreshRest,
  };
}
