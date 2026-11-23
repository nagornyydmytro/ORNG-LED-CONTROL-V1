import type { CommandAck, PresetInfo } from "../vite-env";
import { takeScenePort } from "../runtime/scenePort";

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export interface LookAction {
  id?: string;
  groups: string[];
  palette?: string | null;
  effect: string;
  speed: number;
  intensity: number;
  cycle_beats?: 1 | 2 | 4 | 8 | null;
}

export interface EpisodeCard {
  id: string;
  duration_s: number;
  groups: string[];
  palette: string;
  effect: string;
  speed: number;
  intensity: number;
  transition: string;
  notes?: string | null;
  cycle_beats?: 1 | 2 | 4 | 8 | null;
  actions?: LookAction[];
  look?: string | null;
  head?: "sweep" | "nod" | "cross" | "rise" | "pulse" | null;
  /** Saved Mix behaviour. Ordinary presets leave this empty. */
  mix_style?: "picture" | "classic" | null;
}

export interface PresetDocument {
  schema_version: number;
  id: string;
  label: string;
  hardware_tuned: boolean;
  builtin: boolean;
  musical_version?: number | null;
  episodes: EpisodeCard[];
}

export const PALETTES = [
  "warm_orange",
  "deep_red",
  "amber",
  "white_warm",
  "violet_orange",
  "cool_blue",
  "mint",
  "magenta",
  "rose",
  "deep_blue",
  "violet",
  "cyan",
] as const;

export const EFFECTS = [
  "static",
  "pulse",
  "wave",
  "chase",
  "mirror_sweep",
  "breathe",
  "glow",
  "glow_wave",
  "glow_rise",
  "glow_line",
  "glow_pinch",
  "glow_eq",
] as const;

export const TRANSITIONS = ["cut", "soft", "fade"] as const;

export const TRANSITION_LABELS: Record<(typeof TRANSITIONS)[number], string> = {
  cut: "Різко",
  soft: "М'яко",
  fade: "Згасання",
};

export function effectUsesSpeed(effect: string): boolean {
  return effect !== "static";
}

export function freshLookId(prefix: string): string {
  return `${prefix}${Date.now().toString(36)}${Math.floor(Math.random() * 36).toString(36)}`;
}

export function cloneEpisode(episode: EpisodeCard): EpisodeCard {
  const copy = JSON.parse(JSON.stringify(episode)) as EpisodeCard;
  copy.id = freshLookId("ep");
  if (copy.actions?.length) {
    copy.actions = copy.actions.map((action) => ({ ...action, id: freshLookId("a") }));
  }
  return copy;
}

export const GROUPS = [
  "all_rear",
  "par",
  "bar",
  "beam",
  "outer",
  "inner",
  "left",
  "right",
  "rear",
] as const;

export async function listPresets(): Promise<PresetInfo[]> {
  const port = takeScenePort();
  if (port) return port.listPresets();
  const body = await parseJson<{ presets: PresetInfo[] }>(await fetch("/api/presets"));
  return body.presets;
}

export async function getPreset(id: string): Promise<PresetDocument> {
  const port = takeScenePort();
  if (port) return port.getPreset(id);
  return parseJson(await fetch(`/api/presets/${encodeURIComponent(id)}`));
}

export async function createCustomPreset(id: string, label: string): Promise<CommandAck> {
  const port = takeScenePort();
  if (port) return port.createCustomPreset(id, label);
  return parseJson(
    await fetch("/api/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, label }),
    }),
  );
}

export async function savePreset(id: string, preset: PresetDocument): Promise<CommandAck> {
  const port = takeScenePort();
  if (port) return port.savePreset(id, preset);
  return parseJson(
    await fetch(`/api/presets/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset }),
    }),
  );
}

export async function renamePreset(id: string, label: string): Promise<CommandAck> {
  const port = takeScenePort();
  if (port) return port.renamePreset(id, label);
  return parseJson(
    await fetch(`/api/presets/${encodeURIComponent(id)}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    }),
  );
}

export async function duplicatePreset(
  id: string,
  newId: string,
  label?: string,
): Promise<CommandAck> {
  const port = takeScenePort();
  if (port) return port.duplicatePreset(id, newId, label);
  return parseJson(
    await fetch(`/api/presets/${encodeURIComponent(id)}/duplicate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_id: newId, label }),
    }),
  );
}

export async function deletePreset(id: string): Promise<CommandAck> {
  const port = takeScenePort();
  if (port) return port.deletePreset(id);
  return parseJson(
    await fetch(`/api/presets/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  );
}

export async function previewPreset(id: string, speed = 10): Promise<CommandAck> {
  const port = takeScenePort();
  if (port) return port.previewPreset(id, speed);
  return parseJson(
    await fetch(`/api/presets/${encodeURIComponent(id)}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed }),
    }),
  );
}

export async function startEditorEpisodePreview(body: {
  preset_id: string;
  preset_label: string;
  episode_index: number;
  episode: EpisodeCard;
}): Promise<CommandAck> {
  const port = takeScenePort();
  if (port) return port.startEditorEpisodePreview(body);
  return parseJson(
    await fetch("/api/presets/editor-preview/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function stopEditorEpisodePreview(): Promise<CommandAck> {
  const port = takeScenePort();
  if (port) return port.stopEditorEpisodePreview();
  return parseJson(
    await fetch("/api/presets/editor-preview/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }),
  );
}

export function newEpisode(index: number): EpisodeCard {
  return {
    id: `ep${Date.now()}-${index}`,
    duration_s: 18,
    groups: ["all_rear"],
    palette: "deep_red",
    effect: "pulse",
    speed: 0.5,
    intensity: 0.7,
    transition: "soft",
  };
}

/** Suggest next unused custom id like C01, C02, … */
export function suggestCustomPresetId(existingIds: string[]): string {
  const used = new Set(existingIds.map((id) => id.toUpperCase()));
  for (let n = 1; n < 1000; n += 1) {
    const id = `C${String(n).padStart(2, "0")}`;
    if (!used.has(id)) return id;
  }
  return `C${Date.now().toString(36)}`;
}
