import { takeScenePort } from "../runtime/scenePort";
import type { AutoCatalog, AutoPreviewClip, AutoPreviewQuery } from "../vite-env";

export type { AutoPreviewQuery };

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* plain text */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function fetchAutoCatalog(): Promise<AutoCatalog> {
  const port = takeScenePort();
  if (port?.listAutoCatalog) return port.listAutoCatalog();
  return parseJson(await fetch("/api/auto/catalog"));
}

export async function fetchAutoPreview(query: AutoPreviewQuery): Promise<AutoPreviewClip> {
  const port = takeScenePort();
  if (port?.previewAutoClip) return port.previewAutoClip(query);
  const params = new URLSearchParams({
    look: query.look,
    cycle_beats: String(query.cycleBeats),
    palette: query.palette,
    intensity: query.intensity,
    bpm: String(query.bpm),
  });
  if (query.frames && query.frames > 0) params.set("frames", String(query.frames));
  return parseJson(await fetch(`/api/auto/preview-clip?${params.toString()}`));
}

export const AUTO_GROUP_LABEL: Record<string, string> = {
  par: "PAR",
  bar: "Бари",
  beam: "Голови",
};
