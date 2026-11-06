import type { Platform } from "../db/installations";

/** Device credential scopes. */

export const SCOPE_RUNTIME_CONNECT = "runtime:connect";
export const SCOPE_SCENE_READ_ASSIGNED = "scene:read_assigned";
export const SCOPE_STATE_REPORT = "state:report";
export const SCOPE_SCENE_LIST = "scene:list";
export const SCOPE_SCENE_READ = "scene:read";
export const SCOPE_SCENE_PUBLISH = "scene:publish";
/** Device may download only the assigned/compatible APPLICATION release. */
export const SCOPE_APP_READ_ASSIGNED = "app:read_assigned";

export type DeviceScope =
  | typeof SCOPE_RUNTIME_CONNECT
  | typeof SCOPE_SCENE_READ_ASSIGNED
  | typeof SCOPE_STATE_REPORT
  | typeof SCOPE_SCENE_LIST
  | typeof SCOPE_SCENE_READ
  | typeof SCOPE_SCENE_PUBLISH
  | typeof SCOPE_APP_READ_ASSIGNED;

export const RASPBERRY_SCOPES: readonly DeviceScope[] = [
  SCOPE_RUNTIME_CONNECT,
  SCOPE_SCENE_READ_ASSIGNED,
  SCOPE_APP_READ_ASSIGNED,
  SCOPE_STATE_REPORT,
];

export const DESKTOP_SCOPES: readonly DeviceScope[] = [
  SCOPE_RUNTIME_CONNECT,
  SCOPE_SCENE_READ_ASSIGNED,
  SCOPE_APP_READ_ASSIGNED,
  SCOPE_STATE_REPORT,
  SCOPE_SCENE_LIST,
  SCOPE_SCENE_READ,
  SCOPE_SCENE_PUBLISH,
];

export function defaultScopesForPlatform(platform: Platform): DeviceScope[] {
  if (platform === "raspberry") return [...RASPBERRY_SCOPES];
  return [...DESKTOP_SCOPES];
}

export function parseScopesJson(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((s): s is string => typeof s === "string");
  } catch {
    return [];
  }
}

export function hasScope(scopes: readonly string[], required: string): boolean {
  return scopes.includes(required);
}

export function hasAnyScope(scopes: readonly string[], required: readonly string[]): boolean {
  return required.some((s) => scopes.includes(s));
}
