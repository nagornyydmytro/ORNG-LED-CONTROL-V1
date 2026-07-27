import type { AppState, CommandAck } from "../vite-env";

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

export async function fetchAppConfig(): Promise<Record<string, unknown>> {
  return parseJson(await fetch("/api/config/app"));
}

export async function fetchPatch(): Promise<Record<string, unknown>> {
  return parseJson(await fetch("/api/config/patch"));
}

export async function fetchProfiles(): Promise<Record<string, Record<string, unknown>>> {
  return parseJson(await fetch("/api/config/profiles"));
}

export async function fetchLayout(): Promise<Record<string, unknown>> {
  return parseJson(await fetch("/api/config/layout"));
}

export async function fetchReadiness(): Promise<Record<string, unknown>> {
  return parseJson(await fetch("/api/setup/readiness"));
}

export async function validatePatch(
  patch: Record<string, unknown>,
): Promise<{ ok: boolean; errors: string[] }> {
  return parseJson(
    await fetch("/api/setup/validate-patch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patch }),
    }),
  );
}

export async function saveAppConfig(config: Record<string, unknown>): Promise<CommandAck> {
  return parseJson(
    await fetch("/api/config/app", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    }),
  );
}

export async function savePatch(patch: Record<string, unknown>): Promise<CommandAck> {
  return parseJson(
    await fetch("/api/config/patch", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patch }),
    }),
  );
}

export async function saveProfile(
  profileId: string,
  profile: Record<string, unknown>,
): Promise<CommandAck> {
  return parseJson(
    await fetch(`/api/config/profiles/${encodeURIComponent(profileId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile }),
    }),
  );
}

export async function saveLayout(layout: Record<string, unknown>): Promise<CommandAck> {
  return parseJson(
    await fetch("/api/config/layout", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout }),
    }),
  );
}

export async function setupPost(path: string, body: Record<string, unknown> = {}): Promise<CommandAck> {
  return parseJson(
    await fetch(`/api/setup/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export type { AppState };
