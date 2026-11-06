/// <reference types="vite/client" />
/// <reference types="vitest/globals" />

interface ImportMetaEnv {
  readonly VITE_ORNG_CLOUD_ADMIN?: string;
  /** Local FastAPI UI only: show DEV approve helper when `"true"`. */
  readonly VITE_ORNG_CLOUD_DEV_APPROVE?: string;
  /** App version from repo VERSION or VITE_ORNG_APP_VERSION. */
  readonly VITE_ORNG_APP_VERSION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  transport: string;
  output_armed: boolean;
  artnet_network_enabled: boolean;
  frontend_dist_present: boolean;
  ready?: boolean;
  scene_id?: string | null;
  scene_name?: string | null;
}

export interface SceneInfo {
  scene_id: string;
  name: string;
  path?: string;
  error?: string | null;
  active?: boolean;
}

export interface SceneListResponse {
  scenes: SceneInfo[];
  active: SceneInfo;
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
  /** Per-segment venue preview RGB (parallel to segments). */
  segment_r?: number[];
  segment_g?: number[];
  segment_b?: number[];
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
  session_id?: string | null;
  owner?: string | null;
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
  playback?: "engine" | "palette";
  safe_mock_preview: boolean;
  frames: SimulatorView[];
}

export interface AutoFamilyInfo {
  id: string;
  title: string;
  motion: string;
  envelope: string;
}

export interface AutoCompositionInfo {
  id: string;
  family: string;
  family_title?: string | null;
  title: string;
  groups: string[];
  palettes: string[];
  motion?: string | null;
  notes?: string;
  constraints?: string;
  needs_segments?: boolean;
  program?: "smooth" | "beat" | string;
}

export interface AutoPreviewQuery {
  look: string;
  cycleBeats: number;
  palette: string;
  intensity: string;
  bpm: number;
  frames?: number;
}

export interface AutoAvailableClip {
  look: string;
  cycle_beats: number;
  palette: string;
  intensity: string;
}

export interface AutoCatalog {
  families: AutoFamilyInfo[];
  compositions: AutoCompositionInfo[];
  cycles: number[];
  palettes: string[];
  intensities?: string[];
  count: number;
  default_look?: string | null;
  note?: string;
  available?: AutoAvailableClip[];
}

export interface AutoPreviewClip {
  look: string;
  title?: string;
  cycle_beats: number;
  loop_beats?: number;
  palette: string;
  intensity: string;
  bpm: number;
  seconds: number;
  transport: string;
  playback?: string;
  safe_mock_preview: boolean;
  isolated?: boolean;
  source?: string;
  note?: string;
  compatible: boolean;
  reason?: string | null;
  scene_id?: string | null;
  scene_name?: string | null;
  scene_hash?: string | null;
  library_version?: string | null;
  canonical?: boolean;
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

export type BpmSyncMode = "smooth" | "bar" | "beat";
export type BpmSyncScope = "preset" | "auto";
export type BpmPaletteId =
  | "orange"
  | "red"
  | "blue"
  | "violet"
  | "amber"
  | "white"
  | "ember"
  | "sunset"
  | "ice"
  | "night"
  | "contrast"
  | "forest"
  | "carnival"
  | "traffic"
  | "bars"
  | "heads";
export type BpmCastId = "all" | "bars" | "heads";
export type BpmCharacterId = "smooth" | "rhythmic" | "impact";
export type BpmIntensityId = "soft" | "normal" | "full";
export type BpmPeriodId = "auto" | 0.5 | 1 | 2 | 4 | 8;

export interface BpmSyncState {
  enabled: boolean;
  scope?: BpmSyncScope;
  mode: BpmSyncMode;
  palette?: BpmPaletteId;
  cast?: BpmCastId;
  character?: BpmCharacterId;
  intensity?: BpmIntensityId;
  period?: BpmPeriodId | number | string;
  par_rhythm?: boolean;
  accent?: number;
  par_note?: string | null;
  look?: string | null;
  look_title?: string | null;
  section?: string;
  bpm: number | null;
  beat_phase?: number;
  locked?: boolean;
  /** Energy is present. Absent on an older runtime, which is not a lock. */
  signal?: boolean;
  device: string | null;
  capture: string;
  /** no_input / waiting / searching / locked / hold / fallback. Not a beat claim. */
  sync?: "no_input" | "waiting" | "searching" | "locked" | "hold" | "fallback";
}

export interface AppState {
  engine: EngineState;
  output: OutputState;
  presets: string[];
  fixture_ids: string[];
  frame: number[];
  sequence: number;
  preview_speed: number;
  bpm?: BpmSyncState | null;
  pad_presets?: string[];
  strobe_speed?: number;
  sweep_speed?: number;
  simulator: SimulatorView;
  scene_id?: string | null;
  scene_name?: string | null;
  /** Operator location. Absent on a desktop that has only the active scene. */
  location_id?: string | null;
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
  desk_role?: "local" | "raspberry" | null;
  executor_online?: boolean | null;
  executor_installation_id?: string | null;
  capabilities?: string[] | null;
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
