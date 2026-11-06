/** Per-tab cloud admin source id: `cloud:web:<sessionId>`. */

const STORAGE_KEY = "orng-cloud-web-session-id";

let cached: string | null = null;

function newSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
}

/**
 * Returns `cloud:web:<sessionId>`, created once per browser tab.
 * The Worker also assigns a unique source on WSS open; this id is used for
 * client-side hold tracking and can be passed as a hint in WS query.
 */
export function getCloudWebSessionSourceId(): string {
  if (cached) return cached;

  try {
    const existing = sessionStorage.getItem(STORAGE_KEY);
    if (existing && existing.length > 0) {
      cached = existing.startsWith("cloud:web:") ? existing : `cloud:web:${existing}`;
      return cached;
    }
    const id = `cloud:web:${newSessionId()}`;
    sessionStorage.setItem(STORAGE_KEY, id);
    cached = id;
    return cached;
  } catch {
    cached = `cloud:web:${newSessionId()}`;
    return cached;
  }
}

export function resetCloudWebSessionSourceIdForTests(): void {
  cached = null;
}
