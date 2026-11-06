/**
 * Phase 3 APPLICATION releases — workerd e2e (Access publish, ACL, track_latest,
 * rollout, rollback, reconnect update-result ACK).
 * Device update-result in this suite is a control-plane fake (not a real OS apply).
 */
import { env, exports } from "cloudflare:workers";
import { beforeAll, describe, expect, it } from "vitest";
import {
  decryptCredentialEnvelope,
  envelopeFromB64,
  generateDeviceKeyPair,
} from "../../src/protocol/crypto";
import { makeSignedAppPackage } from "../helpers/appSigning";
import {
  collectWsMessages,
  openWorkerWebSocket,
  waitForCollected,
  type JsonMsg,
} from "./ws";

const SELF = {
  fetch: (input: RequestInfo | URL, init?: RequestInit) =>
    exports.default.fetch(input, init),
};

const ADMIN = "http://localhost:8787";
const DEVICE = "http://localhost:8788";

function adminHeaders(extra: HeadersInit = {}): Headers {
  const h = new Headers(extra);
  h.set("host", "localhost:8787");
  h.set("origin", "http://127.0.0.1:8787");
  h.set("x-dev-access-email", "ops@orng.test");
  return h;
}

function deviceHeaders(extra: HeadersInit = {}): Headers {
  const h = new Headers(extra);
  h.set("host", "localhost:8788");
  return h;
}

async function adminJson(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = adminHeaders(init.headers);
  if (init.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  return SELF.fetch(`${ADMIN}${path}`, { ...init, headers });
}

async function deviceJson(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = deviceHeaders(init.headers);
  if (init.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  return SELF.fetch(`${DEVICE}${path}`, { ...init, headers });
}

async function pairRaspberry(): Promise<{
  installationId: string;
  token: string;
}> {
  const keys = await generateDeviceKeyPair();
  const challenge = `app-e2e-${crypto.randomUUID()}`;
  const startRes = await deviceJson("/pair/start", {
    method: "POST",
    body: JSON.stringify({
      client_challenge: challenge,
      public_key_jwk: keys.publicKeyJwk,
    }),
  });
  expect(startRes.status).toBe(200);
  const started = (await startRes.json()) as { pairing_id: string; code: string };

  const approveRes = await adminJson("/admin/pair/approve", {
    method: "POST",
    body: JSON.stringify({
      pairing_id: started.pairing_id,
      code: started.code,
      name: "App E2E Pi",
      platform: "raspberry",
    }),
  });
  expect(approveRes.status).toBe(200);
  const approved = (await approveRes.json()) as { installation_id: string };

  // Persist arch (heartbeat columns) for app platform matching — never ephemeral-only.
  await env.DB.prepare(
    `UPDATE installations SET arch = ?, capabilities_json = ? WHERE id = ?`,
  )
    .bind("arm64", JSON.stringify({ arch: "arm64" }), approved.installation_id)
    .run();

  const completeRes = await deviceJson("/pair/complete", {
    method: "POST",
    body: JSON.stringify({
      pairing_id: started.pairing_id,
      client_challenge: challenge,
    }),
  });
  expect(completeRes.status).toBe(200);
  const completed = (await completeRes.json()) as { credential_envelope_b64: string };
  const token = await decryptCredentialEnvelope(
    keys.privateKey,
    envelopeFromB64(completed.credential_envelope_b64),
  );
  await deviceJson("/pair/consume", {
    method: "POST",
    body: JSON.stringify({
      pairing_id: started.pairing_id,
      client_challenge: challenge,
    }),
  });
  return { installationId: approved.installation_id, token };
}

describe("workerd APPLICATION releases e2e", () => {
  beforeAll(() => {
    expect(typeof WebSocketPair).toBe("function");
    expect(env.APP_RELEASES).toBeTruthy();
    expect(env.SCENE_RELEASES).toBeTruthy();
  });

  it(
    "Access publish → assign → ACL → track_latest → rollout → rollback → reconnect update-result ACK",
    async () => {
      const { installationId, token } = await pairRaspberry();

      // Unsigned rejected
      const unsigned = await adminJson("/admin/app/releases/publish", {
        method: "POST",
        body: JSON.stringify({
          version: "9.9.9",
          build_id: "u1",
          platform: "raspberry",
          arch: "arm64",
          channel: "stable",
          package_base64: btoa("unsigned-pkg"),
          signature_b64: "",
          key_id: "test-ed25519-1",
        }),
      });
      expect(unsigned.status).toBe(400);
      const unsignedBody = (await unsigned.json()) as { error: { code: string } };
      expect(unsignedBody.error.code).toBe("unsigned");

      // Device Bearer cannot publish
      const devicePublish = await deviceJson("/device/app/releases/publish", {
        method: "POST",
        headers: { authorization: `Bearer ${token}` },
        body: JSON.stringify({ version: "1.0.0" }),
      });
      expect(devicePublish.status).toBe(403);

      const pkg1 = await makeSignedAppPackage({
        version: "3.0.0",
        build_id: "e2e1",
        platform: "raspberry",
        arch: "arm64",
      });
      const pub1 = await adminJson("/admin/app/releases/publish", {
        method: "POST",
        body: JSON.stringify({
          version: "3.0.0",
          build_id: "e2e1",
          platform: "raspberry",
          arch: "arm64",
          channel: "stable",
          package_base64: pkg1.package_base64,
          signature_b64: pkg1.signature_b64,
          key_id: pkg1.key_id,
          manifest: pkg1.manifest,
        }),
      });
      expect(pub1.status).toBe(200);
      const pub1Body = (await pub1.json()) as { idempotent: boolean };
      expect(pub1Body.idempotent).toBe(false);

      // Idempotent re-publish
      const pub1b = await adminJson("/admin/app/releases/publish", {
        method: "POST",
        body: JSON.stringify({
          version: "3.0.0",
          build_id: "e2e1",
          platform: "raspberry",
          arch: "arm64",
          channel: "stable",
          package_base64: pkg1.package_base64,
          signature_b64: pkg1.signature_b64,
          key_id: pkg1.key_id,
          manifest: pkg1.manifest,
        }),
      });
      expect(pub1b.status).toBe(200);
      expect(((await pub1b.json()) as { idempotent: boolean }).idempotent).toBe(true);

      const pkg2 = await makeSignedAppPackage({
        version: "3.1.0",
        build_id: "e2e2",
        platform: "raspberry",
        arch: "arm64",
      });
      await adminJson("/admin/app/releases/publish", {
        method: "POST",
        body: JSON.stringify({
          version: "3.1.0",
          build_id: "e2e2",
          platform: "raspberry",
          arch: "arm64",
          channel: "stable",
          package_base64: pkg2.package_base64,
          signature_b64: pkg2.signature_b64,
          key_id: pkg2.key_id,
          manifest: pkg2.manifest,
        }),
      });

      // track_latest assign → desired becomes latest (3.1.0)
      const assign = await adminJson(`/admin/installations/${installationId}/app/assign`, {
        method: "POST",
        body: JSON.stringify({
          channel: "stable",
          track_latest: true,
          auto_update: false,
        }),
      });
      expect(assign.status).toBe(200);
      const assignBody = (await assign.json()) as {
        assignment: { desired_version: string; desired_build_id: string };
      };
      expect(assignBody.assignment.desired_version).toBe("3.1.0");
      expect(assignBody.assignment.desired_build_id).toBe("e2e2");

      const { ws: deviceWs } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${DEVICE}/ws/device`,
        Object.fromEntries(
          deviceHeaders({ authorization: `Bearer ${token}` }).entries(),
        ),
      );
      const deviceInbox = collectWsMessages(deviceWs);
      await waitForCollected(deviceInbox, (m) => m.type === "session_hello", 8_000);

      const updateCmd = await waitForCollected(
        deviceInbox,
        (m) => m.type === "check_app_update" || m.type === "stage_app_update",
        8_000,
      );
      const cmdId = String((updateCmd.payload as { command_id?: string })?.command_id ?? "");
      expect(cmdId).toBeTruthy();

      // Download contract: manifest JSON + package bytes
      const manOk = await deviceJson(`/device/app/releases/raspberry/arm64/3.1.0/e2e2/manifest`, {
        headers: { authorization: `Bearer ${token}` },
      });
      expect(manOk.status).toBe(200);
      const manBody = (await manOk.json()) as {
        manifest: { version: string };
        signature_b64: string;
        key_id: string;
        content_sha256: string;
        package_size: number;
        package_url: string;
      };
      expect(manBody.manifest.version).toBe("3.1.0");
      expect(manBody.signature_b64).toBeTruthy();
      expect(manBody.package_url).toContain("/package");

      const dlOk = await deviceJson(manBody.package_url, {
        headers: { authorization: `Bearer ${token}` },
      });
      expect(dlOk.status).toBe(200);
      expect(dlOk.headers.get("content-type")).toBe("application/gzip");
      expect(dlOk.headers.get("x-content-sha256")).toBe(manBody.content_sha256);
      expect(dlOk.headers.get("content-length")).toBe(String(manBody.package_size));
      expect(dlOk.headers.get("x-app-version")).toBe("3.1.0");

      // Wrong version denied
      const dlDenied = await deviceJson(`/device/app/releases/raspberry/arm64/3.0.0/e2e1/package`, {
        headers: { authorization: `Bearer ${token}` },
      });
      expect(dlDenied.status).toBe(403);

      // Wrong platform denied
      const dlPlat = await deviceJson(
        `/device/app/releases/desktop_windows/amd64/3.1.0/e2e2/package`,
        {
          headers: { authorization: `Bearer ${token}` },
        },
      );
      expect(dlPlat.status).toBe(403);

      // Production-style host rejects test key (local flag still required)
      const prodHostPublish = await SELF.fetch("http://light.orng.wtf/admin/app/releases/publish", {
        method: "POST",
        headers: adminHeaders({
          host: "light.orng.wtf",
          "content-type": "application/json",
        }),
        body: JSON.stringify({
          version: "8.8.8",
          build_id: "prod-reject",
          platform: "raspberry",
          arch: "arm64",
          channel: "stable",
          package_base64: pkg2.package_base64,
          signature_b64: pkg2.signature_b64,
          key_id: pkg2.key_id,
          manifest: { ...pkg2.manifest, version: "8.8.8", build_id: "prod-reject" },
        }),
      });
      // Access fail-closed on prod host without JWT, or unknown_key if Access somehow bypassed
      expect([400, 401, 403, 503]).toContain(prodHostPublish.status);

      // track_latest: publish 3.2 → bump assignment + notify online
      const pkg3 = await makeSignedAppPackage({
        version: "3.2.0",
        build_id: "e2e3",
        platform: "raspberry",
        arch: "arm64",
      });
      const pub3 = await adminJson("/admin/app/releases/publish", {
        method: "POST",
        body: JSON.stringify({
          version: "3.2.0",
          build_id: "e2e3",
          platform: "raspberry",
          arch: "arm64",
          channel: "stable",
          package_base64: pkg3.package_base64,
          signature_b64: pkg3.signature_b64,
          key_id: pkg3.key_id,
          manifest: pkg3.manifest,
        }),
      });
      expect(pub3.status).toBe(200);
      const trackCmd = await waitForCollected(
        deviceInbox,
        (m: JsonMsg) =>
          (m.type === "check_app_update" || m.type === "stage_app_update") &&
          String((m.payload as { version?: string })?.version ?? "") === "3.2.0",
        10_000,
      );
      expect(trackCmd).toBeTruthy();

      const asgRes = await deviceJson("/device/app/assignment", {
        headers: { authorization: `Bearer ${token}` },
      });
      const asg = (await asgRes.json()) as {
        assignment: { desired_version: string; desired_build_id: string };
      };
      expect(asg.assignment.desired_version).toBe("3.2.0");
      expect(asg.assignment.desired_build_id).toBe("e2e3");

      // Bulk rollout
      const rollout = await adminJson("/admin/app/rollout", {
        method: "POST",
        body: JSON.stringify({
          installation_ids: [installationId],
          desired_version: "3.1.0",
          desired_build_id: "e2e2",
          channel: "stable",
          track_latest: false,
        }),
      });
      expect(rollout.status).toBe(200);
      const rolloutBody = (await rollout.json()) as { updated: string[] };
      expect(rolloutBody.updated).toContain(installationId);

      // Rollback
      const rollback = await adminJson(`/admin/installations/${installationId}/app/rollback`, {
        method: "POST",
        body: JSON.stringify({
          desired_version: "3.0.0",
          desired_build_id: "e2e1",
        }),
      });
      expect(rollback.status).toBe(200);

      // Reconnect update-result ACK (control-plane fake — not a real OS apply)
      deviceWs.close(1000, "reconnect");
      deviceInbox.stop();

      const { ws: deviceWs2 } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${DEVICE}/ws/device`,
        Object.fromEntries(
          deviceHeaders({ authorization: `Bearer ${token}` }).entries(),
        ),
      );
      const inbox2 = collectWsMessages(deviceWs2);
      await waitForCollected(inbox2, (m) => m.type === "session_hello", 8_000);
      const redeliver = await waitForCollected(
        inbox2,
        (m) => m.type === "check_app_update" || m.type === "stage_app_update",
        8_000,
      );
      const reCmdId = String((redeliver.payload as { command_id?: string })?.command_id ?? "");

      // accepted must not finalize — send accepted ack first, then terminal update_result
      deviceWs2.send(
        JSON.stringify({
          type: "ack",
          payload: { command_id: reCmdId, status: "accepted" },
        }),
      );

      const checkResult = await deviceJson("/device/app/update-result", {
        method: "POST",
        headers: { authorization: `Bearer ${token}` },
        body: JSON.stringify({
          event_type: "applied",
          command_id: reCmdId,
          version: "3.0.0",
          build_id: "e2e1",
        }),
      });
      expect(checkResult.status).toBe(200);
      const checkBody = (await checkResult.json()) as { acked: boolean; terminal: boolean };
      expect(checkBody.acked).toBe(false);
      expect(checkBody.terminal).toBe(false);

      const applyCommandId = `apply-e2e:${installationId}:3.0.0:e2e1`;
      const updateResult = await deviceJson("/device/app/update-result", {
        method: "POST",
        headers: { authorization: `Bearer ${token}` },
        body: JSON.stringify({
          event_type: "applied",
          command_id: applyCommandId,
          version: "3.0.0",
          build_id: "e2e1",
        }),
      });
      expect(updateResult.status).toBe(200);
      const ur = (await updateResult.json()) as {
        acked: boolean;
        terminal: boolean;
        note?: string;
      };
      expect(ur.acked).toBe(true);
      expect(ur.terminal).toBe(true);
      expect(ur.note).toBeUndefined();

      // Idempotent redelivery of the same healthy apply
      const updateAgain = await deviceJson("/device/app/update-result", {
        method: "POST",
        headers: { authorization: `Bearer ${token}` },
        body: JSON.stringify({
          event_type: "applied",
          command_id: applyCommandId,
          version: "3.0.0",
          build_id: "e2e1",
        }),
      });
      expect(updateAgain.status).toBe(200);
      expect(((await updateAgain.json()) as { idempotent: boolean }).idempotent).toBe(true);

      // Revoked denied (credentials deleted → 401; revoked_at alone → 403)
      await adminJson(`/admin/devices/${installationId}/revoke`, { method: "POST", body: "{}" });
      const revokedDl = await deviceJson(
        `/device/app/releases/raspberry/arm64/3.0.0/e2e1/package`,
        {
          headers: { authorization: `Bearer ${token}` },
        },
      );
      expect([401, 403]).toContain(revokedDl.status);

      const events = await adminJson("/admin/app/events?limit=20");
      expect(events.status).toBe(200);
      const evBody = (await events.json()) as {
        events: Array<{ event_type: string; installation_id: string }>;
      };
      expect(evBody.events.some((e) => e.event_type === "applied")).toBe(true);

      const audit = await adminJson(`/admin/audit?target=${encodeURIComponent(installationId)}`);
      const auditBody = (await audit.json()) as {
        events: Array<{ action: string }>;
      };
      expect(auditBody.events.some((e) => e.action === "app.assign" || e.action === "app.rollback")).toBe(
        true,
      );

      inbox2.stop();
      deviceWs2.close(1000, "done");
    },
    90_000,
  );

  it("a successful check and a disarmed heartbeat do not apply another staged build", async () => {
    const { installationId, token } = await pairRaspberry();
    const pkg = await makeSignedAppPackage({
      version: "4.1.0",
      build_id: "nudge-b",
      platform: "raspberry",
      arch: "arm64",
    });
    const published = await adminJson("/admin/app/releases/publish", {
      method: "POST",
      body: JSON.stringify({
        version: "4.1.0",
        build_id: "nudge-b",
        platform: "raspberry",
        arch: "arm64",
        channel: "stable",
        package_base64: pkg.package_base64,
        signature_b64: pkg.signature_b64,
        key_id: pkg.key_id,
        manifest: pkg.manifest,
      }),
    });
    expect(published.status).toBe(200);
    const assigned = await adminJson(`/admin/installations/${installationId}/app/assign`, {
      method: "POST",
      body: JSON.stringify({
        channel: "stable",
        track_latest: false,
        auto_update: false,
        desired_version: "4.1.0",
        desired_build_id: "nudge-b",
      }),
    });
    expect(assigned.status).toBe(200);

    const { ws } = await openWorkerWebSocket(
      SELF.fetch.bind(SELF),
      `${DEVICE}/ws/device`,
      Object.fromEntries(deviceHeaders({ authorization: `Bearer ${token}` }).entries()),
    );
    const inbox = collectWsMessages(ws);
    await waitForCollected(inbox, (m) => m.type === "session_hello", 8_000);
    const check = await waitForCollected(inbox, (m) => m.type === "check_app_update", 8_000);
    const commandId = String((check.payload as { command_id?: string }).command_id ?? "");
    expect(commandId.startsWith("desired-app-check:")).toBe(true);

    ws.send(
      JSON.stringify({
        type: "ack",
        payload: { command_id: commandId, status: "applied" },
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 200));
    ws.send(
      JSON.stringify({
        type: "heartbeat",
        payload: {
          armed: false,
          build_id: "running-a",
          app_version: "4.0.0",
          staged_build_id: "staged-a",
          staged_app_version: "4.0.0",
          install_mode: "managed",
          arch: "arm64",
        },
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 400));

    await env.DB.prepare(
      `UPDATE app_assignments SET desired_version = NULL, desired_build_id = NULL, updated_at = ?
       WHERE installation_id = ?`,
    )
      .bind("2000-01-01T00:00:00Z", installationId)
      .run();
    ws.send(
      JSON.stringify({
        type: "heartbeat",
        payload: {
          armed: false,
          build_id: "running-a",
          staged_build_id: "staged-a",
          staged_app_version: "4.0.0",
          install_mode: "managed",
        },
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 400));

    expect(inbox.messages.some((m) => m.type === "apply_app_update")).toBe(false);
    const commands = await env.DB.prepare(
      `SELECT type FROM command_log WHERE installation_id = ?`,
    )
      .bind(installationId)
      .all<{ type: string }>();
    expect((commands.results ?? []).some((row) => row.type === "apply_app_update")).toBe(false);
    inbox.stop();
    ws.close(1000, "done");
  }, 40_000);
});
