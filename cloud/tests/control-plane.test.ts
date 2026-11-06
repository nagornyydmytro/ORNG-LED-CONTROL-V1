import { describe, expect, it, beforeEach } from "vitest";
import worker from "../src/index";
import { hashToken, extractDeviceToken, queryTokenForbidden, verifyDeviceToken } from "../src/auth/device";
import { resolveAccess, verifyAccessJwt, accessConfigurationError } from "../src/auth/access";
import { RASPBERRY_SCOPES, DESKTOP_SCOPES } from "../src/auth/scopes";
import { hostRole } from "../src/env";
import {
  completePairing,
  consumePairingEnvelope,
  hashPairingCode,
  revokeInstallation,
  initiateTokenRotation,
  ackTokenRotation,
  getRotationEnvelope,
  startPairing,
  approvePairing,
} from "../src/pairing";
import {
  isAllowedScenePath,
  packBundle,
  unpackBundle,
  publishRelease,
  assignScene,
  getAssignment,
  assertDownloadAllowed,
  getReleaseBundleAuthorized,
  assertStrictSceneId,
  MAX_FILE_BYTES,
  MAX_TOTAL_DECODED_BYTES,
  ReleaseError,
} from "../src/scenes/releases";
import {
  MOMENTARY_PRESS_TYPES,
  isAllowedCommandType,
  HOLD_LEASE_MS,
} from "../src/protocol/types";
import { getCommand, logCommand, ackCommand } from "../src/db/commands";
import {
  decryptCredentialEnvelope,
  envelopeFromB64,
  generateDeviceKeyPair,
} from "../src/protocol/crypto";
import { createMemoryEnv, seedCredential } from "./helpers/memoryEnv";
import { packValidScene, validSceneFiles } from "./helpers/sceneBundle";
import { publishAssignedWorkspace } from "../src/scenes/workspace";
import { PAIRING_RATE_MAX } from "../src/durable/PairingRateLimitDO";

function adminReq(path: string, init: RequestInit = {}, host = "localhost:8787"): Request {
  const headers = new Headers(init.headers);
  headers.set("host", host);
  if (!headers.has("x-dev-access-email")) headers.set("x-dev-access-email", "admin@test.local");
  if (!headers.has("origin")) headers.set("origin", "http://127.0.0.1:8787");
  return new Request(`http://${host}${path}`, { ...init, headers });
}

function deviceReq(path: string, init: RequestInit = {}, host = "localhost:8788"): Request {
  const headers = new Headers(init.headers);
  headers.set("host", host);
  return new Request(`http://${host}${path}`, { ...init, headers });
}

async function pairDevice(
  env: ReturnType<typeof createMemoryEnv>,
  challenge: string,
  platform: "raspberry" | "desktop_windows" | "development" = "raspberry",
  ip = "10.0.0.1",
) {
  const keys = await generateDeviceKeyPair();
  const started = await startPairing(env, {
    clientChallenge: challenge,
    publicKeyJwk: keys.publicKeyJwk,
    ipHint: ip,
  });
  const approved = await approvePairing(env, {
    pairingId: started.pairing_id,
    code: started.code,
    name: "Test Device",
    platform,
    actor: "admin@test.local",
  });
  expect(approved).not.toHaveProperty("token");
  const completed = await completePairing(env, {
    pairingId: started.pairing_id,
    clientChallenge: challenge,
  });
  const token = await decryptCredentialEnvelope(
    keys.privateKey,
    envelopeFromB64(completed.credential_envelope_b64),
  );
  await consumePairingEnvelope(env, {
    pairingId: started.pairing_id,
    clientChallenge: challenge,
  });
  return { ...approved, token, keys, pairingId: started.pairing_id };
}

describe("host split", () => {
  const env = createMemoryEnv();

  it("device host blocks admin routes", async () => {
    const res = await worker.fetch(
      deviceReq("/admin/devices"),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(404);
    const body = (await res.json()) as { error: { code: string } };
    expect(body.error.code).toBe("not_found");
  });

  it("admin host blocks device pair routes", async () => {
    const res = await worker.fetch(
      adminReq("/pair/start", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ client_challenge: "challenge12345" }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(404);
  });

  it("hostRole maps production hosts", () => {
    expect(hostRole("light.orng.wtf", env)).toBe("admin");
    expect(hostRole("device.light.orng.wtf", env)).toBe("device");
  });

  it("local non-default port still serves the admin shell and asset path", async () => {
    const shell = await worker.fetch(
      adminReq("/", {}, "127.0.0.1:8807"),
      env,
      {} as ExecutionContext,
    );
    expect(shell.status).toBe(200);
    const asset = await worker.fetch(
      adminReq("/assets/index.js", {}, "127.0.0.1:8807"),
      env,
      {} as ExecutionContext,
    );
    const assetBody = (await asset.json()) as { error?: { code: string } };
    expect(assetBody.error?.code).not.toBe("unknown_host");
  });

  it("production unknown host does not serve admin assets", async () => {
    const prod = createMemoryEnv({ ENVIRONMENT: "production" });
    const res = await worker.fetch(
      new Request("https://evil.example/assets/index.js", {
        headers: { host: "evil.example" },
      }),
      prod,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: { code: string } };
    expect(body.error.code).toBe("unknown_host");
  });
});

describe("Admin session auth fail-closed", () => {
  it("production host without password/session secrets → configuration_error", async () => {
    const env = createMemoryEnv({
      ADMIN_PASSWORD_HASH: undefined,
      ADMIN_SESSION_SECRET: undefined,
      DEV_BYPASS_ACCESS: "",
      ADMIN_HOST: "light.orng.wtf",
    });
    const req = new Request("https://light.orng.wtf/admin/devices", {
      headers: { host: "light.orng.wtf" },
    });
    const cfg = accessConfigurationError(env, req);
    expect(cfg?.status).toBe(503);
    const body = (await cfg!.json()) as { error: { code: string } };
    expect(body.error.code).toBe("configuration_error");

    const resolved = await resolveAccess(req, env);
    expect(resolved.ok).toBe(false);
    if (!resolved.ok) expect(resolved.response.status).toBe(503);

    const res = await worker.fetch(
      adminReq("/admin/devices", {}, "light.orng.wtf"),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(503);
  });

  it("production SPA is public; admin API requires session (header spoof ignored)", async () => {
    const env = createMemoryEnv({
      DEV_BYPASS_ACCESS: "1", // ignored on production ENVIRONMENT below
      ENVIRONMENT: "production",
      ADMIN_HOST: "light.orng.wtf",
      ADMIN_PASSWORD_HASH: "v1$pbkdf2-sha256$100000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
      ADMIN_SESSION_SECRET: "prod-session-secret",
    });
    const spa = await worker.fetch(
      new Request("https://light.orng.wtf/", {
        headers: { host: "light.orng.wtf", "x-dev-access-email": "spoof@evil.test" },
      }),
      env,
      {} as ExecutionContext,
    );
    // SPA shell is public so the login form can load (ASSETS may be absent → fallback HTML).
    expect([200, 404]).toContain(spa.status);

    const api = await worker.fetch(
      new Request("https://light.orng.wtf/admin/devices", {
        headers: { host: "light.orng.wtf", "x-dev-access-email": "spoof@evil.test" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(api.status).toBe(401);
  });

  it("production missing session cookie → 401 for API (SPA stays public)", async () => {
    const env = createMemoryEnv({
      DEV_BYPASS_ACCESS: "",
      ENVIRONMENT: "production",
      ADMIN_HOST: "light.orng.wtf",
      ADMIN_PASSWORD_HASH: "v1$pbkdf2-sha256$100000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
      ADMIN_SESSION_SECRET: "prod-session-secret",
    });
    const spa = await worker.fetch(
      new Request("https://light.orng.wtf/", { headers: { host: "light.orng.wtf" } }),
      env,
      {} as ExecutionContext,
    );
    expect([200, 404]).toContain(spa.status);

    const api = await worker.fetch(
      new Request("https://light.orng.wtf/admin/devices", { headers: { host: "light.orng.wtf" } }),
      env,
      {} as ExecutionContext,
    );
    expect(api.status).toBe(401);
  });

  it("admin without session fails (no header trust)", async () => {
    const env = createMemoryEnv({
      DEV_BYPASS_ACCESS: "",
      ADMIN_PASSWORD_HASH: "v1$pbkdf2-sha256$100000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
      ADMIN_SESSION_SECRET: "prod-session-secret",
    });
    const identity = await verifyAccessJwt(
      new Request("http://localhost/admin/devices", {
        headers: {
          host: "localhost:8787",
          "cf-access-authenticated-user-email": "spoof@evil.test",
          "x-dev-access-email": "spoof@evil.test",
        },
      }),
      env,
    );
    expect(identity).toBeNull();

    const res = await worker.fetch(
      adminReq("/admin/devices", {}, "localhost:8787"),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(401);
  });

  it("local bypass requires DEV_BYPASS_ACCESS=1 + localhost + email header", async () => {
    const env = createMemoryEnv({ DEV_BYPASS_ACCESS: "1" });
    const identity = await verifyAccessJwt(adminReq("/admin/devices"), env);
    expect(identity?.email).toBe("admin@test.local");

    const noEmail = await verifyAccessJwt(
      new Request("http://localhost:8787/admin/devices", {
        headers: { host: "localhost:8787" },
      }),
      env,
    );
    expect(noEmail).toBeNull();

    const prodBypassIgnored = await verifyAccessJwt(
      new Request("https://light.orng.wtf/admin/devices", {
        headers: {
          host: "light.orng.wtf",
          "x-dev-access-email": "admin@test.local",
        },
      }),
      {
        ...env,
        ENVIRONMENT: "production",
        DEV_BYPASS_ACCESS: "1",
        ADMIN_PASSWORD_HASH: "v1$pbkdf2-sha256$100000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        ADMIN_SESSION_SECRET: "prod-session-secret",
      },
    );
    expect(prodBypassIgnored).toBeNull();
  });

  it("ENVIRONMENT=production disables localhost DEV bypass and path-host fallback", async () => {
    const env = createMemoryEnv({
      ENVIRONMENT: "production",
      DEV_BYPASS_ACCESS: "1",
      LOCAL_ADMIN_HOST: "",
      LOCAL_DEVICE_HOST: "",
      ADMIN_PASSWORD_HASH: undefined,
      ADMIN_SESSION_SECRET: undefined,
    });
    const bypass = await verifyAccessJwt(adminReq("/admin/devices"), env);
    expect(bypass).toBeNull();
    const cfg = accessConfigurationError(env, adminReq("/admin/devices", {}, "light.orng.wtf"));
    expect(cfg?.status).toBe(503);

    const unknown = await worker.fetch(
      new Request("https://example.workers.dev/admin/devices", {
        headers: { host: "example.workers.dev" },
      }),
      env,
      {} as ExecutionContext,
    );
    // Production workers.dev is locked to /health unless gateway HMAC is present.
    expect(unknown.status).toBe(403);
    const body = (await unknown.json()) as { error?: { code?: string } };
    expect(body.error?.code).toBe("direct_workers_dev_forbidden");
  });
});

describe("pairing envelope (no module-global token)", () => {
  let env: ReturnType<typeof createMemoryEnv>;

  beforeEach(() => {
    env = createMemoryEnv();
  });

  it("stores envelope in D1; admin approve returns no token; cold-start complete decrypts", async () => {
    const keys = await generateDeviceKeyPair();
    const started = await startPairing(env, {
      clientChallenge: "challenge-abc-123",
      publicKeyJwk: keys.publicKeyJwk,
      ipHint: "1.2.3.4",
    });
    expect(started.code).toHaveLength(8);

    const session = env.__db.tables.pairing_sessions[0]!;
    expect(session.code_hash).toBe(await hashPairingCode(started.code));
    expect(session.public_key_jwk).toBeTruthy();
    expect(session.credential_envelope).toBeNull();

    const approved = await approvePairing(env, {
      pairingId: started.pairing_id,
      code: started.code,
      name: "Bar Pi",
      platform: "raspberry",
      actor: "admin@test.local",
    });
    expect(approved).toMatchObject({ approved: true, pairing: "approved" });
    expect(approved).not.toHaveProperty("token");

    const afterApprove = env.__db.tables.pairing_sessions[0]!;
    expect(afterApprove.credential_envelope).toBeTruthy();
    const cred = env.__db.tables.device_credentials[0]!;
    expect(JSON.parse(String(cred.scopes_json))).toEqual([...RASPBERRY_SCOPES]);

    // Simulate cold start: clear any in-memory maps (none) — complete from D1 only
    const completed = await completePairing(env, {
      pairingId: started.pairing_id,
      clientChallenge: "challenge-abc-123",
    });
    expect(completed.credential_envelope_b64).toBeTruthy();
    expect(completed).not.toHaveProperty("token");

    const token = await decryptCredentialEnvelope(
      keys.privateKey,
      envelopeFromB64(completed.credential_envelope_b64),
    );
    expect(cred.token_hash).toBe(await hashToken(token));
    expect(JSON.stringify(env.__db.tables.pairing_sessions)).not.toContain(token);
    expect(JSON.stringify(env.__db.tables.device_credentials)).not.toContain(token);

    // Repeat complete until consume
    const again = await completePairing(env, {
      pairingId: started.pairing_id,
      clientChallenge: "challenge-abc-123",
    });
    expect(again.credential_envelope_b64).toBe(completed.credential_envelope_b64);

    await consumePairingEnvelope(env, {
      pairingId: started.pairing_id,
      clientChallenge: "challenge-abc-123",
    });
    await expect(
      completePairing(env, {
        pairingId: started.pairing_id,
        clientChallenge: "challenge-abc-123",
      }),
    ).rejects.toMatchObject({ code: "token_consumed" });
  });

  it("admin HTTP approve does not return plaintext token", async () => {
    const keys = await generateDeviceKeyPair();
    const startRes = await worker.fetch(
      deviceReq("/pair/start", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          client_challenge: "http-e2e-challenge",
          public_key_jwk: keys.publicKeyJwk,
        }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(startRes.status).toBe(200);
    const started = (await startRes.json()) as { pairing_id: string; code: string };

    const approveRes = await worker.fetch(
      adminReq("/admin/pair/approve", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          pairing_id: started.pairing_id,
          code: started.code,
          name: "Desk",
          platform: "desktop_windows",
        }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(approveRes.status).toBe(200);
    const approvedBody = (await approveRes.json()) as Record<string, unknown>;
    expect(approvedBody.token).toBeUndefined();
    expect(approvedBody.approved).toBe(true);
    expect(approvedBody.installation_id).toBeTruthy();

    const completeRes = await worker.fetch(
      deviceReq("/pair/complete", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          pairing_id: started.pairing_id,
          client_challenge: "http-e2e-challenge",
        }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(completeRes.status).toBe(200);
    const done = (await completeRes.json()) as {
      credential_envelope_b64: string;
      installation_id: string;
    };
    expect(done.credential_envelope_b64).toBeTruthy();
    const scopes = JSON.parse(
      String(env.__db.tables.device_credentials[0]!.scopes_json),
    ) as string[];
    expect(scopes).toEqual([...DESKTOP_SCOPES]);
  });

  it("same IP different challenges still rate limited", async () => {
    const ip = "203.0.113.50";
    for (let i = 0; i < PAIRING_RATE_MAX; i++) {
      const keys = await generateDeviceKeyPair();
      await startPairing(env, {
        clientChallenge: `challenge-unique-${i}-${Date.now()}`,
        publicKeyJwk: keys.publicKeyJwk,
        ipHint: ip,
      });
    }
    const keys = await generateDeviceKeyPair();
    await expect(
      startPairing(env, {
        clientChallenge: "challenge-after-limit-zzzz",
        publicKeyJwk: keys.publicKeyJwk,
        ipHint: ip,
      }),
    ).rejects.toMatchObject({ code: "rate_limited", status: 429 });
  });

  it("revoke clears credentials; token no longer verifies", async () => {
    const paired = await pairDevice(env, "chal-revoke-01");
    expect(await verifyDeviceToken(env, paired.token)).toMatchObject({
      installationId: paired.installation_id,
      revoked: false,
    });
    await revokeInstallation(env, paired.installation_id, "a@b.c");
    expect(await verifyDeviceToken(env, paired.token)).toBeNull();
  });

  it("safe rotation: old valid until ACK; list shows rotation_pending", async () => {
    const paired = await pairDevice(env, "chal-rotate-01");
    const oldToken = paired.token;
    const rotKeys = await generateDeviceKeyPair();
    await initiateTokenRotation(env, paired.installation_id, "admin@test", {
      publicKeyJwk: rotKeys.publicKeyJwk,
    });

    const cred = env.__db.tables.device_credentials[0]!;
    expect(cred.rotation_pending).toBe(1);
    expect(await verifyDeviceToken(env, oldToken)).toMatchObject({
      installationId: paired.installation_id,
      rotationPending: true,
    });

    const envelope = await getRotationEnvelope(env, paired.installation_id);
    expect(envelope.rotation_pending).toBe(true);
    expect(envelope.credential_envelope_b64).toBeTruthy();
    const newToken = await decryptCredentialEnvelope(
      rotKeys.privateKey,
      envelopeFromB64(envelope.credential_envelope_b64!),
    );

    // New token also verifies while pending (OR match)
    expect(await verifyDeviceToken(env, newToken)).toBeTruthy();

    const ack1 = await ackTokenRotation(env, paired.installation_id, newToken);
    expect(ack1.activated).toBe(true);
    const ack2 = await ackTokenRotation(env, paired.installation_id, newToken);
    expect(ack2.activated).toBe(false);

    expect(await verifyDeviceToken(env, oldToken)).toBeNull();
    expect(await verifyDeviceToken(env, newToken)).toMatchObject({
      installationId: paired.installation_id,
      rotationPending: false,
    });
  });
});

describe("WSS token extraction", () => {
  it("production rejects ?token=", () => {
    const env = createMemoryEnv({
      DEV_ALLOW_QUERY_TOKEN: undefined,
    });
    const req = new Request("https://device.light.orng.wtf/ws/device?token=secrettokensecrettoken", {
      headers: { host: "device.light.orng.wtf" },
    });
    expect(queryTokenForbidden(req, env)).toBe(true);
    expect(extractDeviceToken(req, env)).toBeNull();
  });

  it("localhost query token only with DEV_ALLOW_QUERY_TOKEN=1", () => {
    const env = createMemoryEnv({ DEV_ALLOW_QUERY_TOKEN: "1" });
    const req = new Request("http://localhost:8788/ws/device?token=secrettokensecrettoken", {
      headers: { host: "localhost:8788" },
    });
    expect(queryTokenForbidden(req, env)).toBe(false);
    expect(extractDeviceToken(req, env)).toBe("secrettokensecrettoken");
  });

  it("HTTP production query token rejected on upgrade path", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "inst-q", "secrettokensecrettoken99");
    const res = await worker.fetch(
      deviceReq("/ws/device?token=secrettokensecrettoken99", {
        headers: {
          host: "device.light.orng.wtf",
          Upgrade: "websocket",
          Connection: "Upgrade",
        },
      }, "device.light.orng.wtf"),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(401);
    const body = (await res.json()) as { error: { code: string } };
    expect(body.error.code).toBe("query_token_forbidden");
  });
});

describe("scene releases + track_latest", () => {
  let env: ReturnType<typeof createMemoryEnv>;

  beforeEach(() => {
    env = createMemoryEnv();
  });

  it("rejects path traversal and disallowed paths", () => {
    expect(isAllowedScenePath("../etc/passwd")).toBe(false);
    expect(isAllowedScenePath("presets/../../secret")).toBe(false);
    expect(isAllowedScenePath("runtime.yaml")).toBe(false);
    expect(isAllowedScenePath("app.yaml")).toBe(true);
    expect(isAllowedScenePath("presets/P01.yaml")).toBe(true);
  });

  it("assertStrictSceneId rejects unsafe ids before path ops", () => {
    expect(() => assertStrictSceneId("../evil")).toThrow(ReleaseError);
    expect(() => assertStrictSceneId("foo/bar")).toThrow(ReleaseError);
    expect(() => assertStrictSceneId("Foo")).toThrow(ReleaseError);
    expect(() => assertStrictSceneId("a".repeat(65))).toThrow(ReleaseError);
    expect(() => assertStrictSceneId("orng-bar")).not.toThrow();
  });

  it("unpackBundle rejects missing required, duplicate paths, bad b64, oversize, mismatched scene_id", async () => {
    const missing = await packBundle([
      { path: "app.yaml", data: new TextEncoder().encode("a: 1\n") },
      { path: "scene.yaml", data: new TextEncoder().encode("scene_id: demo\nname: Demo\n") },
    ]);
    await expect(unpackBundle(missing.bytes, "demo")).rejects.toMatchObject({
      code: "missing_required",
    });

    const good = await packValidScene("demo");
    const parsed = JSON.parse(new TextDecoder().decode(good.bytes)) as {
      format: string;
      files: Array<{ path: string; sha256: string; data_b64: string }>;
    };
    parsed.files.push({ ...parsed.files[0]! });
    const dupBytes = new TextEncoder().encode(JSON.stringify(parsed));
    await expect(unpackBundle(dupBytes, "demo")).rejects.toMatchObject({ code: "duplicate_path" });

    const badB64 = JSON.parse(new TextDecoder().decode(good.bytes)) as typeof parsed;
    badB64.files[0]!.data_b64 = "!!!not-base64!!!";
    await expect(
      unpackBundle(new TextEncoder().encode(JSON.stringify(badB64)), "demo"),
    ).rejects.toMatchObject({ code: "invalid_base64" });

    await expect(
      packBundle([
        ...validSceneFiles("demo").filter((f) => f.path !== "app.yaml"),
        { path: "app.yaml", data: new Uint8Array(MAX_FILE_BYTES + 1) },
      ]),
    ).rejects.toMatchObject({ code: "file_too_large" });

    // Crafted bundle: decoded total exceeds MAX_TOTAL_DECODED_BYTES
    const chunk = new Uint8Array(Math.min(MAX_FILE_BYTES, 512 * 1024));
    const filesForTotal: Array<{ path: string; sha256: string; data_b64: string }> = [];
    let total = 0;
    let n = 0;
    while (total <= MAX_TOTAL_DECODED_BYTES) {
      const path =
        n === 0
          ? "scene.yaml"
          : n === 1
            ? "app.yaml"
            : n === 2
              ? "patch.yaml"
              : n === 3
                ? "layout.yaml"
                : `presets/P${String(n).padStart(2, "0")}.yaml`;
      const data =
        n < 4
          ? new TextEncoder().encode(
              n === 0 ? "scene_id: demo\nname: Demo\n" : n === 1 ? "a: 1\n" : "fixtures: []\n",
            )
          : chunk;
      let b64 = "";
      for (let i = 0; i < data.length; i++) b64 += String.fromCharCode(data[i]!);
      b64 = btoa(b64);
      const sha = await crypto.subtle.digest("SHA-256", data);
      const hex = [...new Uint8Array(sha)].map((b) => b.toString(16).padStart(2, "0")).join("");
      filesForTotal.push({ path, sha256: hex, data_b64: b64 });
      total += data.byteLength;
      n += 1;
      if (n > 30) break;
    }
    await expect(
      unpackBundle(
        new TextEncoder().encode(
          JSON.stringify({ format: "orng-scene-bundle-v1", files: filesForTotal }),
        ),
        "demo",
      ),
    ).rejects.toMatchObject({ code: "bundle_too_large" });

    const mismatched = await packValidScene("other-id");
    await expect(unpackBundle(mismatched.bytes, "demo")).rejects.toMatchObject({
      code: "scene_id_mismatch",
    });

    const unsafeId = await packBundle([
      { path: "scene.yaml", data: new TextEncoder().encode("scene_id: ../evil\nname: X\n") },
      { path: "app.yaml", data: new TextEncoder().encode("a: 1\n") },
      { path: "patch.yaml", data: new TextEncoder().encode("fixtures: []\n") },
      { path: "layout.yaml", data: new TextEncoder().encode("fixtures: {}\n") },
    ]);
    await expect(unpackBundle(unsafeId.bytes)).rejects.toMatchObject({ code: "invalid_scene_id" });

    await expect(
      publishRelease(env, {
        sceneId: "Bad/Id",
        sceneName: "X",
        bundleBytes: good.bytes,
        baseVersion: null,
        publishedBy: "admin",
      }),
    ).rejects.toMatchObject({ code: "invalid_scene_id" });
  });

  it("publish immutable; optimistic 409; duplicate hash idempotent", async () => {
    const packed = await packValidScene("demo", {
      extra: [
        { path: "profiles/par.yaml", data: new TextEncoder().encode("id: par\n") },
        { path: "presets/P01.yaml", data: new TextEncoder().encode("id: P01\n") },
      ],
    });

    const first = await publishRelease(env, {
      sceneId: "demo",
      sceneName: "Demo",
      bundleBytes: packed.bytes,
      baseVersion: 0,
      publishedBy: "admin@test",
    });
    expect(first.release.version).toBe(1);
    expect(first.idempotent).toBe(false);
    expect(first.assignments_updated).toBe(0);

    const again = await publishRelease(env, {
      sceneId: "demo",
      sceneName: "Demo",
      bundleBytes: packed.bytes,
      baseVersion: 0,
      publishedBy: "admin@test",
    });
    expect(again.idempotent).toBe(true);

    const other = await packValidScene("demo", { appExtra: "master: 1\n" });

    await expect(
      publishRelease(env, {
        sceneId: "demo",
        sceneName: "Demo",
        bundleBytes: other.bytes,
        baseVersion: 0,
        publishedBy: "admin@test",
      }),
    ).rejects.toMatchObject({ status: 409, code: "version_conflict" });

    const v2 = await publishRelease(env, {
      sceneId: "demo",
      sceneName: "Demo",
      bundleBytes: other.bytes,
      baseVersion: 1,
      publishedBy: "admin@test",
    });
    expect(v2.release.version).toBe(2);
  });

  it("idempotent publish repairs track_latest desired_version and notifies online DOs", async () => {
    await seedCredential(env, "pi-repair", "tokentokentokentoken44", { platform: "raspberry" });
    env.__do.setConnected("pi-repair", true);

    const packed = await packValidScene("repair-scene");
    const first = await publishRelease(env, {
      sceneId: "repair-scene",
      sceneName: "Repair",
      bundleBytes: packed.bytes,
      baseVersion: null,
      publishedBy: "admin",
      notifyAssignments: false,
    });
    expect(first.release.version).toBe(1);

    await assignScene(env, {
      installationId: "pi-repair",
      sceneId: "repair-scene",
      desiredVersion: 1,
      trackLatest: true,
      actor: "admin",
    });

    // Simulate drift: track_latest assignment stuck on wrong desired_version.
    const row = env.__db.tables.scene_assignments.find(
      (r) => r.installation_id === "pi-repair",
    )!;
    row.desired_version = 0;

    env.__do.stubs.get("pi-repair")!.commands = [];
    const again = await publishRelease(env, {
      sceneId: "repair-scene",
      sceneName: "Repair",
      bundleBytes: packed.bytes,
      baseVersion: null,
      publishedBy: "admin",
    });
    expect(again.idempotent).toBe(true);
    expect(again.assignments_updated).toBe(1);
    expect(await getAssignment(env, "pi-repair")).toMatchObject({ desired_version: 1 });

    const cmds = env.__do.stubs.get("pi-repair")!.commands;
    expect(cmds.some((c) => (c as { type: string }).type === "sync_scene_release")).toBe(true);
  });

  it("republishing an older bundle does not roll track_latest backward", async () => {
    await seedCredential(env, "pi-keep", "tokentokentokentoken45", { platform: "raspberry" });
    const v1 = await packValidScene("keep-scene", { appExtra: "mark: 1\n" });
    const first = await publishRelease(env, {
      sceneId: "keep-scene",
      sceneName: "Keep",
      bundleBytes: v1.bytes,
      baseVersion: null,
      publishedBy: "admin",
      notifyAssignments: false,
    });
    await assignScene(env, {
      installationId: "pi-keep",
      sceneId: "keep-scene",
      desiredVersion: first.release.version,
      trackLatest: true,
      actor: "admin",
    });
    const v2 = await packValidScene("keep-scene", { appExtra: "mark: 2\n" });
    const second = await publishRelease(env, {
      sceneId: "keep-scene",
      sceneName: "Keep",
      bundleBytes: v2.bytes,
      baseVersion: 1,
      publishedBy: "admin",
      notifyAssignments: false,
    });
    expect(second.release.version).toBe(2);
    const again = await publishRelease(env, {
      sceneId: "keep-scene",
      sceneName: "Keep",
      bundleBytes: v1.bytes,
      baseVersion: null,
      publishedBy: "admin",
    });
    expect(again.idempotent).toBe(true);
    expect(again.release.version).toBe(1);
    expect(await getAssignment(env, "pi-keep")).toMatchObject({ desired_version: 2 });
    const unpackedV1 = await unpackBundle(v1.bytes, "keep-scene");
    const republish = await publishAssignedWorkspace(env, {
      installationId: "pi-keep",
      sceneId: "keep-scene",
      sceneName: "Keep",
      baseVersion: null,
      files: unpackedV1.files.map((file) => ({
        path: file.path,
        text: new TextDecoder().decode(file.data),
      })),
      actor: "desktop",
    });
    expect(republish.downgrade_ignored).toBe(true);
    expect(await getAssignment(env, "pi-keep")).toMatchObject({ desired_version: 2 });
  });

  it("flow YAML overlap is rejected the same way as block YAML", async () => {
    const enc = new TextEncoder();
    const files = validSceneFiles("flow-scene").map((file) =>
      file.path === "patch.yaml"
        ? {
            path: "patch.yaml",
            data: enc.encode(
              'fixtures: [{id: a, profile_id: missing, start_address: "1"}, {id: b, profile_id: missing, start_address: 1}]\n',
            ),
          }
        : file,
    );
    const packed = await packBundle(files);
    await expect(
      publishRelease(env, {
        sceneId: "flow-scene",
        sceneName: "Flow",
        bundleBytes: packed.bytes,
        baseVersion: null,
        publishedBy: "admin",
      }),
    ).rejects.toMatchObject({ status: 400, code: "scene_invalid" });
  });

  it("invalid scene links are rejected before they become latest", async () => {
    const enc = new TextEncoder();
    const files = validSceneFiles("bad-scene").map((file) =>
      file.path === "patch.yaml"
        ? {
            path: "patch.yaml",
            data: enc.encode(
              [
                "fixtures:",
                "- id: a",
                "  profile_id: missing",
                "  start_address: 1",
                "- id: b",
                "  profile_id: missing",
                "  start_address: 1",
              ].join("\n"),
            ),
          }
        : file,
    );
    const packed = await packBundle(files);
    await expect(
      publishRelease(env, {
        sceneId: "bad-scene",
        sceneName: "Bad",
        bundleBytes: packed.bytes,
        baseVersion: null,
        publishedBy: "admin",
      }),
    ).rejects.toMatchObject({ status: 400, code: "scene_invalid" });
    const row = await env.DB.prepare(
      `SELECT MAX(version) AS v FROM scene_releases WHERE scene_id = ?`,
    )
      .bind("bad-scene")
      .first<{ v: number | null }>();
    expect(row?.v ?? null).toBeNull();
  });

  it("track_latest: assign v1 → publish v2 → desired becomes 2; online gets sync; offline D1 only", async () => {
    await seedCredential(env, "pi-online", "tokentokentokentoken01", { platform: "raspberry" });
    await seedCredential(env, "pi-offline", "tokentokentokentoken02", { platform: "raspberry" });
    env.__do.setConnected("pi-online", true);
    env.__do.setConnected("pi-offline", false);

    const v1 = await packValidScene("bar", { appExtra: "a: 1\n" });
    await publishRelease(env, {
      sceneId: "bar",
      sceneName: "Bar",
      bundleBytes: v1.bytes,
      baseVersion: null,
      publishedBy: "admin",
    });

    await assignScene(env, {
      installationId: "pi-online",
      sceneId: "bar",
      desiredVersion: 1,
      trackLatest: true,
      actor: "admin",
    });
    await assignScene(env, {
      installationId: "pi-offline",
      sceneId: "bar",
      desiredVersion: 1,
      trackLatest: true,
      actor: "admin",
    });

    const v2 = await packValidScene("bar", { appExtra: "a: 2\n" });
    const pub = await publishRelease(env, {
      sceneId: "bar",
      sceneName: "Bar",
      bundleBytes: v2.bytes,
      baseVersion: 1,
      publishedBy: "admin",
    });
    expect(pub.release.version).toBe(2);
    expect(pub.assignments_updated).toBe(2);

    expect(await getAssignment(env, "pi-online")).toMatchObject({ desired_version: 2 });
    expect(await getAssignment(env, "pi-offline")).toMatchObject({ desired_version: 2 });

    const onlineCmds = env.__do.stubs.get("pi-online")!.commands;
    expect(onlineCmds.length).toBeGreaterThanOrEqual(1);
    const sync = onlineCmds.find(
      (c) => (c as { type: string }).type === "sync_scene_release",
    ) as { payload: { version: number; scene_id: string } };
    expect(sync.payload.version).toBe(2);
    expect(sync.payload.scene_id).toBe("bar");

    // Offline: no command queued
    expect(env.__do.stubs.get("pi-offline")!.commands).toHaveLength(0);

    // Offline reconnect path: desired still 2 in D1
    env.__do.setConnected("pi-offline", true);
    const offlineDesired = await getAssignment(env, "pi-offline");
    expect(offlineDesired?.desired_version).toBe(2);
  });

  it("download ACL: assigned only; revoked 403; check before R2", async () => {
    await seedCredential(env, "pi-1", "tokentokentokentoken12", { platform: "raspberry" });
    await seedCredential(env, "desk-1", "tokentokentokentoken13", { platform: "desktop_windows" });

    const packed = await packValidScene("bar");
    await publishRelease(env, {
      sceneId: "bar",
      sceneName: "Bar",
      bundleBytes: packed.bytes,
      baseVersion: null,
      publishedBy: "admin",
    });
    const other = await packValidScene("other");
    await publishRelease(env, {
      sceneId: "other",
      sceneName: "Other",
      bundleBytes: other.bytes,
      baseVersion: null,
      publishedBy: "admin",
    });

    await assignScene(env, {
      installationId: "pi-1",
      sceneId: "bar",
      desiredVersion: 1,
      trackLatest: true,
      actor: "admin",
    });

    const piAuth = (await verifyDeviceToken(env, "tokentokentokentoken12"))!;
    await expect(
      assertDownloadAllowed(env, {
        installationId: "pi-1",
        sceneId: "other",
        version: 1,
        scopes: piAuth.scopes,
        revoked: false,
      }),
    ).rejects.toMatchObject({ status: 403 });

    const ok = await getReleaseBundleAuthorized(env, {
      installationId: "pi-1",
      sceneId: "bar",
      version: 1,
      scopes: piAuth.scopes,
      revoked: false,
    });
    expect(ok.manifest.scene_id).toBe("bar");

    const deskAuth = (await verifyDeviceToken(env, "tokentokentokentoken13"))!;
    // Desktop with scene:read can read catalog (other scene)
    await assertDownloadAllowed(env, {
      installationId: "desk-1",
      sceneId: "other",
      version: 1,
      scopes: deskAuth.scopes,
      revoked: false,
    });

    await revokeInstallation(env, "pi-1", "admin");
    await expect(
      assertDownloadAllowed(env, {
        installationId: "pi-1",
        sceneId: "bar",
        version: 1,
        scopes: piAuth.scopes,
        revoked: true,
      }),
    ).rejects.toMatchObject({ status: 403, code: "revoked" });
  });

  it("device HTTP download enforces ACL", async () => {
    await seedCredential(env, "pi-http", "tokentokentokentoken88", { platform: "raspberry" });
    const packed = await packValidScene("bar");
    await publishRelease(env, {
      sceneId: "bar",
      sceneName: "Bar",
      bundleBytes: packed.bytes,
      baseVersion: null,
      publishedBy: "admin",
    });
    await assignScene(env, {
      installationId: "pi-http",
      sceneId: "bar",
      desiredVersion: 1,
      trackLatest: false,
      actor: "admin",
    });

    const ok = await worker.fetch(
      deviceReq("/device/releases/bar/1", {
        headers: { authorization: "Bearer tokentokentokentoken88" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(ok.status).toBe(200);

    const denied = await worker.fetch(
      deviceReq("/device/releases/missing/1", {
        headers: { authorization: "Bearer tokentokentokentoken88" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(denied.status).toBe(403);
  });
});

describe("commands protocol", () => {
  it("allowlist + momentary no-replay set", () => {
    expect(isAllowedCommandType("blackout")).toBe(true);
    expect(isAllowedCommandType("artnet_frame")).toBe(false);
    expect(MOMENTARY_PRESS_TYPES.has("strobe_press")).toBe(true);
    expect(HOLD_LEASE_MS).toBeGreaterThan(0);
  });

  it("command idempotency via command_log", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "d1", "tokentokentokentoken99");
    await logCommand(env, {
      command_id: "cmd-1",
      installation_id: "d1",
      type: "blackout",
      payload: { on: true },
      status: "applied",
    });
    await logCommand(env, {
      command_id: "cmd-1",
      installation_id: "d1",
      type: "blackout",
      payload: { on: true },
      status: "accepted",
    });
    const row = await getCommand(env, "cmd-1");
    expect(row?.status).toBe("applied");
    await ackCommand(env, "cmd-1", "applied");
    expect((await getCommand(env, "cmd-1"))?.acked_at).toBeTruthy();
  });

  it("ACK is scoped to installation + type (cannot ACK another installation)", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "inst-a", "tokentokentokentokenaa");
    await seedCredential(env, "inst-b", "tokentokentokentokenbb");
    await logCommand(env, {
      command_id: "shared-looking-id",
      installation_id: "inst-a",
      type: "sync_scene_release",
      payload: {},
      status: "accepted",
    });

    await ackCommand(env, "shared-looking-id", "applied", null, null, {
      installationId: "inst-b",
      type: "sync_scene_release",
    });
    expect((await getCommand(env, "shared-looking-id"))?.status).toBe("accepted");

    await ackCommand(env, "shared-looking-id", "applied", null, null, {
      installationId: "inst-a",
      type: "blackout",
    });
    expect((await getCommand(env, "shared-looking-id"))?.status).toBe("accepted");

    await ackCommand(env, "shared-looking-id", "applied", null, null, {
      installationId: "inst-a",
      type: "sync_scene_release",
    });
    expect((await getCommand(env, "shared-looking-id"))?.status).toBe("applied");
  });
});

describe("device agent flow (unit — not Workers e2e)", () => {
  it("pair → envelope decrypt → publish → assign → track_latest → scopes", async () => {
    const env = createMemoryEnv();
    const paired = await pairDevice(env, "agent-flow-chal", "raspberry");

    const auth = await verifyDeviceToken(env, paired.token);
    expect(auth?.scopes).toEqual([...RASPBERRY_SCOPES]);

    const packed = await packValidScene("orng-bar", {
      extra: [{ path: "presets/P01.yaml", data: new TextEncoder().encode("id: P01\n") }],
    });
    const pub = await publishRelease(env, {
      sceneId: "orng-bar",
      sceneName: "ORNG Bar",
      bundleBytes: packed.bytes,
      baseVersion: null,
      publishedBy: "ops@orng",
    });
    await assignScene(env, {
      installationId: paired.installation_id,
      sceneId: "orng-bar",
      desiredVersion: pub.release.version,
      trackLatest: true,
      actor: "ops@orng",
    });

    const v2 = await packValidScene("orng-bar", { appExtra: "v: 2\n" });
    env.__do.setConnected(paired.installation_id, true);
    const pub2 = await publishRelease(env, {
      sceneId: "orng-bar",
      sceneName: "ORNG Bar",
      bundleBytes: v2.bytes,
      baseVersion: 1,
      publishedBy: "ops@orng",
    });
    expect(pub2.assignments_updated).toBe(1);
    expect((await getAssignment(env, paired.installation_id))?.desired_version).toBe(2);

    expect(MOMENTARY_PRESS_TYPES.has("strobe_press")).toBe(true);
    const actions = env.__db.tables.audit_events.map((e) => e.action);
    expect(actions).toContain("pair.approve");
    expect(actions).toContain("scene.publish");
    expect(actions).toContain("scene.assign");
  });
});

describe("token hash only in storage", () => {
  it("never stores plaintext token in credentials table", async () => {
    const env = createMemoryEnv();
    const token = "supersecretdevicetokenvalue0001";
    await seedCredential(env, "i1", token);
    const dump = JSON.stringify(env.__db.tables.device_credentials);
    expect(dump).not.toContain(token);
    expect(dump).toContain(await hashToken(token));
  });
});

// Type shim for worker.fetch third arg in node tests
declare const ExecutionContext: { new (): ExecutionContext };
