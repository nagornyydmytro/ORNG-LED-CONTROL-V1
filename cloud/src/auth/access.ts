/**
 * Compatibility shim — Cloudflare Access removed.
 * Admin auth is cookie session + salted password hash (see session.ts / login.ts).
 */

export {
  localBypassAllowed,
  resolveAdminSession as resolveAccess,
  sessionConfigurationError as accessConfigurationError,
  type AdminIdentity as AccessIdentity,
  type SessionResult as AccessResult,
} from "./session";

import type { Env } from "../env";
import { resolveAdminSession } from "./session";

/** @deprecated Use resolveAdminSession — kept for older tests. */
export async function verifyAccessJwt(
  request: Request,
  env: Env,
): Promise<{ email: string; sub: string } | null> {
  const result = await resolveAdminSession(request, env);
  if (!result.ok) return null;
  return { email: result.identity.email, sub: result.identity.sub };
}

/** @deprecated Prefer resolveAdminSession. */
export function requireAccess(identity: { email: string } | null): Response | null {
  if (identity) return null;
  return new Response(JSON.stringify({ error: { code: "unauthorized", message: "Login required" } }), {
    status: 401,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
