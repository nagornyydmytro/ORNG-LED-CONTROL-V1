/**
 * Per-IP admin login rate limit + temporary lockout after repeated failures.
 * State lives only in Events (this DO). Gateway stays stateless.
 */

interface GuardState {
  /** Failure timestamps (ms) inside the window. */
  failures: number[];
  /** Epoch ms until which logins are rejected. */
  lockedUntil: number;
}

const FAIL_WINDOW_MS = 15 * 60_000;
const MAX_FAILURES = 5;
const LOCKOUT_MS = 15 * 60_000;

export class AdminLoginGuardDO implements DurableObject {
  private state: DurableObjectState;

  constructor(state: DurableObjectState, _env: unknown) {
    this.state = state;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/check") {
      return this.check();
    }
    if (request.method === "POST" && url.pathname === "/failure") {
      return this.recordFailure();
    }
    if (request.method === "POST" && url.pathname === "/success") {
      return this.clear();
    }
    return new Response("not found", { status: 404 });
  }

  private async load(): Promise<GuardState> {
    return (
      (await this.state.storage.get<GuardState>("guard")) ?? {
        failures: [],
        lockedUntil: 0,
      }
    );
  }

  private async save(guard: GuardState): Promise<void> {
    await this.state.storage.put("guard", guard);
  }

  private prune(guard: GuardState, now: number): void {
    guard.failures = guard.failures.filter((t) => now - t < FAIL_WINDOW_MS);
    if (guard.lockedUntil && guard.lockedUntil <= now) {
      guard.lockedUntil = 0;
    }
  }

  private async check(): Promise<Response> {
    const now = Date.now();
    const guard = await this.load();
    this.prune(guard, now);
    await this.save(guard);
    if (guard.lockedUntil > now) {
      return Response.json(
        {
          allowed: false,
          reason: "locked",
          retry_after_ms: guard.lockedUntil - now,
        },
        { status: 429 },
      );
    }
    return Response.json({ allowed: true, failures: guard.failures.length });
  }

  private async recordFailure(): Promise<Response> {
    const now = Date.now();
    const guard = await this.load();
    this.prune(guard, now);
    guard.failures.push(now);
    if (guard.failures.length >= MAX_FAILURES) {
      guard.lockedUntil = now + LOCKOUT_MS;
      guard.failures = [];
    }
    await this.save(guard);
    return Response.json({
      allowed: guard.lockedUntil <= now,
      locked: guard.lockedUntil > now,
      failures: guard.failures.length,
      retry_after_ms: guard.lockedUntil > now ? guard.lockedUntil - now : 0,
    });
  }

  private async clear(): Promise<Response> {
    await this.save({ failures: [], lockedUntil: 0 });
    return Response.json({ ok: true });
  }
}

export const ADMIN_LOGIN_FAIL_WINDOW_MS = FAIL_WINDOW_MS;
export const ADMIN_LOGIN_MAX_FAILURES = MAX_FAILURES;
export const ADMIN_LOGIN_LOCKOUT_MS = LOCKOUT_MS;
