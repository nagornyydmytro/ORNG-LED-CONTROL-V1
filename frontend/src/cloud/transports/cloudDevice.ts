import { adminDeviceCommand, adminDeviceLive, adminWsUrl } from "../../api/adminCloud";
import { defaultAccessEmail, defaultAdminBase } from "../mode";
import { getCloudWebSessionSourceId } from "../sessionSourceId";
import type { CloudControlTransport, CloudDeviceLiveState } from "../transport";
import type { SimulatorView } from "../../vite-env";
import { bindCalibrationChannel, clearCalibrationChannel } from "../../runtime/calibrationChannel";
import { acceptPreview, REMOTE_PREVIEW_LIVE_MS, type PreviewStamp } from "../../lib/previewFreshness";

function readStage(value: unknown): SimulatorView | null {
  if (!value || typeof value !== "object") return null;
  const stage = value as SimulatorView;
  if (!Array.isArray(stage.pars) || !Array.isArray(stage.bars) || !Array.isArray(stage.beams)) {
    return null;
  }
  return stage;
}

const KEEPALIVE_MS = 3_500;
const LINK_POLL_MS = 20_000;
const LINK_SOCKET_RETRY_MS = 90_000;
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 15_000;
const COMMAND_TIMEOUT_MS = 12_000;

type HoldTrack = {
  holdId: string;
  timer: number | null;
};

type ControlChannelStatus = "connecting" | "open" | "reconnecting" | "closed";

function jitter(ms: number): number {
  const spread = Math.min(ms * 0.3, 1_000);
  return Math.max(0, Math.round(ms + (Math.random() * 2 - 1) * spread));
}

/**
 * Cloud remote-control transport — HTTP for discrete commands; admin WSS for
 * momentary press → hold_id → keepalive → release. Never sends Art-Net.
 *
 * Device online comes only from DeviceDO device session (heartbeat / hello
 * device_online). Admin socket close means control-channel reconnect, not
 * “Raspberry offline”.
 */
export function createCloudDeviceTransport(opts: {
  installationId: string;
  adminBase?: string;
  accessEmail?: string;
}): CloudControlTransport {
  const adminBase = (opts.adminBase ?? defaultAdminBase()).replace(/\/$/, "");
  const email = opts.accessEmail ?? defaultAccessEmail();
  const sourceId = getCloudWebSessionSourceId();
  const clientOpts = { adminBase, accessEmail: email };

  let socket: WebSocket | null = null;
  let connectPromise: Promise<void> | null = null;
  let disposed = false;
  let linkMode = false;
  let linkSocketTimer: number | null = null;
  let linkTimer: number | null = null;
  let reconnectTimer: number | null = null;
  let reconnectAttempt = 0;
  let controlStatus: ControlChannelStatus = "closed";

  const holds = new Map<string, HoldTrack>();
  const liveListeners = new Set<(s: CloudDeviceLiveState) => void>();
  const ackListeners = new Set<
    (ack: { command_id: string; status: string; type?: string; error?: string }) => void
  >();
  /** Correlate command_result / ack by command_id (never FIFO). */
  const pendingByCommandId = new Map<
    string,
    {
      resolve: (v: Record<string, unknown>) => void;
      reject: (e: Error) => void;
      type: string;
      timer: number;
      /** Discrete commands resolve only on device applied/failed, not HTTP accepted. */
      settle?: "applied" | "result";
    }
  >();

  /** Last known device state — kept across control-channel reconnect. */
  let lastLive: CloudDeviceLiveState = {
    online: false,
    armed: false,
    blackout: true,
  };
  let lastStateAt: number | null = null;
  let shownPreview: PreviewStamp | null = null;
  let optimisticPreset: { id: string; at: number } | null = null;
  let optimisticBlackout: { value: boolean; at: number } | null = null;
  let optimisticFace: { value: boolean; at: number } | null = null;
  let optimisticStrobe: { value: boolean; at: number } | null = null;
  let optimisticSweep: { value: boolean; at: number } | null = null;
  let optimisticSweepV: { value: boolean; at: number } | null = null;
  let optimisticEpisode: { value: number; at: number } | null = null;

  function keepHold<T>(hold: { value: T; at: number } | null, incoming: T): { hold: typeof hold; value: T } {
    if (!hold) return { hold, value: incoming };
    if (incoming === hold.value || Date.now() - hold.at > 8000) {
      return { hold: null, value: incoming };
    }
    return { hold, value: hold.value };
  }

  function snapshotStale(): boolean {
    const at = shownPreview?.at ?? lastStateAt;
    if (at == null) return false;
    return Date.now() - at > REMOTE_PREVIEW_LIVE_MS;
  }

  function emitLive(partial: Partial<CloudDeviceLiveState>) {
    lastLive = {
      ...lastLive,
      ...partial,
      // Never invent armed/blackout from partial online-only updates.
      armed: partial.armed !== undefined ? partial.armed : lastLive.armed,
      blackout: partial.blackout !== undefined ? partial.blackout : lastLive.blackout,
      online: partial.online !== undefined ? partial.online : lastLive.online,
    };
    if (
      partial.armed !== undefined ||
      partial.blackout !== undefined ||
      partial.preset_id !== undefined
    ) {
      lastStateAt = Date.now();
    }
    const out: CloudDeviceLiveState = {
      ...lastLive,
      control_status: controlStatus,
      state_stale: snapshotStale(),
      last_state_at: lastStateAt,
    };
    for (const cb of liveListeners) cb(out);
  }

  function setControlStatus(next: ControlChannelStatus) {
    if (controlStatus === next) return;
    controlStatus = next;
    emitLive({ ...lastLive });
  }

  function emitAck(ack: { command_id: string; status: string; type?: string; error?: string }) {
    for (const cb of ackListeners) cb(ack);
  }

  function applyCurrentEffect(payload: Record<string, unknown>) {
    let bpm: CloudDeviceLiveState["bpm"] = lastLive.bpm;
    const program = payload.program === "beat" || payload.program === "smooth" ? payload.program : null;
    const look = typeof payload.look === "string" ? payload.look : null;
    if (bpm && (program || look)) {
      bpm = { ...bpm, ...(program ? { program } : {}), ...(look ? { look } : {}) };
    }
    shownPreview = acceptPreview(shownPreview, payload);
    emitLive({
      preview_at: shownPreview ? shownPreview.at : null,
      ...(typeof payload.preset_id === "string" ? { preset_id: payload.preset_id } : {}),
      ...(bpm ? { bpm } : {}),
    });
  }

  function stopHold(effect: string) {
    const track = holds.get(effect);
    if (!track) return;
    if (track.timer !== null) window.clearInterval(track.timer);
    holds.delete(effect);
  }

  function releaseLocalHoldsOnDisconnect() {
    for (const effect of [...holds.keys()]) stopHold(effect);
    for (const timer of keyTimers.values()) window.clearInterval(timer);
    keyTimers.clear();
    pressGen.clear();
  }

  function rejectAllPending(reason: string) {
    for (const [id, waiter] of pendingByCommandId) {
      window.clearTimeout(waiter.timer);
      waiter.reject(new Error(reason));
      pendingByCommandId.delete(id);
    }
  }

  function startKeepalive(effect: string, holdId: string) {
    const track = holds.get(effect);
    if (!track || track.holdId !== holdId || track.timer !== null) return;
    const timer = window.setInterval(() => {
      if (linkMode) {
        void httpCommand("hold_keepalive", { hold_id: holdId }).catch(() => undefined);
        return;
      }
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      socket.send(
        JSON.stringify({
          type: "hold_keepalive",
          payload: { hold_id: holdId },
        }),
      );
    }, KEEPALIVE_MS);
    track.timer = timer;
  }

  function clearReconnectTimer() {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function stopLinkPoll() {
    if (linkTimer !== null) {
      window.clearInterval(linkTimer);
      linkTimer = null;
    }
  }

  async function pollLink() {
    if (disposed) return;
    try {
      const live = await adminDeviceLive(opts.installationId, clientOpts);
      // A frame that confirmed the executor while this request was in flight wins.
      if (disposed || !linkMode) return;
      // A stored row is not the device socket. Last-seen must not mark the hall present.
      applyAuthoritativeDeviceState({ ...live, online: false, device_channel: "stored" });
      const effect = live.effect;
      if (effect && typeof effect === "object") {
        applyCurrentEffect(effect as Record<string, unknown>);
      }
      setControlStatus("open");
    } catch {
      if (socket && socket.readyState === WebSocket.OPEN) return;
      if (disposed || !linkMode) return;
      setControlStatus("reconnecting");
    }
  }

  function noteDevicePresent() {
    if (!linkMode) return;
    linkMode = false;
    clearLinkSocketRetry();
    stopLinkPoll();
    setControlStatus("open");
  }

  function clearLinkSocketRetry() {
    if (linkSocketTimer !== null) {
      window.clearTimeout(linkSocketTimer);
      linkSocketTimer = null;
    }
  }

  function scheduleLinkSocketRetry() {
    if (linkSocketTimer !== null || disposed || !linkMode) return;
    linkSocketTimer = window.setTimeout(() => {
      linkSocketTimer = null;
      if (disposed || !linkMode) return;
      if (socket && socket.readyState === WebSocket.OPEN) {
        // The browser channel is still up. That is not the executor.
        return;
      }
      void ensureSocket()
        .then(() => {
          if (disposed || !linkMode) return;
          if (!(socket && socket.readyState === WebSocket.OPEN)) scheduleLinkSocketRetry();
        })
        .catch(() => {
          if (!disposed && linkMode) scheduleLinkSocketRetry();
        });
    }, LINK_SOCKET_RETRY_MS);
  }

  function startLinkPoll() {
    linkMode = true;
    scheduleLinkSocketRetry();
    if (linkTimer !== null) return;
    void pollLink();
    linkTimer = window.setInterval(() => {
      void pollLink();
    }, LINK_POLL_MS);
  }

  function scheduleReconnect() {
    if (disposed) return;
    if (linkMode) {
      startLinkPoll();
      return;
    }
    clearReconnectTimer();
    setControlStatus("reconnecting");
    const delay = jitter(
      Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.min(reconnectAttempt, 5)),
    );
    reconnectAttempt += 1;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      void ensureSocket().catch(() => {
        /* ensureSocket schedules another attempt on close */
      });
    }, delay);
  }

  async function httpCommand(type: string, payload: Record<string, unknown> = {}) {
    if (!linkMode) await ensureSocket();
    const commandId =
      typeof payload.command_id === "string" && payload.command_id
        ? payload.command_id
        : `http-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const applied = new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        if (pendingByCommandId.has(commandId)) {
          pendingByCommandId.delete(commandId);
          reject(new Error(`timeout waiting for ${type}`));
        }
      }, COMMAND_TIMEOUT_MS);
      pendingByCommandId.set(commandId, { resolve, reject, type, timer, settle: "applied" });
    });
    let data: { ok?: boolean; command_id?: string; status?: string; error?: string };
    try {
      data = (await adminDeviceCommand(
        opts.installationId,
        type,
        { ...payload, command_id: commandId, source: sourceId },
        clientOpts,
      )) as typeof data;
    } catch (err) {
      const waiter = pendingByCommandId.get(commandId);
      if (waiter) {
        window.clearTimeout(waiter.timer);
        pendingByCommandId.delete(commandId);
      }
      throw err;
    }
    emitAck({
      command_id: commandId,
      status: data.status ?? (data.ok ? "accepted" : "failed"),
      type,
      error: data.error,
    });
    if (data.ok === false || data.status === "failed") {
      const waiter = pendingByCommandId.get(commandId);
      if (waiter) {
        window.clearTimeout(waiter.timer);
        pendingByCommandId.delete(commandId);
      }
      throw new Error(data.error ?? "command failed");
    }
    if (data.status === "applied") {
      const waiter = pendingByCommandId.get(commandId);
      if (waiter) {
        window.clearTimeout(waiter.timer);
        pendingByCommandId.delete(commandId);
      }
      return data;
    }
    // queued and accepted are not applied. Wait for the device ack.
    return applied;
  }

  function authoritativePreset(payload: Record<string, unknown>): string | null {
    const incoming = payload.preset_id != null ? String(payload.preset_id) : null;
    if (!optimisticPreset) return incoming;
    if (incoming === optimisticPreset.id || Date.now() - optimisticPreset.at > 8000) {
      optimisticPreset = null;
      return incoming;
    }
    return optimisticPreset.id;
  }

  function applyAuthoritativeDeviceState(payload: Record<string, unknown>) {
    const desired =
      payload.desired_app_release && typeof payload.desired_app_release === "object"
        ? (payload.desired_app_release as Record<string, unknown>)
        : null;
    const updateState =
      payload.update_state != null
        ? String(payload.update_state)
        : payload.app_update_status != null
          ? String(payload.app_update_status)
          : null;
    const blackoutHold = keepHold(optimisticBlackout, Boolean(payload.blackout));
    optimisticBlackout = blackoutHold.hold;
    const faceIncoming = typeof payload.face_on === "boolean" ? payload.face_on : lastLive.face_on;
    const faceHold = keepHold(
      optimisticFace,
      typeof faceIncoming === "boolean" ? faceIncoming : false,
    );
    optimisticFace = faceHold.hold;
    const strobeIncoming =
      typeof payload.strobe_held === "boolean" ? payload.strobe_held : Boolean(lastLive.strobe_held);
    const strobeHold = keepHold(optimisticStrobe, strobeIncoming);
    optimisticStrobe = strobeHold.hold;
    const sweepIncoming =
      typeof payload.sweep_active === "boolean" ? payload.sweep_active : Boolean(lastLive.sweep_active);
    const sweepHold = keepHold(optimisticSweep, sweepIncoming);
    optimisticSweep = sweepHold.hold;
    const sweepVIncoming =
      typeof payload.vertical_sweep_active === "boolean"
        ? payload.vertical_sweep_active
        : Boolean(lastLive.vertical_sweep_active);
    const sweepVHold = keepHold(optimisticSweepV, sweepVIncoming);
    optimisticSweepV = sweepVHold.hold;
    const episodeIncoming =
      typeof payload.episode_index === "number" ? payload.episode_index : lastLive.episode_index ?? 0;
    const episodeHold = keepHold(optimisticEpisode, episodeIncoming);
    optimisticEpisode = episodeHold.hold;
    const stored = payload.device_channel === "stored";
    emitLive({
      online: stored ? lastLive.online : payload.online === undefined ? true : Boolean(payload.online),
      armed: Boolean(payload.armed),
      blackout: blackoutHold.value,
      transport: payload.transport != null ? String(payload.transport) : null,
      current_scene_id:
        payload.current_scene_id != null ? String(payload.current_scene_id) : null,
      scene_name: payload.scene_name != null ? String(payload.scene_name) : null,
      pad_presets: Array.isArray(payload.pad_presets)
        ? payload.pad_presets.map((x) => String(x)).slice(0, 9)
        : null,
      preset_id: authoritativePreset(payload),
      face_on: optimisticFace ? faceHold.value : typeof payload.face_on === "boolean" ? payload.face_on : undefined,
      strobe_held: optimisticStrobe
        ? strobeHold.value
        : typeof payload.strobe_held === "boolean"
          ? payload.strobe_held
          : undefined,
      sweep_active: optimisticSweep
        ? sweepHold.value
        : typeof payload.sweep_active === "boolean"
          ? payload.sweep_active
          : undefined,
      vertical_sweep_active: optimisticSweepV
        ? sweepVHold.value
        : typeof payload.vertical_sweep_active === "boolean"
          ? payload.vertical_sweep_active
          : undefined,
      episode_index: optimisticEpisode
        ? episodeHold.value
        : typeof payload.episode_index === "number"
          ? payload.episode_index
          : undefined,
      episode_time_s: typeof payload.episode_time_s === "number" ? payload.episode_time_s : undefined,
      episode_count: typeof payload.episode_count === "number" ? payload.episode_count : undefined,
      preset_time_s: typeof payload.preset_time_s === "number" ? payload.preset_time_s : undefined,
      cycle_duration_s:
        typeof payload.cycle_duration_s === "number" ? payload.cycle_duration_s : undefined,
      face_brightness:
        typeof payload.face_brightness === "number" ? payload.face_brightness : undefined,
      preview_speed: typeof payload.preview_speed === "number" ? payload.preview_speed : undefined,
      ...(payload.bpm && typeof payload.bpm === "object"
        ? { bpm: payload.bpm as CloudDeviceLiveState["bpm"] }
        : {}),
      ...(Array.isArray(payload.capabilities)
        ? { capabilities: payload.capabilities.map((item) => String(item)) }
        : {}),
      udp_active: typeof payload.udp_active === "boolean" ? payload.udp_active : undefined,
      strobe_speed: typeof payload.strobe_speed === "number" ? payload.strobe_speed : undefined,
      sweep_speed: typeof payload.sweep_speed === "number" ? payload.sweep_speed : undefined,
      ...(readStage(payload.stage) ? { stage: readStage(payload.stage) } : {}),
      ...(payload.beam_calibration && typeof payload.beam_calibration === "object"
        ? {
            beam_calibration: payload.beam_calibration as CloudDeviceLiveState["beam_calibration"],
          }
        : {}),
      current_release_version:
        typeof payload.current_release_version === "number"
          ? payload.current_release_version
          : null,
      current_content_hash:
        payload.current_content_hash != null ? String(payload.current_content_hash) : null,
      last_synced_hash:
        payload.last_synced_hash != null ? String(payload.last_synced_hash) : null,
      last_synced_version:
        typeof payload.last_synced_version === "number" ? payload.last_synced_version : null,
      desired_scene_version:
        typeof payload.desired_scene_version === "number" ? payload.desired_scene_version : null,
      desired_content_hash:
        payload.desired_content_hash != null ? String(payload.desired_content_hash) : null,
      sync_status: payload.sync_status != null ? String(payload.sync_status) : null,
      last_error:
        payload.last_error === null || payload.last_error === undefined
          ? null
          : String(payload.last_error),
      app_version: payload.app_version != null ? String(payload.app_version) : null,
      build_id: payload.build_id != null ? String(payload.build_id) : null,
      platform: payload.platform != null ? String(payload.platform) : null,
      arch: payload.arch != null ? String(payload.arch) : null,
      install_mode: payload.install_mode != null ? String(payload.install_mode) : null,
      update_state: updateState,
      update_progress:
        typeof payload.update_progress === "number" ? payload.update_progress : null,
      last_update_error:
        payload.last_update_error === null || payload.last_update_error === undefined
          ? null
          : String(payload.last_update_error),
      staged_app_version:
        payload.staged_app_version != null
          ? String(payload.staged_app_version)
          : payload.staged_version != null
            ? String(payload.staged_version)
            : null,
      staged_build_id:
        payload.staged_build_id != null ? String(payload.staged_build_id) : null,
      desired_app_version: desired?.version != null ? String(desired.version) : null,
      desired_build_id: desired?.build_id != null ? String(desired.build_id) : null,
      hardware_acceptance:
        payload.hardware_acceptance != null ? String(payload.hardware_acceptance) : "pending",
      wifi_connected:
        typeof payload.wifi_connected === "boolean" ? payload.wifi_connected : null,
      cloud_connected:
        typeof payload.cloud_connected === "boolean" ? payload.cloud_connected : null,
      ethernet_link:
        typeof payload.ethernet_link === "boolean" ? payload.ethernet_link : null,
      artnet_route_valid:
        typeof payload.artnet_route_valid === "boolean" ? payload.artnet_route_valid : null,
      keyboard_connected:
        typeof payload.keyboard_connected === "boolean" ? payload.keyboard_connected : null,
      keyboard_count:
        typeof payload.keyboard_count === "number" ? payload.keyboard_count : null,
      disk_free_bytes:
        typeof payload.disk_free_bytes === "number" ? payload.disk_free_bytes : null,
      cpu_temp_c: typeof payload.cpu_temp_c === "number" ? payload.cpu_temp_c : null,
    });
  }

  function ensureSocket(): Promise<void> {
    if (disposed) return Promise.reject(new Error("transport disposed"));
    if (socket && socket.readyState === WebSocket.OPEN) return Promise.resolve();
    if (connectPromise) return connectPromise;

    if (controlStatus !== "reconnecting") {
      setControlStatus("connecting");
    }

    connectPromise = new Promise<void>((resolve, reject) => {
      const url = adminWsUrl(opts.installationId, {
        ...clientOpts,
        sessionHint: sourceId.replace(/^cloud:web:/, ""),
      });
      const ws = new WebSocket(url);
      socket = ws;
      let settled = false;

      const onFail = (err: Error) => {
        if (settled) return;
        settled = true;
        connectPromise = null;
        reject(err);
      };

      const sendCalibration = (type: string, payload: Record<string, unknown>) =>
        wsCommand(type, payload, "applied");

      ws.addEventListener("open", () => {
        settled = true;
        connectPromise = null;
        reconnectAttempt = 0;
        setControlStatus("open");
        bindCalibrationChannel(sendCalibration);
        resolve();
      });

      ws.addEventListener("message", (event) => {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(String(event.data)) as Record<string, unknown>;
        } catch {
          return;
        }
        const type = String(msg.type ?? "");

        if (type === "admin_hello") {
          const payload = (msg.payload ?? {}) as Record<string, unknown>;
          const deviceOnline = Boolean(payload.online ?? payload.device_online);
          // admin_hello must NOT invent armed=false / blackout=true.
          // Prefer cached last device state when Worker includes it.
          const cached =
            (payload.last_state && typeof payload.last_state === "object"
              ? (payload.last_state as Record<string, unknown>)
              : null) ??
            (payload.last_device_state && typeof payload.last_device_state === "object"
              ? (payload.last_device_state as Record<string, unknown>)
              : null);
          if (cached) {
            applyAuthoritativeDeviceState({ ...cached, online: deviceOnline });
          } else {
            emitLive({
              online: deviceOnline,
              armed: lastLive.armed,
              blackout: lastLive.blackout,
            });
          }
          if (deviceOnline) noteDevicePresent();
          else if (!disposed) startLinkPoll();
          return;
        }

        if (type === "device_heartbeat" || type === "device_state") {
          const payload = (msg.payload ?? {}) as Record<string, unknown>;
          noteDevicePresent();
          applyAuthoritativeDeviceState(payload);
          return;
        }

        if (type === "device_preview") {
          noteDevicePresent();
          applyCurrentEffect((msg.payload ?? {}) as Record<string, unknown>);
          return;
        }

        if (type === "device_offline") {
          shownPreview = null;
          emitLive({
            online: false,
            armed: lastLive.armed,
            blackout: lastLive.blackout,
            stage: null,
            preview_at: null,
          });
          if (!disposed) startLinkPoll();
          return;
        }

        if (type === "ack") {
          const payload = (msg.payload ?? {}) as Record<string, unknown>;
          const commandId = String(payload.command_id ?? "");
          const status = String(payload.status ?? "");
          emitAck({
            command_id: commandId,
            status: status || "unknown",
            error: payload.error_message != null ? String(payload.error_message) : undefined,
          });
          const waiter = commandId ? pendingByCommandId.get(commandId) : undefined;
          if (waiter && status !== "accepted") {
            const finished =
              status === "applied" || status === "failed" || status === "unsupported";
            if (!finished && waiter.settle === "applied") return;
            window.clearTimeout(waiter.timer);
            pendingByCommandId.delete(commandId);
            if (status === "applied") waiter.resolve(payload);
            else {
              waiter.reject(
                new Error(String(payload.error_message ?? (status || "command failed"))),
              );
            }
          }
          return;
        }

        if (type === "command_result") {
          const payload = (msg.payload ?? {}) as Record<string, unknown>;
          const commandId = String(payload.command_id ?? "");
          const waiter = commandId ? pendingByCommandId.get(commandId) : undefined;
          if (waiter && waiter.settle === "applied" && String(payload.status ?? "accepted") === "accepted") {
            return;
          }
          if (waiter) {
            const resultStatus = String(payload.status ?? "");
            if (waiter.settle === "applied" && resultStatus === "accepted") return;
            const rejected =
              resultStatus === "failed" ||
              resultStatus === "unsupported" ||
              (waiter.settle === "applied" && resultStatus !== "applied");
            if (waiter.settle === "applied" && resultStatus !== "applied" && !rejected) return;
            window.clearTimeout(waiter.timer);
            pendingByCommandId.delete(commandId);
            if (rejected || (waiter.settle === "applied" && resultStatus !== "applied")) {
              waiter.reject(
                new Error(String(payload.error ?? (resultStatus || "command failed"))),
              );
              return;
            }
            waiter.resolve(payload);
            emitAck({
              command_id: commandId,
              status: String(payload.status ?? "accepted"),
              type: waiter.type,
              error: payload.error != null ? String(payload.error) : undefined,
            });
          } else if (commandId) {
            emitAck({
              command_id: commandId,
              status: String(payload.status ?? "accepted"),
              error: payload.error != null ? String(payload.error) : undefined,
            });
          }
          return;
        }

        if (type === "error") {
          const payload = (msg.payload ?? {}) as Record<string, unknown>;
          const commandId = payload.command_id != null ? String(payload.command_id) : "";
          const waiter = commandId ? pendingByCommandId.get(commandId) : undefined;
          if (waiter) {
            window.clearTimeout(waiter.timer);
            pendingByCommandId.delete(commandId);
            waiter.reject(new Error(String(payload.message ?? payload.code ?? "ws error")));
          }
        }
      });

      ws.addEventListener("close", () => {
        socket = null;
        connectPromise = null;
        clearCalibrationChannel(sendCalibration);
        if (linkMode) {
          if (!settled) onFail(new Error("admin websocket closed before open"));
          if (!disposed) startLinkPoll();
          return;
        }
        releaseLocalHoldsOnDisconnect();
        rejectAllPending("admin websocket closed");
        // Control channel down ≠ device offline. Keep lastLive.online/armed/blackout.
        setControlStatus(disposed ? "closed" : "reconnecting");
        if (!disposed) {
          scheduleReconnect();
        }
        if (!settled) {
          onFail(new Error("admin websocket closed before open"));
        }
      });

      ws.addEventListener("error", () => {
        // close handler drives reconnect; only fail the open promise here.
        if (!settled) {
          onFail(new Error("admin websocket error"));
        }
      });
    });

    return connectPromise;
  }

  let fxFlight: Promise<void> | null = null;
  let fxQueued: { strobe_speed?: number; sweep_speed?: number; persist?: boolean } | null = null;
  const pressGen = new Map<string, number>();
  const keyGen = new Map<string, number>();
  const keyTimers = new Map<string, number>();

  async function sendLiveFxSpeed(body: {
    strobe_speed?: number;
    sweep_speed?: number;
    persist?: boolean;
  }) {
    fxQueued = { ...(fxQueued ?? {}), ...body };
    if (fxFlight) return fxFlight;
    fxFlight = (async () => {
      while (fxQueued) {
        const next = fxQueued;
        fxQueued = null;
        try {
          await httpCommand("set_live_fx_speed", next);
        } catch (err) {
          if (!fxQueued) throw err;
        }
      }
    })().finally(() => {
      fxFlight = null;
    });
    return fxFlight;
  }

  async function wsCommand(
    type: string,
    payload: Record<string, unknown> = {},
    settle: "result" | "applied" = "result",
  ): Promise<Record<string, unknown>> {
    if (linkMode) return httpCommand(type, payload);
    await ensureSocket();
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      throw new Error("admin websocket not open");
    }
    const commandId =
      typeof payload.command_id === "string" && payload.command_id
        ? payload.command_id
        : `ws-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const body = { ...payload, command_id: commandId };
    const result = new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        if (pendingByCommandId.has(commandId)) {
          pendingByCommandId.delete(commandId);
          reject(new Error(`timeout waiting for ${type}`));
        }
      }, COMMAND_TIMEOUT_MS);
      pendingByCommandId.set(commandId, { resolve, reject, type, timer, settle });
    });
    socket.send(JSON.stringify({ type, payload: body }));
    return result;
  }

  function sendRelease(releaseType: string, holdId: string) {
    return wsCommand(releaseType, { hold_id: holdId }).then(() => undefined);
  }

  function momentary(
    pressType: string,
    releaseType: string,
    effect: string,
    edge: "press" | "release",
  ) {
    if (edge === "press") {
      if (effect === "strobe") {
        optimisticStrobe = { value: true, at: Date.now() };
        emitLive({ strobe_held: true });
      } else if (effect === "sweep") {
        optimisticSweep = { value: true, at: Date.now() };
        emitLive({ sweep_active: true });
      } else if (effect === "vertical_sweep") {
        optimisticSweepV = { value: true, at: Date.now() };
        emitLive({ vertical_sweep_active: true });
      }
      const holdId = `h-${effect}-${Math.random().toString(36).slice(2, 10)}`;
      const gen = (pressGen.get(effect) ?? 0) + 1;
      pressGen.set(effect, gen);
      holds.set(effect, { holdId, timer: null });
      void wsCommand(pressType, { hold_id: holdId })
        .then(() => {
          if (pressGen.get(effect) !== gen) {
            // Finger already up, before this press was stored. Send the
            // matching release now — an earlier one would have been rejected.
            void sendRelease(releaseType, holdId).catch(() => undefined);
            return;
          }
          startKeepalive(effect, holdId);
        })
        .catch(() => {
          if (pressGen.get(effect) === gen) {
            stopHold(effect);
            if (effect === "strobe") {
              optimisticStrobe = null;
              emitLive({ strobe_held: false });
            } else if (effect === "sweep") {
              optimisticSweep = null;
              emitLive({ sweep_active: false });
            } else if (effect === "vertical_sweep") {
              optimisticSweepV = null;
              emitLive({ vertical_sweep_active: false });
            }
          } else void sendRelease(releaseType, holdId).catch(() => undefined);
        });
      return Promise.resolve();
    }
    if (effect === "strobe") {
      optimisticStrobe = { value: false, at: Date.now() };
      emitLive({ strobe_held: false });
    } else if (effect === "sweep") {
      optimisticSweep = { value: false, at: Date.now() };
      emitLive({ sweep_active: false });
    } else if (effect === "vertical_sweep") {
      optimisticSweepV = { value: false, at: Date.now() };
      emitLive({ vertical_sweep_active: false });
    }
    pressGen.set(effect, (pressGen.get(effect) ?? 0) + 1);
    const track = holds.get(effect);
    const holdId = track?.holdId;
    const pressStillPending = Boolean(track && track.timer === null);
    stopHold(effect);
    if (!holdId || pressStillPending) return Promise.resolve();
    return sendRelease(releaseType, holdId);
  }

  return {
    kind: "cloud",
    installationId: opts.installationId,
    sourceId,
    async connect() {
      startLinkPoll();
      const opened = await Promise.race([
        ensureSocket().then(() => true).catch(() => false),
        new Promise<boolean>((resolve) => {
          window.setTimeout(() => resolve(false), 2500);
        }),
      ]);
      if (opened && socket && socket.readyState === WebSocket.OPEN) {
        // admin_hello decides whether the executor fallback stays.
        return;
      }
      linkMode = true;
      clearReconnectTimer();
      try {
        socket?.close();
      } catch {
        /* ignore */
      }
      socket = null;
      connectPromise = null;
      startLinkPoll();
    },
    dispose() {
      disposed = true;
      clearReconnectTimer();
      clearLinkSocketRetry();
      stopLinkPoll();
      for (const effect of [...holds.keys()]) stopHold(effect);
      connectPromise = null;
      setControlStatus("closed");
      try {
        socket?.close();
      } catch {
        /* ignore */
      }
      socket = null;
      rejectAllPending("transport disposed");
    },
    onLiveState(cb) {
      liveListeners.add(cb);
      cb({
        ...lastLive,
        control_status: controlStatus,
        state_stale: snapshotStale(),
        last_state_at: lastStateAt,
      });
      return () => liveListeners.delete(cb);
    },
    onCommandAck(cb) {
      ackListeners.add(cb);
      return () => ackListeners.delete(cb);
    },
    async blackout(enabled) {
      const previous = lastLive.blackout;
      optimisticBlackout = { value: enabled, at: Date.now() };
      emitLive({ blackout: enabled });
      try {
        await httpCommand("blackout", { enabled });
      } catch (err) {
        optimisticBlackout = null;
        emitLive({ blackout: previous });
        throw err;
      }
    },
    async arm() {
      await httpCommand("arm");
    },
    async disarm() {
      await httpCommand("disarm");
    },
    async selectPreset(presetId) {
      const previous = lastLive.preset_id ?? null;
      optimisticPreset = { id: presetId, at: Date.now() };
      emitLive({ preset_id: presetId });
      try {
        await httpCommand("select_preset", { preset_id: presetId });
      } catch (err) {
        optimisticPreset = null;
        emitLive({ preset_id: previous });
        throw err;
      }
    },
    async setLiveFxSpeed(body: { strobe_speed?: number; sweep_speed?: number; persist?: boolean }) {
      await sendLiveFxSpeed(body);
    },
    async keyboard(event: {
      code: string;
      type: "keydown" | "keyup";
      repeat: boolean;
      profile_id?: "qwerty" | "ch57x";
      source_id?: string;
    }) {
      const profile = event.profile_id ?? "qwerty";
      const keyId = `${profile}:${event.code}`;
      if (event.type === "keyup") {
        keyGen.set(keyId, (keyGen.get(keyId) ?? 0) + 1);
        const timer = keyTimers.get(keyId);
        if (timer !== undefined) window.clearInterval(timer);
        keyTimers.delete(keyId);
      }
      const gen = event.type === "keydown" ? (keyGen.get(keyId) ?? 0) + 1 : keyGen.get(keyId);
      if (event.type === "keydown" && !event.repeat) keyGen.set(keyId, gen ?? 1);
      await wsCommand(
        "keyboard",
        {
          code: event.code,
          type: event.type,
          repeat: event.repeat,
          profile_id: profile,
        },
        "result",
      );
      if (event.type === "keydown" && !event.repeat && keyGen.get(keyId) === gen) {
        const timer = window.setInterval(() => {
          if (!socket || socket.readyState !== WebSocket.OPEN) return;
          socket.send(
            JSON.stringify({
              type: "hold_keepalive",
              payload: { code: event.code, profile_id: profile },
            }),
          );
        }, KEEPALIVE_MS);
        keyTimers.set(keyId, timer);
      }
    },
    async setProgramSpeed(value: number) {
      await httpCommand("set_program_speed", { value });
    },
    async setBpmSync(
      enabled: boolean,
      patch?: {
        mode?: "smooth" | "bar" | "beat";
        scope?: "preset" | "auto";
        palette?: import("../../vite-env").BpmPaletteId;
        cast?: "all" | "bars" | "heads";
        groups?: Array<"par" | "bar" | "beam">;
        excluded?: string[];
        character?: "smooth" | "rhythmic" | "impact";
        intensity?: "soft" | "normal" | "full";
        period?: "auto" | 0.5 | 1 | 2 | 4 | 8;
        parRhythm?: boolean;
        accent?: number;
      },
    ) {
      await httpCommand("bpm_sync", {
        enabled,
        ...(patch?.mode ? { mode: patch.mode } : {}),
        ...(patch?.scope ? { scope: patch.scope } : {}),
        ...(patch?.palette ? { palette: patch.palette } : {}),
        ...(patch?.cast ? { cast: patch.cast } : {}),
        ...(patch?.groups != null ? { groups: patch.groups } : {}),
        ...(patch?.excluded != null ? { excluded: patch.excluded } : {}),
        ...(patch?.character ? { character: patch.character } : {}),
        ...(patch?.intensity ? { intensity: patch.intensity } : {}),
        ...(patch?.period != null ? { period: patch.period } : {}),
        ...(patch?.parRhythm != null ? { par_rhythm: patch.parRhythm } : {}),
        ...(patch?.accent != null ? { accent: patch.accent } : {}),
      });
    },
    async nextAutoLook() {
      await httpCommand("auto_next_look", {});
    },
    async face(enabled: boolean, brightness?: number) {
      const previous = lastLive.face_on;
      optimisticFace = { value: enabled, at: Date.now() };
      emitLive({
        face_on: enabled,
        ...(brightness !== undefined ? { face_brightness: brightness } : {}),
      });
      try {
        await httpCommand("face", {
          enabled,
          ...(brightness !== undefined ? { brightness } : {}),
        });
      } catch (err) {
        optimisticFace = null;
        emitLive({ face_on: previous });
        throw err;
      }
    },
    async releaseSource() {
      await wsCommand("release_source", {}, "applied");
    },
    async selectPad(slot) {
      await httpCommand("select_pad", { pad_slot: slot });
    },
    strobe: (edge) => momentary("strobe_press", "strobe_release", "strobe", edge),
    sweep: (edge, mode = "horizontal") => {
      if (mode === "vertical") {
        return momentary(
          "vertical_sweep_press",
          "vertical_sweep_release",
          "vertical_sweep",
          edge,
        );
      }
      return momentary("sweep_press", "sweep_release", "sweep", edge);
    },
    async faceToggle() {
      await httpCommand("face_toggle");
    },
    async episodeNudge(delta) {
      const current = lastLive.episode_index ?? 0;
      const count = lastLive.episode_count ?? 0;
      const next = Math.max(0, count > 0 ? Math.min(count - 1, current + delta) : current + delta);
      const previous = lastLive.episode_index;
      optimisticEpisode = { value: next, at: Date.now() };
      emitLive({ episode_index: next });
      try {
        await httpCommand("episode_nudge", { delta });
      } catch (err) {
        optimisticEpisode = null;
        emitLive({ episode_index: previous });
        throw err;
      }
    },
    async seekEpisode(index: number) {
      await httpCommand("seek_episode", { episode_index: index });
    },
    async speed(kind, delta) {
      await httpCommand("speed", { kind, delta });
    },
    async activateScene(sceneId) {
      await httpCommand("activate_scene", { scene_id: sceneId });
    },
    async syncSceneRelease(sceneId: string, version?: number | null) {
      const payload: Record<string, unknown> = { scene_id: sceneId };
      if (version != null) payload.version = version;
      await httpCommand("sync_scene_release", payload);
    },
  };
}
