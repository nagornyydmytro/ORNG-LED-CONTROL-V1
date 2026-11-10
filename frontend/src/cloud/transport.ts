/** Shared control transport — local runtime or cloud device proxy. */

export type SyncBadge = "dirty" | "synced" | "update" | "conflict" | "error" | "unknown" | "local";

export interface ControlTransport {
  readonly kind: "local" | "cloud";
  blackout(enabled: boolean): Promise<void>;
  arm(): Promise<void>;
  disarm(): Promise<void>;
  selectPreset(presetId: string): Promise<void>;
  selectPad(slot: number): Promise<void>;
  strobe(edge: "press" | "release"): Promise<void>;
  sweep(edge: "press" | "release", mode?: "horizontal" | "vertical"): Promise<void>;
  faceToggle(): Promise<void>;
  episodeNudge(delta: number): Promise<void>;
  /** Nudge program or live-FX speed (cloud `speed` command). */
  speed(kind: "program" | "live_fx", delta: number): Promise<void>;
  activateScene(sceneId: string): Promise<void>;
}

export interface CloudDeviceSummary {
  id: string;
  name: string;
  platform: string;
  online: boolean;
  revoked: boolean;
  current_scene_id: string | null;
  current_release_version: number | null;
  armed: boolean;
  blackout: boolean;
  last_seen_at: string | null;
}

export type CloudDeviceLiveState = {
  online: boolean;
  armed: boolean;
  blackout: boolean;
  /** Admin WSS channel — independent of device online. */
  control_status?: "idle" | "connecting" | "open" | "reconnecting" | "closed";
  /** True when the last device snapshot is too old to treat as live. */
  state_stale?: boolean;
  /** Queued is still waiting. It is not an applied result. */
  command_status?: "idle" | "queued" | "applied" | "failed";
  /** Epoch ms of last authoritative armed/blackout/preset snapshot. */
  last_state_at?: number | null;
  transport?: string | null;
  current_scene_id?: string | null;
  current_release_version?: number | null;
  current_content_hash?: string | null;
  last_synced_hash?: string | null;
  last_synced_version?: number | null;
  desired_scene_version?: number | null;
  desired_content_hash?: string | null;
  sync_status?: string | null;
  strobe_held?: boolean;
  sweep_active?: boolean;
  vertical_sweep_active?: boolean;
  face_on?: boolean;
  preset_id?: string | null;
  pad_presets?: string[] | null;
  scene_name?: string | null;
  last_error?: string | null;
  /** Phase 3 app updater (from device heartbeat). */
  app_version?: string | null;
  build_id?: string | null;
  platform?: string | null;
  arch?: string | null;
  install_mode?: string | null;
  update_state?: string | null;
  update_progress?: number | null;
  last_update_error?: string | null;
  staged_app_version?: string | null;
  staged_build_id?: string | null;
  desired_app_version?: string | null;
  desired_build_id?: string | null;
  /** Phase 4 venue diagnostics (sanitized). */
  hardware_acceptance?: string | null;
  wifi_connected?: boolean | null;
  cloud_connected?: boolean | null;
  ethernet_link?: boolean | null;
  artnet_route_valid?: boolean | null;
  udp_active?: boolean | null;
  keyboard_connected?: boolean | null;
  keyboard_count?: number | null;
  episode_index?: number | null;
  episode_time_s?: number | null;
  episode_count?: number | null;
  preset_time_s?: number | null;
  cycle_duration_s?: number | null;
  face_brightness?: number | null;
  preview_speed?: number | null;
  bpm?: import("../vite-env").BpmSyncState | null;
  capabilities?: string[] | null;
  strobe_speed?: number | null;
  sweep_speed?: number | null;
  /** Fixture light from the executor. No cables and no Art-Net node. */
  stage?: import("../vite-env").SimulatorView | null;
  /** Epoch ms of the live-preview frame. The heartbeat does not set this. */
  preview_at?: number | null;
  disk_free_bytes?: number | null;
  cpu_temp_c?: number | null;
  beam_calibration?: import("../vite-env").BeamCalibrationState | null;
};

export interface CloudControlTransport extends ControlTransport {
  readonly kind: "cloud";
  readonly installationId: string;
  /** Client-side tab source hint (`cloud:web:<id>`). Worker assigns the live WSS source. */
  readonly sourceId: string;
  connect(): Promise<void>;
  dispose(): void;
  /** Pull/apply assigned release — distinct from activateScene. */
  syncSceneRelease(sceneId: string, version?: number | null): Promise<void>;
  onLiveState(cb: (state: CloudDeviceLiveState) => void): () => void;
  onCommandAck(
    cb: (ack: { command_id: string; status: string; type?: string; error?: string }) => void,
  ): () => void;
  setLiveFxSpeed(body: {
    strobe_speed?: number;
    sweep_speed?: number;
    persist?: boolean;
  }): Promise<void>;
  setProgramSpeed(value: number): Promise<void>;
  setBpmSync(
    enabled: boolean,
    patch?: {
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
    },
  ): Promise<void>;
  nextAutoLook(): Promise<void>;
  face(enabled: boolean, brightness?: number): Promise<void>;
  releaseSource(sourceId?: string): Promise<void>;
  seekEpisode(index: number): Promise<void>;
  keyboard(event: {
    code: string;
    type: "keydown" | "keyup";
    repeat: boolean;
    profile_id?: "qwerty" | "ch57x";
    source_id?: string;
  }): Promise<void>;
}
