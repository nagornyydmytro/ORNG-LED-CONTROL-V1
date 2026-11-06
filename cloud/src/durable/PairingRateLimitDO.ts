/**
 * Per-IP pairing rate limit Durable Object.
 * Survives isolate restarts via DO storage. Challenge changes do not bypass IP limits.
 */

interface BucketState {
  /** Timestamps (ms) of recent pairing start attempts. */
  attempts: number[];
  /** Active (non-expired) pairing session ids from this IP. */
  activeSessions: Array<{ id: string; expiresAt: number }>;
}

const RATE_WINDOW_MS = 60_000;
const RATE_MAX = 8;
/** Soft cap on concurrent open sessions per IP (above RATE_MAX so attempt window is primary). */
const MAX_ACTIVE_SESSIONS = 24;

export class PairingRateLimitDO implements DurableObject {
  private state: DurableObjectState;

  constructor(state: DurableObjectState, _env: unknown) {
    this.state = state;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/check") {
      const body = (await request.json()) as {
        pairing_id?: string;
        expires_at?: string;
        register?: boolean;
      };
      return this.checkAndMaybeRegister(body);
    }
    if (request.method === "POST" && url.pathname === "/release") {
      const body = (await request.json()) as { pairing_id?: string };
      return this.release(body.pairing_id);
    }
    return new Response("not found", { status: 404 });
  }

  private async load(): Promise<BucketState> {
    return (
      (await this.state.storage.get<BucketState>("bucket")) ?? {
        attempts: [],
        activeSessions: [],
      }
    );
  }

  private async save(bucket: BucketState): Promise<void> {
    await this.state.storage.put("bucket", bucket);
  }

  private async checkAndMaybeRegister(body: {
    pairing_id?: string;
    expires_at?: string;
    register?: boolean;
  }): Promise<Response> {
    const now = Date.now();
    const bucket = await this.load();

    // Prune window + expired sessions
    bucket.attempts = bucket.attempts.filter((t) => now - t < RATE_WINDOW_MS);
    bucket.activeSessions = bucket.activeSessions.filter((s) => s.expiresAt > now);

    if (bucket.attempts.length >= RATE_MAX) {
      await this.save(bucket);
      return Response.json(
        { allowed: false, reason: "rate_limited", retry_after_ms: RATE_WINDOW_MS },
        { status: 429 },
      );
    }

    if (bucket.activeSessions.length >= MAX_ACTIVE_SESSIONS) {
      await this.save(bucket);
      return Response.json(
        { allowed: false, reason: "too_many_sessions", active: bucket.activeSessions.length },
        { status: 429 },
      );
    }

    bucket.attempts.push(now);

    if (body.register && body.pairing_id && body.expires_at) {
      bucket.activeSessions.push({
        id: body.pairing_id,
        expiresAt: Date.parse(body.expires_at),
      });
    }

    await this.save(bucket);
    return Response.json({
      allowed: true,
      attempts: bucket.attempts.length,
      active_sessions: bucket.activeSessions.length,
    });
  }

  private async release(pairingId: string | undefined): Promise<Response> {
    if (!pairingId) return Response.json({ ok: false }, { status: 400 });
    const bucket = await this.load();
    bucket.activeSessions = bucket.activeSessions.filter((s) => s.id !== pairingId);
    await this.save(bucket);
    return Response.json({ ok: true });
  }
}

export const PAIRING_RATE_WINDOW_MS = RATE_WINDOW_MS;
export const PAIRING_RATE_MAX = RATE_MAX;
export const PAIRING_MAX_ACTIVE_SESSIONS = MAX_ACTIVE_SESSIONS;
