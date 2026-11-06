/** WebSocket helpers for workerd / vitest-pool-workers tests. */

export type JsonMsg = Record<string, unknown>;

export function parseWsData(data: string | ArrayBuffer): JsonMsg {
  const text = typeof data === "string" ? data : new TextDecoder().decode(data);
  return JSON.parse(text) as JsonMsg;
}

export function waitForWsMessage(
  ws: WebSocket,
  predicate: (msg: JsonMsg) => boolean,
  timeoutMs = 8_000,
  already?: JsonMsg[],
): Promise<JsonMsg> {
  const prior = already?.find(predicate);
  if (prior) return Promise.resolve(prior);

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`WebSocket message timeout after ${timeoutMs}ms`));
    }, timeoutMs);

    const onMessage = (event: MessageEvent) => {
      try {
        const msg = parseWsData(event.data as string | ArrayBuffer);
        if (predicate(msg)) {
          cleanup();
          resolve(msg);
        }
      } catch {
        /* ignore non-JSON */
      }
    };

    const onError = () => {
      cleanup();
      reject(new Error("WebSocket error while waiting for message"));
    };

    const cleanup = () => {
      clearTimeout(timer);
      ws.removeEventListener("message", onMessage);
      ws.removeEventListener("error", onError);
    };

    ws.addEventListener("message", onMessage);
    ws.addEventListener("error", onError);
  });
}

/** Wait until a collected inbox contains a matching message (avoids race with HTTP-triggered pushes). */
export async function waitForCollected(
  inbox: { messages: JsonMsg[] },
  predicate: (msg: JsonMsg) => boolean,
  timeoutMs = 10_000,
): Promise<JsonMsg> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const hit = inbox.messages.find(predicate);
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, 25));
  }
  throw new Error(
    `Collected WebSocket message timeout after ${timeoutMs}ms (seen=${inbox.messages.map((m) => m.type).join(",")})`,
  );
}

export function collectWsMessages(ws: WebSocket): {
  messages: JsonMsg[];
  stop: () => void;
} {
  const messages: JsonMsg[] = [];
  const onMessage = (event: MessageEvent) => {
    try {
      messages.push(parseWsData(event.data as string | ArrayBuffer));
    } catch {
      /* ignore */
    }
  };
  ws.addEventListener("message", onMessage);
  return {
    messages,
    stop: () => ws.removeEventListener("message", onMessage),
  };
}

export async function openWorkerWebSocket(
  fetchFn: typeof fetch,
  url: string,
  headers: HeadersInit,
): Promise<{ response: Response; ws: WebSocket }> {
  const response = await fetchFn(url, {
    headers: {
      Upgrade: "websocket",
      Connection: "Upgrade",
      ...Object.fromEntries(new Headers(headers).entries()),
    },
  });
  if (response.status !== 101 || !response.webSocket) {
    const body = await response.text().catch(() => "");
    throw new Error(`Expected 101 WebSocket upgrade, got ${response.status}: ${body}`);
  }
  response.webSocket.accept();
  return { response, ws: response.webSocket };
}
