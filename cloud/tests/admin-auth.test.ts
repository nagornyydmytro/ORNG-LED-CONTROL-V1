import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { hashPassword, verifyPassword } from "../src/auth/password";
import {
  CSRF_COOKIE,
  CSRF_HEADER,
  SESSION_COOKIE,
  createSessionToken,
  requireCsrf,
  verifySessionToken,
} from "../src/auth/session";
import { ADMIN_LOGIN_MAX_FAILURES } from "../src/durable/AdminLoginGuardDO";
import { createMemoryEnv } from "./helpers/memoryEnv";

const USER = "y3k_notfound";
const PASS = "unit-test-password-not-prod";

function cookiePairs(headers: Headers): string {
  const listed = (headers as Headers & { getSetCookie?: () => string[] }).getSetCookie?.() ?? [];
  if (listed.length > 0) {
    return listed.map((item) => item.split(";")[0] ?? item).join("; ");
  }
  return headers.get("set-cookie") ?? "";
}

describe("password hash", () => {
  it("round-trips verify and rejects wrong password", async () => {
    const encoded = await hashPassword(PASS);
    expect(encoded.startsWith("v1$pbkdf2-sha256$")).toBe(true);
    expect(await verifyPassword(PASS, encoded)).toBe(true);
    expect(await verifyPassword("wrong", encoded)).toBe(false);
    expect(await verifyPassword(PASS, "not-a-hash")).toBe(false);
  });
});

describe("session cookie", () => {
  it("signs and verifies; rejects tamper", async () => {
    const secret = "session-secret-for-unit";
    const tok = await createSessionToken(secret, USER, 3600);
    const id = await verifySessionToken(secret, tok);
    expect(id?.username).toBe(USER);
    const tampered = tok.slice(0, -4) + "xxxx";
    expect(await verifySessionToken(secret, tampered)).toBeNull();
  });

  it("CSRF requires matching header for POST", () => {
    const ok = new Request("https://light.orng.wtf/admin/devices/x/revoke", {
      method: "POST",
      headers: {
        cookie: `${CSRF_COOKIE}=abc`,
        [CSRF_HEADER]: "abc",
      },
    });
    expect(requireCsrf(ok)).toBeNull();

    const bad = new Request("https://light.orng.wtf/admin/devices/x/revoke", {
      method: "POST",
      headers: {
        cookie: `${CSRF_COOKIE}=abc`,
        [CSRF_HEADER]: "zzz",
      },
    });
    expect(requireCsrf(bad)?.status).toBe(403);
  });
});

describe("admin login API", () => {
  it("logs in, sets cookies, unlocks API; wrong password 401", async () => {
    const encoded = await hashPassword(PASS);
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      DEV_BYPASS_ACCESS: "",
      ADMIN_USERNAME: USER,
      ADMIN_PASSWORD_HASH: encoded,
      ADMIN_SESSION_SECRET: "unit-admin-session-secret",
      ADMIN_HOST: "light.orng.wtf",
    });

    const bad = await worker.fetch(
      new Request("https://light.orng.wtf/admin/auth/login", {
        method: "POST",
        headers: { host: "light.orng.wtf", "content-type": "application/json" },
        body: JSON.stringify({ username: USER, password: "nope" }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(bad.status).toBe(401);

    const login = await worker.fetch(
      new Request("https://light.orng.wtf/admin/auth/login", {
        method: "POST",
        headers: { host: "light.orng.wtf", "content-type": "application/json" },
        body: JSON.stringify({ username: USER, password: PASS }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(login.status).toBe(200);
    const cookieJoined = cookiePairs(login.headers);
    expect(cookieJoined).toContain(SESSION_COOKIE);
    expect(cookieJoined).toContain(CSRF_COOKIE);

    const csrf = /orng_admin_csrf=([^;]+)/.exec(cookieJoined)?.[1];
    expect(csrf).toBeTruthy();

    const devices = await worker.fetch(
      new Request("https://light.orng.wtf/admin/devices", {
        headers: {
          host: "light.orng.wtf",
          cookie: cookieJoined,
        },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(devices.status).toBe(200);

    const mutateNoCsrf = await worker.fetch(
      new Request("https://light.orng.wtf/admin/devices/x/revoke", {
        method: "POST",
        headers: {
          host: "light.orng.wtf",
          cookie: cookieJoined,
          "content-type": "application/json",
        },
        body: "{}",
      }),
      env,
      {} as ExecutionContext,
    );
    expect(mutateNoCsrf.status).toBe(403);
  });

  it("locks after repeated failures", async () => {
    const encoded = await hashPassword(PASS);
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      DEV_BYPASS_ACCESS: "",
      ADMIN_USERNAME: USER,
      ADMIN_PASSWORD_HASH: encoded,
      ADMIN_SESSION_SECRET: "unit-admin-session-secret",
      ADMIN_HOST: "light.orng.wtf",
    });

    for (let i = 0; i < ADMIN_LOGIN_MAX_FAILURES; i++) {
      const res = await worker.fetch(
        new Request("https://light.orng.wtf/admin/auth/login", {
          method: "POST",
          headers: {
            host: "light.orng.wtf",
            "content-type": "application/json",
            "cf-connecting-ip": "203.0.113.9",
          },
          body: JSON.stringify({ username: USER, password: "wrong" }),
        }),
        env,
        {} as ExecutionContext,
      );
      expect(res.status).toBe(401);
    }

    const locked = await worker.fetch(
      new Request("https://light.orng.wtf/admin/auth/login", {
        method: "POST",
        headers: {
          host: "light.orng.wtf",
          "content-type": "application/json",
          "cf-connecting-ip": "203.0.113.9",
        },
        body: JSON.stringify({ username: USER, password: PASS }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(locked.status).toBe(429);
  });

  it("device host has no login form route surface (pair still device-only)", async () => {
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      DEV_BYPASS_ACCESS: "",
      ADMIN_PASSWORD_HASH: await hashPassword(PASS),
      ADMIN_SESSION_SECRET: "unit-admin-session-secret",
    });
    const res = await worker.fetch(
      new Request("https://device.light.orng.wtf/admin/auth/login", {
        method: "POST",
        headers: { host: "device.light.orng.wtf", "content-type": "application/json" },
        body: JSON.stringify({ username: USER, password: PASS }),
      }),
      env,
      {} as ExecutionContext,
    );
    // Device host must not expose admin login — 404 from device router or unknown admin.
    expect([404, 400, 405]).toContain(res.status);
  });

  it("still logs in when the lockout counter is over its request quota", async () => {
    const encoded = await hashPassword(PASS);
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      DEV_BYPASS_ACCESS: "",
      ADMIN_USERNAME: USER,
      ADMIN_PASSWORD_HASH: encoded,
      ADMIN_SESSION_SECRET: "unit-admin-session-secret",
      ADMIN_HOST: "light.orng.wtf",
    });
    env.ADMIN_LOGIN_GUARD_DO = {
      idFromName: () => ({ name: "exhausted" }) as DurableObjectId,
      get: () =>
        ({
          fetch: async () => {
            throw new Error(
              "Exceeded allowed volume of requests in Durable Objects free tier.",
            );
          },
        }) as unknown as DurableObjectStub,
    } as unknown as DurableObjectNamespace;

    const wrong = await worker.fetch(
      new Request("https://light.orng.wtf/admin/auth/login", {
        method: "POST",
        headers: { host: "light.orng.wtf", "content-type": "application/json" },
        body: JSON.stringify({ username: USER, password: "nope" }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(wrong.status).toBe(401);
    expect(await wrong.text()).not.toContain("<!DOCTYPE");

    const login = await worker.fetch(
      new Request("https://light.orng.wtf/admin/auth/login", {
        method: "POST",
        headers: { host: "light.orng.wtf", "content-type": "application/json" },
        body: JSON.stringify({ username: USER, password: PASS }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(login.status).toBe(200);
    expect(cookiePairs(login.headers)).toContain(SESSION_COOKIE);
  });
});
