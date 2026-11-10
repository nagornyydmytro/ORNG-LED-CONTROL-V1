/** Versioned cloud ↔ device protocol (no Art-Net on the wire). */

export const PROTOCOL_VERSION = 1;

/** Commands the cloud may send to a device. Anything else is rejected. */
export const ALLOWED_COMMAND_TYPES = [
  "request_state",
  "activate_scene",
  "sync_scene_release",
  "select_preset",
  "select_pad",
  "blackout",
  "arm",
  "disarm",
  "strobe_press",
  "strobe_release",
  "sweep_press",
  "sweep_release",
  "vertical_sweep_press",
  "vertical_sweep_release",
  "face_toggle",
  "face",
  "episode_nudge",
  "seek_episode",
  "speed",
  "set_program_speed",
  "bpm_sync",
  "auto_next_look",
  "set_live_fx_speed",
  "keyboard",
  "release_source",
  "hold_keepalive",
  // Phase 3 APPLICATION updates (control-plane only — device applies locally)
  "check_app_update",
  "stage_app_update",
  "apply_app_update",
  "cancel_staged_update",
  "rollback_app_update",
  // Phase 4 operational (no reboot / shell / apt)
  "request_diagnostics",
  "restart_runtime",
  "calibration_begin",
  "calibration_set",
  "calibration_visible",
  "calibration_home",
  "calibration_end",
] as const;

export type AllowedCommandType = (typeof ALLOWED_COMMAND_TYPES)[number];

/** Momentary press commands must NOT be replayed after reconnect. */
export const MOMENTARY_PRESS_TYPES: ReadonlySet<string> = new Set([
  "strobe_press",
  "sweep_press",
  "vertical_sweep_press",
]);

/** Physical calibration is not replayed after reconnect. */
export const CALIBRATION_COMMAND_TYPES: ReadonlySet<string> = new Set([
  "calibration_begin",
  "calibration_set",
  "calibration_visible",
  "calibration_home",
  "calibration_end",
]);

export const CALIBRATION_LEASE_MS = 20_000;

export const MOMENTARY_RELEASE_TYPES: ReadonlySet<string> = new Set([
  "strobe_release",
  "sweep_release",
  "vertical_sweep_release",
]);

export type AckStatus = "accepted" | "applied" | "failed";

export interface EnvelopeBase {
  protocol_version: number;
  message_id: string;
  type: string;
  device_id: string;
  sent_at: string;
  payload: Record<string, unknown>;
}

export interface CommandEnvelope extends EnvelopeBase {
  type: AllowedCommandType;
  payload: {
    command_id: string;
    idempotent?: boolean;
    ttl_ms?: number;
    /** Cloud hold lease for momentary FX. */
    hold?: {
      source: string;
      hold_id: string;
      lease_ms: number;
    };
    [key: string]: unknown;
  };
}

export interface AckEnvelope extends EnvelopeBase {
  type: "ack";
  payload: {
    command_id: string;
    status: AckStatus;
    error_code?: string;
    error_message?: string;
  };
}

export interface HeartbeatEnvelope extends EnvelopeBase {
  type: "heartbeat";
  payload: {
    uptime_s?: number;
    armed?: boolean;
    blackout?: boolean;
    transport?: string;
    udp_active?: boolean;
    current_scene_id?: string;
    current_release_version?: number;
    current_content_hash?: string;
    last_synced_hash?: string;
    last_synced_version?: number;
    desired_scene_version?: number;
    desired_content_hash?: string;
    sync_status?: string;
    last_error?: string | null;
    app_version?: string;
    build_id?: string;
    /** Persisted on installations.arch — required for app assign/download. */
    arch?: string;
    install_mode?: string;
    updater_protocol_version?: number;
    /** idle | checking | staging | staged | applying | failed | rolled_back */
    app_update_status?: string;
    staged_app_version?: string | null;
    staged_build_id?: string | null;
  };
}

/** Device → cloud: terminal (or staged) app-update outcome with original command_id. */
export interface UpdateResultEnvelope extends EnvelopeBase {
  type: "update_result";
  payload: {
    command_id: string;
    status: "applied" | "failed" | "rolled_back" | "staged" | "cancelled" | string;
    detail?: Record<string, unknown>;
    error_code?: string;
    error_message?: string;
  };
}

export interface StateEnvelope extends EnvelopeBase {
  type: "state";
  payload: Record<string, unknown>;
}

export type DeviceToCloud =
  | AckEnvelope
  | HeartbeatEnvelope
  | UpdateResultEnvelope
  | StateEnvelope
  | EnvelopeBase;

export function isAllowedCommandType(type: string): type is AllowedCommandType {
  return (ALLOWED_COMMAND_TYPES as readonly string[]).includes(type);
}

/** A press, a key, or a calibration step. Replaying it after reconnect fires it again. */
export function isEphemeralCommand(type: string): boolean {
  return (
    MOMENTARY_PRESS_TYPES.has(type) ||
    MOMENTARY_RELEASE_TYPES.has(type) ||
    CALIBRATION_COMMAND_TYPES.has(type) ||
    type === "keyboard" ||
    type === "hold_keepalive" ||
    type === "face_toggle" ||
    type === "release_source"
  );
}

/** A location desk must not drive updates, restart, or output arming. */
export const DESK_SERVICE_COMMANDS: ReadonlySet<string> = new Set([
  "arm",
  "disarm",
  "check_app_update",
  "stage_app_update",
  "apply_app_update",
  "cancel_staged_update",
  "rollback_app_update",
  "request_diagnostics",
  "restart_runtime",
]);

export const DEFAULT_COMMAND_TTL_MS = 30_000;
export const HOLD_LEASE_MS = 8_000;
export const HOLD_KEEPALIVE_MS = 3_000;

/** Reconnect guidance for device agents (documented + returned on close). */
export const RECONNECT_GUIDANCE = {
  initial_ms: 500,
  max_ms: 30_000,
  jitter_ratio: 0.2,
  formula: "min(max_ms, initial_ms * 2^attempt) * (1 ± jitter_ratio)",
} as const;
