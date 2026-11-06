/**
 * Host-based router for ORNG LED Cloudflare control plane.
 *
 * - Admin host (light.orng.wtf / local admin): Access-protected admin API
 * - Device host (device.light.orng.wtf / local device): pairing + device WSS only
 * - Direct workers.dev: /health only unless valid edge-gateway HMAC
 *
 * Cloudflare never generates Art-Net — only allowlisted commands to devices.
 */

import { handleAdminRequest, isAdminSpaPath } from "./admin/routes";
import { handleDeviceRequest } from "./device/routes";
import type { Env } from "./env";
import { errorJson, hostRole, isProductionEnvironment, json } from "./env";
import {
  DEFAULT_GATEWAY_SKEW_S,
  GATEWAY_HOST_HEADER,
  GATEWAY_SIG_HEADER,
  GATEWAY_TS_HEADER,
  verifyGatewayRequest,
} from "./gateway/hmac";

export { DeviceDO as DeviceDOV5 } from "./durable/DeviceDO";
export { PairingRateLimitDO } from "./durable/PairingRateLimitDO";
export { AdminLoginGuardDO } from "./durable/AdminLoginGuardDO";

function isWorkersDevHost(hostHeader: string | null): boolean {
  const portHost = (hostHeader ?? "").toLowerCase().trim();
  const hostOnly = portHost.split(":")[0] ?? "";
  return portHost.includes("workers.dev") || hostOnly.endsWith(".workers.dev");
}

function isHealthPath(pathname: string): boolean {
  return pathname === "/health" || pathname === "/api/health";
}

function pathWithQuery(url: URL): string {
  return `${url.pathname}${url.search}`;
}

function isWebSocketUpgrade(request: Request): boolean {
  return (request.headers.get("upgrade") ?? "").toLowerCase() === "websocket";
}

function withHostHeader(request: Request, host: string): Request {
  const headers = new Headers(request.headers);
  headers.set("host", host);
  return new Request(request, { headers });
}

export default {
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const incomingHost = request.headers.get("host");
    const ws = isWebSocketUpgrade(request);

    // Body for HMAC: empty for GET/HEAD/WebSocket; otherwise buffer once.
    let bodyBuf: ArrayBuffer = new ArrayBuffer(0);
    let routed: Request = request;
    if (!ws && request.method !== "GET" && request.method !== "HEAD") {
      bodyBuf = await request.arrayBuffer();
      routed = new Request(request.url, {
        method: request.method,
        headers: request.headers,
        body: bodyBuf.byteLength ? bodyBuf : undefined,
      });
    }

    const secret = (env.GATEWAY_HMAC_SECRET ?? "").trim();
    const hasGatewayHeaders =
      request.headers.has(GATEWAY_HOST_HEADER) ||
      request.headers.has(GATEWAY_TS_HEADER) ||
      request.headers.has(GATEWAY_SIG_HEADER);

    let effectiveHost: string | null = incomingHost;
    let gatewayOk = false;

    if (hasGatewayHeaders) {
      if (!secret) {
        return errorJson(503, "configuration_error", "Gateway HMAC secret not configured");
      }
      const skew = Number(env.GATEWAY_MAX_SKEW_S ?? DEFAULT_GATEWAY_SKEW_S);
      const verified = await verifyGatewayRequest({
        secret,
        method: request.method,
        pathWithQuery: pathWithQuery(url),
        body: bodyBuf,
        hostnameHeader: request.headers.get(GATEWAY_HOST_HEADER),
        timestampHeader: request.headers.get(GATEWAY_TS_HEADER),
        signatureHeader: request.headers.get(GATEWAY_SIG_HEADER),
        maxSkewS: Number.isFinite(skew) ? skew : DEFAULT_GATEWAY_SKEW_S,
      });
      if (!verified.ok) {
        return errorJson(401, "gateway_auth_failed", "Invalid gateway signature");
      }
      effectiveHost = verified.hostname;
      gatewayOk = true;
      routed = withHostHeader(routed, verified.hostname);
    } else if (isWorkersDevHost(incomingHost) && isProductionEnvironment(env)) {
      if (!isHealthPath(url.pathname)) {
        return errorJson(
          403,
          "direct_workers_dev_forbidden",
          "Sensitive routes require the edge gateway",
        );
      }
    }

    let role = hostRole(effectiveHost, env);

    // Path-based fallback for single-port local wrangler only — never in production.
    // Host may be any loopback port; the shell at / must be able to load /assets.
    if (role === "unknown" && !isProductionEnvironment(env)) {
      if (url.pathname.startsWith("/admin") || isAdminSpaPath(url.pathname)) {
        role = "admin";
      } else if (
        url.pathname.startsWith("/pair") ||
        url.pathname.startsWith("/ws/device") ||
        url.pathname.startsWith("/device")
      ) {
        role = "device";
      }
    }

    if (isHealthPath(url.pathname)) {
      return json({
        service: "orng-led-cloud",
        role,
        host: effectiveHost,
        gateway: gatewayOk,
        ok: true,
        artnet: false,
        note: "Control plane only — Art-Net stays on-device",
      });
    }

    if (role === "admin") {
      if (
        url.pathname.startsWith("/pair") ||
        url.pathname.startsWith("/ws/device") ||
        url.pathname.startsWith("/device/")
      ) {
        return errorJson(404, "not_found", "Device routes are not available on the admin host");
      }
      return handleAdminRequest(routed, env);
    }

    if (role === "device") {
      return handleDeviceRequest(routed, env);
    }

    return errorJson(400, "unknown_host", `Unrecognized host: ${effectiveHost ?? "(none)"}`);
  },
} satisfies ExportedHandler<Env>;
