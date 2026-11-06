import { describe, expect, it } from "vitest";
import {
  signGatewayRequest,
  timingSafeEqualHex,
  verifyGatewayRequest,
} from "../src/gateway/hmac";

const SECRET = "test-gateway-secret-do-not-use-prod";

describe("gateway HMAC", () => {
  it("signs and verifies a POST body", async () => {
    const body = JSON.stringify({ hello: "world" });
    const ts = Math.floor(Date.now() / 1000);
    const { signatureHex } = await signGatewayRequest({
      secret: SECRET,
      method: "POST",
      hostname: "light.orng.wtf",
      pathWithQuery: "/api/admin/ping",
      timestampSec: ts,
      body,
    });
    const ok = await verifyGatewayRequest({
      secret: SECRET,
      method: "POST",
      pathWithQuery: "/api/admin/ping",
      body,
      hostnameHeader: "light.orng.wtf",
      timestampHeader: String(ts),
      signatureHeader: signatureHex,
    });
    expect(ok).toEqual({ ok: true, hostname: "light.orng.wtf", timestampSec: ts });
  });

  it("rejects unknown host", async () => {
    const ts = Math.floor(Date.now() / 1000);
    const { signatureHex } = await signGatewayRequest({
      secret: SECRET,
      method: "GET",
      hostname: "light.orng.wtf",
      pathWithQuery: "/health",
      timestampSec: ts,
      body: "",
    });
    const bad = await verifyGatewayRequest({
      secret: SECRET,
      method: "GET",
      pathWithQuery: "/health",
      body: "",
      hostnameHeader: "evil.example.com",
      timestampHeader: String(ts),
      signatureHeader: signatureHex,
    });
    expect(bad).toEqual({ ok: false, reason: "invalid_host" });
  });

  it("rejects tampered path", async () => {
    const ts = Math.floor(Date.now() / 1000);
    const { signatureHex } = await signGatewayRequest({
      secret: SECRET,
      method: "GET",
      hostname: "device.light.orng.wtf",
      pathWithQuery: "/pair",
      timestampSec: ts,
      body: "",
    });
    const bad = await verifyGatewayRequest({
      secret: SECRET,
      method: "GET",
      pathWithQuery: "/pair?x=1",
      body: "",
      hostnameHeader: "device.light.orng.wtf",
      timestampHeader: String(ts),
      signatureHeader: signatureHex,
    });
    expect(bad).toEqual({ ok: false, reason: "signature_mismatch" });
  });

  it("rejects expired timestamp", async () => {
    const ts = Math.floor(Date.now() / 1000) - 600;
    const { signatureHex } = await signGatewayRequest({
      secret: SECRET,
      method: "GET",
      hostname: "light.orng.wtf",
      pathWithQuery: "/health",
      timestampSec: ts,
      body: "",
    });
    const bad = await verifyGatewayRequest({
      secret: SECRET,
      method: "GET",
      pathWithQuery: "/health",
      body: "",
      hostnameHeader: "light.orng.wtf",
      timestampHeader: String(ts),
      signatureHeader: signatureHex,
      maxSkewS: 60,
    });
    expect(bad).toEqual({ ok: false, reason: "timestamp_skew" });
  });

  it("timingSafeEqualHex is length-sensitive", () => {
    expect(timingSafeEqualHex("aa", "aa")).toBe(true);
    expect(timingSafeEqualHex("aa", "ab")).toBe(false);
    expect(timingSafeEqualHex("aa", "aaa")).toBe(false);
  });
});
