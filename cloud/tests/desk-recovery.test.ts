/**
 * Desk recovery: queue time, ephemeral commands, snapshot age, and which store is down.
 * The command_log index is measured here before any index change.
 */
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { handleAdminRequest } from "../src/admin/routes";
import { DeviceDO } from "../src/durable/DeviceDO";
import { getCommand, logCommand } from "../src/db/commands";
import { takeFreshQueuedCommands } from "../src/db/deskLink";
import type { Env } from "../src/env";
import { createMemoryEnv, seedCredential } from "./helpers/memoryEnv";

class MemoryStorage {
  private data = new Map<string, unknown>();
  async get<T>(key: string): Promise<T | undefined> {
    return this.data.get(key) as T | undefined;
  }
  async put(key: string, value: unknown): Promise<void> {
    this.data.set(key, value);
  }
  async delete(key: string): Promise<boolean> {
    return this.data.delete(key);
  }
  async setAlarm(): Promise<void> {}
  async deleteAlarm(): Promise<void> {}
  async getAlarm(): Promise<number | null> {
    return null;
  }
}

class MemoryDOState {
  storage = new MemoryStorage();
  acceptWebSocket(): void {}
  getWebSockets(): WebSocket[] {
    return [];
  }
  blockConcurrencyWhile<T>(fn: () => Promise<T>): Promise<T> {
    return fn();
  }
}

class PairSocket {
  sent: string[] = [];
  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {}
  serializeAttachment(): void {}
  deserializeAttachment(): null {
    return null;
  }
  addEventListener(): void {}
}

function installSocketPair(): void {
  const pair = function WebSocketPair(this: unknown) {
    return [new PairSocket(), new PairSocket()];
  };
  (globalThis as { WebSocketPair?: unknown }).WebSocketPair = pair;
}

function admin(path: string, method = "GET", body?: unknown): Request {
  return new Request(`http://127.0.0.1${path}`, {
    method,
    headers: {
      host: "127.0.0.1",
      "content-type": "application/json",
      "x-dev-access-email": "desk@example.com",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

describe("command queue window", () => {
  it("uses the installation time index and does not read the old history", async () => {
    const db = new DatabaseSync(":memory:");
    const schema = readFileSync(new URL("../migrations/0001_init.sql", import.meta.url), "utf8");
    db.exec(schema);
    db.prepare(
      `INSERT INTO installations (id, name, platform, created_at) VALUES ('hall', 'Hall', 'raspberry', ?)`,
    ).run("2026-01-01T00:00:00.000Z");
    const insert = db.prepare(
      `INSERT INTO command_log (command_id, installation_id, type, payload_json, status, created_at)
       VALUES (?, 'hall', ?, ?, 'queued', ?)`,
    );
    const now = Date.parse("2026-11-11T00:00:00.000Z");
    for (let i = 0; i < 800; i += 1) {
      insert.run(`old-${i}`, "blackout", "{}", new Date(now - 86_400_000 - i * 1000).toISOString());
    }
    insert.run("short", "blackout", JSON.stringify({ ttl_ms: 1_000 }), new Date(now - 10_000).toISOString());
    insert.run("normal", "select_preset", JSON.stringify({ ttl_ms: 30_000 }), new Date(now - 10_000).toISOString());
    insert.run("long-inside", "arm", JSON.stringify({ ttl_ms: 120_000 }), new Date(now - 10_000).toISOString());
    insert.run("long-outside", "disarm", JSON.stringify({ ttl_ms: 120_000 }), new Date(now - 45_000).toISOString());
    insert.run("press", "strobe_press", "{}", new Date(now - 1_000).toISOString());
    const sql = `SELECT command_id, type, payload_json, created_at FROM command_log
     WHERE installation_id = ? AND status = 'queued' AND created_at >= ?
     ORDER BY created_at ASC
     LIMIT 20`;
    const plan = db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all() as Array<{ detail: string }>;
    expect(plan.map((row) => row.detail).join(" ")).toContain("idx_command_log_installation");
    expect(plan.map((row) => row.detail).join(" ")).not.toContain("SCAN");
    const env = {
      DB: {
        prepare(text: string) {
          const stmt = db.prepare(text);
          let binds: unknown[] = [];
          return {
            bind(...args: unknown[]) {
              binds = args;
              return this;
            },
            async all() {
              return { results: stmt.all(...(binds as never[])) };
            },
          };
        },
      },
    } as Env;
    const due = await takeFreshQueuedCommands(env, "hall", now);
    expect(due.map((row) => row.command_id).sort()).toEqual(["long-inside", "long-outside", "normal"]);
    const windowRows = db
      .prepare(
        `SELECT COUNT(*) AS n FROM command_log WHERE installation_id = ? AND created_at >= ?`,
      )
      .get("hall", new Date(now - 120_000).toISOString()) as { n: number };
    expect(windowRows.n).toBe(5);
    expect(db.prepare(`SELECT COUNT(*) AS n FROM command_log`).get() as { n: number }).toMatchObject({
      n: 805,
    });
  });
});

describe("DeviceDO recovery", () => {
  it("keeps the original time when a send fails and when the same command is retried", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "do-1", "tokentokentokentoken77");
    const dob = new DeviceDO(new MemoryDOState() as unknown as DurableObjectState, env);
    const socket = {
      send(): void {
        throw new Error("send failed");
      },
    };
    (dob as unknown as { deviceSocket: typeof socket }).deviceSocket = socket;
    const first = await dob.fetch(
      new Request("https://device-do/command", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          type: "blackout",
          installation_id: "do-1",
          payload: { command_id: "cmd-1", enabled: true },
        }),
      }),
    );
    const firstBody = (await first.json()) as { status: string };
    expect(firstBody.status).toBe("queued");
    const queued = await (
      dob as unknown as { state: MemoryDOState }
    ).state.storage.get<Array<{ command_id: string; enqueued_at: number }>>("mailbox_queue");
    const born = queued?.[0]?.enqueued_at;
    expect(typeof born).toBe("number");
    const second = await dob.fetch(
      new Request("https://device-do/command", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          type: "blackout",
          installation_id: "do-1",
          payload: { command_id: "cmd-1", enabled: true },
        }),
      }),
    );
    expect(((await second.json()) as { status: string }).status).toBe("queued");
    const again = await (
      dob as unknown as { state: MemoryDOState }
    ).state.storage.get<Array<{ enqueued_at: number }>>("mailbox_queue");
    expect(again?.[0]?.enqueued_at).toBe(born);
    const row = await getCommand(env, "cmd-1");
    expect(row?.status).toBe("queued");
    expect(row?.created_at).toBeTruthy();
  });

  it("does not play a press after the socket returns, and resends a look at its original time", async () => {
    installSocketPair();
    const env = createMemoryEnv();
    await seedCredential(env, "do-1", "tokentokentokentoken77");
    const state = new MemoryDOState();
    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    const pending = (
      dob as unknown as {
        pending: Map<string, Record<string, unknown>>;
      }
    ).pending;
    pending.set("press", {
      command_id: "press",
      type: "strobe_press",
      created_at: 1_000,
      ttl_ms: 30_000,
      payload: {},
      momentary_press: true,
      desired_sync: false,
    });
    pending.set("look", {
      command_id: "look",
      type: "select_preset",
      created_at: 1_000,
      ttl_ms: 30_000,
      payload: { preset_id: "A" },
      momentary_press: false,
      desired_sync: false,
    });
    await expect(
      dob.fetch(
        new Request("https://device-do/connect/device", {
          headers: { "x-installation-id": "do-1", Upgrade: "websocket" },
        }),
      ),
    ).rejects.toThrow(/status/);
    const server = (dob as unknown as { deviceSocket: PairSocket }).deviceSocket;
    const types = server.sent.map((raw) => (JSON.parse(raw) as { type: string }).type);
    expect(types).not.toContain("strobe_press");
    expect(types).toContain("select_preset");
    const look = server.sent
      .map((raw) => JSON.parse(raw) as { type: string; sent_at: string })
      .find((msg) => msg.type === "select_preset");
    expect(look?.sent_at).toBe(new Date(1_000).toISOString());
    expect(pending.has("press")).toBe(false);
    expect(pending.get("look")?.created_at).toBe(1_000);
  });

  it("leaves an unacked command queued at its original time until the ttl", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "do-1", "tokentokentokentoken77");
    const dob = new DeviceDO(new MemoryDOState() as unknown as DurableObjectState, env);
    (dob as unknown as { deviceSocket: { send: () => void } }).deviceSocket = { send() {} };
    const sent = await dob.fetch(
      new Request("https://device-do/command", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          type: "blackout",
          installation_id: "do-1",
          payload: { command_id: "cmd-ack", enabled: true, ttl_ms: 1_000 },
        }),
      }),
    );
    expect(((await sent.json()) as { status: string }).status).toBe("accepted");
    const pending = (dob as unknown as { pending: Map<string, { created_at: number }> }).pending;
    const born = pending.get("cmd-ack")?.created_at;
    const row = await getCommand(env, "cmd-ack");
    expect(row?.status).toBe("accepted");
    expect(row?.created_at).toBeTruthy();
    const again = await dob.fetch(
      new Request("https://device-do/command", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          type: "blackout",
          installation_id: "do-1",
          payload: { command_id: "cmd-ack", enabled: true, ttl_ms: 1_000 },
        }),
      }),
    );
    expect(((await again.json()) as { status: string }).status).toBe("accepted");
    expect(pending.get("cmd-ack")?.created_at).toBe(born);
    expect((await getCommand(env, "cmd-ack"))?.created_at).toBe(row?.created_at);
    const clock = (born ?? 0) + 1_000;
    const now = Date.now;
    Date.now = () => clock;
    try {
      await dob.alarm();
    } finally {
      Date.now = now;
    }
    const expired = await getCommand(env, "cmd-ack");
    expect(expired?.status).toBe("failed");
    expect(expired?.error_code).toBe("ttl_expired");
    expect(expired?.created_at).toBe(row?.created_at);
    expect(pending.has("cmd-ack")).toBe(false);
  });

  it("expires a short command and keeps one whose own ttl is still open", async () => {
    const env = createMemoryEnv();
    const dob = new DeviceDO(new MemoryDOState() as unknown as DurableObjectState, env);
    const pending = (dob as unknown as { pending: Map<string, Record<string, unknown>> }).pending;
    (dob as unknown as { installationId: string }).installationId = "do-1";
    const now = Date.now();
    pending.set("short", {
      command_id: "short",
      type: "blackout",
      created_at: now - 5_000,
      ttl_ms: 1_000,
      payload: {},
      momentary_press: false,
      desired_sync: false,
    });
    pending.set("long", {
      command_id: "long",
      type: "arm",
      created_at: now - 5_000,
      ttl_ms: 30_000,
      payload: {},
      momentary_press: false,
      desired_sync: false,
    });
    await dob.alarm();
    expect(pending.has("short")).toBe(false);
    expect(pending.has("long")).toBe(true);
  });

  it("ignores an ack from another installation", async () => {
    const env = createMemoryEnv();
    await logCommand(env, {
      command_id: "c1",
      installation_id: "do-1",
      type: "blackout",
      payload: { enabled: true },
      status: "accepted",
    });
    const dob = new DeviceDO(new MemoryDOState() as unknown as DurableObjectState, env);
    const pending = (dob as unknown as { pending: Map<string, Record<string, unknown>> }).pending;
    pending.set("c1", {
      command_id: "c1",
      type: "blackout",
      created_at: Date.now(),
      ttl_ms: 30_000,
      payload: {},
      momentary_press: false,
      desired_sync: false,
    });
    await (
      dob as unknown as {
        onDeviceMessage: (att: unknown, msg: unknown) => Promise<void>;
      }
    ).onDeviceMessage(
      { role: "device", installationId: "other", sessionId: "s" },
      { type: "ack", payload: { command_id: "c1", status: "applied" } },
    );
    expect((await getCommand(env, "c1"))?.status).toBe("accepted");
    expect(pending.has("c1")).toBe(true);
    await (
      dob as unknown as {
        onDeviceMessage: (att: unknown, msg: unknown) => Promise<void>;
      }
    ).onDeviceMessage(
      { role: "device", installationId: "do-1", sessionId: "s" },
      { type: "ack", payload: { command_id: "c1", status: "applied" } },
    );
    expect((await getCommand(env, "c1"))?.status).toBe("applied");
    expect(pending.has("c1")).toBe(false);
  });
});

describe("admin desk when a store is down", () => {
  it("returns the stored frame when the durable object is down and ages it", async () => {
    const env = createMemoryEnv();
    const storedAt = new Date(Date.now() - 10_000).toISOString();
    env.__db.tables.installations.push({
      id: "hall",
      name: "Hall",
      platform: "raspberry",
      capabilities_json: JSON.stringify({
        desk: { session: "show", sequence: 4, age_ms: 400, updated_at: storedAt, look: "bars" },
      }),
      armed: 1,
      blackout: 0,
      last_seen_at: new Date().toISOString(),
      created_at: storedAt,
      revoked_at: null,
      udp_active: 0,
    });
    env.DEVICE_DO = {
      idFromName: (name: string) => ({ name, toString: () => name }) as DurableObjectId,
      get: () =>
        ({
          fetch: async () => {
            throw new Error("Exceeded allowed volume of requests in Durable Objects free tier.");
          },
        }) as unknown as DurableObjectStub,
    } as unknown as DurableObjectNamespace;
    const res = await handleAdminRequest(admin("/admin/devices/hall/live"), env);
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      effect: { age_ms: number; session: string };
      online: boolean;
      device_channel: string;
      recently_seen: boolean;
    };
    expect(body.effect.session).toBe("show");
    expect(body.effect.age_ms).toBeGreaterThanOrEqual(10_400);
    expect(body.online).toBe(false);
    expect(body.device_channel).toBe("stored");
    expect(body.recently_seen).toBe(true);
  });

  it("returns the durable frame when the database read is refused", async () => {
    const env = createMemoryEnv();
    const real = env.DB.prepare.bind(env.DB);
    env.DB.prepare = ((sql: string) => {
      if (/FROM installations/i.test(sql)) throw new Error("exceeded D1 daily row read limit");
      return real(sql);
    }) as typeof env.DB.prepare;
    const storedAt = new Date(Date.now() - 8_000).toISOString();
    env.DEVICE_DO = {
      idFromName: (name: string) => ({ name, toString: () => name }) as DurableObjectId,
      get: () =>
        ({
          fetch: async () =>
            Response.json({
              desk: { session: "show", sequence: 2, age_ms: 200, updated_at: storedAt, look: "wash" },
            }),
        }) as unknown as DurableObjectStub,
    } as unknown as DurableObjectNamespace;
    const res = await handleAdminRequest(admin("/admin/devices/hall/live"), env);
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      online: boolean;
      recently_seen: boolean;
      effect: { age_ms: number; look: string | null };
    };
    expect(body.online).toBe(false);
    expect(body.recently_seen).toBe(true);
    expect(body.effect.look).toBe("wash");
    expect(body.effect.age_ms).toBeGreaterThanOrEqual(8_200);
  });

  it("says the desk is unavailable when both stores fail", async () => {
    const env = createMemoryEnv();
    env.DB.prepare = (() => {
      throw new Error("exceeded D1 daily row read limit");
    }) as typeof env.DB.prepare;
    env.DEVICE_DO = {
      idFromName: (name: string) => ({ name, toString: () => name }) as DurableObjectId,
      get: () =>
        ({
          fetch: async () => {
            throw new Error("Exceeded allowed volume of requests in Durable Objects free tier.");
          },
        }) as unknown as DurableObjectStub,
    } as unknown as DurableObjectNamespace;
    const live = await handleAdminRequest(admin("/admin/devices/hall/live"), env);
    expect(live.status).toBe(503);
    const liveBody = (await live.json()) as { error: { message: string } };
    expect(liveBody.error.message).toContain("Немає ні живого залу");
    const command = await handleAdminRequest(
      admin("/admin/devices/hall/command", "POST", { type: "blackout", payload: { enabled: true } }),
      env,
    );
    expect(command.status).toBe(503);
    const commandBody = (await command.json()) as { status?: string; error: { message: string } };
    expect(commandBody.status).not.toBe("applied");
    expect(commandBody.status).not.toBe("queued");
    expect(commandBody.error.message).toContain("Немає ні живого залу");
  });

  it("queues a durable command when the object is down and does not queue a press", async () => {
    const env = createMemoryEnv();
    const res = await handleAdminRequest(
      admin("/admin/devices/hall/command", "POST", { type: "select_preset", payload: { preset_id: "A" } }),
      env,
    );
    const body = (await res.json()) as { status: string };
    expect(body.status).toBe("queued");
    const press = await handleAdminRequest(
      admin("/admin/devices/hall/command", "POST", { type: "strobe_press", payload: {} }),
      env,
    );
    expect(press.status).toBe(409);
    expect(((await press.json()) as { status: string }).status).toBe("failed");
  });
});
