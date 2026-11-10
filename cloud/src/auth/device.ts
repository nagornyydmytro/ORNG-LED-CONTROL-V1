import { parseScopesJson, SCOPE_RUNTIME_CONNECT } from "./scopes";
import { sha256Hex, timingSafeEqual } from "../protocol/crypto";
import type { Env } from "../env";
import { isLocalhostHost, isProductionEnvironment } from "../env";

export interface DeviceAuth {
  installationId: string;
  revoked: boolean;
  scopes: string[];
  rotationPending: boolean;
  platform: string | null;
}

export async function hashToken(token: string): Promise<string> {
  return sha256Hex(`orng-device:${token}`);
}

/**
 * Extract device token from Authorization: Bearer.
 * Query ?token= only when DEV_ALLOW_QUERY_TOKEN=1 and host is localhost.
 */
export function extractDeviceToken(request: Request, env: Env): string | null {
  const auth = request.headers.get("authorization");
  if (auth) {
    const m = /^Bearer\s+(.+)$/i.exec(auth.trim());
    if (m?.[1]) return m[1].trim();
  }

  const url = new URL(request.url);
  const q = url.searchParams.get("token");
  if (!q) return null;

  if (
    !isProductionEnvironment(env) &&
    env.DEV_ALLOW_QUERY_TOKEN === "1" &&
    isLocalhostHost(request.headers.get("host"))
  ) {
    return q.trim();
  }
  // Production / non-dev: reject query tokens (caller should 401).
  return null;
}

/** True when request attempted ?token= in a context where it is forbidden. */
export function queryTokenForbidden(request: Request, env: Env): boolean {
  const url = new URL(request.url);
  if (!url.searchParams.has("token")) return false;
  if (
    !isProductionEnvironment(env) &&
    env.DEV_ALLOW_QUERY_TOKEN === "1" &&
    isLocalhostHost(request.headers.get("host"))
  ) {
    return false;
  }
  return true;
}

export async function verifyDeviceToken(
  env: Env,
  token: string,
): Promise<DeviceAuth | null> {
  if (!token || token.length < 16) return null;
  const tokenHash = await hashToken(token);

  // Active token
  const row = await env.DB.prepare(
    `SELECT c.installation_id AS installation_id,
            c.scopes_json AS scopes_json,
            c.rotation_pending AS rotation_pending,
            c.pending_token_hash AS pending_token_hash,
            i.revoked_at AS revoked_at,
            i.platform AS platform
     FROM device_credentials c
     JOIN installations i ON i.id = c.installation_id
     WHERE c.token_hash = ? OR (c.rotation_pending = 1 AND c.pending_token_hash = ?)`,
  )
    .bind(tokenHash, tokenHash)
    .first<{
      installation_id: string;
      scopes_json: string | null;
      rotation_pending: number;
      pending_token_hash: string | null;
      revoked_at: string | null;
      platform: string | null;
    }>();

  if (!row) return null;
  return {
    installationId: row.installation_id,
    revoked: Boolean(row.revoked_at),
    scopes: parseScopesJson(row.scopes_json),
    rotationPending: Boolean(row.rotation_pending),
    platform: row.platform,
  };
}

export function isDatabaseReadLimit(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err);
  return message.includes("exceeded D1") && message.includes("row read");
}

export function isDurableVolumeLimit(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err);
  return message.includes("Exceeded allowed volume of requests in Durable Objects");
}

/** The one hall, when the database will not answer a read. */
export function readLimitedLocation(): {
  id: string;
  name: string;
  scene_id: string;
  installation_id: string;
  installations: Array<{ id: string; name: string; platform: string; revoked: boolean }>;
} {
  const id = "7e4457ad-c47e-4aaf-beba-87f83a1c92d0";
  return {
    id: "loc-orng-bar",
    name: "ORNG BAR",
    scene_id: "orng-bar",
    installation_id: id,
    installations: [{ id, name: "ORNG BAR Raspberry", platform: "raspberry", revoked: false }],
  };
}

export async function fallbackDeviceAuth(request: Request, env: Env): Promise<DeviceAuth | null> {
  const encoded = (env.DEVICE_LINK_FALLBACK ?? "").trim();
  const splitAt = encoded.indexOf(":");
  if (splitAt <= 0) return null;
  const expected = encoded.slice(0, splitAt);
  const installationId = encoded.slice(splitAt + 1);
  if (!installationId) return null;
  const token = extractDeviceToken(request, env);
  if (!token) return null;
  const hash = await hashToken(token);
  if (!timingSafeEqual(hash, expected)) return null;
  return {
    installationId,
    revoked: false,
    scopes: [SCOPE_RUNTIME_CONNECT],
    rotationPending: false,
    platform: "raspberry",
  };
}

export async function tokenMatchesHash(token: string, storedHash: string): Promise<boolean> {
  const h = await hashToken(token);
  return timingSafeEqual(h, storedHash);
}
