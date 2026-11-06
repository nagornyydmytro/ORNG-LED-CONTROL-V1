import { describe, expect, it, vi, afterEach } from "vitest";
import worker, { type Env } from "../src/index";
import { GATEWAY_HOST_HEADER, GATEWAY_SIG_HEADER, GATEWAY_TS_HEADER } from "../src/hmac";

const SECRET = "edge-fetch-secret";

function env(overrides: Partial<Env> = {}): Env {
  return {
    ORIGIN_URL: "https://orng-led-control.orng-events-bot.workers.dev",
    ADMIN_HOST: "light.orng.wtf",
    DEVICE_HOST: "device.light.orng.wtf",
    GATEWAY_HMAC_SECRET: SECRET,
    GATEWAY_MAX_SKEW_S: "60",
    ENVIRONMENT: "production",
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("edge gateway fetch", () => {
  it("returns 404 for unknown host", async () => {
    const res = await worker.fetch(
      new Request("https://evil.example/health-not", {
        headers: { host: "evil.example" },
      }),
      env(),
      {} as ExecutionContext,
    );
    expect(res.status).toBe(404);
  });

  it("local /health does not require origin", async () => {
    const res = await worker.fetch(
      new Request("https://light.orng.wtf/health", { headers: { host: "light.orng.wtf" } }),
      env(),
      {} as ExecutionContext,
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as { service: string; bindings: unknown[] };
    expect(body.service).toBe("orng-led-edge-gateway");
    expect(body.bindings).toEqual([]);
  });

  it("strips forged gateway headers and signs before proxy", async () => {
    const calls: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const req = input instanceof Request ? input : new Request(String(input), init);
        calls.push(req);
        return new Response(JSON.stringify({ proxied: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );

    const res = await worker.fetch(
      new Request("https://light.orng.wtf/api/admin/x", {
        method: "POST",
        headers: {
          host: "light.orng.wtf",
          "content-type": "application/json",
          [GATEWAY_HOST_HEADER]: "device.light.orng.wtf",
          [GATEWAY_TS_HEADER]: "1",
          [GATEWAY_SIG_HEADER]: "deadbeef",
          authorization: "Bearer should-pass-through",
        },
        body: JSON.stringify({ a: 1 }),
      }),
      env(),
      {} as ExecutionContext,
    );
    expect(res.status).toBe(200);
    expect(calls).toHaveLength(1);
    const forwarded = calls[0]!;
    expect(forwarded.url).toBe(
      "https://orng-led-control.orng-events-bot.workers.dev/api/admin/x",
    );
    expect(forwarded.headers.get(GATEWAY_HOST_HEADER)).toBe("light.orng.wtf");
    expect(forwarded.headers.get(GATEWAY_TS_HEADER)).not.toBe("1");
    expect(forwarded.headers.get(GATEWAY_SIG_HEADER)).not.toBe("deadbeef");
    expect(forwarded.headers.get("authorization")).toBe("Bearer should-pass-through");
  });

  it("proxies websocket upgrade to origin", async () => {
    const calls: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const req = input instanceof Request ? input : new Request(String(input), init);
        calls.push(req);
        // Node's Response forbids status 101; assert upgrade forwarding instead.
        return new Response("switching", { status: 200, statusText: "OK" });
      }),
    );

    const res = await worker.fetch(
      new Request("https://device.light.orng.wtf/ws/device", {
        headers: {
          host: "device.light.orng.wtf",
          upgrade: "websocket",
          connection: "Upgrade",
          "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
          "sec-websocket-version": "13",
        },
      }),
      env(),
      {} as ExecutionContext,
    );
    expect(res.status).toBe(200);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.headers.get("upgrade")).toBe("websocket");
    expect(calls[0]!.headers.get("sec-websocket-key")).toBe("dGhlIHNhbXBsZSBub25jZQ==");
    expect(calls[0]!.headers.get(GATEWAY_HOST_HEADER)).toBe("device.light.orng.wtf");
  });

  it("fails closed without secret", async () => {
    const res = await worker.fetch(
      new Request("https://light.orng.wtf/x", { headers: { host: "light.orng.wtf" } }),
      env({ GATEWAY_HMAC_SECRET: "" }),
      {} as ExecutionContext,
    );
    expect(res.status).toBe(503);
  });
});
