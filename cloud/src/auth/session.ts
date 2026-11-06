/**
 * Signed admin session + CSRF cookies.
 * Secure; HttpOnly session; SameSite=Strict. CSRF cookie is readable by JS (double-submit).
 */

import type { Env } from "../env";
import { errorJson, isLocalhostHost, isProductionEnvironment } from "../env";
import { timingSafeEqual } from "../protocol/crypto";

export const SESSION_COOKIE = "orng_admin_session";
export const CSRF_COOKIE = "orng_admin_csrf";
export const CSRF_HEADER = "x-csrf-token";
export const DEFAULT_SESSION_TTL_S = 12 * 3600;

const textEncoder = new TextEncoder();

export interface AdminIdentity {
  /** Stable actor id for audit (username). */
  username: string;
  /** Alias used by existing audit call sites that expected Access email. */
  email: string;
  sub: string;
}

export type SessionResult =
  | { ok: true; identity: AdminIdentity }
  | { ok: false; response: Response };

function b64urlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function b64urlDecode(input: string): Uint8Array {
  const padded = input.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((input.length + 3) % 4);
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

function parseCookies(header: string | null): Map<string, string> {
  const out = new Map<string, string>();
  if (!header) return out;
  for (const part of header.split(";")) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    const k = part.slice(0, idx).trim();
    const v = part.slice(idx + 1).trim();
    if (k) out.set(k, v);
  }
  return out;
}

async function hmacSign(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    textEncoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, textEncoder.encode(message));
  return b64urlEncode(new Uint8Array(sig));
}

export function localBypassAllowed(env: Env, request: Request): boolean {
  if (isProductionEnvironment(env)) return false;
  if (env.DEV_BYPASS_ACCESS !== "1") return false;
  return isLocalhostHost(request.headers.get("host"));
}

export function sessionConfigurationError(env: Env, request: Request): Response | null {
  const sessionSecret = (env.ADMIN_SESSION_SECRET ?? "").trim();
  const passwordHash = (env.ADMIN_PASSWORD_HASH ?? "").trim();
  const username = (env.ADMIN_USERNAME ?? "").trim();
  if (sessionSecret && passwordHash && username) return null;
  if (localBypassAllowed(env, request)) return null;
  return errorJson(
    503,
    "configuration_error",
    "Admin session auth is not configured",
  );
}

export async function createSessionToken(
  secret: string,
  username: string,
  ttlS = DEFAULT_SESSION_TTL_S,
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    v: 1 as const,
    u: username,
    iat: now,
    exp: now + ttlS,
  };
  const body = b64urlEncode(textEncoder.encode(JSON.stringify(payload)));
  const sig = await hmacSign(secret, body);
  return `${body}.${sig}`;
}

export async function verifySessionToken(
  secret: string,
  token: string,
): Promise<AdminIdentity | null> {
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const [body, sig] = parts;
  if (!body || !sig) return null;
  const expected = await hmacSign(secret, body);
  if (!timingSafeEqual(sig, expected)) return null;
  try {
    const json = JSON.parse(new TextDecoder().decode(b64urlDecode(body))) as {
      v?: number;
      u?: string;
      exp?: number;
      iat?: number;
    };
    if (json.v !== 1) return null;
    const username = String(json.u ?? "").trim();
    if (!username) return null;
    const now = Math.floor(Date.now() / 1000);
    if (typeof json.exp !== "number" || json.exp < now) return null;
    if (typeof json.iat === "number" && json.iat > now + 60) return null;
    return { username, email: username, sub: `admin:${username}` };
  } catch {
    return null;
  }
}

export function randomToken(bytes = 32): string {
  return b64urlEncode(crypto.getRandomValues(new Uint8Array(bytes)));
}

/** Constant-time username compare (pad to equal length via hash-ish normalize). */
export function timingSafeUsername(a: string, b: string): boolean {
  // Length leak is acceptable for usernames; still avoid short-circuit on chars.
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

export function cookieSecureFlag(request: Request): boolean {
  const url = new URL(request.url);
  if (url.protocol === "https:") return true;
  // Gateway terminates TLS; origin may see http://workers.dev — still set Secure for prod hosts.
  const host = (request.headers.get("host") ?? "").toLowerCase();
  if (host.endsWith("orng.wtf") || host.includes("workers.dev")) return true;
  return false;
}

export function buildSessionCookie(
  token: string,
  request: Request,
  maxAgeS = DEFAULT_SESSION_TTL_S,
): string {
  const secure = cookieSecureFlag(request) ? "; Secure" : "";
  return `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAgeS}${secure}`;
}

export function buildCsrfCookie(
  token: string,
  request: Request,
  maxAgeS = DEFAULT_SESSION_TTL_S,
): string {
  const secure = cookieSecureFlag(request) ? "; Secure" : "";
  // Not HttpOnly — SPA reads it for X-CSRF-Token double-submit.
  return `${CSRF_COOKIE}=${token}; Path=/; SameSite=Strict; Max-Age=${maxAgeS}${secure}`;
}

export function clearSessionCookie(request: Request): string {
  const secure = cookieSecureFlag(request) ? "; Secure" : "";
  return `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${secure}`;
}

export function clearCsrfCookie(request: Request): string {
  const secure = cookieSecureFlag(request) ? "; Secure" : "";
  return `${CSRF_COOKIE}=; Path=/; SameSite=Strict; Max-Age=0${secure}`;
}

export function readCookie(request: Request, name: string): string | null {
  return parseCookies(request.headers.get("cookie")).get(name) ?? null;
}

/** Safe methods skip CSRF; mutating methods require header == CSRF cookie. */
export function requireCsrf(request: Request): Response | null {
  const method = request.method.toUpperCase();
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return null;
  // WebSocket upgrade is GET with Upgrade — no CSRF body; session cookie is enough + SameSite.
  if ((request.headers.get("upgrade") ?? "").toLowerCase() === "websocket") return null;

  const cookieTok = readCookie(request, CSRF_COOKIE);
  const headerTok = (request.headers.get(CSRF_HEADER) ?? "").trim();
  if (!cookieTok || !headerTok || !timingSafeEqual(cookieTok, headerTok)) {
    return errorJson(403, "csrf_failed", "CSRF token missing or invalid");
  }
  return null;
}

export async function resolveAdminSession(
  request: Request,
  env: Env,
): Promise<SessionResult> {
  const configErr = sessionConfigurationError(env, request);
  if (configErr) return { ok: false, response: configErr };

  if (localBypassAllowed(env, request)) {
    const urlEmail = new URL(request.url).searchParams.get("dev_email");
    const email =
      request.headers.get("x-dev-access-email") ??
      urlEmail;
    if (email?.trim()) {
      const username = email.trim();
      return { ok: true, identity: { username, email: username, sub: `dev:${username}` } };
    }
  }

  const secret = (env.ADMIN_SESSION_SECRET ?? "").trim();
  const token = readCookie(request, SESSION_COOKIE);
  if (!token) {
    return { ok: false, response: errorJson(401, "unauthorized", "Login required") };
  }
  const identity = await verifySessionToken(secret, token);
  if (!identity) {
    return { ok: false, response: errorJson(401, "unauthorized", "Session invalid or expired") };
  }
  // Optional: bind session username to configured ADMIN_USERNAME.
  const expected = (env.ADMIN_USERNAME ?? "").trim();
  if (expected && !timingSafeEqual(identity.username, expected)) {
    return { ok: false, response: errorJson(401, "unauthorized", "Session invalid or expired") };
  }
  return { ok: true, identity };
}
