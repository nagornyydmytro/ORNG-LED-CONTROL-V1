/**
 * One accelerated day through the real handlers.
 * The clock is fake. Nothing here is a Cloudflare production day.
 */
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { handleAdminRequest } from "../src/admin/routes";
import { DeviceDO } from "../src/durable/DeviceDO";
import { takeFreshQueuedCommands } from "../src/db/deskLink";
import { getCommand, logCommand } from "../src/db/commands";
import type { Env } from "../src/env";
import { createMemoryEnv, seedCredential } from "./helpers/memoryEnv";

class MemoryStorage {
  private data = new Map<string, unknown>();
  puts = 0;
  gets = 0;
  alarms: number[] = [];
  deletedAlarms = 0;
  async get<T>(key: string): Promise<T | undefined> {
    this.gets += 1;
    return this.data.get(key) as T | undefined;
  }
  async put(key: string, value: unknown): Promise<void> {
    this.puts += 1;
    this.data.set(key, value);
  }
  async delete(): Promise<boolean> {
    return true;
  }
  async setAlarm(at: number): Promise<void> {
    this.alarms.push(at);
  }
  async deleteAlarm(): Promise<void> {
    this.deletedAlarms += 1;
  }
  async getAlarm(): Promise<number | null> {
    return this.alarms.at(-1) ?? null;
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

const START = Date.parse("2026-11-10T18:00:00.000Z");
const DAY = 86_400_000;
const MIDNIGHT = Date.parse("2026-11-11T00:00:00.000Z");

function countPrepares(env: ReturnType<typeof createMemoryEnv>) {
  const counts = { prepare: 0, run: 0, read: 0 };
  const original = env.DB.prepare.bind(env.DB);
  const watch = (stmt: ReturnType<typeof original>) => {
    const run = stmt.run.bind(stmt);
    const all = stmt.all?.bind(stmt);
    const first = stmt.first?.bind(stmt);
    const bind = stmt.bind.bind(stmt);
    stmt.run = (async (...args: unknown[]) => {
      counts.run += 1;
      return run(...args);
    }) as typeof stmt.run;
    if (all) {
      stmt.all = (async (...args: unknown[]) => {
        counts.read += 1;
        return all(...args);
      }) as typeof stmt.all;
    }
    if (first) {
      stmt.first = (async (...args: unknown[]) => {
        counts.read += 1;
        return first(...args);
      }) as typeof stmt.first;
    }
    stmt.bind = ((...args: unknown[]) => watch(bind(...args))) as typeof stmt.bind;
    return stmt;
  };
  env.DB.prepare = ((sql: string) => {
    counts.prepare += 1;
    return watch(original(sql));
  }) as typeof env.DB.prepare;
  return counts;
}

function adminLive(): Request {
  return new Request("http://127.0.0.1/admin/devices/hall/live", {
    headers: {
      host: "127.0.0.1",
      "content-type": "application/json",
      "x-dev-access-email": "desk@example.com",
    },
  });
}

describe("instrumented handlers across one fake day", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("counts storage calls through midnight without rearming an old update", async () => {
    const env = createMemoryEnv();
    await seedCredential(env, "hall", "tokentokentokentoken77");
    const state = new MemoryDOState();
    const born = START - 120_000;
    await state.storage.put("meta", {
      installationId: "hall",
      pending: [
        {
          command_id: "upd-apply",
          type: "apply_app_update",
          created_at: born,
          ttl_ms: 30_000,
          momentary_press: false,
          desired_sync: true,
          payload: { build_id: "build-keep" },
        },
      ],
    });
    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(state.storage.alarms.filter((at) => at <= born + 30_000)).toEqual([]);

    const db = countPrepares(env);
    const putsAtStart = state.storage.puts;
    vi.useFakeTimers({ now: START });
    const view = dob as unknown as {
      onDeviceMessage: (att: object, msg: object) => Promise<void>;
      pending: Map<string, { created_at: number; type: string }>;
    };
    const beat = {
      type: "heartbeat",
      payload: {
        armed: true,
        blackout: true,
        transport: "artnet",
        udp_active: true,
        arch: "aarch64",
        build_id: "945a5370a73e",
        app_version: "0.2.0",
        uptime_s: 1,
      },
    };
    let heartbeats = 0;
    let prepareBeforeMidnight = 0;
    let prepareAfterMidnight = 0;
    const hour = 3_600_000;
    for (let t = START; t < START + DAY; t += 45_000) {
      vi.setSystemTime(t);
      (beat.payload as { uptime_s: number }).uptime_s += 45;
      const before = db.prepare;
      await view.onDeviceMessage(
        { role: "device", installationId: "hall", sessionId: "device" },
        beat,
      );
      for (let i = 0; i < 6; i += 1) await Promise.resolve();
      heartbeats += 1;
      const used = db.prepare - before;
      if (t >= MIDNIGHT - hour && t < MIDNIGHT) prepareBeforeMidnight += used;
      if (t >= MIDNIGHT && t < MIDNIGHT + hour) prepareAfterMidnight += used;
    }
    expect(heartbeats).toBe(1920);
    expect(db.prepare).toBeGreaterThan(100);
    expect(db.prepare).toBeLessThan(heartbeats);
    expect(prepareAfterMidnight).toBeLessThan(prepareBeforeMidnight * 2 + 5);
    const telemetry = {
      prepares: db.prepare,
      reads: db.read,
      writes: db.run,
      storagePuts: state.storage.puts - putsAtStart,
    };
    expect(view.pending.has("upd-apply")).toBe(true);
    expect((await getCommand(env, "upd-apply"))?.error_code ?? null).not.toBe("ttl_expired");
    await dob.alarm();
    expect(view.pending.has("upd-apply")).toBe(true);

    view.pending.set("cmd-lost", {
      command_id: "cmd-lost",
      type: "blackout",
      created_at: START,
      ttl_ms: 30_000,
      payload: {},
      momentary_press: false,
      desired_sync: false,
    } as never);
    await logCommand(env, {
      command_id: "cmd-lost",
      installation_id: "hall",
      type: "blackout",
      payload: { enabled: true },
      status: "accepted",
    });
    const created = (await getCommand(env, "cmd-lost"))?.created_at;
    vi.setSystemTime(START + 30_000);
    await dob.alarm();
    const expired = await getCommand(env, "cmd-lost");
    expect(expired?.status).toBe("failed");
    expect(expired?.error_code).toBe("ttl_expired");
    expect(expired?.created_at).toBe(created);

    const sqlite = new DatabaseSync(":memory:");
    sqlite.exec(readFileSync(new URL("../migrations/0001_init.sql", import.meta.url), "utf8"));
    sqlite.prepare(
      `INSERT INTO installations (id, name, platform, created_at) VALUES ('hall', 'Hall', 'raspberry', ?)`,
    ).run("2026-01-01T00:00:00.000Z");
    const insert = sqlite.prepare(
      `INSERT INTO command_log (command_id, installation_id, type, payload_json, status, created_at)
       VALUES (?, 'hall', 'blackout', '{}', 'queued', ?)`,
    );
    for (let i = 0; i < 800; i += 1) {
      insert.run(`old-${i}`, new Date(START - DAY - i * 1000).toISOString());
    }
    insert.run("fresh", new Date(START + 1_000).toISOString());
    const bridge = {
      DB: {
        prepare(sql: string) {
          const stmt = sqlite.prepare(sql);
          let binds: unknown[] = [];
          return {
            bind(...args: unknown[]) {
              binds = args;
              return this;
            },
            async all() {
              db.read += 1;
              return { results: stmt.all(...(binds as never[])) };
            },
          };
        },
      },
    } as Env;
    const due = await takeFreshQueuedCommands(bridge, "hall", START + 2_000);
    expect(due.map((row) => row.command_id)).toEqual(["fresh"]);
    const total = sqlite.prepare(`SELECT COUNT(*) AS n FROM command_log`).get() as { n: number };
    expect(total.n).toBe(801);

    const holds = (dob as unknown as { holds: Map<string, { hold_id: string }> }).holds;
    holds.set("desk::h1", {
      hold_id: "h1",
      source: "cloud:web:a",
      effect: "strobe",
      lease_until: START + 60_000,
      adminSessionId: "desk",
    } as never);
    vi.setSystemTime(START + 60_000);
    await dob.alarm();
    expect(holds.size).toBe(0);

    const failures = { handlers: 0, stored: 0, durable: 0, bothDown: 0 };
    const storedEnv = createMemoryEnv();
    const storedAt = new Date(START).toISOString();
    storedEnv.__db.tables.installations.push({
      id: "hall",
      name: "Hall",
      platform: "raspberry",
      capabilities_json: JSON.stringify({
        desk: { session: "show", sequence: 4, age_ms: 400, updated_at: storedAt, look: "bars" },
      }),
      armed: 1,
      blackout: 0,
      last_seen_at: storedAt,
      created_at: storedAt,
      revoked_at: null,
      udp_active: 0,
    });
    storedEnv.DEVICE_DO = {
      idFromName: (name: string) => ({ name, toString: () => name }) as DurableObjectId,
      get: () =>
        ({
          fetch: async () => {
            throw new Error("Exceeded allowed volume of requests in Durable Objects free tier.");
          },
        }) as unknown as DurableObjectStub,
    } as unknown as DurableObjectNamespace;
    failures.handlers += 1;
    const stored = await handleAdminRequest(adminLive(), storedEnv);
    failures.stored = stored.status;

    const durableEnv = createMemoryEnv();
    durableEnv.DB.prepare = (() => {
      throw new Error("exceeded D1 daily row read limit");
    }) as typeof durableEnv.DB.prepare;
    durableEnv.DEVICE_DO = {
      idFromName: (name: string) => ({ name, toString: () => name }) as DurableObjectId,
      get: () =>
        ({
          fetch: async () =>
            Response.json({
              desk: { session: "show", sequence: 2, age_ms: 200, updated_at: storedAt, look: "wash" },
            }),
        }) as unknown as DurableObjectStub,
    } as unknown as DurableObjectNamespace;
    failures.handlers += 1;
    const durable = await handleAdminRequest(adminLive(), durableEnv);
    failures.durable = durable.status;

    const downEnv = createMemoryEnv();
    downEnv.DB.prepare = (() => {
      throw new Error("exceeded D1 daily row read limit");
    }) as typeof downEnv.DB.prepare;
    downEnv.DEVICE_DO = storedEnv.DEVICE_DO;
    failures.handlers += 1;
    const down = await handleAdminRequest(adminLive(), downEnv);
    failures.bothDown = down.status;
    expect(failures).toEqual({ handlers: 3, stored: 200, durable: 200, bothDown: 503 });

    const summary = {
      heartbeats,
      prepares: telemetry.prepares,
      reads: telemetry.reads,
      writes: telemetry.writes,
      storagePuts: telemetry.storagePuts,
      storageGets: state.storage.gets,
      alarmsArmed: state.storage.alarms.length,
      prepareHourBeforeMidnight: prepareBeforeMidnight,
      prepareHourAfterMidnight: prepareAfterMidnight,
      historyRows: total.n,
      historyReturned: due.length,
      laterWrites: db.run - telemetry.writes,
      failures,
    };
    expect(summary.storagePuts).toBeGreaterThan(0);
    expect(summary).toMatchObject({ heartbeats: 1920, historyReturned: 1 });
    console.info(`instrumented-day ${JSON.stringify(summary)}`);
  }, 60_000);
});
