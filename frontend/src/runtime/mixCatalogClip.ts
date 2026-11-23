import pack from "./mixCatalogClip.json";
import type { PreviewClip, SimulatorView } from "../vite-env";

type BarRow = [string, number, number, number, number, number[]];
type ParRow = [string, number, number, number, number];
type BeamRow = [
  string,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
];
type PackedFrame = [BarRow[], ParRow[], BeamRow[]];

interface MixCatalogPack {
  version: string;
  scene_stamp: string;
  episode_stamp: string;
  fps: number;
  seconds: number;
  recorded_s?: number;
  wrap_pad_s?: number;
  frames: PackedFrame[];
}

const catalog = pack as MixCatalogPack;

export const MIX_CATALOG_VERSION = "preset-catalog-2";

type StampAction = {
  id?: string;
  groups?: string[];
  palette?: string | null;
  effect?: string;
  speed?: number;
  intensity?: number;
  cycle_beats?: number | null;
};

type StampEpisode = {
  id?: string;
  duration_s?: number;
  groups?: string[];
  palette?: string;
  effect?: string;
  speed?: number;
  intensity?: number;
  transition?: string;
  notes?: string | null;
  cycle_beats?: number | null;
  actions?: StampAction[];
  look?: string | null;
  head?: string | null;
  mix_style?: "picture" | "classic" | null;
};

function numText(value: unknown, fallback = 0): string {
  const number = Number(value);
  return (Number.isFinite(number) ? number : fallback).toFixed(4);
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  const row = value as Record<string, unknown>;
  const keys = Object.keys(row).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(row[key])}`).join(",")}}`;
}

function normalizeAction(action: StampAction): Record<string, unknown> {
  return {
    cycle_beats: action.cycle_beats == null ? null : Number(action.cycle_beats),
    effect: String(action.effect ?? "pulse"),
    groups: Array.isArray(action.groups) ? action.groups.map(String) : ["all_rear"],
    id: String(action.id ?? ""),
    intensity: numText(action.intensity, 0.7),
    palette: action.palette == null ? null : String(action.palette),
    speed: numText(action.speed, 0.5),
  };
}

function normalizeEpisode(episode: StampEpisode): Record<string, unknown> {
  return {
    actions: Array.isArray(episode.actions) ? episode.actions.map(normalizeAction) : [],
    cycle_beats: episode.cycle_beats == null ? null : Number(episode.cycle_beats),
    duration_s: numText(episode.duration_s, 18),
    effect: String(episode.effect ?? "pulse"),
    groups: Array.isArray(episode.groups) ? episode.groups.map(String) : ["all_rear"],
    head: episode.head ?? null,
    id: String(episode.id ?? ""),
    intensity: numText(episode.intensity, 0.7),
    look: episode.look ?? null,
    mix_style: episode.mix_style ?? null,
    notes: episode.notes ?? null,
    palette: String(episode.palette ?? "deep_red"),
    speed: numText(episode.speed, 0.5),
    transition: String(episode.transition ?? "soft"),
  };
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function mixEpisodeStamp(episodes: StampEpisode[]): Promise<string> {
  const body = stableStringify(episodes.map((episode) => normalizeEpisode(episode)));
  return sha256(`${MIX_CATALOG_VERSION}\n${body}`);
}

export async function mixSceneStamp(files: Map<string, string>): Promise<string> {
  const preset = (files.get("presets/Mix.yaml") ?? "").replace(/\r\n/g, "\n");
  const chunks: string[] = [];
  for (const name of ["patch.yaml", "layout.yaml"]) {
    if (!files.has(name)) continue;
    chunks.push(`${name}\n${(files.get(name) ?? "").replace(/\r\n/g, "\n")}`);
  }
  const profiles = [...files.keys()]
    .filter((path) => path.startsWith("profiles/") && path.endsWith(".yaml"))
    .sort();
  for (const name of profiles) {
    chunks.push(`${name}\n${(files.get(name) ?? "").replace(/\r\n/g, "\n")}`);
  }
  return sha256(`${MIX_CATALOG_VERSION}\n${preset}\n${chunks.join("\n")}`);
}

export function mixCatalogStamps(): { scene: string; episodes: string; version: string; fps: number } {
  return {
    scene: catalog.scene_stamp,
    episodes: catalog.episode_stamp,
    version: catalog.version,
    fps: catalog.fps,
  };
}

function recordedSeconds(): number {
  return Number(catalog.recorded_s ?? catalog.seconds) || catalog.seconds;
}

function clipIndex(timeS: number): number | null {
  const count = catalog.frames.length;
  if (count <= 0) return null;
  const index = Math.round(timeS * catalog.fps) - 1;
  if (index < 0 || index >= count) return null;
  return index;
}

function expand(row: PackedFrame): SimulatorView {
  const bars = row[0].map((item) => ({
    id: item[0],
    label: item[0],
    kind: "bar",
    side: "",
    ring: "",
    order: null,
    dimmer: item[1],
    r: item[2],
    g: item[3],
    b: item[4],
    segments: item[5],
  }));
  const pars = row[1].map((item) => ({
    id: item[0],
    label: item[0],
    kind: "par",
    side: "",
    ring: "",
    face: false,
    order: null,
    r: item[1],
    g: item[2],
    b: item[3],
    w: 0,
    intensity: item[4],
  }));
  const beams = row[2].map((item) => ({
    id: item[0],
    label: item[0],
    kind: "beam",
    side: "",
    order: null,
    pan: item[1],
    tilt: item[2],
    dimmer: item[3],
    shutter_open: item[7] === 1,
    r: item[4],
    g: item[5],
    b: item[6],
    strobe: 0,
    hit_x: item[8],
    hit_z: item[9],
    dir_x: item[10],
    dir_y: item[11],
    dir_z: item[12],
    throw: item[13],
  }));
  const lit =
    bars.filter((item) => item.dimmer > 0.02).length +
    pars.filter((item) => item.intensity > 0.02).length +
    beams.filter((item) => item.dimmer > 0.02).length;
  return {
    pars,
    bars,
    beams,
    faces: [],
    nonzero_channels: lit,
    blackout_visual: false,
  };
}

/** Slice packed renderer frames. Returns null when the window is outside the pack. */
export function sliceMixCatalog(
  seconds: number,
  fps: number,
  startS: number,
): PreviewClip | null {
  if (catalog.version !== MIX_CATALOG_VERSION) return null;
  // Never invent motion between stored samples.
  const rate = Math.min(catalog.fps, Math.max(1, Math.round(fps || catalog.fps)));
  const span = Math.max(1, Math.min(20, Number(seconds) || 6));
  const cycle = Math.max(0.5, catalog.seconds);
  const start = Math.max(0, Math.min(Number(startS) || 0, cycle));
  const recorded = recordedSeconds();
  const total = Math.max(1, Math.round(span * rate));
  const frames: SimulatorView[] = [];
  for (let i = 0; i < total; i += 1) {
    const time = start + (i + 1) / rate;
    if (time > recorded + 1e-9) return null;
    const index = clipIndex(time);
    if (index == null) return null;
    frames.push(expand(catalog.frames[index]!));
  }
  return {
    preset_id: "Mix",
    fps: rate,
    seconds: span,
    start_s: start,
    transport: "none",
    playback: "engine",
    safe_mock_preview: true,
    frames,
  };
}
