/**
 * Cross-account edge gateway HMAC (Events origin ↔ Notifications gateway).
 *
 * Canonical string (UTF-8):
 *   v1\\n{METHOD}\\n{HOSTNAME}\\n{PATH_WITH_QUERY}\\n{UNIX_TS}\\n{BODY_SHA256_HEX}
 *
 * Headers (lowercase):
 *   x-orng-gateway-host  — allowed public hostname
 *   x-orng-gateway-ts    — unix seconds
 *   x-orng-gateway-sig   — hex HMAC-SHA256
 *
 * Keep in sync with cloud/src/gateway/hmac.ts
 */

export const GATEWAY_HOST_HEADER = "x-orng-gateway-host";
export const GATEWAY_TS_HEADER = "x-orng-gateway-ts";
export const GATEWAY_SIG_HEADER = "x-orng-gateway-sig";

/** Strip any client-supplied gateway headers before (re)signing. */
export const GATEWAY_INTERNAL_HEADERS = [
  GATEWAY_HOST_HEADER,
  GATEWAY_TS_HEADER,
  GATEWAY_SIG_HEADER,
] as const;

export const ALLOWED_GATEWAY_HOSTS = new Set(["light.orng.wtf", "device.light.orng.wtf"]);

export const DEFAULT_GATEWAY_SKEW_S = 60;

const te = new TextEncoder();

export function normalizeGatewayHost(host: string | null | undefined): string | null {
  if (!host) return null;
  const h = host.toLowerCase().trim().split(":")[0] ?? "";
  return ALLOWED_GATEWAY_HOSTS.has(h) ? h : null;
}

export async function sha256Hex(data: ArrayBuffer | Uint8Array | string): Promise<string> {
  const bytes =
    typeof data === "string"
      ? te.encode(data)
      : data instanceof Uint8Array
        ? data
        : new Uint8Array(data);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return bufferToHex(digest);
}

export function canonicalGatewayString(parts: {
  method: string;
  hostname: string;
  pathWithQuery: string;
  timestampSec: number;
  bodySha256Hex: string;
}): string {
  return [
    "v1",
    parts.method.toUpperCase(),
    parts.hostname.toLowerCase(),
    parts.pathWithQuery || "/",
    String(parts.timestampSec),
    parts.bodySha256Hex.toLowerCase(),
  ].join("\n");
}

export async function hmacSha256Hex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    te.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, te.encode(message));
  return bufferToHex(sig);
}

/** Constant-time hex compare; lengths must match. */
export function timingSafeEqualHex(a: string, b: string): boolean {
  const aa = a.toLowerCase();
  const bb = b.toLowerCase();
  if (aa.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < aa.length; i++) {
    diff |= aa.charCodeAt(i) ^ bb.charCodeAt(i);
  }
  return diff === 0;
}

export async function signGatewayRequest(opts: {
  secret: string;
  method: string;
  hostname: string;
  pathWithQuery: string;
  timestampSec: number;
  body: ArrayBuffer | Uint8Array | string;
}): Promise<{ signatureHex: string; bodySha256Hex: string; canonical: string }> {
  const bodySha256Hex = await sha256Hex(opts.body);
  const canonical = canonicalGatewayString({
    method: opts.method,
    hostname: opts.hostname,
    pathWithQuery: opts.pathWithQuery,
    timestampSec: opts.timestampSec,
    bodySha256Hex,
  });
  const signatureHex = await hmacSha256Hex(opts.secret, canonical);
  return { signatureHex, bodySha256Hex, canonical };
}

export type GatewayVerifyResult =
  | { ok: true; hostname: string; timestampSec: number }
  | { ok: false; reason: string };

export async function verifyGatewayRequest(opts: {
  secret: string;
  method: string;
  pathWithQuery: string;
  body: ArrayBuffer | Uint8Array | string;
  hostnameHeader: string | null;
  timestampHeader: string | null;
  signatureHeader: string | null;
  nowSec?: number;
  maxSkewS?: number;
}): Promise<GatewayVerifyResult> {
  const hostname = normalizeGatewayHost(opts.hostnameHeader);
  if (!hostname) return { ok: false, reason: "invalid_host" };

  const tsRaw = (opts.timestampHeader ?? "").trim();
  if (!/^\d+$/.test(tsRaw)) return { ok: false, reason: "invalid_timestamp" };
  const timestampSec = Number(tsRaw);
  const now = opts.nowSec ?? Math.floor(Date.now() / 1000);
  const skew = opts.maxSkewS ?? DEFAULT_GATEWAY_SKEW_S;
  if (Math.abs(now - timestampSec) > skew) return { ok: false, reason: "timestamp_skew" };

  const sig = (opts.signatureHeader ?? "").trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(sig)) return { ok: false, reason: "invalid_signature" };

  const { signatureHex } = await signGatewayRequest({
    secret: opts.secret,
    method: opts.method,
    hostname,
    pathWithQuery: opts.pathWithQuery,
    timestampSec,
    body: opts.body,
  });
  if (!timingSafeEqualHex(sig, signatureHex)) return { ok: false, reason: "signature_mismatch" };
  return { ok: true, hostname, timestampSec };
}

function bufferToHex(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < bytes.length; i++) out += bytes[i]!.toString(16).padStart(2, "0");
  return out;
}
