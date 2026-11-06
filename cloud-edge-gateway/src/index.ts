/**
 * Stateless edge gateway (Notifications account).
 * Proxies light.orng.wtf / device.light.orng.wtf → Events workers.dev origin with HMAC.
 * No D1/R2/DO/KV/Queues. Never log secrets, session cookies, or device tokens.
 */

import {
  ALLOWED_GATEWAY_HOSTS,
  DEFAULT_GATEWAY_SKEW_S,
  GATEWAY_HOST_HEADER,
  GATEWAY_INTERNAL_HEADERS,
  GATEWAY_SIG_HEADER,
  GATEWAY_TS_HEADER,
  normalizeGatewayHost,
  signGatewayRequest,
} from "./hmac";

export interface Env {
  ORIGIN_URL: string;
  ADMIN_HOST: string;
  DEVICE_HOST: string;
  GATEWAY_HMAC_SECRET: string;
  GATEWAY_MAX_SKEW_S?: string;
  ENVIRONMENT?: string;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function requestHost(request: Request): string {
  const raw = (request.headers.get("host") ?? "").toLowerCase().trim();
  return raw.split(":")[0] ?? "";
}

function isWebSocketUpgrade(request: Request): boolean {
  const upgrade = (request.headers.get("upgrade") ?? "").toLowerCase();
  return upgrade === "websocket";
}

/** Headers safe to forward to origin (drop hop-by-hop + forged gateway + host). */
function buildForwardHeaders(
  request: Request,
  hostname: string,
  ts: number,
  sig: string,
): Headers {
  const out = new Headers();
  const drop = new Set<string>([
    "host",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "cf-connecting-ip",
    "cf-ray",
    "cf-visitor",
    "cf-ew-via",
    "cdn-loop",
    ...GATEWAY_INTERNAL_HEADERS,
  ]);
  request.headers.forEach((value, key) => {
    const lk = key.toLowerCase();
    if (drop.has(lk)) return;
    // Preserve Cookie / Authorization for origin session + device token checks.
    out.set(key, value);
  });
  out.set(GATEWAY_HOST_HEADER, hostname);
  out.set(GATEWAY_TS_HEADER, String(ts));
  out.set(GATEWAY_SIG_HEADER, sig);
  // Origin uses Host for some local checks; set to workers.dev target host from ORIGIN_URL.
  return out;
}

export default {
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health" || url.pathname === "/api/health") {
      return json(200, {
        service: "orng-led-edge-gateway",
        ok: true,
        bindings: [],
        note: "stateless edge only",
      });
    }

    const hostname = normalizeGatewayHost(requestHost(request));
    if (!hostname) {
      return json(404, { error: { code: "unknown_host", message: "Unrecognized host" } });
    }
    // Defense in depth vs env drift.
    if (!ALLOWED_GATEWAY_HOSTS.has(hostname)) {
      return json(404, { error: { code: "unknown_host", message: "Unrecognized host" } });
    }

    const secret = (env.GATEWAY_HMAC_SECRET ?? "").trim();
    if (!secret) {
      return json(503, {
        error: { code: "configuration_error", message: "Gateway secret not configured" },
      });
    }

    const originBase = (env.ORIGIN_URL ?? "").replace(/\/$/, "");
    if (!originBase.startsWith("https://")) {
      return json(503, {
        error: { code: "configuration_error", message: "ORIGIN_URL misconfigured" },
      });
    }

    const pathWithQuery = `${url.pathname}${url.search}`;
    // Buffer body once for signing (WebSocket GET has empty body).
    const bodyBuf = isWebSocketUpgrade(request)
      ? new Uint8Array()
      : new Uint8Array(await request.arrayBuffer());
    const timestampSec = Math.floor(Date.now() / 1000);
    const { signatureHex } = await signGatewayRequest({
      secret,
      method: request.method,
      hostname,
      pathWithQuery,
      timestampSec,
      body: bodyBuf,
    });

    const forwardHeaders = buildForwardHeaders(request, hostname, timestampSec, signatureHex);
    const originUrl = `${originBase}${pathWithQuery}`;

    if (isWebSocketUpgrade(request)) {
      // Preserve upgrade headers for 101.
      forwardHeaders.set("upgrade", "websocket");
      const connection = request.headers.get("connection");
      if (connection) forwardHeaders.set("connection", connection);
      const wsKey = request.headers.get("sec-websocket-key");
      if (wsKey) forwardHeaders.set("sec-websocket-key", wsKey);
      const wsVer = request.headers.get("sec-websocket-version");
      if (wsVer) forwardHeaders.set("sec-websocket-version", wsVer);
      const wsProto = request.headers.get("sec-websocket-protocol");
      if (wsProto) forwardHeaders.set("sec-websocket-protocol", wsProto);
      const wsExt = request.headers.get("sec-websocket-extensions");
      if (wsExt) forwardHeaders.set("sec-websocket-extensions", wsExt);

      return fetch(originUrl, {
        method: request.method,
        headers: forwardHeaders,
      });
    }

    return fetch(originUrl, {
      method: request.method,
      headers: forwardHeaders,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : bodyBuf,
      redirect: "manual",
    });
  },
} satisfies ExportedHandler<Env>;

// Keep DEFAULT_GATEWAY_SKEW_S referenced for config docs / tree-shaking check in tests.
void DEFAULT_GATEWAY_SKEW_S;
