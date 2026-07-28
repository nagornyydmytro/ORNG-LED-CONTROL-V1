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

export interface ChannelRoleOption {
  role: string;
  label: string;
}

/** Fallback catalog if GET /api/setup/channel-roles is unavailable. */
export const FALLBACK_CHANNEL_ROLES: ChannelRoleOption[] = [
  { role: "unused", label: "Не використовується / завжди 0" },
  { role: "dimmer", label: "Master Dimmer" },
  { role: "red", label: "Red" },
  { role: "green", label: "Green" },
  { role: "blue", label: "Blue" },
  { role: "white", label: "White" },
  { role: "amber", label: "Amber" },
  { role: "uv", label: "UV" },
  { role: "strobe", label: "Strobe" },
  { role: "strobe_speed", label: "Strobe Speed" },
  { role: "program", label: "Program / Effect" },
  { role: "effect_speed", label: "Effect Speed" },
  { role: "direction_mode", label: "Direction / Mode" },
  { role: "whole_color", label: "Whole Fixture Color / Palette" },
  { role: "segment_color", label: "Segment Color" },
  { role: "segment", label: "Segment Level" },
  { role: "pan_coarse", label: "Pan" },
  { role: "pan_fine", label: "Pan Fine" },
  { role: "tilt_coarse", label: "Tilt" },
  { role: "tilt_fine", label: "Tilt Fine" },
  { role: "movement_speed", label: "Movement Speed" },
  { role: "color", label: "Color Wheel" },
  { role: "gobo", label: "Gobo" },
  { role: "gobo_rotation", label: "Gobo Rotation" },
  { role: "prism", label: "Prism" },
  { role: "prism_rotation", label: "Prism Rotation" },
  { role: "focus", label: "Focus" },
  { role: "zoom", label: "Zoom" },
  { role: "reset", label: "Reset" },
  { role: "fixed", label: "Фіксоване значення" },
  { role: "shutter", label: "Shutter" },
];

export const PALETTE_KEYS = [
  "off",
  "red",
  "green",
  "blue",
  "white",
  "amber",
  "cyan",
  "purple",
] as const;

export const DEFAULT_CHANNEL_PALETTE: Record<(typeof PALETTE_KEYS)[number], number> = {
  off: 0,
  red: 16,
  green: 32,
  blue: 48,
  white: 64,
  amber: 80,
  cyan: 96,
  purple: 112,
};

export async function fetchChannelRoles(): Promise<ChannelRoleOption[]> {
  try {
    const body = await parseJson<{ roles: ChannelRoleOption[] }>(
      await fetch("/api/setup/channel-roles"),
    );
    if (Array.isArray(body.roles) && body.roles.length > 0) {
      return body.roles;
    }
  } catch {
    // fall through
  }
  return FALLBACK_CHANNEL_ROLES;
}

export type { AppState };
