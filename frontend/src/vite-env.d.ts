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
  blackout: boolean;
  face_on: boolean;
  face_brightness: number;
  strobe_held: boolean;
  white_hit_active: boolean;
  master_brightness: number;
  time_s: number;
}

export interface OutputState {
  transport: string;
  armed: boolean;
  last_error: string | null;
  frames_sent: number;
  network_allowed: boolean;
  target_ip: string | null;
  universe: number;
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
}
