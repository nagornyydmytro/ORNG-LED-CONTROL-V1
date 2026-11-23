import { computed, onMounted, onUnmounted, ref, shallowRef, watch, type Ref } from "vue";
import { adminGetLocationShowPrefs, adminPutLocationShowPrefs } from "../api/adminCloud";
import { fetchPresets } from "../api/client";
import { fetchAppConfig, saveAppConfig } from "../api/setup";
import type { CloudControlTransport, CloudDeviceLiveState } from "../cloud/transport";
import {
  beginBpmChange,
  bindBpmCommandId,
  confirmBpmFromTelemetry,
  finishBpmChange,
  settlePendingBpmCommands,
} from "../lib/bpmIntent";
import { createCommandId } from "../api/client";
import {
  asPeriod,
  bpmChoiceKey,
  bpmControlsLocked,
  readBpmChoice,
  writeBpmChoice,
} from "../lib/bpmPalettes";
import type { AppState, EngineState, OutputState, PresetInfo, SceneInfo, SimulatorView } from "../vite-env";
import { useKeyboardPad } from "./useKeyboardPad";
import type { ConnectionStatus } from "./useAppState";
import type { ToastApi } from "./useToasts";

export type BpmSyncPatch = {
  enabled?: boolean;
  mode?: "smooth" | "bar" | "beat";
  scope?: "preset" | "auto";
  palette?: import("../vite-env").BpmPaletteId;
  cast?: "all" | "bars" | "heads";
  groups?: Array<"par" | "bar" | "beam">;
  excluded?: string[];
  character?: "smooth" | "rhythmic" | "impact";
  intensity?: "soft" | "normal" | "full";
  period?: "auto" | 0.5 | 1 | 2 | 4 | 8;
  parRhythm?: boolean;
  accent?: number;
};

type BpmSyncNext = {
  enabled: boolean;
  mode: "smooth" | "bar" | "beat";
  scope: "preset" | "auto";
  palette: string;
  cast: "all" | "bars" | "heads";
  groups?: Array<"par" | "bar" | "beam">;
  excluded?: string[];
  character: "smooth" | "rhythmic" | "impact";
  intensity: "soft" | "normal" | "full";
  period: "auto" | 0.5 | 1 | 2 | 4 | 8 | string | number;
  parRhythm: boolean;
  accent: number;
};

/** Only the fields this desk changed, plus the snapshot it read for those fields. */
export function buildShowPrefsWrite(
  patch: BpmSyncPatch,
  next: BpmSyncNext,
  base: {
    mode?: string;
    scope?: string;
    palette?: string;
    cast?: string;
    groups?: Array<"par" | "bar" | "beam">;
    excluded?: string[];
    character?: string;
    intensity?: string;
    period?: string | number;
    par_rhythm?: boolean;
    accent?: number;
  } | null,
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  const seen: Record<string, unknown> = {};
  const put = (key: string, value: unknown, seenValue?: unknown) => {
    body[key] = value;
    if (base && seenValue !== undefined) seen[key] = seenValue;
  };
  if (patch.mode != null) put("mode", next.mode, base?.mode);
  if (patch.scope != null) put("scope", next.scope, base?.scope);
  if (patch.palette != null) put("palette", next.palette, base?.palette);
  if (patch.cast != null) put("cast", next.cast, base?.cast);
  if (patch.groups != null) put("groups", next.groups, base?.groups);
  if (patch.excluded != null) put("excluded", next.excluded, base?.excluded);
  if (patch.character != null) put("character", next.character, base?.character);
  if (patch.intensity != null) put("intensity", next.intensity, base?.intensity);
  if (patch.period != null) put("period", next.period, base?.period);
  if (patch.parRhythm != null) put("par_rhythm", next.parRhythm, base?.par_rhythm);
  if (patch.accent != null) put("accent", next.accent, base?.accent);
  if (base && Object.keys(seen).length > 0) body.base = seen;
  return body;
}

export function shouldApplyLocationPrefs(
  requestedLocationId: string,
  currentLocationId: string | null,
  requestedEpoch: number,
  currentEpoch: number,
): boolean {
  return requestedLocationId === currentLocationId && requestedEpoch === currentEpoch;
}

function engineFromLive(live: CloudDeviceLiveState | null): EngineState | null {
  if (!live) return null;
  return {
    preset_id: live.preset_id ?? "NONE",
    preset_time_s: live.preset_time_s ?? 0,
    episode_index: live.episode_index ?? 0,
    episode_time_s: live.episode_time_s ?? 0,
    episode_count: live.episode_count ?? 0,
    cycle_duration_s: live.cycle_duration_s ?? 180,
    blackout: live.blackout,
    face_on: Boolean(live.face_on),
    face_brightness: live.face_brightness ?? 1,
    strobe_held: Boolean(live.strobe_held),
    white_hit_active: false,
    master_brightness: live.master_brightness ?? 1,
    time_s: live.preset_time_s ?? 0,
    color_hit_active: false,
    sweep_active: Boolean(live.sweep_active),
    vertical_sweep_active: Boolean(live.vertical_sweep_active),
    strobe_speed: live.strobe_speed ?? undefined,
    sweep_speed: live.sweep_speed ?? undefined,
  };
}

function simulatorFromLive(live: CloudDeviceLiveState | null): SimulatorView {
  const stage = live?.stage;
  if (
    stage &&
    Array.isArray(stage.pars) &&
    (stage.pars.length > 0 || stage.bars.length > 0 || stage.beams.length > 0 || stage.faces.length > 0)
  ) {
    return stage;
  }
  return {
    pars: [],
    bars: [],
    beams: [],
    faces: [],
    nonzero_channels: 0,
    blackout_visual: live?.blackout ?? true,
  };
}

function outputFromLive(live: CloudDeviceLiveState | null): OutputState | null {
  if (!live) return null;
  return {
    transport: live.transport ?? "artnet",
    preferred_transport: live.transport ?? "artnet",
    armed: live.armed,
    last_error: live.last_error ?? null,
    frames_sent: -1,
    network_allowed: live.udp_active === true,
    udp_active: live.udp_active === true,
    target_ip: null,
    universe: 0,
    frame_sum: 0,
    nonzero_channels: 0,
  };
}

export function useCloudAppState(
  transport: Ref<CloudControlTransport | null>,
  toasts: ToastApi,
  location: Ref<{ id: string; name: string; sceneId: string | null; sceneName: string | null } | null>,
) {
  const live = shallowRef<CloudDeviceLiveState | null>(null);
  const state = computed<AppState | null>(() => {
    const engine = engineFromLive(live.value);
    const output = outputFromLive(live.value);
    if (!engine || !output) return null;
    return {
      engine,
      output,
      presets: [...availableIds.value],
      fixture_ids: [],
      frame: [],
      sequence: 0,
      preview_speed: live.value?.preview_speed ?? 1,
      pad_presets:
        live.value?.pad_presets && live.value.pad_presets.length > 0
          ? live.value.pad_presets
          : scenePad.value,
      strobe_speed: live.value?.strobe_speed ?? undefined,
      sweep_speed: live.value?.sweep_speed ?? undefined,
      simulator: simulatorFromLive(live.value),
      preview_at: live.value?.preview_at ?? null,
      scene_id: live.value?.current_scene_id ?? location.value?.sceneId ?? null,
      scene_name: live.value?.scene_name ?? location.value?.sceneName ?? null,
      location_id: location.value?.id ?? null,
      beam_calibration: live.value?.beam_calibration ?? null,
      bpm: live.value?.bpm ?? null,
      capabilities: live.value?.capabilities ?? null,
      executor_online: live.value?.online === true,
    };
  });
  const engine = computed(() => state.value?.engine ?? null);
  const output = computed(() => state.value?.output ?? null);
  const availablePresets = ref<PresetInfo[]>([]);
  const scenePad = ref<string[]>([]);
  const availableIds = computed(() => {
    const ids = new Set(availablePresets.value.map((preset) => preset.id));
    ids.add("NONE");
    return ids;
  });
  const availableScenes = computed<SceneInfo[]>(() => {
    const id = location.value?.sceneId;
    if (!id) return [];
    return [
      {
        scene_id: id,
        name: location.value?.sceneName || id,
        active: true,
      },
    ];
  });
  const sceneId = computed(
    () => live.value?.current_scene_id ?? location.value?.sceneId ?? "",
  );
  const sceneName = computed(
    () => live.value?.scene_name ?? location.value?.sceneName ?? sceneId.value,
  );
  const connection = ref<ConnectionStatus>("connecting");
  const loading = ref(true);
  const lastError = ref<string | null>(null);
  const sceneSwitching = ref(false);
  let unsubLive: (() => void) | null = null;
  let unsubAck: (() => void) | null = null;
  let generation = 0;
  let prefsEpoch = 0;
  let prefsBase: {
    mode: "smooth" | "bar" | "beat";
    scope: "preset" | "auto";
    palette: string;
    cast?: "all" | "bars" | "heads";
    groups?: Array<"par" | "bar" | "beam">;
    excluded?: string[];
    character?: "smooth" | "rhythmic" | "impact";
    intensity?: "soft" | "normal" | "full";
    period?: string | number;
    par_rhythm?: boolean;
    accent?: number;
    updated_at: string;
  } | null = null;

  function sessionToken(): { gen: number; locationId: string | null } {
    return { gen: generation, locationId: location.value?.id ?? null };
  }

  function still(token: { gen: number; locationId: string | null }): boolean {
    return token.gen === generation && (location.value?.id ?? null) === token.locationId;
  }

  function connectionFrom(next: CloudDeviceLiveState): ConnectionStatus {
    const channel = next.control_status;
    if (channel === "reconnecting" || channel === "connecting") return "reconnecting";
    if (channel !== "open") return "offline";
    return "online";
  }

  let localFx: { strobe?: number; sweep?: number; until: number } | null = null;
  let strobeOrigin: number | null = null;
  let sweepOrigin: number | null = null;
  let strobeRest: number | null = null;
  let sweepRest: number | null = null;

  function applyLive(
    next: CloudDeviceLiveState,
    token?: { gen: number; locationId: string | null },
  ) {
    if (token && !still(token)) return;
    const merged = { ...live.value, ...next };
    if (localFx && Date.now() < localFx.until) {
      if (localFx.strobe !== undefined && strobeRest == null) merged.strobe_speed = localFx.strobe;
      if (localFx.sweep !== undefined && sweepRest == null) merged.sweep_speed = localFx.sweep;
    } else {
      localFx = null;
    }
    if (strobeRest != null) {
      if (typeof next.strobe_speed === "number" && Math.abs(next.strobe_speed - strobeRest) < 0.03) {
        strobeRest = null;
      } else {
        merged.strobe_speed = strobeRest;
      }
    }
    if (sweepRest != null) {
      if (typeof next.sweep_speed === "number" && Math.abs(next.sweep_speed - sweepRest) < 0.03) {
        sweepRest = null;
      } else {
        merged.sweep_speed = sweepRest;
      }
    }
    live.value = merged;
    if (merged.bpm) {
      confirmBpmFromTelemetry(
        bpmChoiceKey({ locationId: location.value?.id, sceneId: location.value?.sceneId }),
        {
          enabled: merged.bpm.enabled === true,
          mode: merged.bpm.mode,
          scope: merged.bpm.scope,
          palette: merged.bpm.palette,
          cast: merged.bpm.cast,
          groups: merged.bpm.groups,
          excluded: merged.bpm.excluded,
          character: merged.bpm.character,
          intensity: merged.bpm.intensity,
          period: merged.bpm.period,
          parRhythm: merged.bpm.par_rhythm,
          accent: merged.bpm.accent,
          revision: merged.bpm.revision,
          session: merged.bpm.session,
          epoch: merged.bpm.epoch,
        },
      );
    }
    connection.value = connectionFrom(live.value);
  }

  async function refreshRest() {
    const token = sessionToken();
    try {
      const presets = await fetchPresets();
      const app = await fetchAppConfig();
      if (!still(token)) return;
      lastError.value = null;
      availablePresets.value = presets;
      if (Array.isArray(app.pad_presets)) {
        scenePad.value = app.pad_presets.map((id) => String(id));
      }
    } catch (err) {
      if (!still(token)) return;
      lastError.value = err instanceof Error ? err.message : "Сцену не прочитано";
    }
  }

  function noteError(err: unknown) {
    const message = err instanceof Error ? err.message : "Команду не застосовано";
    lastError.value = message;
    toasts.push(message, "error");
  }

  function noteBpmError(err: unknown) {
    const raw = err instanceof Error ? err.message : "";
    if (raw === "bpm_sync" || raw === "not_allowlisted" || raw.includes("bpm_sync")) {
      noteError(
        new Error("Встановлений плеєр ще не приймає BPM. Потрібне оновлення Raspberry."),
      );
      return;
    }
    noteError(err);
  }

  async function selectPreset(presetId: string) {
    const token = sessionToken();
    const current = transport.value;
    if (!current) {
      noteError(new Error("Виконавця немає"));
      throw new Error("Виконавця немає");
    }
    try {
      await current.selectPreset(presetId);
    } catch (err) {
      if (!still(token)) return;
      noteError(err);
      throw err;
    }
  }

  async function seekEpisode(episodeIndex: number) {
    const token = sessionToken();
    const current = transport.value;
    if (!current) {
      noteError(new Error("Виконавця немає"));
      throw new Error("Виконавця немає");
    }
    try {
      await current.seekEpisode(episodeIndex);
    } catch (err) {
      if (!still(token)) return;
      noteError(err);
      throw err;
    }
  }

  async function nudgeEpisode(delta: number) {
    const token = sessionToken();
    const current = transport.value;
    if (!current) {
      noteError(new Error("Виконавця немає"));
      throw new Error("Виконавця немає");
    }
    try {
      await current.episodeNudge(delta >= 0 ? 1 : -1);
    } catch (err) {
      if (!still(token)) return;
      noteError(err);
      throw err;
    }
  }

  function strobePress() {
    const token = sessionToken();
    strobeRest = null;
    strobeOrigin = live.value?.strobe_speed ?? 0.1;
    void transport.value?.strobe("press").catch((err) => {
      if (still(token)) noteError(err);
    });
  }
  function strobeRelease() {
    const token = sessionToken();
    const rest = strobeOrigin ?? live.value?.strobe_speed ?? 0.1;
    strobeOrigin = null;
    strobeRest = rest;
    if (localFx) {
      localFx = localFx.sweep !== undefined ? { sweep: localFx.sweep, until: localFx.until } : null;
    }
    if (live.value) live.value = { ...live.value, strobe_speed: rest };
    void transport.value?.strobe("release").catch((err) => {
      if (still(token)) noteError(err);
    });
  }
  function sweepPress(mode: "horizontal" | "vertical" = "horizontal") {
    const token = sessionToken();
    sweepRest = null;
    sweepOrigin = live.value?.sweep_speed ?? 0.1;
    void transport.value?.sweep("press", mode).catch((err) => {
      if (still(token)) noteError(err);
    });
  }
  function sweepRelease(mode: "horizontal" | "vertical" = "horizontal") {
    const token = sessionToken();
    const rest = sweepOrigin ?? live.value?.sweep_speed ?? 0.1;
    sweepOrigin = null;
    sweepRest = rest;
    if (localFx) {
      localFx = localFx.strobe !== undefined ? { strobe: localFx.strobe, until: localFx.until } : null;
    }
    if (live.value) live.value = { ...live.value, sweep_speed: rest };
    void transport.value?.sweep("release", mode).catch((err) => {
      if (still(token)) noteError(err);
    });
  }
  function verticalSweepPress() {
    sweepPress("vertical");
  }
  function verticalSweepRelease() {
    sweepRelease("vertical");
  }
  function releaseAllSweeps() {
    sweepRelease("horizontal");
    sweepRelease("vertical");
    strobeRelease();
  }

  async function setBlackout(enabled: boolean) {
    const token = sessionToken();
    const current = transport.value;
    if (!current) {
      noteError(new Error("Виконавця немає"));
      throw new Error("Виконавця немає");
    }
    try {
      await current.blackout(enabled);
    } catch (err) {
      if (!still(token)) return;
      noteError(err);
      throw err;
    }
  }

  async function toggleBlackout() {
    await setBlackout(!(engine.value?.blackout ?? true));
  }

  async function setFace(enabled: boolean, brightness?: number) {
    const token = sessionToken();
    const current = transport.value;
    if (!current) {
      noteError(new Error("Виконавця немає"));
      throw new Error("Виконавця немає");
    }
    try {
      await current.face(enabled, brightness);
    } catch (err) {
      if (!still(token)) return;
      noteError(err);
      throw err;
    }
  }

  async function setMasterBrightness(value: number) {
    const token = sessionToken();
    const current = transport.value;
    if (!current) {
      noteError(new Error("Виконавця немає"));
      return;
    }
    try {
      await current.setMasterBrightness(value);
    } catch (err) {
      if (!still(token)) return;
      noteError(err);
    }
  }

  function sendHeadDesk(payload: {
    enabled: boolean;
    light: boolean;
    scene: boolean;
    color: string;
    x: number;
    y: number;
  }) {
    const current = transport.value;
    if (!current) return;
    current.sendHeadDesk(payload);
  }

  async function setPreviewSpeed(value: number) {
    const token = sessionToken();
    if (bpmControlsLocked(state.value?.bpm)) return;
    const current = transport.value;
    if (!current) {
      noteError(new Error("Виконавця немає"));
      return;
    }
    try {
      await current.setProgramSpeed(value);
    } catch (err) {
      if (!still(token)) return;
      noteError(err);
    }
  }

  async function nextAutoLook(): Promise<void> {
    const current = transport.value;
    if (!current) {
      noteError(new Error("Виконавця немає"));
      throw new Error("Виконавця немає");
    }
    try {
      await current.nextAutoLook();
    } catch (err) {
      noteError(err);
      throw err;
    }
  }

  async function setBpmSync(patch: {
    enabled?: boolean;
    mode?: "smooth" | "bar" | "beat";
    scope?: "preset" | "auto";
    palette?: import("../vite-env").BpmPaletteId;
    cast?: "all" | "bars" | "heads";
    groups?: Array<"par" | "bar" | "beam">;
    excluded?: string[];
    character?: "smooth" | "rhythmic" | "impact";
    intensity?: "soft" | "normal" | "full";
    period?: "auto" | 0.5 | 1 | 2 | 4 | 8;
    parRhythm?: boolean;
    accent?: number;
  }): Promise<boolean> {
    const token = sessionToken();
    const current = live.value?.bpm;
    const key = bpmChoiceKey({
      locationId: state.value?.location_id,
      sceneId: state.value?.scene_id,
    });
    const staged = beginBpmChange(key, patch, {
      enabled: current?.enabled === true,
      mode: current?.mode,
      scope: current?.scope,
      palette: current?.palette,
      cast: current?.cast,
      groups: current?.groups,
      excluded: current?.excluded,
      character: current?.character,
      intensity: current?.intensity,
      period: current?.period,
      parRhythm: current?.par_rhythm,
      accent: current?.accent,
      revision: current?.revision,
      session: current?.session,
      epoch: current?.epoch,
    });
    const musical =
      patch.mode != null ||
      patch.scope != null ||
      patch.palette != null ||
      patch.cast != null ||
      patch.groups != null ||
      patch.excluded != null ||
      patch.character != null ||
      patch.intensity != null ||
      patch.period != null ||
      patch.parRhythm != null ||
      patch.accent != null;
    const locationId = location.value?.id;
    if (musical && locationId) {
      const applied = staged.command;
      const body = buildShowPrefsWrite(patch, {
        enabled: applied.enabled,
        mode: applied.mode,
        scope: applied.scope,
        palette: applied.palette,
        cast: applied.cast,
        groups: applied.groups,
        excluded: applied.excluded,
        character: applied.character,
        intensity: applied.intensity,
        period: applied.period,
        parRhythm: applied.parRhythm,
        accent: applied.accent,
      }, prefsBase) as Parameters<typeof adminPutLocationShowPrefs>[1];
      const requestedEpoch = prefsEpoch;
      void Promise.race([
        adminPutLocationShowPrefs(locationId, body),
        new Promise<never>((_, reject) => {
          window.setTimeout(() => reject(new Error("Збереження налаштувань не відповіло")), 4000);
        }),
      ])
        .then((savedPrefs) => {
          if (!shouldApplyLocationPrefs(locationId, location.value?.id ?? null, requestedEpoch, prefsEpoch)) return;
          if (!savedPrefs.prefs) return;
          prefsBase = savedPrefs.prefs;
        })
        .catch((err) => {
          if (!still(token)) return;
          noteBpmError(err);
        });
    }
    const currentTransport = transport.value;
    if (!currentTransport) {
      finishBpmChange(key, staged.fieldRevs, false, "Виконавця немає");
      noteError(new Error("Виконавця немає"));
      return false;
    }
    const commandId = createCommandId("bpm-sync");
    bindBpmCommandId(key, staged.fieldRevs, commandId);
    try {
      const applied = staged.command;
      await currentTransport.setBpmSync(applied.enabled, {
        mode: applied.mode,
        scope: applied.scope,
        palette: applied.palette,
        cast: applied.cast,
        groups: applied.groups,
        excluded: applied.excluded,
        character: applied.character,
        intensity: applied.intensity,
        period: asPeriod(applied.period, applied.character),
        parRhythm: applied.parRhythm,
        accent: applied.accent,
        intentMs: staged.intentMs,
        commandId,
      });
      if (!still(token)) return false;
      finishBpmChange(key, staged.fieldRevs, true);
      return true;
    } catch (err) {
      if (!still(token)) return false;
      finishBpmChange(
        key,
        staged.fieldRevs,
        false,
        err instanceof Error ? err.message : "Не підтверджено",
      );
      noteBpmError(err);
      return false;
    }
  }

  async function settleBpmAfterReload() {
    const currentTransport = transport.value;
    if (!currentTransport) return;
    const key = bpmChoiceKey({
      locationId: location.value?.id ?? state.value?.location_id,
      sceneId: location.value?.sceneId ?? state.value?.scene_id,
    });
    await settlePendingBpmCommands(key, async (command) => {
      await currentTransport.setBpmSync(command.body.enabled, {
        mode: command.body.mode,
        scope: command.body.scope,
        palette: command.body.palette as import("../vite-env").BpmPaletteId,
        cast: command.body.cast,
        groups: command.body.groups,
        excluded: command.body.excluded,
        character: command.body.character,
        intensity: command.body.intensity,
        period: asPeriod(command.body.period, command.body.character),
        parRhythm: command.body.parRhythm,
        accent: command.body.accent,
        intentMs: command.intentMs,
        commandId: command.commandId,
      });
      return { ok: true };
    });
  }

  async function colorHit() {
    toasts.push("Color hit лишається локальною дією сцени", "info");
  }

  async function setLiveFxSpeeds(payload: { strobe_speed?: number; sweep_speed?: number }) {
    const token = sessionToken();
    if (payload.strobe_speed !== undefined) strobeRest = null;
    if (payload.sweep_speed !== undefined) sweepRest = null;
    localFx = {
      strobe: payload.strobe_speed,
      sweep: payload.sweep_speed,
      until: Date.now() + 1500,
    };
    if (live.value) {
      live.value = {
        ...live.value,
        strobe_speed: payload.strobe_speed ?? live.value.strobe_speed,
        sweep_speed: payload.sweep_speed ?? live.value.sweep_speed,
      };
    }
    try {
      await transport.value?.setLiveFxSpeed({ ...payload, persist: false });
    } catch (err) {
      if (!still(token)) return;
      localFx = null;
      noteError(err);
    }
  }

  async function setPadPresets(presetIds: string[]) {
    const token = sessionToken();
    const app = await fetchAppConfig();
    if (!still(token)) return;
    await saveAppConfig({ ...app, pad_presets: presetIds });
    if (!still(token)) return;
    scenePad.value = presetIds;
  }

  async function switchScene(nextSceneId: string) {
    const token = sessionToken();
    sceneSwitching.value = true;
    try {
      const current = transport.value;
      if (!current) throw new Error("Виконавця немає");
      await current.activateScene(nextSceneId);
    } catch (err) {
      if (still(token)) noteError(err);
    } finally {
      if (still(token)) sceneSwitching.value = false;
    }
  }

  function liveView() {
    return (
      state.value?.simulator ?? {
        pars: [],
        bars: [],
        beams: [],
        faces: [],
        nonzero_channels: 0,
        blackout_visual: true,
      }
    );
  }

  function liveFrame(): number[] {
    return [];
  }

  function bind(next: CloudControlTransport | null) {
    generation += 1;
    const token = sessionToken();
    unsubLive?.();
    unsubAck?.();
    unsubLive = null;
    unsubAck = null;
    localFx = null;
    strobeOrigin = null;
    sweepOrigin = null;
    strobeRest = null;
    sweepRest = null;
    live.value = null;
    availablePresets.value = [];
    scenePad.value = [];
    if (!next) {
      connection.value = "offline";
      loading.value = false;
      return;
    }
    connection.value = "connecting";
    unsubLive = next.onLiveState((state) => applyLive(state, token));
    unsubAck = next.onCommandAck((ack) => {
      if (!still(token)) return;
      if (ack.status === "queued") {
        lastError.value = "Команду поставлено в чергу. Зал її ще не застосував.";
        return;
      }
      if (ack.status === "applied" && lastError.value?.startsWith("Команду поставлено в чергу")) {
        lastError.value = null;
        return;
      }
      if (ack.status === "failed" || ack.status === "unknown") {
        const rejected = new Error(ack.error || "Команду не підтверджено");
        if (ack.type === "bpm_sync") noteBpmError(rejected);
        else noteError(rejected);
      }
    });
    void next
      .connect()
      .then(async () => {
        if (!still(token)) return;
        await settleBpmAfterReload();
      })
      .catch((err) => {
        if (!still(token)) return;
        noteError(err);
      });
  }

  watch(
    () => [location.value?.id ?? null, location.value?.sceneId ?? null] as const,
    ([id, scene], previous) => {
      if (previous && (previous[0] !== id || previous[1] !== scene)) {
        live.value = null;
        bind(transport.value);
      }
      prefsBase = null;
      prefsEpoch += 1;
      const requestedEpoch = prefsEpoch;
      if (!id) return;
      void adminGetLocationShowPrefs(id)
        .then((body) => {
          if (!shouldApplyLocationPrefs(id, location.value?.id ?? null, requestedEpoch, prefsEpoch)) return;
          if (!body.prefs) return;
          prefsBase = body.prefs;
          writeBpmChoice(
            {
              ...readBpmChoice(bpmChoiceKey({ locationId: id })),
              mode: body.prefs.mode,
              scope: body.prefs.scope,
              palette: body.prefs.palette as ReturnType<typeof readBpmChoice>["palette"],
              cast: (body.prefs.cast ?? "all") as ReturnType<typeof readBpmChoice>["cast"],
              character: (body.prefs.character ?? "rhythmic") as ReturnType<
                typeof readBpmChoice
              >["character"],
              intensity: (body.prefs.intensity ?? "full") as ReturnType<
                typeof readBpmChoice
              >["intensity"],
              period:
                body.prefs.period === "auto" || body.prefs.period == null
                  ? "auto"
                  : (Number(body.prefs.period) as ReturnType<typeof readBpmChoice>["period"]),
              parRhythm: body.prefs.par_rhythm === true,
              accent: typeof body.prefs.accent === "number" ? body.prefs.accent : 0,
            },
            bpmChoiceKey({ locationId: id }),
          );
        })
        .catch((err) => {
          if (!shouldApplyLocationPrefs(id, location.value?.id ?? null, requestedEpoch, prefsEpoch)) return;
          lastError.value =
            err instanceof Error ? err.message : "Налаштування локації не прочитано";
        });
    },
    { immediate: true },
  );

  watch(
    transport,
    (next) => {
      bind(next);
      void refreshRest();
    },
    { immediate: true },
  );

  watch(connection, (value) => {
    if (value !== "online" && value !== "reconnecting") {
      window.dispatchEvent(new CustomEvent("orng-release-holds"));
    }
  });

  useKeyboardPad({
    enabled: () => connection.value === "online" || connection.value === "reconnecting",
    onState: () => undefined,
    onIgnored: (reason) => noteError(new Error(reason)),
    dispatch: async (event) => {
      const current = transport.value;
      if (!current) return;
      await current.keyboard({
        code: event.code,
        type: event.type,
        repeat: event.repeat,
        profile_id: event.profile_id,
      });
    },
  });

  function releaseThisSource() {
    const current = transport.value;
    if (!current) return;
    void current.releaseSource(current.sourceId).catch((err) => {
      lastError.value = err instanceof Error ? err.message : "Утримання не знято";
    });
    releaseAllSweeps();
  }

  function onVisibility() {
    if (document.visibilityState === "hidden") releaseThisSource();
  }

  onMounted(async () => {
    window.addEventListener("blur", releaseThisSource);
    document.addEventListener("visibilitychange", onVisibility);
    await refreshRest();
    loading.value = false;
  });

  onUnmounted(() => {
    window.removeEventListener("blur", releaseThisSource);
    document.removeEventListener("visibilitychange", onVisibility);
    unsubLive?.();
    unsubAck?.();
    const current = transport.value;
    if (!current) return;
    void current.releaseSource(current.sourceId).finally(() => current.dispose());
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
    sendHeadDesk,
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
