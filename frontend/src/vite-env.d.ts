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
  frame_sum?: number;
  nonzero_channels?: number;
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
  simulator: SimulatorView;
  raw_tester?: {
    active: boolean;
    nonzero_channels: number;
    frame: number[] | null;
    universe_size: number;
  } | null;
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
