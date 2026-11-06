/**
 * DeviceDO hold persistence + lease expiry under workerd (WebSocketPair available).
 */

import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";
import { DeviceDO } from "../../src/durable/DeviceDO";
import { HOLD_LEASE_MS } from "../../src/protocol/types";

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

describe("DeviceDO holds hibernation + lease expiry (workerd)", () => {
  it("restores holds from storage after DO recreation and schedules alarm", async () => {
    const state = new MemoryDOState();
    const leaseUntil = Date.now() + HOLD_LEASE_MS * 2;
    await state.storage.put("meta", {
      pending: [],
      holds: [
        {
          hold_id: "h1",
          source: "cloud:web:t1",
          effect: "sweep",
          lease_until: leaseUntil,
          adminSessionId: "s1",
        },
      ],
      installationId: "inst-hold",
    });

    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    await new Promise((r) => setTimeout(r, 10));

    const status = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: Array<{ hold_id: string; effect: string }>;
    };
    expect(status.active_holds).toHaveLength(1);
    expect(status.active_holds[0]!.effect).toBe("sweep");
    expect(await state.storage.getAlarm()).toBe(leaseUntil);
  });

  it("restores two admin holds that share hold_id", async () => {
    const state = new MemoryDOState();
    const leaseUntil = Date.now() + HOLD_LEASE_MS * 2;
    await state.storage.put("meta", {
      pending: [],
      holds: [
        {
          hold_id: "shared",
          source: "cloud:web:a",
          effect: "strobe",
          lease_until: leaseUntil,
          adminSessionId: "sess-a",
        },
        {
          hold_id: "shared",
          source: "cloud:web:b",
          effect: "strobe",
          lease_until: leaseUntil,
          adminSessionId: "sess-b",
        },
      ],
      installationId: "inst-shared-hold",
    });

    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    await new Promise((r) => setTimeout(r, 10));

    const status = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: Array<{ hold_id: string; source: string }>;
    };
    expect(status.active_holds).toHaveLength(2);
    expect(status.active_holds.filter((h) => h.hold_id === "shared")).toHaveLength(2);
    expect(status.active_holds.map((h) => h.source).sort()).toEqual([
      "cloud:web:a",
      "cloud:web:b",
    ]);
  });

  it("lease expiry clears hold and logs effect-specific release (not release_source)", async () => {
    const installationId = `inst-lease-${crypto.randomUUID().slice(0, 8)}`;
    await env.DB.prepare(
      `INSERT INTO installations (id, name, platform, capabilities_json, armed, blackout, created_at)
       VALUES (?, 'Lease Test', 'development', '{}', 0, 1, ?)`,
    )
      .bind(installationId, new Date().toISOString())
      .run();

    const state = new MemoryDOState();
    await state.storage.put("meta", {
      pending: [],
      holds: [
        {
          hold_id: "v-expired",
          source: "cloud:web:t1",
          effect: "vertical_sweep",
          lease_until: Date.now() - 1,
          adminSessionId: "s1",
        },
      ],
      installationId,
    });

    const dob = new DeviceDO(state as unknown as DurableObjectState, env);
    await new Promise((r) => setTimeout(r, 10));

    // No device socket: release is attempted as vertical_sweep_release and fails offline.
    await dob.alarm();
    await new Promise((r) => setTimeout(r, 20));

    const status = (await (await dob.fetch(new Request("https://device-do/status"))).json()) as {
      active_holds: unknown[];
    };
    expect(status.active_holds).toHaveLength(0);

    const row = await env.DB.prepare(
      `SELECT type, status, error_code FROM command_log
       WHERE installation_id = ? AND type = ? ORDER BY created_at DESC LIMIT 1`,
    )
      .bind(installationId, "vertical_sweep_release")
      .first<{ type: string; status: string; error_code: string | null }>();
    expect(row?.type).toBe("vertical_sweep_release");
    expect(row?.status).toBe("failed");
    expect(row?.error_code).toBe("device_offline");

    const releaseSource = await env.DB.prepare(
      `SELECT COUNT(*) AS n FROM command_log WHERE installation_id = ? AND type = 'release_source'`,
    )
      .bind(installationId)
      .first<{ n: number }>();
    expect(Number(releaseSource?.n ?? 0)).toBe(0);
  });
});

