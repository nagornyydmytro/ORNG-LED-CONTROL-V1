import type { AppState, CommandAck, HealthResponse, PresetInfo } from "../vite-env";

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

export async function fetchHealth(): Promise<HealthResponse> {
  return parseJson(await fetch("/api/health"));
}

export async function fetchState(): Promise<AppState> {
  return parseJson(await fetch("/api/state"));
}

export async function fetchPresets(): Promise<PresetInfo[]> {
  const body = await parseJson<{ presets: PresetInfo[] }>(await fetch("/api/presets"));
  return body.presets;
}

export async function postCommand(
  path: string,
  payload: Record<string, unknown> = {},
): Promise<CommandAck> {
  const response = await fetch(`/api/commands/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export function createCommandId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}
