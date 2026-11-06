import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { signGatewayRequest } from "../src/gateway/hmac";
import { createMemoryEnv } from "./helpers/memoryEnv";

const SECRET = "unit-gateway-hmac-secret";

describe("Events origin gateway gate", () => {
  it("allows /health on direct workers.dev in production", async () => {
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      GATEWAY_HMAC_SECRET: SECRET,
    });
    const res = await worker.fetch(
      new Request("https://orng-led-control.orng-events-bot.workers.dev/health", {
        headers: { host: "orng-led-control.orng-events-bot.workers.dev" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as { ok: boolean; gateway: boolean };
    expect(body.ok).toBe(true);
    expect(body.gateway).toBe(false);
  });

  it("blocks direct workers.dev admin path in production", async () => {
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      GATEWAY_HMAC_SECRET: SECRET,
      ADMIN_PASSWORD_HASH: "v1$pbkdf2-sha256$100000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
      ADMIN_SESSION_SECRET: "gw-origin-session",
    });
    const res = await worker.fetch(
      new Request("https://orng-led-control.orng-events-bot.workers.dev/api/admin/installations", {
        headers: { host: "orng-led-control.orng-events-bot.workers.dev" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(403);
    const body = (await res.json()) as { error: { code: string } };
    expect(body.error.code).toBe("direct_workers_dev_forbidden");
  });

  it("blocks direct workers.dev device pairing path in production", async () => {
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      GATEWAY_HMAC_SECRET: SECRET,
    });
    const res = await worker.fetch(
      new Request("https://orng-led-control.orng-events-bot.workers.dev/pair", {
        method: "POST",
        headers: {
          host: "orng-led-control.orng-events-bot.workers.dev",
          "content-type": "application/json",
        },
        body: "{}",
      }),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(403);
  });

  it("accepts HMAC-forwarded admin host (session still required)", async () => {
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      GATEWAY_HMAC_SECRET: SECRET,
      ADMIN_PASSWORD_HASH: "v1$pbkdf2-sha256$100000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
      ADMIN_SESSION_SECRET: "gw-origin-session",
      ADMIN_HOST: "light.orng.wtf",
    });
    const path = "/admin/devices";
    const ts = Math.floor(Date.now() / 1000);
    const { signatureHex } = await signGatewayRequest({
      secret: SECRET,
      method: "GET",
      hostname: "light.orng.wtf",
      pathWithQuery: path,
      timestampSec: ts,
      body: "",
    });
    const res = await worker.fetch(
      new Request(`https://orng-led-control.orng-events-bot.workers.dev${path}`, {
        headers: {
          host: "orng-led-control.orng-events-bot.workers.dev",
          "x-orng-gateway-host": "light.orng.wtf",
          "x-orng-gateway-ts": String(ts),
          "x-orng-gateway-sig": signatureHex,
        },
      }),
      env,
      {} as ExecutionContext,
    );
    // Signature accepted → admin role → login session required → 401
    expect(res.status).toBe(401);
  });

  it("rejects forged gateway headers without valid signature", async () => {
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      GATEWAY_HMAC_SECRET: SECRET,
    });
    const res = await worker.fetch(
      new Request("https://orng-led-control.orng-events-bot.workers.dev/pair", {
        method: "POST",
        headers: {
          host: "orng-led-control.orng-events-bot.workers.dev",
          "content-type": "application/json",
          "x-orng-gateway-host": "device.light.orng.wtf",
          "x-orng-gateway-ts": String(Math.floor(Date.now() / 1000)),
          "x-orng-gateway-sig": "00".repeat(32),
        },
        body: "{}",
      }),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(401);
  });
});
