import { createCommandId } from "./client";
import type { AppState, SceneInfo, SceneListResponse } from "../vite-env";

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

export async function fetchScenes(): Promise<SceneListResponse> {
  return parseJson(await fetch("/api/scenes"));
}

export async function fetchActiveScene(): Promise<SceneInfo> {
  return parseJson(await fetch("/api/scenes/active"));
}

export async function activateScene(sceneId: string): Promise<{
  ok: boolean;
  idempotent_replay: boolean;
  state: AppState;
}> {
  return parseJson(
    await fetch("/api/scenes/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scene_id: sceneId,
        client_command_id: createCommandId(`scene-${sceneId}`),
      }),
    }),
  );
}
