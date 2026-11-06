/** Worker bindings and typed environment. */

export interface Env {
  DB: D1Database;
  SCENE_RELEASES: R2Bucket;
  /**
   * APPLICATION release packages (separate from scene bundles).
   * PRODUCTION BLOCKER: create real R2 bucket `orng-led-app-releases` before prod deploy.
   */
  APP_RELEASES: R2Bucket;
  DEVICE_DO: DurableObjectNamespace;
  PAIRING_RATE_LIMIT_DO: DurableObjectNamespace;
  ADMIN_LOGIN_GUARD_DO: DurableObjectNamespace;
  ASSETS?: Fetcher;
  ADMIN_ORIGIN: string;
  /** Single admin username (not a secret). Password lives only as ADMIN_PASSWORD_HASH. */
  ADMIN_USERNAME: string;
  /** Salted PBKDF2 hash secret — never plaintext password. */
  ADMIN_PASSWORD_HASH?: string;
  /** HMAC key for signed session cookies. */
  ADMIN_SESSION_SECRET?: string;
  /**
   * @deprecated Cloudflare Access removed — kept optional for old .dev.vars only.
   */
  ACCESS_AUD?: string;
  /** @deprecated Cloudflare Access removed. */
  ACCESS_TEAM_DOMAIN?: string;
  ADMIN_HOST: string;
  DEVICE_HOST: string;
  LOCAL_ADMIN_HOST: string;
  LOCAL_DEVICE_HOST: string;
  PROTOCOL_VERSION: string;
  /**
   * Explicit environment. When `production`, all DEV_* flags are ignored and
   * path-based host fallbacks are disabled (fail-closed).
   */
  ENVIRONMENT?: string;
  /** Set only in local .dev.vars — never in production. Requires localhost host. */
  DEV_BYPASS_ACCESS?: string;
  /** Allow ?token= on device WSS only when set and host is localhost. */
  DEV_ALLOW_QUERY_TOKEN?: string;
  /**
   * JSON map of key_id → raw Ed25519 public key (base64). Rotatable without code change.
   * Example: {"prod-20260425-01":"<b64>"}
   */
  APP_TRUSTED_KEYS?: string;
  /**
   * Allow test-ed25519-1 verification only with localhost (or unit memoryEnv).
   * Never enable on production hosts. DEV_BYPASS_ACCESS=1 also enables locally.
   */
  DEV_ALLOW_TEST_APP_KEYS?: string;
  /**
   * Shared HMAC secret with Notifications edge gateway.
   * Required in production to accept x-orng-gateway-* forwarded hosts.
   */
  GATEWAY_HMAC_SECRET?: string;
  /** Max |now - gateway ts| seconds (default 60). */
  GATEWAY_MAX_SKEW_S?: string;
}

/** True when Worker vars declare ENVIRONMENT=production (case-insensitive). */
export function isProductionEnvironment(env: Env): boolean {
  return (env.ENVIRONMENT ?? "").trim().toLowerCase() === "production";
}

export type HostRole = "admin" | "device" | "unknown";

export function hostRole(hostHeader: string | null, env: Env): HostRole {
  const portHost = (hostHeader ?? "").toLowerCase().trim();
  const hostOnly = portHost.split(":")[0] ?? "";

  const adminExact = new Set(
    [
      env.ADMIN_HOST,
      env.LOCAL_ADMIN_HOST,
      "127.0.0.1:8787",
      "localhost:8787",
      "light.orng.wtf",
    ]
      .filter(Boolean)
      .map((h) => h.toLowerCase()),
  );
  const deviceExact = new Set(
    [
      env.DEVICE_HOST,
      env.LOCAL_DEVICE_HOST,
      "127.0.0.1:8788",
      "localhost:8788",
      "device.light.orng.wtf",
    ]
      .filter(Boolean)
      .map((h) => h.toLowerCase()),
  );

  // Prefer full host:port when present so localhost:8788 is never admin.
  if (adminExact.has(portHost) || adminExact.has(hostOnly)) {
    // hostOnly match only when no port was provided (production hostnames).
    if (adminExact.has(portHost)) return "admin";
    if (!portHost.includes(":") && adminExact.has(hostOnly)) return "admin";
  }
  if (deviceExact.has(portHost) || (!portHost.includes(":") && deviceExact.has(hostOnly))) {
    return "device";
  }

  // wrangler workers.dev / single-port local: path prefix decides later;
  // default to unknown so callers must be explicit.
  if (portHost.includes("workers.dev") || hostOnly.endsWith(".workers.dev")) {
    return "unknown";
  }
  return "unknown";
}

/** True when the request Host is loopback (with or without port). */
export function isLocalhostHost(hostHeader: string | null): boolean {
  const portHost = (hostHeader ?? "").toLowerCase().trim();
  const hostOnly = portHost.split(":")[0] ?? "";
  return hostOnly === "localhost" || hostOnly === "127.0.0.1" || hostOnly === "::1";
}

export function isProductionAdminHost(hostHeader: string | null, env: Env): boolean {
  const portHost = (hostHeader ?? "").toLowerCase().trim();
  const hostOnly = portHost.split(":")[0] ?? "";
  const prod = (env.ADMIN_HOST || "light.orng.wtf").toLowerCase();
  return hostOnly === prod || portHost === prod || hostOnly === "light.orng.wtf";
}

export function json(data: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...headers,
    },
  });
}

export function errorJson(status: number, code: string, message: string): Response {
  return json({ error: { code, message } }, status);
}

export function nowIso(): string {
  return new Date().toISOString();
}

export function uuid(): string {
  return crypto.randomUUID();
}
