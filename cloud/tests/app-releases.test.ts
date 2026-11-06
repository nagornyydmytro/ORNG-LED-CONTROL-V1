/**
 * Phase 3 APPLICATION release control-plane (unit / memoryEnv).
 * Unit tests use fake device ACKs (control-plane only — NOT a real OS apply).
 */
import { beforeEach, describe, expect, it } from "vitest";
import worker from "../src/index";
import {
  AppReleaseError,
  appPackageR2KeyByHash,
  assignAppRelease,
  assertAppDownloadAllowed,
  bulkAppRollout,
  getAppAssignment,
  getAppManifestAuthorized,
  getAppPackageAuthorized,
  listAppUpdateEvents,
  publishAppRelease,
  reportAppUpdateResult,
  rollbackAppAssignment,
} from "../src/app/releases";
import { TEST_APP_KEY_ID, allowTestAppSigningKey, resolveTrustedAppKey } from "../src/app/trusted_keys";
import { verifyAppReleaseSignature } from "../src/app/signing";
import { RASPBERRY_SCOPES, DESKTOP_SCOPES, SCOPE_APP_READ_ASSIGNED } from "../src/auth/scopes";
import { isAllowedCommandType } from "../src/protocol/types";
import { createMemoryEnv, seedCredential } from "./helpers/memoryEnv";
import { makeSignedAppPackage } from "./helpers/appSigning";
import { listAudit } from "../src/db/audit";
import { getCommand, logCommand } from "../src/db/commands";
import { touchHeartbeat } from "../src/db/installations";

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

describe("app scopes + commands", () => {
  it("raspberry and desktop include app:read_assigned", () => {
    expect(RASPBERRY_SCOPES).toContain(SCOPE_APP_READ_ASSIGNED);
    expect(DESKTOP_SCOPES).toContain(SCOPE_APP_READ_ASSIGNED);
  });

  it("allowlists app update commands", () => {
    for (const t of [
      "check_app_update",
      "stage_app_update",
      "apply_app_update",
      "cancel_staged_update",
      "rollback_app_update",
    ]) {
      expect(isAllowedCommandType(t)).toBe(true);
    }
  });
});

describe("app release publish + signing", () => {
  let env: ReturnType<typeof createMemoryEnv>;

  beforeEach(() => {
    env = createMemoryEnv();
  });

  it("rejects unsigned publish", async () => {
    const pkg = await makeSignedAppPackage({ version: "1.0.0", build_id: "b1" });
    await expect(
      publishAppRelease(env, {
        version: "1.0.0",
        build_id: "b1",
        platform: "raspberry",
        arch: "arm64",
        channel: "stable",
        packageBytes: pkg.packageBytes,
        signatureB64: "",
        keyId: pkg.key_id,
        publishedBy: "admin@test.local",
        manifest: pkg.manifest,
      }),
    ).rejects.toMatchObject({ code: "unsigned" });
  });

  it("rejects bad signature", async () => {
    const pkg = await makeSignedAppPackage({ version: "1.0.0", build_id: "b1" });
    await expect(
      publishAppRelease(env, {
        version: "1.0.0",
        build_id: "b1",
        platform: "raspberry",
        arch: "arm64",
        channel: "stable",
        packageBytes: pkg.packageBytes,
        signatureB64: pkg.signature_b64.replace(/^./, "A"),
        keyId: pkg.key_id,
        publishedBy: "admin@test.local",
        manifest: pkg.manifest,
      }),
    ).rejects.toMatchObject({ code: "bad_signature" });
  });

  it("Access-only: device Bearer cannot publish app releases", async () => {
    await seedCredential(env, "inst-1", "tok-desktop-publish-1", { platform: "desktop_windows" });
    const pkg = await makeSignedAppPackage({ version: "1.0.0", build_id: "b1" });
    const res = await worker.fetch(
      deviceReq("/device/app/releases/publish", {
        method: "POST",
        headers: {
          authorization: "Bearer tok-desktop-publish-1",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          version: "1.0.0",
          build_id: "b1",
          platform: "desktop_windows",
          arch: "amd64",
          package_base64: pkg.package_base64,
          signature_b64: pkg.signature_b64,
          key_id: pkg.key_id,
        }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(res.status).toBe(403);
  });

  it("admin publish stores R2 + D1; idempotent on same identity+hash", async () => {
    const pkg = await makeSignedAppPackage({ version: "1.2.0", build_id: "build42" });
    const pub = await worker.fetch(
      adminReq("/admin/app/releases/publish", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          version: "1.2.0",
          build_id: "build42",
          platform: "raspberry",
          arch: "arm64",
          channel: "stable",
          package_base64: pkg.package_base64,
          signature_b64: pkg.signature_b64,
          key_id: pkg.key_id,
          manifest: pkg.manifest,
          notes: "phase3",
        }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(pub.status).toBe(200);
    const body = (await pub.json()) as { idempotent: boolean; release: { content_sha256: string } };
    expect(body.idempotent).toBe(false);
    expect(env.__appR2.store.has(appPackageR2KeyByHash(body.release.content_sha256))).toBe(true);

    const again = await publishAppRelease(env, {
      version: "1.2.0",
      build_id: "build42",
      platform: "raspberry",
      arch: "arm64",
      channel: "stable",
      packageBytes: pkg.packageBytes,
      signatureB64: pkg.signature_b64,
      keyId: pkg.key_id,
      publishedBy: "admin@test.local",
      manifest: pkg.manifest,
    });
    expect(again.idempotent).toBe(true);

    const events = await listAudit(env, { limit: 20 });
    expect(events.some((e) => e.action === "app.publish")).toBe(true);
  });

  it("track_latest bumps matching platform/arch/channel and notifies online DO", async () => {
    await seedCredential(env, "pi-1", "tok-pi-track-latest1", {
      platform: "raspberry",
      capabilities: { arch: "arm64" },
    });
    await seedCredential(env, "win-1", "tok-win-track-latest1", {
      platform: "desktop_windows",
      capabilities: { arch: "amd64" },
    });
    await assignAppRelease(env, {
      installationId: "pi-1",
      desiredVersion: null,
      desiredBuildId: null,
      channel: "stable",
      trackLatest: true,
      actor: "admin@test.local",
      notify: false,
    });
    await assignAppRelease(env, {
      installationId: "win-1",
      desiredVersion: null,
      desiredBuildId: null,
      channel: "stable",
      trackLatest: true,
      actor: "admin@test.local",
      notify: false,
    });
    env.__do.setConnected("pi-1", true);
    env.__do.setConnected("win-1", true);

    const pkg = await makeSignedAppPackage({
      version: "2.0.0",
      build_id: "b200",
      platform: "raspberry",
      arch: "arm64",
    });
    const result = await publishAppRelease(env, {
      version: "2.0.0",
      build_id: "b200",
      platform: "raspberry",
      arch: "arm64",
      channel: "stable",
      packageBytes: pkg.packageBytes,
      signatureB64: pkg.signature_b64,
      keyId: pkg.key_id,
      publishedBy: "admin@test.local",
      manifest: pkg.manifest,
    });
    expect(result.assignments_updated).toBe(1);
    const pi = await getAppAssignment(env, "pi-1");
    expect(pi?.desired_version).toBe("2.0.0");
    expect(pi?.desired_build_id).toBe("b200");
    const win = await getAppAssignment(env, "win-1");
    expect(win?.desired_version).not.toBe("2.0.0");

    const cmds = env.__do.stubs.get("pi-1")?.commands as Array<{ type: string; payload: unknown }>;
    expect(cmds?.some((c) => c.type === "check_app_update" || c.type === "stage_app_update")).toBe(
      true,
    );
  });
});

describe("app ACL + rollout + rollback + update-result", () => {
  let env: ReturnType<typeof createMemoryEnv>;

  beforeEach(async () => {
    env = createMemoryEnv();
    await seedCredential(env, "pi-acl", "tok-acl-device-token", {
      platform: "raspberry",
      capabilities: { arch: "arm64" },
    });
    const pkg = await makeSignedAppPackage({ version: "1.0.0", build_id: "b1" });
    await publishAppRelease(env, {
      version: "1.0.0",
      build_id: "b1",
      platform: "raspberry",
      arch: "arm64",
      channel: "stable",
      packageBytes: pkg.packageBytes,
      signatureB64: pkg.signature_b64,
      keyId: pkg.key_id,
      publishedBy: "admin@test.local",
      manifest: pkg.manifest,
      notifyAssignments: false,
    });
    const pkg2 = await makeSignedAppPackage({ version: "1.1.0", build_id: "b2" });
    await publishAppRelease(env, {
      version: "1.1.0",
      build_id: "b2",
      platform: "raspberry",
      arch: "arm64",
      channel: "stable",
      packageBytes: pkg2.packageBytes,
      signatureB64: pkg2.signature_b64,
      keyId: pkg2.key_id,
      publishedBy: "admin@test.local",
      manifest: pkg2.manifest,
      notifyAssignments: false,
    });
  });

  it("download ACL allows assigned; wrong platform denied", async () => {
    await assignAppRelease(env, {
      installationId: "pi-acl",
      desiredVersion: "1.1.0",
      desiredBuildId: "b2",
      channel: "stable",
      trackLatest: false,
      actor: "admin@test.local",
      notify: false,
    });
    await assertAppDownloadAllowed(env, {
      installationId: "pi-acl",
      platform: "raspberry",
      arch: "arm64",
      version: "1.1.0",
      build_id: "b2",
      scopes: [...RASPBERRY_SCOPES],
      revoked: false,
    });
    await expect(
      assertAppDownloadAllowed(env, {
        installationId: "pi-acl",
        platform: "desktop_windows",
        arch: "amd64",
        version: "1.1.0",
        build_id: "b2",
        scopes: [...RASPBERRY_SCOPES],
        revoked: false,
      }),
    ).rejects.toBeInstanceOf(AppReleaseError);

    await expect(
      assertAppDownloadAllowed(env, {
        installationId: "pi-acl",
        platform: "raspberry",
        arch: "arm64",
        version: "1.0.0",
        build_id: "b1",
        scopes: [...RASPBERRY_SCOPES],
        revoked: false,
      }),
    ).rejects.toMatchObject({ code: "forbidden" });

    const ok = await getAppPackageAuthorized(env, {
      installationId: "pi-acl",
      platform: "raspberry",
      arch: "arm64",
      version: "1.1.0",
      build_id: "b2",
      scopes: [...RASPBERRY_SCOPES],
      revoked: false,
    });
    expect(ok.release.version).toBe("1.1.0");
  });

  it("revoked installation denied", async () => {
    await assignAppRelease(env, {
      installationId: "pi-acl",
      desiredVersion: "1.1.0",
      desiredBuildId: "b2",
      channel: "stable",
      trackLatest: false,
      actor: "admin@test.local",
      notify: false,
    });
    const row = env.__db.tables.installations.find((r) => r.id === "pi-acl")!;
    row.revoked_at = new Date().toISOString();
    await expect(
      assertAppDownloadAllowed(env, {
        installationId: "pi-acl",
        platform: "raspberry",
        arch: "arm64",
        version: "1.1.0",
        build_id: "b2",
        scopes: [...RASPBERRY_SCOPES],
        revoked: true,
      }),
    ).rejects.toMatchObject({ code: "revoked" });
  });

  it("bulk rollout + rollback + audit", async () => {
    await seedCredential(env, "pi-2", "tok-pi2-rollout-ok", {
      platform: "raspberry",
      capabilities: { arch: "arm64" },
    });
    const rollout = await bulkAppRollout(env, {
      installationIds: ["pi-acl", "pi-2"],
      desiredVersion: "1.1.0",
      desiredBuildId: "b2",
      channel: "stable",
      actor: "admin@test.local",
    });
    expect(rollout.updated).toEqual(["pi-acl", "pi-2"]);
    expect((await getAppAssignment(env, "pi-acl"))?.desired_version).toBe("1.1.0");

    const rolled = await rollbackAppAssignment(env, {
      installationId: "pi-acl",
      desiredVersion: "1.0.0",
      desiredBuildId: "b1",
      actor: "admin@test.local",
    });
    expect(rolled.desired_version).toBe("1.0.0");
    expect(rolled.track_latest).toBe(0);

    const audit = await listAudit(env, { limit: 50 });
    expect(audit.some((e) => e.action === "app.rollout")).toBe(true);
    expect(audit.some((e) => e.action === "app.rollback")).toBe(true);
  });

  it("update-result ACK after reconnect (unit fake device — not real OS apply)", async () => {
    const staleId = "desired-app:pi-acl:1.1.0:b2:t1";
    const stale = await reportAppUpdateResult(env, {
      installationId: "pi-acl",
      eventType: "applied",
      commandId: staleId,
      detail: { version: "1.1.0", build_id: "b2" },
    });
    expect(stale.acked).toBe(false);
    expect(stale.terminal).toBe(false);
    const before = env.__db.tables.installations.find((r) => r.id === "pi-acl");
    expect(before?.build_id).not.toBe("b2");

    const commandId = "apply-current:pi-acl:1.0.0:b1";
    const result = await reportAppUpdateResult(env, {
      installationId: "pi-acl",
      eventType: "applied",
      commandId,
      detail: { version: "1.0.0", build_id: "b1" },
    });
    expect(result.acked).toBe(true);
    expect(result.terminal).toBe(true);
    expect(result.event_id).toBeTruthy();

    const events = await listAppUpdateEvents(env, { installationId: "pi-acl" });
    expect(events[0]?.event_type).toBe("applied");
    expect((events[0]?.detail as { note?: string }).note).toBeUndefined();

    const inst = env.__db.tables.installations.find((r) => r.id === "pi-acl");
    expect(inst?.app_version).toBe("1.0.0");
    expect(inst?.build_id).toBe("b1");

    const again = await reportAppUpdateResult(env, {
      installationId: "pi-acl",
      eventType: "applied",
      commandId,
      detail: { version: "1.0.0", build_id: "b1" },
    });
    expect(again.idempotent).toBe(true);

    await assignAppRelease(env, {
      installationId: "pi-acl",
      desiredVersion: "1.1.0",
      desiredBuildId: "b2",
      channel: "stable",
      trackLatest: false,
      actor: "admin@test.local",
      notify: false,
    });
    const running = env.__db.tables.installations.find((r) => r.id === "pi-acl");
    if (running) {
      running.app_version = "1.1.0";
      running.build_id = "b2";
    }
    const late = await reportAppUpdateResult(env, {
      installationId: "pi-acl",
      eventType: "applied",
      commandId: "desired-app-apply:pi-acl:old-a",
      detail: { version: "1.0.0", build_id: "b1" },
    });
    expect(late.acked).toBe(false);
    const afterLate = env.__db.tables.installations.find((r) => r.id === "pi-acl");
    expect(afterLate?.build_id).toBe("b2");
    expect(afterLate?.app_version).toBe("1.1.0");

    const http = await worker.fetch(
      deviceReq("/device/app/update-result", {
        method: "POST",
        headers: {
          authorization: "Bearer tok-acl-device-token",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          event_type: "failed",
          command_id: "cmd-fail-1",
          error_message: "disk full",
        }),
      }),
      env,
      {} as ExecutionContext,
    );
    expect(http.status).toBe(200);
    const body = (await http.json()) as { acked: boolean; note?: string; terminal: boolean };
    expect(body.acked).toBe(true);
    expect(body.terminal).toBe(true);
    expect(body.note).toBeUndefined();
  });

  it("accepted update-result does not finalize command as applied", async () => {
    const commandId = "desired-app:pi-acl:stage:1";
    await logCommand(env, {
      command_id: commandId,
      installation_id: "pi-acl",
      type: "apply_app_update",
      payload: {},
      status: "accepted",
    });
    const staged = await reportAppUpdateResult(env, {
      installationId: "pi-acl",
      eventType: "staged",
      commandId,
      detail: { version: "1.1.0" },
    });
    expect(staged.terminal).toBe(false);
    expect(staged.command_status).toBe("accepted");
    const row = await getCommand(env, commandId);
    expect(row?.status).toBe("accepted");
  });

  it("device assignment + download manifest/package contract", async () => {
    await assignAppRelease(env, {
      installationId: "pi-acl",
      desiredVersion: "1.1.0",
      desiredBuildId: "b2",
      channel: "stable",
      trackLatest: false,
      actor: "admin@test.local",
      notify: false,
    });
    const asg = await worker.fetch(
      deviceReq("/device/app/assignment", {
        headers: { authorization: "Bearer tok-acl-device-token" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(asg.status).toBe(200);
    const asgBody = (await asg.json()) as {
      assignment: { desired_version: string; auto_stage: boolean };
    };
    expect(asgBody.assignment.desired_version).toBe("1.1.0");
    expect(asgBody.assignment.auto_stage).toBe(false);

    const manifestRes = await worker.fetch(
      deviceReq("/device/app/releases/raspberry/arm64/1.1.0/b2/manifest", {
        headers: { authorization: "Bearer tok-acl-device-token" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(manifestRes.status).toBe(200);
    const manifestBody = (await manifestRes.json()) as {
      manifest: { version: string; content_sha256: string };
      signature_b64: string;
      key_id: string;
      content_sha256: string;
      package_size: number;
      package_url: string;
    };
    expect(manifestBody.manifest.version).toBe("1.1.0");
    expect(manifestBody.signature_b64).toBeTruthy();
    expect(manifestBody.key_id).toBe(TEST_APP_KEY_ID);
    expect(manifestBody.content_sha256).toBe(manifestBody.manifest.content_sha256);
    expect(manifestBody.package_size).toBeGreaterThan(0);
    expect(manifestBody.package_url).toBe(
      "/device/app/releases/raspberry/arm64/1.1.0/b2/package",
    );

    const pkgRes = await worker.fetch(
      deviceReq(manifestBody.package_url, {
        headers: { authorization: "Bearer tok-acl-device-token" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(pkgRes.status).toBe(200);
    expect(pkgRes.headers.get("content-type")).toBe("application/gzip");
    expect(pkgRes.headers.get("content-length")).toBe(String(manifestBody.package_size));
    expect(pkgRes.headers.get("x-content-sha256")).toBe(manifestBody.content_sha256);

    const legacy = await worker.fetch(
      deviceReq("/device/app/releases/raspberry/arm64/1.1.0/b2", {
        headers: { authorization: "Bearer tok-acl-device-token" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(legacy.status).toBe(200);
    expect(legacy.headers.get("deprecation")).toBe("true");

    const denied = await worker.fetch(
      deviceReq("/device/app/releases/raspberry/arm64/1.0.0/b1/package", {
        headers: { authorization: "Bearer tok-acl-device-token" },
      }),
      env,
      {} as ExecutionContext,
    );
    expect(denied.status).toBe(403);

    const viaHelper = await getAppManifestAuthorized(env, {
      installationId: "pi-acl",
      platform: "raspberry",
      arch: "arm64",
      version: "1.1.0",
      build_id: "b2",
      scopes: [...RASPBERRY_SCOPES],
      revoked: false,
    });
    expect(viaHelper.package_url).toContain("/package");
  });
});

describe("trust boundary + arch + concurrent R2", () => {
  it("production host rejects test-ed25519-1; localhost with flag accepts", async () => {
    const local = createMemoryEnv({ DEV_BYPASS_ACCESS: "1", DEV_ALLOW_TEST_APP_KEYS: "1" });
    expect(allowTestAppSigningKey(local, "localhost:8787")).toBe(true);
    expect(resolveTrustedAppKey(TEST_APP_KEY_ID, { env: local, hostHeader: "localhost:8787" })).toBeTruthy();

    const prod = createMemoryEnv({
      DEV_BYPASS_ACCESS: undefined,
      DEV_ALLOW_TEST_APP_KEYS: undefined,
    });
    expect(allowTestAppSigningKey(prod, "light.orng.wtf")).toBe(false);
    expect(
      resolveTrustedAppKey(TEST_APP_KEY_ID, { env: prod, hostHeader: "light.orng.wtf" }),
    ).toBeNull();

    const pkg = await makeSignedAppPackage({ version: "1.0.0", build_id: "trust1" });
    await expect(
      verifyAppReleaseSignature({
        manifest: pkg.manifest,
        signatureB64: pkg.signature_b64,
        keyId: pkg.key_id,
        trust: { env: prod, hostHeader: "light.orng.wtf" },
      }),
    ).rejects.toMatchObject({ code: "unknown_key" });

    await verifyAppReleaseSignature({
      manifest: pkg.manifest,
      signatureB64: pkg.signature_b64,
      keyId: pkg.key_id,
      trust: { env: local, hostHeader: "localhost:8787" },
    });

    await expect(
      publishAppRelease(prod, {
        version: "1.0.0",
        build_id: "trust1",
        platform: "raspberry",
        arch: "arm64",
        channel: "stable",
        packageBytes: pkg.packageBytes,
        signatureB64: pkg.signature_b64,
        keyId: pkg.key_id,
        publishedBy: "admin@test.local",
        manifest: pkg.manifest,
        trust: { env: prod, hostHeader: "light.orng.wtf" },
      }),
    ).rejects.toMatchObject({ code: "unknown_key" });
  });

  it("rejects invalid signature length / base64", async () => {
    const env = createMemoryEnv();
    const pkg = await makeSignedAppPackage({ version: "1.0.0", build_id: "bad-sig" });
    await expect(
      verifyAppReleaseSignature({
        manifest: pkg.manifest,
        signatureB64: "not!!!base64",
        keyId: pkg.key_id,
        trust: { env, hostHeader: "localhost:8787" },
      }),
    ).rejects.toMatchObject({ code: "invalid_signature" });
    await expect(
      verifyAppReleaseSignature({
        manifest: pkg.manifest,
        signatureB64: btoa("short"),
        keyId: pkg.key_id,
        trust: { env, hostHeader: "localhost:8787" },
      }),
    ).rejects.toMatchObject({ code: "invalid_signature" });
  });

  it("unknown arch fails assign/download — never defaults arm64", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "no-arch", "tok-no-arch", {
      platform: "raspberry",
      capabilities: {},
    });
    const row = env.__db.tables.installations.find((r) => r.id === "no-arch")!;
    row.arch = null;
    row.capabilities_json = "{}";

    await expect(
      assignAppRelease(env, {
        installationId: "no-arch",
        desiredVersion: "1.0.0",
        desiredBuildId: "b1",
        channel: "stable",
        trackLatest: false,
        actor: "admin@test.local",
        notify: false,
      }),
    ).rejects.toMatchObject({ code: "unknown_arch" });
  });

  it("heartbeat persists arch for assign + download", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "hb-arch", "tok-hb-arch", {
      platform: "raspberry",
      capabilities: {},
    });
    const row = env.__db.tables.installations.find((r) => r.id === "hb-arch")!;
    row.arch = null;
    row.capabilities_json = "{}";

    await touchHeartbeat(env, "hb-arch", {
      arch: "arm64",
      install_mode: "managed",
      updater_protocol_version: 1,
      app_version: "0.1.0",
      build_id: "x",
    });
    expect(row.arch).toBe("arm64");
    expect(JSON.parse(String(row.capabilities_json)).arch).toBe("arm64");

    const pkg = await makeSignedAppPackage({ version: "1.0.0", build_id: "hb1" });
    await publishAppRelease(env, {
      version: "1.0.0",
      build_id: "hb1",
      platform: "raspberry",
      arch: "arm64",
      channel: "stable",
      packageBytes: pkg.packageBytes,
      signatureB64: pkg.signature_b64,
      keyId: pkg.key_id,
      publishedBy: "admin@test.local",
      manifest: pkg.manifest,
      notifyAssignments: false,
    });
    await assignAppRelease(env, {
      installationId: "hb-arch",
      desiredVersion: "1.0.0",
      desiredBuildId: "hb1",
      channel: "stable",
      trackLatest: false,
      actor: "admin@test.local",
      notify: false,
    });
    await assertAppDownloadAllowed(env, {
      installationId: "hb-arch",
      platform: "raspberry",
      arch: "arm64",
      version: "1.0.0",
      build_id: "hb1",
      scopes: [...RASPBERRY_SCOPES],
      revoked: false,
    });
  });

  it("track_latest arch isolation: amd64 publish does not bump arm64", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "pi-arm", "tok-pi-arm", {
      platform: "raspberry",
      capabilities: { arch: "arm64" },
    });
    await seedCredential(env, "pi-amd", "tok-pi-amd", {
      platform: "development",
      capabilities: { arch: "amd64" },
    });

    await assignAppRelease(env, {
      installationId: "pi-arm",
      desiredVersion: null,
      desiredBuildId: null,
      channel: "stable",
      trackLatest: true,
      actor: "a",
      notify: false,
    });
    await assignAppRelease(env, {
      installationId: "pi-amd",
      desiredVersion: null,
      desiredBuildId: null,
      channel: "stable",
      trackLatest: true,
      actor: "a",
      notify: false,
    });

    const arm = await makeSignedAppPackage({
      version: "5.0.0",
      build_id: "arm",
      platform: "raspberry",
      arch: "arm64",
    });
    await publishAppRelease(env, {
      version: "5.0.0",
      build_id: "arm",
      platform: "raspberry",
      arch: "arm64",
      channel: "stable",
      packageBytes: arm.packageBytes,
      signatureB64: arm.signature_b64,
      keyId: arm.key_id,
      publishedBy: "a",
      manifest: arm.manifest,
      notifyAssignments: false,
    });
    expect((await getAppAssignment(env, "pi-arm"))?.desired_version).toBe("5.0.0");
    expect((await getAppAssignment(env, "pi-amd"))?.desired_version).not.toBe("5.0.0");

    const amd = await makeSignedAppPackage({
      version: "5.0.0",
      build_id: "amd",
      platform: "linux",
      arch: "amd64",
    });
    const pubAmd = await publishAppRelease(env, {
      version: "5.0.0",
      build_id: "amd",
      platform: "linux",
      arch: "amd64",
      channel: "stable",
      packageBytes: amd.packageBytes,
      signatureB64: amd.signature_b64,
      keyId: amd.key_id,
      publishedBy: "a",
      manifest: amd.manifest,
      notifyAssignments: false,
    });
    expect(pubAmd.assignments_updated).toBe(1);
    expect((await getAppAssignment(env, "pi-amd"))?.desired_version).toBe("5.0.0");
    expect((await getAppAssignment(env, "pi-amd"))?.desired_build_id).toBe("amd");
    expect((await getAppAssignment(env, "pi-arm"))?.desired_build_id).toBe("arm");
  });

  it("concurrent R2 publish same identity different hash: loser does not delete winner", async () => {
    const env = createMemoryEnv();
    const bytesA = new TextEncoder().encode("package-content-A-unique");
    const bytesB = new TextEncoder().encode("package-content-B-unique-different");
    const pkgA = await makeSignedAppPackage({
      version: "9.0.0",
      build_id: "race",
      packageBytes: bytesA,
    });
    const pkgB = await makeSignedAppPackage({
      version: "9.0.0",
      build_id: "race",
      packageBytes: bytesB,
    });
    expect(pkgA.manifest.content_sha256).not.toBe(pkgB.manifest.content_sha256);

    const settled = await Promise.allSettled([
      publishAppRelease(env, {
        version: "9.0.0",
        build_id: "race",
        platform: "raspberry",
        arch: "arm64",
        channel: "stable",
        packageBytes: pkgA.packageBytes,
        signatureB64: pkgA.signature_b64,
        keyId: pkgA.key_id,
        publishedBy: "a",
        manifest: pkgA.manifest,
        notifyAssignments: false,
      }),
      publishAppRelease(env, {
        version: "9.0.0",
        build_id: "race",
        platform: "raspberry",
        arch: "arm64",
        channel: "stable",
        packageBytes: pkgB.packageBytes,
        signatureB64: pkgB.signature_b64,
        keyId: pkgB.key_id,
        publishedBy: "a",
        manifest: pkgB.manifest,
        notifyAssignments: false,
      }),
    ]);

    const ok = settled.filter((r) => r.status === "fulfilled");
    const bad = settled.filter((r) => r.status === "rejected");
    expect(ok).toHaveLength(1);
    expect(bad).toHaveLength(1);
    const err = (bad[0] as PromiseRejectedResult).reason as AppReleaseError;
    expect(err.code).toBe("conflict");

    const winnerSha = (
      ok[0] as PromiseFulfilledResult<{ release: { content_sha256: string } }>
    ).value.release.content_sha256;
    const loserSha =
      winnerSha === pkgA.manifest.content_sha256
        ? pkgB.manifest.content_sha256
        : pkgA.manifest.content_sha256;
    expect(env.__appR2.store.has(appPackageR2KeyByHash(winnerSha))).toBe(true);
    expect(env.__appR2.store.has(appPackageR2KeyByHash(loserSha))).toBe(false);
  });
});

