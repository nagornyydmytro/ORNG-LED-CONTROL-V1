import { ackCommand } from "./commands";
import type { Env } from "../env";
import { nowIso } from "../env";
import { DEFAULT_COMMAND_TTL_MS, isEphemeralCommand } from "../protocol/types";

/** Oldest created_at the relay reads. The index range, not the table. */
export const QUEUE_LOOKBACK_MS = 120_000;

export type DeskSnapshot = {
  preset_id?: string | null;
  look?: string | null;
  program?: string | null;
  bpm?: unknown;
  face_on?: boolean | null;
  strobe_held?: boolean | null;
  sweep_active?: boolean | null;
  vertical_sweep_active?: boolean | null;
  preset_time_s?: number | null;
  episode_index?: number | null;
  episode_count?: number | null;
  scene_name?: string | null;
  pad_presets?: unknown;
  capabilities?: string[] | null;
  armed?: boolean | null;
  blackout?: boolean | null;
  transport?: string | null;
  current_scene_id?: string | null;
  session?: string | null;
  sequence?: number | null;
  age_ms?: number | null;
  updated_at?: string;
};

export function fresherDesk(stored: DeskSnapshot | null, mailbox: DeskSnapshot | null): DeskSnapshot | null {
  const storedAt = stored?.updated_at ? Date.parse(stored.updated_at) : NaN;
  const mailAt = mailbox?.updated_at ? Date.parse(mailbox.updated_at) : NaN;
  if (Number.isFinite(mailAt) && (!Number.isFinite(storedAt) || mailAt >= storedAt)) return mailbox;
  return stored ?? mailbox;
}

export function readDeskSnapshot(capabilitiesJson: string | null | undefined): DeskSnapshot | null {
  if (!capabilitiesJson) return null;
  try {
    const caps = JSON.parse(capabilitiesJson) as { desk?: DeskSnapshot };
    return caps.desk && typeof caps.desk === "object" ? caps.desk : null;
  } catch {
    return null;
  }
}

export function stampDeskSnapshot(
  previous: DeskSnapshot | null,
  next: DeskSnapshot,
  now = Date.now(),
): DeskSnapshot {
  const sameFrame =
    Boolean(previous?.session) &&
    previous?.session === next.session &&
    previous?.sequence === next.sequence &&
    typeof previous?.age_ms === "number" &&
    Boolean(previous?.updated_at);
  if (sameFrame && previous) {
    return { ...next, age_ms: previous.age_ms, updated_at: previous.updated_at };
  }
  return { ...next, updated_at: new Date(now).toISOString() };
}

/** Age at read time. A stored snapshot keeps aging; a reread does not make it young. */
export function replayAgeMs(desk: DeskSnapshot, now = Date.now()): number | null {
  if (typeof desk.age_ms !== "number" || !Number.isFinite(desk.age_ms)) return null;
  const stored = desk.updated_at ? Date.parse(desk.updated_at) : NaN;
  const elapsed = Number.isFinite(stored) ? Math.max(0, now - stored) : 0;
  return Math.max(0, desk.age_ms) + elapsed;
}

export async function saveDeskSnapshot(
  env: Env,
  installationId: string,
  snapshot: DeskSnapshot,
): Promise<void> {
  const row = await env.DB.prepare(`SELECT capabilities_json FROM installations WHERE id = ?`)
    .bind(installationId)
    .first<{ capabilities_json: string }>();
  let caps: Record<string, unknown> = {};
  try {
    caps = JSON.parse(row?.capabilities_json ?? "{}") as Record<string, unknown>;
  } catch {
    caps = {};
  }
  const previous = caps.desk && typeof caps.desk === "object" ? (caps.desk as DeskSnapshot) : null;
  caps.desk = stampDeskSnapshot(previous, snapshot);
  await env.DB.prepare(`UPDATE installations SET capabilities_json = ? WHERE id = ?`)
    .bind(JSON.stringify(caps), installationId)
    .run();
}

export function commandDue(createdAt: string, ttlMs: number, now = Date.now()): boolean {
  const start = Date.parse(createdAt);
  if (!Number.isFinite(start)) return false;
  const ttl = Number.isFinite(ttlMs) && ttlMs > 0 ? ttlMs : DEFAULT_COMMAND_TTL_MS;
  return now < start + ttl;
}

export async function pollQueuedCommands(
  read: () => Promise<Array<{ command_id: string; type: string; payload: Record<string, unknown> }>>,
  waitMs: number,
  stepMs = 250,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  now: () => number = Date.now,
): Promise<Array<{ command_id: string; type: string; payload: Record<string, unknown> }>> {
  const deadline = now() + Math.max(0, waitMs);
  for (;;) {
    const commands = await read();
    if (commands.length > 0 || now() + stepMs > deadline) return commands;
    await sleep(stepMs);
  }
}

export async function takeFreshQueuedCommands(
  env: Env,
  installationId: string,
  now = Date.now(),
): Promise<Array<{ command_id: string; type: string; payload: Record<string, unknown> }>> {
  const since = new Date(now - QUEUE_LOOKBACK_MS).toISOString();
  const res = await env.DB.prepare(
    `SELECT command_id, type, payload_json, created_at FROM command_log
     WHERE installation_id = ? AND status = 'queued' AND created_at >= ?
     ORDER BY created_at ASC
     LIMIT 20`,
  )
    .bind(installationId, since)
    .all<{ command_id: string; type: string; payload_json: string; created_at: string }>();
  const due: Array<{ command_id: string; type: string; payload: Record<string, unknown> }> = [];
  for (const row of res.results ?? []) {
    if (isEphemeralCommand(row.type)) continue;
    let payload: Record<string, unknown> = {};
    try {
      payload = JSON.parse(row.payload_json) as Record<string, unknown>;
    } catch {
      payload = {};
    }
    const ttl = typeof payload.ttl_ms === "number" ? payload.ttl_ms : DEFAULT_COMMAND_TTL_MS;
    if (!commandDue(row.created_at, ttl, now)) continue;
    payload.command_id = row.command_id;
    due.push({ command_id: row.command_id, type: row.type, payload });
  }
  return due;
}

export async function takeQueuedCommands(
  env: Env,
  installationId: string,
): Promise<Array<{ command_id: string; type: string; payload: Record<string, unknown> }>> {
  const since = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString();
  const res = await env.DB.prepare(
    `SELECT command_id, type, payload_json FROM command_log
     WHERE installation_id = ? AND status = 'queued' AND created_at >= ?
     ORDER BY created_at ASC
     LIMIT 20`,
  )
    .bind(installationId, since)
    .all<{ command_id: string; type: string; payload_json: string }>();
  return (res.results ?? []).map((row) => {
    let payload: Record<string, unknown> = {};
    try {
      payload = JSON.parse(row.payload_json) as Record<string, unknown>;
    } catch {
      payload = {};
    }
    payload.command_id = row.command_id;
    return { command_id: row.command_id, type: row.type, payload };
  });
}

export async function ackDeskCommands(
  env: Env,
  installationId: string,
  acks: Array<{ command_id?: string; status?: string; error_code?: string | null; error_message?: string | null }>,
): Promise<void> {
  for (const ack of acks) {
    if (!ack.command_id || !ack.status) continue;
    await ackCommand(env, ack.command_id, ack.status, ack.error_code, ack.error_message, {
      installationId,
    });
  }
}
