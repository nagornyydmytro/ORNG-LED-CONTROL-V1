/**
 * Real DeviceDO (workerd): a desk socket is not an admin service channel.
 * restart/update stay off this source. Two sockets keep two sources.
 */

import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

async function connect(
  installationId: string,
  source: string,
): Promise<{ ws: WebSocket; inbox: Record<string, unknown>[] }> {
  const stub = env.DEVICE_DO.get(env.DEVICE_DO.idFromName(installationId));
  const response = await stub.fetch(
    new Request("https://device-do/connect/admin", {
      headers: {
        Upgrade: "websocket",
        Connection: "Upgrade",
        "x-installation-id": installationId,
        "x-admin-source": source,
      },
    }),
  );
  if (response.status !== 101 || !response.webSocket) {
    throw new Error(`expected 101, got ${response.status}`);
  }
  const ws = response.webSocket;
  const inbox: Record<string, unknown>[] = [];
  ws.addEventListener("message", (event) => {
    inbox.push(JSON.parse(String(event.data)) as Record<string, unknown>);
  });
  ws.accept();
  return { ws, inbox };
}

async function waitFor(
  inbox: Record<string, unknown>[],
  type: string,
): Promise<Record<string, unknown>> {
  const started = Date.now();
  while (Date.now() - started < 3000) {
    const found = inbox.find((msg) => msg.type === type);
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timeout waiting for ${type}`);
}

describe("desk control session on DeviceDO", () => {
  it("two desk sockets stay distinct and cannot restart the executor", async () => {
    const installationId = `desk-${crypto.randomUUID().slice(0, 8)}`;
    const first = await connect(installationId, "cloud:desk:window-a");
    const second = await connect(installationId, "cloud:desk:window-b");
    const [a, b] = await Promise.all([
      waitFor(first.inbox, "admin_hello"),
      waitFor(second.inbox, "admin_hello"),
    ]);
    expect((a.payload as { source?: string }).source).toBe("cloud:desk:window-a");
    expect((b.payload as { source?: string }).source).toBe("cloud:desk:window-b");

    const denied = waitFor(first.inbox, "error");
    first.ws.send(
      JSON.stringify({ type: "restart_runtime", payload: { command_id: "restart-1" } }),
    );
    const err = await denied;
    const payload = err.payload as { code?: string; command_id?: string };
    expect(payload.code).toBe("desk_forbidden");
    expect(payload.command_id).toBe("restart-1");
  });
});
