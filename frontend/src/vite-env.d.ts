/// <reference types="vite/client" />
/// <reference types="vitest/globals" />

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  transport: string;
  output_armed: boolean;
  artnet_network_enabled: boolean;
  frontend_dist_present: boolean;
  ready?: boolean;
}

export interface EngineState {
  preset_id: string;
  preset_time_s: number;
  episode_index: number;
  episode_time_s: number;
  episode_count: number;
  cycle_duration_s: number;
  blackout: boolean;
  face_on: boolean;
  face_brightness: number;
  strobe_held: boolean;
  white_hit_active: boolean;
  master_brightness: number;
  time_s: number;
  drop_active?: boolean;
  color_hit_active?: boolean;
  sweep_active?: boolean;
  vertical_sweep_active?: boolean;
  strobe_speed?: number;
  sweep_speed?: number;
}

export interface OutputState {
  transport: string;
  preferred_transport?: string;
  armed: boolean;
  last_error: string | null;
  frames_sent: number;
  network_allowed: boolean;
  udp_active?: boolean;
  target_ip: string | null;
  universe: number;
  /** Wire (outbound) frame counters — same as wire_frame_sum. */
  frame_sum?: number;
  nonzero_channels?: number;
  source_frame_sum?: number;
  source_nonzero_channels?: number;
  wire_frame_sum?: number;
  wire_nonzero_channels?: number;
  source_owner?: "raw_tester" | "engine" | "none" | "beam_calibration_test";
}

export interface ParFixtureView {
  id: string;
  label: string;
  kind: string;
  side: string;
  ring: string;
  face: boolean;
  order?: number | null;
  r: number;
  g: number;
  b: number;
  w: number;
  intensity: number;
}

export interface BarFixtureView {
  id: string;
  label: string;
  kind: string;
  side: string;
  ring: string;
  order?: number | null;
  dimmer: number;
  r: number;
  g: number;
  b: number;
  segments: number[];
}

export interface BeamFixtureView {
  id: string;
  label: string;
  kind: string;
  side: string;
  order?: number | null;
  pan: number;
  tilt: number;
  /** Physical (post-calibration) normalized pan/tilt actually on the wire. */
  physical_pan?: number;
  physical_tilt?: number;
  dimmer: number;
  shutter_open: boolean;
  r: number;
  g: number;
  b: number;
  strobe: number;
  dir_x: number;
  dir_y: number;
  dir_z: number;
  hit_x: number;
  hit_z: number;
  throw: number;
  /** Physical rig mount from layout.yaml (e.g. "ceiling", "truss"). */
  mount?: string;
  /** Whether beam_calibration_confirmed is set for this head. */
  calibration_confirmed?: boolean;
  /** Human-readable reason the physical Beam output is blocked, if any. */
  calibration_blocker?: string | null;
}

export interface BeamCalibrationRoles {
  pan_coarse: number | null;
  pan_fine: number | null;
  tilt_coarse: number | null;
  tilt_fine: number | null;
}

export interface BeamCalibrationEncoded {
  pan_coarse: number;
  pan_fine: number | null;
  tilt_coarse: number;
  tilt_fine: number | null;
  pan_16bit: number | null;
  tilt_16bit: number | null;
}

export interface BeamCalibrationBeamView {
  fixture_id: string;
  label: string;
  side: string;
  start_address: number;
  mount: string | null;
  calibration_confirmed: boolean;
  pan_invert: boolean;
  tilt_invert: boolean;
  pan_offset: number;
  tilt_offset: number;
  pan_min: number;
  pan_max: number;
  tilt_min: number;
  tilt_max: number;
  home_pan: number;
  home_tilt: number;
  max_pan_speed: number;
  max_tilt_speed: number;
  notes: string | null;
  semantic_pan: number;
  semantic_tilt: number;
  physical_pan: number;
  physical_tilt: number;
  roles: BeamCalibrationRoles;
  encoded: BeamCalibrationEncoded;
  physical_output_blocker: string | null;
}

export interface BeamCalibrationSessionView {
  active: boolean;
  fixture_id: string | null;
  semantic_pan: number;
  semantic_tilt: number;
  visible_beam_requested: boolean;
  visible_beam_confirmed: boolean;
  visible_beam_on: boolean;
  nonzero_channels: number;
  last_error: string | null;
  visible_beam_blockers?: string[];
}

export interface BeamCalibrationState {
  session: BeamCalibrationSessionView;
  beams: BeamCalibrationBeamView[];
  source_nonzero_channels: number;
  wire_nonzero_channels: number;
  source_owner: string;
}

export interface StagePlacement {
  fixture_id: string;
  kind: string;
  label?: string;
  groups?: string[];
  x: number;
  y: number;
  z: number;
  width: number;
  height: number;
  rotation_deg: number;
  orientation: "vertical" | "horizontal" | "point";
  mount: string;
  aim_x: number;
  aim_y: number;
  aim_z: number;
  zone?: string | null;
  notes?: string | null;
}

export interface StageLayout {
  description: string;
  placements: StagePlacement[];
  cable_chain: string[];
  artnet_node: StagePlacement | null;
  hardware_verified: boolean;
  notes: string;
}

export interface PreviewClip {
  preset_id: string;
  fps: number;
  seconds: number;
  transport: string;
  safe_mock_preview: boolean;
  frames: SimulatorView[];
}

export interface SimulatorView {
  pars: ParFixtureView[];
  bars: BarFixtureView[];
  beams: BeamFixtureView[];
  faces: ParFixtureView[];
  nonzero_channels: number;
  blackout_visual: boolean;
}

export interface AppState {
  engine: EngineState;
  output: OutputState;
  presets: string[];
  fixture_ids: string[];
  frame: number[];
  sequence: number;
  preview_speed: number;
  pad_presets?: string[];
  strobe_speed?: number;
  sweep_speed?: number;
  simulator: SimulatorView;
  raw_tester?: {
    active: boolean;
    nonzero_channels: number;
    frame: number[] | null;
    prepared_channels?: Array<{ channel: number; value: number }>;
    universe_size: number;
  } | null;
  preset_editor_preview?: PresetEditorPreviewState | null;
  live_effects?: LiveEffectsState | null;
  beam_calibration?: BeamCalibrationState | null;
}

export interface PresetEditorPreviewState {
  active: boolean;
  preset_id?: string | null;
  preset_label?: string | null;
  episode_index?: number | null;
  episode_id?: string | null;
  episode_title?: string | null;
  episode_duration_s?: number;
  elapsed_s?: number;
  restored_preset_id?: string | null;
  blockers?: string[];
  source_nonzero_channels?: number;
  wire_nonzero_channels?: number;
}

export interface LiveEffectSkipped {
  fixture_id: string;
  label?: string;
  missing: string[];
  partial?: boolean;
}

export interface LiveEffectInfo {
  id: string;
  label: string;
  target_groups: string[];
  target_fixture_ids: string[];
  applied_fixture_ids: string[];
  skipped: LiveEffectSkipped[];
}

export interface LiveEffectsState {
  active: boolean;
  active_ids: string[];
  effects: LiveEffectInfo[];
  warnings: string[];
  source_nonzero_channels?: number;
  wire_nonzero_channels?: number;
  source_owner?: string;
}

export interface CommandAck {
  ok: boolean;
  idempotent_replay: boolean;
  state: AppState;
}

export interface PresetInfo {
  id: string;
  label: string;
  builtin?: boolean;
  hardware_tuned?: boolean;
  episode_count?: number;
  total_duration_s?: number;
  source?: string;
  palettes?: string[];
  avg_intensity?: number;
  avg_speed?: number;
  effects?: string[];
}
