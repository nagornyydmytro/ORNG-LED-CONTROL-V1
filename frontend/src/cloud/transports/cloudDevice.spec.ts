import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { adminDeviceCommand, adminDeviceLive } from "../../api/adminCloud";
import { createCloudDeviceTransport } from "./cloudDevice";
import type { CloudDeviceLiveState } from "../transport";

vi.mock("../../api/adminCloud", () => ({
  adminDeviceLive: vi.fn(async () => ({ online: true, blackout: true, armed: false })),
  adminDeviceCommand: vi.fn(async () => ({ ok: true, status: "queued" })),
  adminWsUrl: () => "ws://127.0.0.1/admin",
}));

vi.mock("../../runtime/calibrationChannel", () => ({
  bindCalibrationChannel: vi.fn(),
  clearCalibrationChannel: vi.fn(),
}));

vi.mock("../sessionSourceId", () => ({
  getCloudWebSessionSourceId: () => "cloud:web:test",
}));

vi.mock("../mode", () => ({
  defaultAccessEmail: () => "desk@example.com",
  defaultAdminBase: () => "http://127.0.0.1",
}));

class FakeSocket {
  static instances: FakeSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = 0;
  url: string;
  private listeners = new Map<string, Set<(ev: unknown) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  addEventListener(type: string, fn: (ev: unknown) => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(fn);
    this.listeners.set(type, set);
  }

  removeEventListener(): void {}

  send(): void {}

  close(): void {
    if (this.readyState === FakeSocket.CLOSED) return;
    this.readyState = FakeSocket.CLOSED;
    this.emit("close");
  }

  open(): void {
    this.readyState = FakeSocket.OPEN;
    this.emit("open");
  }

  push(data: unknown): void {
    this.emit("message", { data: JSON.stringify(data) });
  }

  private emit(type: string, ev?: unknown): void {
    for (const fn of this.listeners.get(type) ?? []) fn(ev);
  }
}

const live = vi.mocked(adminDeviceLive);
const command = vi.mocked(adminDeviceCommand);

async function boot(online: boolean) {
  const transport = createCloudDeviceTransport({ installationId: "hall" });
  const states: CloudDeviceLiveState[] = [];
  const acks: Array<{ status: string }> = [];
  transport.onLiveState((state) => {
    states.push({ ...state });
  });
  transport.onCommandAck((ack) => {
    acks.push({ status: ack.status });
  });
  const pending = transport.connect();
  await Promise.resolve();
  await Promise.resolve();
  const sock = FakeSocket.instances.at(-1);
  if (!sock) throw new Error("socket was not opened");
  sock.open();
  await vi.advanceTimersByTimeAsync(2_600);
  await pending;
  sock.push({ type: "admin_hello", payload: { online, device_online: online } });
  await Promise.resolve();
  return { transport, states, acks, sock };
}

describe("cloud desk recovery", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    live.mockClear();
    command.mockClear();
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps polling when the desk opens onto an offline device, past the socket retry", async () => {
    const { states } = await boot(false);
    const before = live.mock.calls.length;
    expect(states.at(-1)?.online).toBe(false);
    expect(states.at(-1)?.control_status).toBe("open");
    await vi.advanceTimersByTimeAsync(95_000);
    expect(live.mock.calls.length).toBeGreaterThan(before + 3);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(states.at(-1)?.online).toBe(false);
    expect(states.at(-1)?.control_status).toBe("open");
  });

  it("keeps polling across three 90-second retry windows", async () => {
    const { states } = await boot(false);
    const added: number[] = [];
    for (let cycle = 0; cycle < 3; cycle += 1) {
      const before = live.mock.calls.length;
      await vi.advanceTimersByTimeAsync(90_000);
      added.push(live.mock.calls.length - before);
    }
    expect(added.every((count) => count >= 3)).toBe(true);
    expect(FakeSocket.instances.filter((item) => item.readyState === FakeSocket.OPEN)).toHaveLength(1);
    expect(states.at(-1)?.online).toBe(false);
  });

  it("keeps the fallback when only the device socket drops", async () => {
    const { sock } = await boot(true);
    const settled = live.mock.calls.length;
    await vi.advanceTimersByTimeAsync(25_000);
    expect(live.mock.calls.length).toBe(settled);
    sock.push({ type: "device_offline" });
    const dropped = live.mock.calls.length;
    await vi.advanceTimersByTimeAsync(95_000);
    expect(live.mock.calls.length).toBeGreaterThan(dropped + 3);
    expect(FakeSocket.instances.filter((item) => item.readyState === FakeSocket.OPEN)).toHaveLength(1);
  });

  it("reconnects the admin socket without starting the device poll", async () => {
    const { sock, states } = await boot(true);
    sock.push({ type: "device_heartbeat", payload: { online: true, armed: true, blackout: false } });
    const settled = live.mock.calls.length;
    sock.close();
    await vi.advanceTimersByTimeAsync(1_000);
    const next = FakeSocket.instances.at(-1);
    next?.open();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(25_000);
    expect(live.mock.calls.length).toBe(settled);
    expect(states.at(-1)?.online).toBe(true);
    expect(states.at(-1)?.control_status).toBe("open");
  });

  it("stops the fallback only after the device channel returns", async () => {
    const { sock } = await boot(false);
    await vi.advanceTimersByTimeAsync(20_000);
    const polling = live.mock.calls.length;
    expect(polling).toBeGreaterThan(1);
    sock.push({
      type: "device_preview",
      payload: { session: "show", sequence: 1, age_ms: 100 },
    });
    await Promise.resolve();
    const restored = live.mock.calls.length;
    await vi.advanceTimersByTimeAsync(95_000);
    expect(live.mock.calls.length).toBe(restored);
    sock.push({ type: "device_offline" });
    sock.push({ type: "device_heartbeat", payload: { online: true, armed: true, blackout: false } });
    const again = live.mock.calls.length;
    await vi.advanceTimersByTimeAsync(40_000);
    expect(live.mock.calls.length).toBe(again);
  });

  it("does not add a second poll when the offline event repeats", async () => {
    const { sock } = await boot(true);
    sock.push({ type: "device_offline" });
    sock.push({ type: "device_offline" });
    const started = live.mock.calls.length;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(live.mock.calls.length - started).toBeLessThanOrEqual(2);
  });

  it("drops the poll and the retry when the desk is closed", async () => {
    const { transport } = await boot(false);
    const calls = live.mock.calls.length;
    const sockets = FakeSocket.instances.length;
    transport.dispose();
    await vi.advanceTimersByTimeAsync(100_000);
    expect(live.mock.calls.length).toBe(calls);
    expect(FakeSocket.instances.length).toBe(sockets);
  });

  it("does not treat a queued command as applied", async () => {
    const { transport, acks } = await boot(true);
    let settled = "";
    const pending = transport.blackout(true).then(
      () => {
        settled = "applied";
      },
      () => {
        settled = "timeout";
      },
    );
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(11_000);
    expect(settled).toBe("");
    expect(acks.map((ack) => ack.status)).toContain("queued");
    expect(acks.map((ack) => ack.status)).not.toContain("applied");
    await vi.advanceTimersByTimeAsync(1_500);
    await pending;
    expect(settled).toBe("timeout");
  });
});
