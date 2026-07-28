import type { CommandAck, PresetInfo } from "../vite-env";

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
}

export interface PresetDocument {
  schema_version: number;
  id: string;
  label: string;
  hardware_tuned: boolean;
  builtin: boolean;
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
] as const;

export const EFFECTS = [
  "static",
  "pulse",
  "wave",
  "chase",
  "mirror_sweep",
  "breathe",
] as const;

export const TRANSITIONS = ["cut", "soft", "fade"] as const;

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
  const body = await parseJson<{ presets: PresetInfo[] }>(await fetch("/api/presets"));
  return body.presets;
}

export async function getPreset(id: string): Promise<PresetDocument> {
  return parseJson(await fetch(`/api/presets/${encodeURIComponent(id)}`));
}

export async function createCustomPreset(id: string, label: string): Promise<CommandAck> {
  return parseJson(
    await fetch("/api/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, label }),
    }),
  );
}

export async function savePreset(id: string, preset: PresetDocument): Promise<CommandAck> {
  return parseJson(
    await fetch(`/api/presets/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset }),
    }),
  );
}

export async function renamePreset(id: string, label: string): Promise<CommandAck> {
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
  return parseJson(
    await fetch(`/api/presets/${encodeURIComponent(id)}/duplicate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_id: newId, label }),
    }),
  );
}

export async function deletePreset(id: string): Promise<CommandAck> {
  return parseJson(
    await fetch(`/api/presets/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  );
}

export async function previewPreset(id: string, speed = 10): Promise<CommandAck> {
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
  return parseJson(
    await fetch("/api/presets/editor-preview/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function stopEditorEpisodePreview(): Promise<CommandAck> {
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
    palette: "warm_orange",
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
