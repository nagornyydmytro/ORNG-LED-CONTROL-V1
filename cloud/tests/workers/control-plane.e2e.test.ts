/**
 * Real workerd Durable Object + WebSocket integration (vitest-pool-workers / Miniflare).
 *
 * Flow: pair → approve → cold-start complete → Bearer WSS → publish → assign → sync
 * → download ACL → track_latest v2 → admin WSS press/keepalive/release
 * → disconnect source-scoped → no momentary replay.
 *
 * Uses SELF + env.DEVICE_DO — not the Node memoryEnv fake DO.
 */

import { env, exports } from "cloudflare:workers";
import { beforeAll, describe, expect, it } from "vitest";

const SELF = {
  fetch: (input: RequestInfo | URL, init?: RequestInit) =>
    exports.default.fetch(input, init),
};
import {
  decryptCredentialEnvelope,
  envelopeFromB64,
  generateDeviceKeyPair,
} from "../../src/protocol/crypto";
import { packValidScene } from "../helpers/sceneBundle";
import {
  collectWsMessages,
  openWorkerWebSocket,
  waitForCollected,
  waitForWsMessage,
  type JsonMsg,
} from "./ws";

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

function bundleB64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]!);
  return btoa(s);
}

describe("workerd DO + WebSocket control-plane e2e", () => {
  beforeAll(() => {
    // Sanity: real Workers runtime APIs (not Node memoryEnv).
    expect(typeof WebSocketPair).toBe("function");
    expect(env.DEVICE_DO).toBeTruthy();
    expect(env.PAIRING_RATE_LIMIT_DO).toBeTruthy();
    expect(env.DB).toBeTruthy();
    expect(env.SCENE_RELEASES).toBeTruthy();
  });

  it(
    "pair→approve→cold-start complete→Bearer WSS→publish→assign→sync→ACL→track_latest→admin holds→no momentary replay",
    async () => {
      // --- Pairing (device ECDH; admin never sees plaintext token) ---
      const keys = await generateDeviceKeyPair();
      const challenge = `workers-e2e-${crypto.randomUUID()}`;

      const startRes = await deviceJson("/pair/start", {
        method: "POST",
        body: JSON.stringify({
          client_challenge: challenge,
          public_key_jwk: keys.publicKeyJwk,
        }),
      });
      expect(startRes.status).toBe(200);
      const started = (await startRes.json()) as {
        pairing_id: string;
        code: string;
        expires_at: string;
      };
      expect(started.pairing_id).toBeTruthy();
      expect(started.code).toHaveLength(8);

      const approveRes = await adminJson("/admin/pair/approve", {
        method: "POST",
        body: JSON.stringify({
          pairing_id: started.pairing_id,
          code: started.code,
          name: "Workers E2E Pi",
          platform: "raspberry",
        }),
      });
      expect(approveRes.status).toBe(200);
      const approved = (await approveRes.json()) as {
        installation_id: string;
        approved: boolean;
        token?: string;
      };
      expect(approved.approved).toBe(true);
      expect(approved.installation_id).toBeTruthy();
      expect(approved.token).toBeUndefined();

      // Cold-start complete: credential_envelope only from D1 (no isolate Map).
      const completeRes = await deviceJson("/pair/complete", {
        method: "POST",
        body: JSON.stringify({
          pairing_id: started.pairing_id,
          client_challenge: challenge,
        }),
      });
      expect(completeRes.status).toBe(200);
      const completed = (await completeRes.json()) as {
        installation_id: string;
        credential_envelope_b64: string;
        token?: string;
      };
      expect(completed.token).toBeUndefined();
      expect(completed.credential_envelope_b64).toBeTruthy();

      const token = await decryptCredentialEnvelope(
        keys.privateKey,
        envelopeFromB64(completed.credential_envelope_b64),
      );
      expect(token.length).toBeGreaterThan(32);

      const consumeRes = await deviceJson("/pair/consume", {
        method: "POST",
        body: JSON.stringify({
          pairing_id: started.pairing_id,
          client_challenge: challenge,
        }),
      });
      expect(consumeRes.status).toBe(200);

      const installationId = completed.installation_id;

      // Production-style: query token rejected on device host.
      const queryReject = await SELF.fetch(
        `${DEVICE}/ws/device?token=${encodeURIComponent(token)}`,
        {
          headers: deviceHeaders({
            Upgrade: "websocket",
            Connection: "Upgrade",
          }),
        },
      );
      expect(queryReject.status).toBe(401);
      const qBody = (await queryReject.json()) as { error: { code: string } };
      expect(qBody.error.code).toBe("query_token_forbidden");

      // --- Bearer device WebSocket (real DeviceDO) ---
      const { ws: deviceWs } = await openWorkerWebSocket(SELF.fetch.bind(SELF), `${DEVICE}/ws/device`, {
        host: "localhost:8788",
        Authorization: `Bearer ${token}`,
      });
      const deviceInbox = collectWsMessages(deviceWs);

      const hello = await waitForWsMessage(deviceWs, (m) => m.type === "session_hello");
      expect(hello.device_id).toBe(installationId);

      // Confirm DO reports online via stub status.
      const stub = env.DEVICE_DO.get(env.DEVICE_DO.idFromName(installationId));
      const status1 = (await (await stub.fetch("https://device-do/status")).json()) as {
        online: boolean;
        device_connected: boolean;
      };
      expect(status1.online || status1.device_connected).toBe(true);

      // --- Publish v1 + assign track_latest (full valid scene bundle) ---
      const packedV1 = await packValidScene("workers-bar", {
        extra: [{ path: "presets/P01.yaml", data: new TextEncoder().encode("id: P01\n") }],
      });

      const pub1 = await adminJson("/admin/scenes/workers-bar/publish", {
        method: "POST",
        body: JSON.stringify({
          name: "Workers Bar",
          base_version: null,
          bundle_base64: bundleB64(packedV1.bytes),
        }),
      });
      expect(pub1.status).toBe(200);
      const pub1Body = (await pub1.json()) as {
        release: { version: number };
        idempotent: boolean;
        assignments_updated: number;
      };
      expect(pub1Body.release.version).toBe(1);
      expect(pub1Body.idempotent).toBe(false);

      const assignRes = await adminJson(`/admin/devices/${installationId}/assign`, {
        method: "POST",
        body: JSON.stringify({
          scene_id: "workers-bar",
          desired_version: 1,
          track_latest: true,
        }),
      });
      expect(assignRes.status).toBe(200);

      // Device should receive sync_scene_release (assign notify). Fake device ACKs (real apply is Python-side).
      const sync1 = await waitForCollected(
        deviceInbox,
        (m) =>
          m.type === "sync_scene_release" &&
          Number((m.payload as { version?: number } | undefined)?.version) === 1,
        10_000,
      );
      const sync1Payload = (sync1.payload ?? {}) as {
        scene_id?: string;
        version?: number;
        command_id?: string;
      };
      expect(sync1Payload.scene_id).toBe("workers-bar");
      expect(sync1Payload.version).toBe(1);
      deviceWs.send(
        JSON.stringify({
          type: "ack",
          payload: {
            command_id: sync1Payload.command_id,
            status: "applied",
          },
        }),
      );

      // --- Download ACL: assigned ok; other scene forbidden ---
      const dlOk = await deviceJson("/device/releases/workers-bar/1", {
        headers: { Authorization: `Bearer ${token}` },
      });
      expect(dlOk.status).toBe(200);

      // Seed a second scene the Pi is not assigned to.
      const otherPacked = await packValidScene("other-scene");
      const pubOther = await adminJson("/admin/scenes/other-scene/publish", {
        method: "POST",
        body: JSON.stringify({
          name: "Other",
          bundle_base64: bundleB64(otherPacked.bytes),
        }),
      });
      expect(pubOther.status).toBe(200);

      const dlDenied = await deviceJson("/device/releases/other-scene/1", {
        headers: { Authorization: `Bearer ${token}` },
      });
      expect(dlDenied.status).toBe(403);

      // --- track_latest: publish v2 → desired=2 + live sync with real version ---
      const packedV2 = await packValidScene("workers-bar", { appExtra: "v: 2\n" });
      const pub2 = await adminJson("/admin/scenes/workers-bar/publish", {
        method: "POST",
        body: JSON.stringify({
          name: "Workers Bar",
          base_version: 1,
          bundle_base64: bundleB64(packedV2.bytes),
        }),
      });
      expect(pub2.status).toBe(200);
      const pub2Body = (await pub2.json()) as {
        release: { version: number };
        assignments_updated: number;
      };
      expect(pub2Body.release.version).toBe(2);
      expect(pub2Body.assignments_updated).toBeGreaterThanOrEqual(1);

      const sync2 = await waitForCollected(
        deviceInbox,
        (m) =>
          m.type === "sync_scene_release" &&
          Number((m.payload as { version?: number } | undefined)?.version) === 2,
        10_000,
      );
      expect((sync2.payload as { version: number }).version).toBe(2);
      deviceWs.send(
        JSON.stringify({
          type: "ack",
          payload: {
            command_id: (sync2.payload as { command_id?: string }).command_id,
            status: "applied",
          },
        }),
      );

      const assignmentRes = await deviceJson("/device/assignment", {
        headers: { Authorization: `Bearer ${token}` },
      });
      expect(assignmentRes.status).toBe(200);
      const assignmentBody = (await assignmentRes.json()) as {
        assignment: { desired_version: number; scene_id: string };
      };
      expect(assignmentBody.assignment.desired_version).toBe(2);
      expect(assignmentBody.assignment.scene_id).toBe("workers-bar");

      // --- Admin WSS: press → hold → keepalive → release (source-scoped) ---
      const { ws: adminWsA } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${ADMIN}/admin/ws/device/${installationId}`,
        Object.fromEntries(adminHeaders().entries()),
      );
      const adminInboxA = collectWsMessages(adminWsA);
      const adminHelloA = await waitForCollected(adminInboxA, (m) => m.type === "admin_hello");
      const sourceA = String((adminHelloA.payload as { source?: string })?.source ?? "");
      expect(sourceA.startsWith("cloud:web:")).toBe(true);

      const { ws: adminWsB } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${ADMIN}/admin/ws/device/${installationId}`,
        Object.fromEntries(adminHeaders().entries()),
      );
      const adminInboxB = collectWsMessages(adminWsB);
      const adminHelloB = await waitForCollected(adminInboxB, (m) => m.type === "admin_hello");
      const sourceB = String((adminHelloB.payload as { source?: string })?.source ?? "");
      expect(sourceB).not.toBe(sourceA);

      // Tab A press — same hold_id as B; ownership must not collapse to one record.
      const sharedHoldId = "shared-hold-e2e";
      adminWsA.send(JSON.stringify({ type: "strobe_press", payload: { hold_id: sharedHoldId } }));
      const pressCmd = await waitForCollected(
        deviceInbox,
        (m) =>
          m.type === "strobe_press" &&
          (m.payload as { hold?: { source: string } })?.hold?.source === sourceA,
        8_000,
      );
      const hold = (pressCmd.payload as { hold?: { hold_id: string; source: string } })?.hold;
      expect(hold?.hold_id).toBe(sharedHoldId);
      expect(hold?.source).toBe(sourceA);

      const cmdResultA = await waitForCollected(
        adminInboxA,
        (m) => m.type === "command_result",
        8_000,
      );
      expect((cmdResultA.payload as { ok?: boolean })?.ok).toBe(true);

      // Keepalive for A's hold
      adminWsA.send(
        JSON.stringify({
          type: "hold_keepalive",
          payload: { hold_id: hold!.hold_id },
        }),
      );

      // Tab B independent press with the same hold_id
      adminWsB.send(JSON.stringify({ type: "strobe_press", payload: { hold_id: sharedHoldId } }));
      const pressB = await waitForCollected(
        deviceInbox,
        (m) =>
          m.type === "strobe_press" &&
          (m.payload as { hold?: { source: string } })?.hold?.source === sourceB,
        8_000,
      );
      const holdB = (pressB.payload as { hold?: { hold_id: string; source: string } })?.hold;
      expect(holdB?.source).toBe(sourceB);
      expect(holdB?.hold_id).toBe(sharedHoldId);

      const statusHolds = (await (await stub.fetch("https://device-do/status")).json()) as {
        active_holds: Array<{ source: string; hold_id: string }>;
      };
      expect(statusHolds.active_holds.filter((h) => h.hold_id === sharedHoldId)).toHaveLength(2);
      expect(statusHolds.active_holds.some((h) => h.source === sourceA)).toBe(true);
      expect(statusHolds.active_holds.some((h) => h.source === sourceB)).toBe(true);

      // Disconnect tab A → only A's holds released via release_source (not effect release)
      adminWsA.close(1000, "tab-a-done");
      const releaseA = await waitForCollected(
        deviceInbox,
        (m) =>
          m.type === "release_source" &&
          (m.payload as { source?: string })?.source === sourceA,
        8_000,
      );
      expect((releaseA.payload as { source?: string })?.source).toBe(sourceA);

      // B's hold still present
      const afterA = (await (await stub.fetch("https://device-do/status")).json()) as {
        active_holds: Array<{ source: string }>;
      };
      expect(afterA.active_holds.some((h) => h.source === sourceA)).toBe(false);
      expect(afterA.active_holds.some((h) => h.source === sourceB)).toBe(true);

      // Explicit release requires hold_id
      adminWsB.send(
        JSON.stringify({
          type: "strobe_release",
          payload: { hold_id: holdB!.hold_id },
        }),
      );
      await waitForCollected(deviceInbox, (m) => m.type === "strobe_release", 5_000);
      adminWsB.close(1000, "tab-b-done");

      // --- No momentary replay after reconnect ---
      // Drop device, queue a momentary while offline via HTTP command, reconnect.
      deviceInbox.stop();
      deviceWs.close(1000, "device-reconnect-test");

      // Wait until DO sees disconnect
      for (let i = 0; i < 20; i++) {
        const st = (await (await stub.fetch("https://device-do/status")).json()) as {
          online: boolean;
          device_connected: boolean;
        };
        if (!st.online && !st.device_connected) break;
        await new Promise((r) => setTimeout(r, 50));
      }

      const offlineCmd = await stub.fetch(
        new Request("https://device-do/command", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            type: "strobe_press",
            installation_id: installationId,
            source: "cloud:offline-test",
            payload: {},
          }),
        }),
      );
      const offlineBody = (await offlineCmd.json()) as {
        ok: boolean;
        error?: string;
        status: string;
      };
      expect(offlineBody.ok).toBe(false);
      expect(offlineBody.error).toBe("device_offline");
      const afterOffline = (await (await stub.fetch("https://device-do/status")).json()) as {
        active_holds: Array<{ source: string }>;
      };
      expect(afterOffline.active_holds.some((h) => h.source === "cloud:offline-test")).toBe(false);

      const { ws: deviceWs2 } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${DEVICE}/ws/device`,
        {
          host: "localhost:8788",
          Authorization: `Bearer ${token}`,
        },
      );
      const inbox2 = collectWsMessages(deviceWs2);
      await waitForWsMessage(deviceWs2, (m) => m.type === "session_hello");

      // Give DO a beat to flush any (incorrect) replay
      await new Promise((r) => setTimeout(r, 200));
      const replayed = inbox2.messages.filter((m: JsonMsg) => m.type === "strobe_press");
      expect(replayed).toHaveLength(0);

      inbox2.stop();
      deviceWs2.close(1000, "done");
    },
    90_000,
  );

  it("production admin API rejects header spoof; SPA shell stays reachable (workerd)", async () => {
    // DEV_BYPASS_ACCESS is localhost-only. Prod admin host ignores x-dev-access-email.
    // Without session secrets in miniflare vars, API → 503 configuration_error; SPA may still serve assets.
    const spa = await SELF.fetch("https://light.orng.wtf/", {
      headers: {
        host: "light.orng.wtf",
        "x-dev-access-email": "spoof@evil.test",
      },
    });
    expect(spa.status).toBeLessThan(500);

    const api = await SELF.fetch("https://light.orng.wtf/admin/devices", {
      headers: {
        host: "light.orng.wtf",
        "x-dev-access-email": "spoof@evil.test",
      },
    });
    expect([401, 503]).toContain(api.status);
  });

  it(
    "desired sync_scene_release redelivered after reconnect until applied ACK",
    async () => {
      const keys = await generateDeviceKeyPair();
      const challenge = `desired-sync-${crypto.randomUUID()}`;
      const startRes = await deviceJson("/pair/start", {
        method: "POST",
        body: JSON.stringify({
          client_challenge: challenge,
          public_key_jwk: keys.publicKeyJwk,
        }),
      });
      const started = (await startRes.json()) as { pairing_id: string; code: string };
      const approveRes = await adminJson("/admin/pair/approve", {
        method: "POST",
        body: JSON.stringify({
          pairing_id: started.pairing_id,
          code: started.code,
          name: "Desired Sync Pi",
          platform: "raspberry",
        }),
      });
      const approved = (await approveRes.json()) as { installation_id: string };
      const completeRes = await deviceJson("/pair/complete", {
        method: "POST",
        body: JSON.stringify({
          pairing_id: started.pairing_id,
          client_challenge: challenge,
        }),
      });
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
      const installationId = approved.installation_id;

      const packed = await packValidScene("desired-sync");
      expect(
        (
          await adminJson("/admin/scenes/desired-sync/publish", {
            method: "POST",
            body: JSON.stringify({
              name: "Desired Sync",
              bundle_base64: bundleB64(packed.bytes),
            }),
          })
        ).status,
      ).toBe(200);

      // Assign while offline so reconnect pushDesiredAssignment owns the sync.
      await adminJson(`/admin/devices/${installationId}/assign`, {
        method: "POST",
        body: JSON.stringify({
          scene_id: "desired-sync",
          desired_version: 1,
          track_latest: true,
        }),
      });

      const { ws: deviceWs } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${DEVICE}/ws/device`,
        { host: "localhost:8788", Authorization: `Bearer ${token}` },
      );
      const inbox = collectWsMessages(deviceWs);
      await waitForWsMessage(deviceWs, (m) => m.type === "session_hello");

      const sync1 = await waitForCollected(
        inbox,
        (m) =>
          m.type === "sync_scene_release" &&
          String((m.payload as { command_id?: string })?.command_id ?? "").startsWith("desired:"),
        10_000,
      );
      const cmdId = String((sync1.payload as { command_id?: string })?.command_id ?? "");
      expect(cmdId.startsWith("desired:")).toBe(true);
      // Deliberately do NOT ACK — simulate disconnect before applied ACK.
      inbox.stop();
      deviceWs.close(1000, "drop-before-ack");

      const stub = env.DEVICE_DO.get(env.DEVICE_DO.idFromName(installationId));
      for (let i = 0; i < 40; i++) {
        const st = (await (await stub.fetch("https://device-do/status")).json()) as {
          online: boolean;
          device_connected: boolean;
        };
        if (!st.online && !st.device_connected) break;
        await new Promise((r) => setTimeout(r, 50));
      }

      const { ws: deviceWs2 } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${DEVICE}/ws/device`,
        { host: "localhost:8788", Authorization: `Bearer ${token}` },
      );
      const inbox2 = collectWsMessages(deviceWs2);
      await waitForWsMessage(deviceWs2, (m) => m.type === "session_hello");

      const sync2 = await waitForCollected(
        inbox2,
        (m) =>
          m.type === "sync_scene_release" &&
          (m.payload as { command_id?: string })?.command_id === cmdId,
        10_000,
      );
      expect((sync2.payload as { command_id: string }).command_id).toBe(cmdId);

      // Fake device ACK (real apply is Python-side)
      deviceWs2.send(
        JSON.stringify({
          type: "ack",
          payload: { command_id: cmdId, status: "applied" },
        }),
      );
      await new Promise((r) => setTimeout(r, 50));
      inbox2.stop();
      deviceWs2.close(1000, "done");
    },
    60_000,
  );

  it(
    "H+V holds: explicit H release leaves V; V release clears only V; two tabs source-scoped",
    async () => {
      const keys = await generateDeviceKeyPair();
      const challenge = `hv-holds-${crypto.randomUUID()}`;
      const startRes = await deviceJson("/pair/start", {
        method: "POST",
        body: JSON.stringify({
          client_challenge: challenge,
          public_key_jwk: keys.publicKeyJwk,
        }),
      });
      const started = (await startRes.json()) as { pairing_id: string; code: string };
      const approveRes = await adminJson("/admin/pair/approve", {
        method: "POST",
        body: JSON.stringify({
          pairing_id: started.pairing_id,
          code: started.code,
          name: "HV Holds Pi",
          platform: "raspberry",
        }),
      });
      const approved = (await approveRes.json()) as { installation_id: string };
      const completeRes = await deviceJson("/pair/complete", {
        method: "POST",
        body: JSON.stringify({
          pairing_id: started.pairing_id,
          client_challenge: challenge,
        }),
      });
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
      const installationId = approved.installation_id;

      const { ws: deviceWs } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${DEVICE}/ws/device`,
        { host: "localhost:8788", Authorization: `Bearer ${token}` },
      );
      const deviceInbox = collectWsMessages(deviceWs);
      await waitForWsMessage(deviceWs, (m) => m.type === "session_hello");

      const { ws: adminWs } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${ADMIN}/admin/ws/device/${installationId}`,
        Object.fromEntries(adminHeaders().entries()),
      );
      const adminInbox = collectWsMessages(adminWs);
      await waitForCollected(adminInbox, (m) => m.type === "admin_hello");

      adminWs.send(JSON.stringify({ type: "sweep_press", payload: {} }));
      const hPress = await waitForCollected(
        deviceInbox,
        (m) => m.type === "sweep_press",
        8_000,
      );
      const hHoldId = String((hPress.payload as { hold?: { hold_id: string } })?.hold?.hold_id);
      expect(hHoldId).toBeTruthy();

      adminWs.send(JSON.stringify({ type: "vertical_sweep_press", payload: {} }));
      const vPress = await waitForCollected(
        deviceInbox,
        (m) =>
          m.type === "vertical_sweep_press" &&
          (m.payload as { hold?: { hold_id: string } })?.hold?.hold_id !== hHoldId,
        8_000,
      );
      const vHoldId = String((vPress.payload as { hold?: { hold_id: string } })?.hold?.hold_id);
      expect(vHoldId).toBeTruthy();

      const stub = env.DEVICE_DO.get(env.DEVICE_DO.idFromName(installationId));
      let status = (await (await stub.fetch("https://device-do/status")).json()) as {
        active_holds: Array<{ hold_id: string; effect: string }>;
      };
      expect(status.active_holds).toHaveLength(2);

      // Keepalive V across more than one lease window (logical — refresh lease)
      for (let i = 0; i < 3; i++) {
        adminWs.send(JSON.stringify({ type: "hold_keepalive", payload: { hold_id: vHoldId } }));
        await new Promise((r) => setTimeout(r, 20));
      }

      adminWs.send(JSON.stringify({ type: "sweep_release", payload: { hold_id: hHoldId } }));
      await waitForCollected(deviceInbox, (m) => m.type === "sweep_release", 8_000);

      status = (await (await stub.fetch("https://device-do/status")).json()) as {
        active_holds: Array<{ hold_id: string; effect: string }>;
      };
      expect(status.active_holds).toHaveLength(1);
      expect(status.active_holds[0]!.hold_id).toBe(vHoldId);
      expect(status.active_holds[0]!.effect).toBe("vertical_sweep");

      adminWs.send(
        JSON.stringify({ type: "vertical_sweep_release", payload: { hold_id: vHoldId } }),
      );
      await waitForCollected(deviceInbox, (m) => m.type === "vertical_sweep_release", 8_000);

      status = (await (await stub.fetch("https://device-do/status")).json()) as {
        active_holds: Array<{ hold_id: string }>;
      };
      expect(status.active_holds).toHaveLength(0);

      // Second tab is source-scoped (independent)
      const { ws: adminWsB } = await openWorkerWebSocket(
        SELF.fetch.bind(SELF),
        `${ADMIN}/admin/ws/device/${installationId}`,
        Object.fromEntries(adminHeaders().entries()),
      );
      const adminInboxB = collectWsMessages(adminWsB);
      const helloB = await waitForCollected(adminInboxB, (m) => m.type === "admin_hello");
      const sourceB = String((helloB.payload as { source?: string })?.source ?? "");
      expect(sourceB.startsWith("cloud:web:")).toBe(true);

      adminInbox.stop();
      adminInboxB.stop();
      deviceInbox.stop();
      adminWs.close(1000, "done");
      adminWsB.close(1000, "done");
      deviceWs.close(1000, "done");
    },
    60_000,
  );
});
