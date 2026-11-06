/** Stable tab-lifetime source id for HTTP input holds (`web:<session-id>`). */

const STORAGE_KEY = "orng-web-session-id";

let cached: string | null = null;

function newSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
}

/**
 * Returns `web:<session-id>`, created once per browser tab (sessionStorage)
 * with a module singleton fallback when storage is unavailable.
 */
export function getWebSessionSourceId(): string {
  if (cached) return cached;

  try {
    const existing = sessionStorage.getItem(STORAGE_KEY);
    if (existing && existing.length > 0) {
      cached = existing.startsWith("web:") ? existing : `web:${existing}`;
      return cached;
    }
    const id = `web:${newSessionId()}`;
    sessionStorage.setItem(STORAGE_KEY, id);
    cached = id;
    return cached;
  } catch {
    cached = `web:${newSessionId()}`;
    return cached;
  }
}

/** Test helper — clears the in-memory cache (does not touch sessionStorage). */
export function resetWebSessionSourceIdForTests(): void {
  cached = null;
}
