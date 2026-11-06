import type { Env } from "../env";

/** CORS + Origin checks — admin API only speaks to ADMIN_ORIGIN. */

export function adminCorsHeaders(env: Env, request: Request): Headers {
  const headers = new Headers();
  const origin = request.headers.get("origin");
  const allowed = env.ADMIN_ORIGIN || "http://127.0.0.1:8787";
  if (origin && origin === allowed) {
    headers.set("access-control-allow-origin", origin);
    headers.set("vary", "Origin");
    headers.set("access-control-allow-credentials", "true");
    headers.set(
      "access-control-allow-headers",
      "content-type, authorization, x-csrf-token, x-dev-access-email",
    );
    headers.set("access-control-allow-methods", "GET,POST,OPTIONS");
  }
  return headers;
}

export function originAllowed(env: Env, request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) {
    // Same-origin / non-browser (curl, device tooling) — allow.
    return true;
  }
  return origin === (env.ADMIN_ORIGIN || "http://127.0.0.1:8787");
}

export function rejectBadOrigin(env: Env, request: Request): Response | null {
  if (originAllowed(env, request)) return null;
  return new Response(JSON.stringify({ error: { code: "forbidden_origin", message: "Origin not allowed" } }), {
    status: 403,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
