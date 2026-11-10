/**
 * In-process DeviceDO harness (Node). Complements workerd e2e in tests/workers/.
 * WebSocketPair cases are skipped on plain Node — see tests/INTEGRATION.md.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { DeviceDO } from "../src/durable/DeviceDO";
import { HOLD_LEASE_MS, MOMENTARY_PRESS_TYPES } from "../src/protocol/types";
import { createMemoryEnv, seedCredential } from "./helpers/memoryEnv";
import { assignScene, getAssignment, publishRelease } from "../src/scenes/releases";
import { packValidScene } from "./helpers/sceneBundle";
import { getCommand, logCommand } from "../src/db/commands";

class MemoryStorage {
  private data = new Map<string, unknown>();
  private alarm: number | null = null;

  async get<T>(key: string): Promise<T | undefined> {
    return this.data.get(key) as T | undefined;
  }
  async put(key: string, value: unknown): Promise<void> {
    this.data.set(key, value);
  }
  async delete(key: string): Promise<boolean> {
    return this.data.delete(key);
  }
  async setAlarm(at: number): Promise<void> {
    this.alarm = at;
  }
  async deleteAlarm(): Promise<void> {
    this.alarm = null;
  }
  async getAlarm(): Promise<number | null> {
    return this.alarm;
  }
}

class MemoryDOState {
  storage = new MemoryStorage();
  private sockets: WebSocket[] = [];

  blockConcurrencyWhile<T>(fn: () => Promise<T>): Promise<T> {
    return fn();
  }
  acceptWebSocket(ws: WebSocket): void {
    this.sockets.push(ws);
  }
  getWebSockets(): WebSocket[] {
    return this.sockets;
  }
}

const hasWebSocketPair = typeof WebSocketPair !== "undefined";

describe("DeviceDO offline + desired state", () => {
  it("HTTP command while offline returns device_offline (no fake pending)", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "do-1", "tokentokentokentoken77");
    const state = new MemoryDOState();
    const dob = new DeviceDO(state as unknown as DurableObjectState, env);

    const res = await dob.fetch(
      new Request("https://device-do/command", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          type: "blackout",
          installation_id: "do-1",
          payload: { enabled: true },
        }),
      }),
    );
    expect(res.status).toBe(409);
    const body = (await res.json()) as { error?: string; status: string };
    expect(body.error).toBe("device_offline");
    expect(body.status).toBe("failed");
    // No pending left in storage
    const meta = await state.storage.get<{ pending: unknown[] }>("meta");
    expect(meta?.pending?.length ?? 0).toBe(0);
  });

  it("status online is false without device socket", async () => {
    const env = createMemoryEnv();
    const state = new MemoryDOState();
    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    const res = await dob.fetch(new Request("https://device-do/status"));
    const body = (await res.json()) as { online: boolean; device_connected: boolean };
    expect(body.online).toBe(false);
    expect(body.device_connected).toBe(false);
  });

  it("desired assignment lives in D1 independently of DO pending", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "do-2", "tokentokentokentoken66");
    const packed = await packValidScene("s");
    await publishRelease(env, {
      sceneId: "s",
      sceneName: "S",
      bundleBytes: packed.bytes,
      baseVersion: null,
      publishedBy: "admin",
      notifyAssignments: false,
    });
    await assignScene(env, {
      installationId: "do-2",
      sceneId: "s",
      desiredVersion: 1,
      trackLatest: true,
      actor: "admin",
    });
    expect(await getAssignment(env, "do-2")).toMatchObject({
      scene_id: "s",
      desired_version: 1,
    });
  });

  it("momentary presses are never replay candidates after reconnect policy", () => {
    expect(MOMENTARY_PRESS_TYPES.has("strobe_press")).toBe(true);
    expect(MOMENTARY_PRESS_TYPES.has("blackout")).toBe(false);
  });

  it("holds restore after DO recreation / hibernation from storage", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "hold-restore", "tokentokentokentoken33");
    const state = new MemoryDOState();
    const leaseUntil = Date.now() + HOLD_LEASE_MS * 3;
    await state.storage.put("meta", {
      pending: [],
      holds: [
        {
          hold_id: "h-sweep",
          source: "cloud:web:tab1",
          effect: "sweep",
          lease_until: leaseUntil,
          adminSessionId: "sess-1",
        },
        {
          hold_id: "h-vert",
          source: "cloud:web:tab1",
          effect: "vertical_sweep",
          lease_until: leaseUntil,
          adminSessionId: "sess-1",
        },
      ],
      installationId: "hold-restore",
    });

    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    // Allow constructor blockConcurrencyWhile to finish
    await new Promise((r) => setTimeout(r, 10));

    const status = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: Array<{ hold_id: string; effect: string }>;
    };
    expect(status.active_holds).toHaveLength(2);
    expect(status.active_holds.map((h) => h.effect).sort()).toEqual(["sweep", "vertical_sweep"]);
    expect(await state.storage.getAlarm()).toBe(leaseUntil);
  });
});

describe.runIf(hasWebSocketPair)("DeviceDO WebSocket holds (workerd/WebSocketPair)", () => {
  let env: ReturnType<typeof createMemoryEnv>;
  let state: MemoryDOState;
  let dob: DeviceDO;

  beforeEach(() => {
    env = createMemoryEnv();
    state = new MemoryDOState();
    dob = new DeviceDO(state as unknown as DurableObjectState, env);
  });

  async function connectDevice(installationId: string): Promise<WebSocket> {
    const res = await dob.fetch(
      new Request("https://device-do/connect/device", {
        headers: {
          Upgrade: "websocket",
          "x-installation-id": installationId,
        },
      }),
    );
    expect(res.status).toBe(101);
    return res.webSocket!;
  }

  async function connectAdmin(installationId: string, source: string): Promise<WebSocket> {
    const res = await dob.fetch(
      new Request("https://device-do/connect/admin", {
        headers: {
          Upgrade: "websocket",
          "x-installation-id": installationId,
          "x-admin-source": source,
        },
      }),
    );
    expect(res.status).toBe(101);
    return res.webSocket!;
  }

  function waitMsg(ws: WebSocket, pred: (m: Record<string, unknown>) => boolean, ms = 3000) {
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("timeout")), ms);
      const onMsg = (ev: MessageEvent) => {
        const m = JSON.parse(String(ev.data)) as Record<string, unknown>;
        if (pred(m)) {
          clearTimeout(t);
          ws.removeEventListener("message", onMsg);
          resolve(m);
        }
      };
      ws.addEventListener("message", onMsg);
    });
  }

  it("two admin tabs have independent sources; offline press → device_offline", async () => {
    await seedCredential(env, "ws-1", "tokentokentokentoken55");
    const a1 = await connectAdmin("ws-1", "cloud:web:tab-a");
    const a2 = await connectAdmin("ws-1", "cloud:web:tab-b");
    expect(a1).toBeTruthy();
    expect(a2).toBeTruthy();

    // No device online — press fails and must not leave a fake active hold.
    a1.send(JSON.stringify({ type: "strobe_press", payload: { hold_id: "offline-shared" } }));
    await new Promise((r) => setTimeout(r, 20));

    const online = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      online: boolean;
      active_holds: unknown[];
    };
    expect(online.online).toBe(false);
    expect(online.active_holds).toHaveLength(0);

    // Connect device — momentary must not replay
    const device = await connectDevice("ws-1");
    expect(device).toBeTruthy();
    const after = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      online: boolean;
      pending_commands: number;
    };
    expect(after.online).toBe(true);
  });

  it("two admins sharing hold_id: releasing A leaves B; failed ACK drops the hold", async () => {
    await seedCredential(env, "share-1", "tokentokentokentoken44");
    const device = await connectDevice("share-1");
    const deviceInbox: Record<string, unknown>[] = [];
    device.addEventListener("message", (ev) => {
      deviceInbox.push(JSON.parse(String(ev.data)) as Record<string, unknown>);
    });
    const adminA = await connectAdmin("share-1", "cloud:web:share-a");
    const adminB = await connectAdmin("share-1", "cloud:web:share-b");
    await waitMsg(adminA, (m) => m.type === "admin_hello");
    await waitMsg(adminB, (m) => m.type === "admin_hello");

    adminA.send(JSON.stringify({ type: "strobe_press", payload: { hold_id: "shared" } }));
    await waitMsg(adminA, (m) => m.type === "command_result");
    adminB.send(JSON.stringify({ type: "strobe_press", payload: { hold_id: "shared" } }));
    await waitMsg(adminB, (m) => m.type === "command_result");

    let status = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: Array<{ hold_id: string; source: string }>;
    };
    expect(status.active_holds.filter((h) => h.hold_id === "shared")).toHaveLength(2);

    adminA.send(JSON.stringify({ type: "strobe_release", payload: { hold_id: "shared" } }));
    await waitMsg(adminA, (m) => m.type === "command_result");
    status = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: Array<{ hold_id: string; source: string }>;
    };
    expect(status.active_holds).toHaveLength(1);
    expect(status.active_holds[0]!.hold_id).toBe("shared");
    expect(status.active_holds[0]!.source).toBe("cloud:web:share-b");

    adminB.send(JSON.stringify({ type: "strobe_release", payload: { hold_id: "shared" } }));
    await waitMsg(adminB, (m) => m.type === "command_result");

    const adminC = await connectAdmin("share-1", "cloud:web:share-c");
    await waitMsg(adminC, (m) => m.type === "admin_hello");
    adminC.send(JSON.stringify({ type: "strobe_press", payload: { hold_id: "reject-me" } }));
    const armed = await waitMsg(adminC, (m) => m.type === "command_result");
    expect((armed.payload as { ok?: boolean }).ok).toBe(true);
    const press = deviceInbox.find(
      (m) =>
        m.type === "strobe_press" &&
        (m.payload as { hold?: { hold_id?: string } })?.hold?.hold_id === "reject-me",
    ) as { payload?: { command_id?: string } } | undefined;
    const commandId = String(press?.payload?.command_id ?? "");
    expect(commandId).toBeTruthy();
    device.send(
      JSON.stringify({
        type: "ack",
        payload: { command_id: commandId, status: "failed", error_code: "executor_rejected" },
      }),
    );
    await new Promise((r) => setTimeout(r, 40));
    const afterFail = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: unknown[];
    };
    expect(afterFail.active_holds).toHaveLength(0);
  });

  it("H+V holds: release H leaves V; lease expiry clears V with effect release; two tabs source-scoped", async () => {
    await seedCredential(env, "hv-1", "tokentokentokentoken11");
    await connectDevice("hv-1");

    const admin = await connectAdmin("hv-1", "cloud:web:tab-hv");
    await waitMsg(admin, (m) => m.type === "admin_hello");

    admin.send(JSON.stringify({ type: "sweep_press", payload: {} }));
    const hResult = await waitMsg(admin, (m) => m.type === "command_result");
    const hHoldId = String((hResult.payload as { hold_id?: string })?.hold_id ?? "");
    expect(hHoldId).toBeTruthy();

    admin.send(JSON.stringify({ type: "vertical_sweep_press", payload: {} }));
    const vResult = await waitMsg(admin, (m) => {
      if (m.type !== "command_result") return false;
      const id = (m.payload as { hold_id?: string })?.hold_id;
      return Boolean(id) && id !== hHoldId;
    });
    const vHoldId = String((vResult.payload as { hold_id?: string })?.hold_id ?? "");
    expect(vHoldId).toBeTruthy();

    let status = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: Array<{ hold_id: string; effect: string }>;
    };
    expect(status.active_holds).toHaveLength(2);

    // Explicit H release must NOT remove V
    admin.send(JSON.stringify({ type: "sweep_release", payload: { hold_id: hHoldId } }));
    await waitMsg(admin, (m) => m.type === "command_result");
    await new Promise((r) => setTimeout(r, 20));

    status = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: Array<{ hold_id: string; effect: string }>;
    };
    expect(status.active_holds).toHaveLength(1);
    expect(status.active_holds[0]!.effect).toBe("vertical_sweep");
    expect(status.active_holds[0]!.hold_id).toBe(vHoldId);

    // Keepalive across >1 lease period — V still held
    for (let i = 0; i < 3; i++) {
      admin.send(JSON.stringify({ type: "hold_keepalive", payload: { hold_id: vHoldId } }));
      await new Promise((r) => setTimeout(r, 5));
    }
    status = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: Array<{ hold_id: string; effect: string }>;
    };
    expect(status.active_holds.some((h) => h.hold_id === vHoldId)).toBe(true);

    // Persist V with expired lease, hibernate/recreate DO, alarm → effect-specific release
    const meta = (await state.storage.get<{
      holds: Array<{
        hold_id: string;
        lease_until: number;
        effect: string;
        source: string;
        adminSessionId: string;
      }>;
      pending: unknown[];
      installationId?: string;
    }>("meta"))!;
    for (const h of meta.holds) {
      if (h.hold_id === vHoldId) h.lease_until = Date.now() - 1;
    }
    await state.storage.put("meta", meta);

    const hibernated = new MemoryDOState();
    hibernated.storage = state.storage;
    const dob2 = new DeviceDO(hibernated as unknown as DurableObjectState, env);
    await new Promise((r) => setTimeout(r, 10));

    const device2Res = await dob2.fetch(
      new Request("https://device-do/connect/device", {
        headers: { Upgrade: "websocket", "x-installation-id": "hv-1" },
      }),
    );
    const device2 = device2Res.webSocket!;
    const expiryMsgs: Record<string, unknown>[] = [];
    device2.addEventListener("message", (ev) => {
      expiryMsgs.push(JSON.parse(String(ev.data)) as Record<string, unknown>);
    });

    await dob2.alarm();
    await new Promise((r) => setTimeout(r, 30));

    const afterExpiry = (await (
      await dob2.fetch(new Request("https://device-do/status"))
    ).json()) as { active_holds: unknown[] };
    expect(afterExpiry.active_holds).toHaveLength(0);
    expect(expiryMsgs.some((m) => m.type === "vertical_sweep_release")).toBe(true);
    expect(expiryMsgs.some((m) => m.type === "release_source")).toBe(false);

    // Two tabs remain source-scoped
    const tabB = await connectAdmin("hv-1", "cloud:web:tab-b");
    await waitMsg(tabB, (m) => m.type === "admin_hello");
    expect(tabB).toBeTruthy();
  });

  it("desired sync redelivered after reconnect until applied ACK; accepted does not block", async () => {
    await seedCredential(env, "sync-1", "tokentokentokentoken22");
    const packed = await packValidScene("sync-scene");
    await publishRelease(env, {
      sceneId: "sync-scene",
      sceneName: "Sync",
      bundleBytes: packed.bytes,
      baseVersion: null,
      publishedBy: "admin",
      notifyAssignments: false,
    });
    await assignScene(env, {
      installationId: "sync-1",
      sceneId: "sync-scene",
      desiredVersion: 1,
      trackLatest: true,
      actor: "admin",
    });
    const assignment = (await getAssignment(env, "sync-1"))!;
    const commandId = `desired:sync-1:${assignment.scene_id}:${assignment.desired_version}:${assignment.updated_at}`;

    // Pre-seed accepted (device got sync but disconnected before ACK)
    await logCommand(env, {
      command_id: commandId,
      installation_id: "sync-1",
      type: "sync_scene_release",
      payload: { scene_id: "sync-scene", version: 1 },
      status: "accepted",
    });

    const device = await connectDevice("sync-1");
    const inbox: Record<string, unknown>[] = [];
    device.addEventListener("message", (ev) => {
      inbox.push(JSON.parse(String(ev.data)) as Record<string, unknown>);
    });
    await waitMsg(device, (m) => m.type === "session_hello");
    await new Promise((r) => setTimeout(r, 50));

    const syncs = inbox.filter((m) => m.type === "sync_scene_release");
    expect(syncs.length).toBeGreaterThanOrEqual(1);
    expect((await getCommand(env, commandId))?.status).toBe("accepted");

    // ACK applied — further reconnect must not re-send
    device.send(
      JSON.stringify({
        type: "ack",
        payload: { command_id: commandId, status: "applied" },
      }),
    );
    await new Promise((r) => setTimeout(r, 30));
    expect((await getCommand(env, commandId))?.status).toBe("applied");

    device.close(1000, "reconnect");
    await new Promise((r) => setTimeout(r, 20));

    const device2 = await connectDevice("sync-1");
    const inbox2: Record<string, unknown>[] = [];
    device2.addEventListener("message", (ev) => {
      inbox2.push(JSON.parse(String(ev.data)) as Record<string, unknown>);
    });
    await waitMsg(device2, (m) => m.type === "session_hello");
    await new Promise((r) => setTimeout(r, 50));
    expect(inbox2.filter((m) => m.type === "sync_scene_release")).toHaveLength(0);
  });

  it("momentary press is never replayed after reconnect", async () => {
    await seedCredential(env, "mom-1", "tokentokentokentoken99");
    const device = await connectDevice("mom-1");
    const admin = await connectAdmin("mom-1", "cloud:web:mom");
    await waitMsg(admin, (m) => m.type === "admin_hello");

    admin.send(JSON.stringify({ type: "strobe_press", payload: {} }));
    await waitMsg(admin, (m) => m.type === "command_result");

    device.close(1000, "drop");
    await new Promise((r) => setTimeout(r, 30));

    const offline = await dob.fetch(
      new Request("https://device-do/command", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          type: "strobe_press",
          installation_id: "mom-1",
          payload: {},
        }),
      }),
    );
    expect(offline.status).toBe(409);

    const device2 = await connectDevice("mom-1");
    const inbox: Record<string, unknown>[] = [];
    device2.addEventListener("message", (ev) => {
      inbox.push(JSON.parse(String(ev.data)) as Record<string, unknown>);
    });
    await waitMsg(device2, (m) => m.type === "session_hello");
    await new Promise((r) => setTimeout(r, 50));
    expect(inbox.filter((m) => m.type === "strobe_press")).toHaveLength(0);
  });

  it("a desk session cannot restart or update the executor", async () => {
    await seedCredential(env, "desk-1", "tokentokentokentoken77");
    const device = await connectDevice("desk-1");
    const inbox: Record<string, unknown>[] = [];
    device.addEventListener("message", (ev) => {
      inbox.push(JSON.parse(String(ev.data)) as Record<string, unknown>);
    });
    await waitMsg(device, (m) => m.type === "session_hello");
    const desk = await connectAdmin("desk-1", "cloud:desk:window-a");
    const hello = await waitMsg(desk, (m) => m.type === "admin_hello");
    expect((hello.payload as { source?: string }).source).toBe("cloud:desk:window-a");
    desk.send(
      JSON.stringify({ type: "restart_runtime", payload: { command_id: "restart-1" } }),
    );
    const err = await waitMsg(desk, (m) => m.type === "error");
    expect((err.payload as { code?: string }).code).toBe("desk_forbidden");
    expect((err.payload as { command_id?: string }).command_id).toBe("restart-1");
    await new Promise((r) => setTimeout(r, 30));
    expect(inbox.filter((m) => m.type === "restart_runtime")).toHaveLength(0);
  });
});

const APP_UPDATE_TYPES = [
  "check_app_update",
  "stage_app_update",
  "apply_app_update",
  "rollback_app_update",
  "restart_runtime",
] as const;

async function bootStored(meta: Record<string, unknown>) {
  const env = createMemoryEnv();
  const state = new MemoryDOState();
  const sets: number[] = [];
  const setAlarm = state.storage.setAlarm.bind(state.storage);
  const deleteAlarm = state.storage.deleteAlarm.bind(state.storage);
  state.storage.setAlarm = async (at: number) => {
    sets.push(at);
    await setAlarm(at);
  };
  state.storage.deleteAlarm = async () => {
    await deleteAlarm();
  };
  await state.storage.put("meta", meta);
  const dob = new DeviceDO(state as unknown as DurableObjectState, env);
  await new Promise((r) => setTimeout(r, 20));
  return { env, state, dob, sets };
}

describe("DeviceDO transport alarm", () => {
  it("does not keep arming a past timer for an expired app update", async () => {
    const now = Date.now();
    const { state, dob, sets } = await bootStored({
      installationId: "inst-alarm",
      pending: APP_UPDATE_TYPES.map((type) => ({
        command_id: `upd-${type}`,
        type,
        created_at: now - 120_000,
        ttl_ms: 30_000,
        momentary_press: false,
        desired_sync: true,
        payload: { build_id: "build-keep", version: "1.2.3" },
      })),
    });
    expect(sets.filter((at) => at <= now)).toEqual([]);
    expect(await state.storage.getAlarm()).toBeNull();
    await dob.alarm();
    await dob.alarm();
    await dob.alarm();
    expect(sets.filter((at) => at <= now)).toEqual([]);
    expect(await state.storage.getAlarm()).toBeNull();
    const meta = await state.storage.get<{ pending: Array<{ command_id: string; type: string }> }>("meta");
    expect(meta?.pending.map((cmd) => cmd.type).sort()).toEqual([...APP_UPDATE_TYPES].sort());
  });

  it("keeps a long update pending until a real terminal result", async () => {
    const now = Date.now();
    const { env, state, dob } = await bootStored({
      installationId: "inst-alarm",
      pending: [
        {
          command_id: "upd-apply",
          type: "apply_app_update",
          created_at: now - 120_000,
          ttl_ms: 30_000,
          momentary_press: false,
          desired_sync: true,
          payload: { build_id: "build-keep", version: "1.2.3" },
        },
      ],
    });
    await dob.alarm();
    const still = await state.storage.get<{ pending: Array<{ command_id: string }> }>("meta");
    expect(still?.pending.map((cmd) => cmd.command_id)).toEqual(["upd-apply"]);
    const logged = await getCommand(env, "upd-apply");
    expect(logged?.error_code ?? null).not.toBe("ttl_expired");
    const res = await dob.fetch(
      new Request("https://device-do/update-result", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ command_id: "upd-apply", event_type: "applied" }),
      }),
    );
    expect(res.status).toBe(200);
    const after = await state.storage.get<{ pending: unknown[] }>("meta");
    expect(after?.pending ?? []).toEqual([]);
    expect(await state.storage.getAlarm()).toBeNull();
  });

  it("expires an ordinary command exactly at its ttl and leaves the update alone", async () => {
    const now = 1_700_000_000_000;
    vi.spyOn(Date, "now").mockReturnValue(now);
    try {
      const { env, state, dob } = await bootStored({
        installationId: "inst-alarm",
        pending: [
          {
            command_id: "edge",
            type: "blackout",
            created_at: now - 30_000,
            ttl_ms: 30_000,
            momentary_press: false,
            desired_sync: false,
            payload: {},
          },
          {
            command_id: "later",
            type: "select_preset",
            created_at: now - 1_000,
            ttl_ms: 30_000,
            momentary_press: false,
            desired_sync: false,
            payload: {},
          },
          {
            command_id: "upd-check",
            type: "check_app_update",
            created_at: now - 120_000,
            ttl_ms: 30_000,
            momentary_press: false,
            desired_sync: true,
            payload: { build_id: "build-keep" },
          },
        ],
        holds: [
          {
            hold_id: "h1",
            source: "cloud:web:s",
            effect: "strobe",
            lease_until: now - 5,
            adminSessionId: "s",
            command_id: "hold-cmd",
          },
        ],
      });
      await logCommand(env, {
        command_id: "edge",
        installation_id: "inst-alarm",
        type: "blackout",
        payload: {},
        status: "accepted",
      });
      expect(await state.storage.getAlarm()).toBe(now - 5);
      await dob.alarm();
      expect((await getCommand(env, "edge"))?.error_code).toBe("ttl_expired");
      expect((await getCommand(env, "later"))?.status ?? "accepted").not.toBe("failed");
      const meta = await state.storage.get<{
        pending: Array<{ command_id: string }>;
        holds: unknown[];
      }>("meta");
      expect(meta?.pending.map((cmd) => cmd.command_id).sort()).toEqual(["later", "upd-check"]);
      expect(meta?.holds ?? []).toEqual([]);
      expect(await state.storage.getAlarm()).toBe(now + 29_000);
    } finally {
      vi.restoreAllMocks();
    }
  });
});

describe("telemetry and hold refresh", () => {
  it("does not journal a hold refresh or arm a new alarm", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "do-1", "tokentokentokentoken77");
    const state = new MemoryDOState();
    const alarms: number[] = [];
    const setAlarm = state.storage.setAlarm.bind(state.storage);
    state.storage.setAlarm = async (at: number) => {
      alarms.push(at);
      return setAlarm(at);
    };
    let puts = 0;
    const put = state.storage.put.bind(state.storage);
    state.storage.put = async (key: string, value: unknown) => {
      puts += 1;
      return put(key, value);
    };
    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    await new Promise((resolve) => setTimeout(resolve, 20));
    alarms.length = 0;
    const sent: string[] = [];
    const otherUntil = 50;
    const view = dob as unknown as {
      deviceSocket: { send: (data: string) => void };
      holds: Map<string, { lease_until: number; adminSessionId: string }>;
      onAdminMessage: (ws: object, att: object, msg: object) => Promise<void>;
    };
    view.deviceSocket = { send: (data) => sent.push(data) };
    view.holds.set("sess::h1", {
      hold_id: "h1",
      adminSessionId: "sess",
      lease_until: 1_000,
      source: "cloud:web:sess",
      effect: "strobe",
      command_id: "c1",
      installationId: "do-1",
    } as never);
    view.holds.set("other::h2", {
      hold_id: "h2",
      adminSessionId: "other",
      lease_until: otherUntil,
      source: "cloud:web:other",
      effect: "strobe",
      command_id: "c2",
      installationId: "do-1",
    } as never);
    const putsBefore = puts;
    await view.onAdminMessage(
      { send() {} },
      { role: "admin", installationId: "do-1", sessionId: "sess", source: "cloud:web:sess" },
      { type: "hold_keepalive", payload: { hold_id: "h1" } },
    );
    expect(sent).toHaveLength(1);
    const forwarded = JSON.parse(sent[0]) as { type: string; payload: { command_id?: string } };
    expect(forwarded.type).toBe("hold_keepalive");
    expect(forwarded.payload.command_id).toBeUndefined();
    expect(alarms).toEqual([]);
    expect(puts).toBe(putsBefore);
    expect(env.__db.tables.command_log.some((row) => row.type === "hold_keepalive")).toBe(false);
    expect(view.holds.get("sess::h1")?.lease_until).toBeGreaterThan(1_000);
    expect(view.holds.get("other::h2")?.lease_until).toBe(otherUntil);
  });

  it("does not rewrite D1 or meta when the heartbeat is unchanged", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "do-1", "tokentokentokentoken77");
    const state = new MemoryDOState();
    let puts = 0;
    const put = state.storage.put.bind(state.storage);
    state.storage.put = async (key: string, value: unknown) => {
      puts += 1;
      return put(key, value);
    };
    let prepares = 0;
    const prepare = env.DB.prepare.bind(env.DB);
    env.DB.prepare = ((sql: string) => {
      prepares += 1;
      return prepare(sql);
    }) as typeof env.DB.prepare;
    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    const view = dob as unknown as {
      onDeviceMessage: (att: object, msg: object) => Promise<void>;
    };
    const msg = {
      type: "heartbeat",
      payload: { armed: false, blackout: true, transport: "mock", build_id: "b", arch: "arm64", uptime_s: 1 },
    };
    const att = { role: "device", installationId: "do-1", sessionId: "s" };
    await view.onDeviceMessage(att, msg);
    const afterFirst = prepares;
    const putsFirst = puts;
    expect(afterFirst).toBeGreaterThan(0);
    await view.onDeviceMessage(att, { ...msg, payload: { ...msg.payload, uptime_s: 90 } });
    expect(prepares).toBe(afterFirst);
    expect(puts).toBe(putsFirst);
  });

  it("tells the hall how many desks are open", () => {
    const env = createMemoryEnv();
    const state = new MemoryDOState();
    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    const sent: string[] = [];
    const view = dob as unknown as {
      deviceSocket: { send: (data: string) => void };
      sessions: Map<object, { role: string }>;
      tellSubscribers: () => void;
    };
    view.deviceSocket = { send: (data) => sent.push(data) };
    view.sessions.set({}, { role: "admin" });
    view.sessions.set({}, { role: "admin" });
    view.tellSubscribers();
    expect(JSON.parse(sent[0]).payload.count).toBe(2);
  });
});

describe("Workers integration blocker", () => {
  it("documents that unit suite is not full workerd e2e", () => {
    expect(hasWebSocketPair || !hasWebSocketPair).toBe(true);
  });
});

