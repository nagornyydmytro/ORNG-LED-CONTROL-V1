import { describe, expect, it } from "vitest";
import { signGatewayRequest, verifyGatewayRequest } from "../src/hmac";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const SECRET = "edge-gateway-unit-secret";

describe("edge gateway HMAC + config hygiene", () => {
  it("round-trips signature for device host", async () => {
    const ts = Math.floor(Date.now() / 1000);
    const { signatureHex } = await signGatewayRequest({
      secret: SECRET,
      method: "GET",
      hostname: "device.light.orng.wtf",
      pathWithQuery: "/ws/device",
      timestampSec: ts,
      body: "",
    });
    const ok = await verifyGatewayRequest({
      secret: SECRET,
      method: "GET",
      pathWithQuery: "/ws/device",
      body: "",
      hostnameHeader: "device.light.orng.wtf",
      timestampHeader: String(ts),
      signatureHeader: signatureHex,
    });
    expect(ok.ok).toBe(true);
  });

  it("wrangler.jsonc has no D1/R2/DO/KV/Queues bindings", () => {
    const raw = readFileSync(resolve(__dirname, "../wrangler.jsonc"), "utf8");
    // Strip // comments for a rough scan
    const text = raw.replace(/\/\/.*$/gm, "");
    expect(text).toMatch(/"account_id"\s*:\s*"a0ccabcaebdd05adca6a91cb7fddbfee"/);
    expect(text).toMatch(/"name"\s*:\s*"orng-led-edge-gateway"/);
    expect(text).not.toMatch(/d1_databases/);
    expect(text).not.toMatch(/r2_buckets/);
    expect(text).not.toMatch(/durable_objects/);
    expect(text).not.toMatch(/kv_namespaces/);
    expect(text).not.toMatch(/queues/);
  });
});
