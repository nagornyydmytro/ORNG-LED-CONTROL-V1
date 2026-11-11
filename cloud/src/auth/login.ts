/**
 * Admin login / logout — single hardcoded username + salted password hash secret.
 * No registration, reset, or user directory.
 */

import type { Env } from "../env";
import { errorJson, json } from "../env";
import { verifyPassword } from "./password";
import {
  buildCsrfCookie,
  buildSessionCookie,
  clearCsrfCookie,
  clearSessionCookie,
  createSessionToken,
  DEFAULT_SESSION_TTL_S,
  randomToken,
  timingSafeUsername,
} from "./session";

function clientIp(request: Request): string {
  return (
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    "unknown"
  );
}

function guardStub(env: Env, ip: string) {
  // Avoid ":" in DO names — keep a stable opaque id.
  const safe = ip.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 120) || "unknown";
  // Name prefix versioned so ops can rotate lockout buckets without wiping the class.
  const id = env.ADMIN_LOGIN_GUARD_DO.idFromName(`admin-login-v1-${safe}`);
  return env.ADMIN_LOGIN_GUARD_DO.get(id);
}

/** Lockout storage is best-effort. A quota failure must not replace the login form with a worker crash. */
async function guardCall(stub: DurableObjectStub, path: "/check" | "/failure" | "/success"): Promise<Response | null> {
  try {
    return await stub.fetch(`https://admin-login-guard${path}`, { method: "POST" });
  } catch (err) {
    console.error("admin login guard unavailable", err);
    return null;
  }
}

export async function handleAdminLogin(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return errorJson(405, "method_not_allowed", "POST required");
  }

  const usernameCfg = (env.ADMIN_USERNAME ?? "").trim();
  const hash = (env.ADMIN_PASSWORD_HASH ?? "").trim();
  const sessionSecret = (env.ADMIN_SESSION_SECRET ?? "").trim();
  if (!usernameCfg || !hash || !sessionSecret) {
    return errorJson(503, "configuration_error", "Admin auth is not configured");
  }
  if (!env.ADMIN_LOGIN_GUARD_DO) {
    return errorJson(503, "configuration_error", "Admin login guard is not bound");
  }

  const ip = clientIp(request);
  const stub = guardStub(env, ip);
  const checkRes = await guardCall(stub, "/check");
  if (checkRes?.status === 429) {
    return errorJson(429, "locked", "Too many failed login attempts; try again later");
  }

  let body: { username?: string; password?: string };
  try {
    body = (await request.json()) as { username?: string; password?: string };
  } catch {
    return errorJson(400, "bad_request", "JSON body required");
  }

  const username = String(body.username ?? "").trim();
  const password = String(body.password ?? "");

  // Always run password verify even on username mismatch (constant-time + PBKDF2 cost).
  const userOk = timingSafeUsername(username, usernameCfg);
  const passOk = await verifyPassword(password, hash);
  if (!userOk || !passOk) {
    await guardCall(stub, "/failure");
    return errorJson(401, "invalid_credentials", "Invalid username or password");
  }

  await guardCall(stub, "/success");

  const sessionTok = await createSessionToken(sessionSecret, usernameCfg, DEFAULT_SESSION_TTL_S);
  const csrfTok = randomToken(32);
  const headers = new Headers({ "content-type": "application/json; charset=utf-8" });
  headers.append("set-cookie", buildSessionCookie(sessionTok, request));
  headers.append("set-cookie", buildCsrfCookie(csrfTok, request));

  return new Response(JSON.stringify({ ok: true, username: usernameCfg }), {
    status: 200,
    headers,
  });
}

export async function handleAdminLogout(request: Request, _env: Env): Promise<Response> {
  const headers = new Headers({ "content-type": "application/json; charset=utf-8" });
  headers.append("set-cookie", clearSessionCookie(request));
  headers.append("set-cookie", clearCsrfCookie(request));
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers });
}

export function handleAdminMe(identityUsername: string): Response {
  return json({ ok: true, username: identityUsername });
}
