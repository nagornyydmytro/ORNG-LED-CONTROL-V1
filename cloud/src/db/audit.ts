import type { Env } from "../env";
import { nowIso, uuid } from "../env";

export async function writeAudit(
  env: Env,
  actor: string,
  action: string,
  target: string | null,
  detail: Record<string, unknown> = {},
): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO audit_events (id, actor, action, target, detail_json, created_at)
     VALUES (?, ?, ?, ?, ?, ?)`,
  )
    .bind(uuid(), actor, action, target, JSON.stringify(detail), nowIso())
    .run();
}

export async function listAudit(
  env: Env,
  opts?: { limit?: number; target?: string | null },
): Promise<
  Array<{
    id: string;
    actor: string;
    action: string;
    target: string | null;
    detail: unknown;
    created_at: string;
  }>
> {
  const limit = Math.min(Math.max(opts?.limit ?? 50, 1), 200);
  const target = opts?.target?.trim() || null;
  const rows = target
    ? await env.DB.prepare(
        `SELECT id, actor, action, target, detail_json, created_at
         FROM audit_events WHERE target = ? ORDER BY created_at DESC LIMIT ?`,
      )
        .bind(target, limit)
        .all<{
          id: string;
          actor: string;
          action: string;
          target: string | null;
          detail_json: string;
          created_at: string;
        }>()
    : await env.DB.prepare(
        `SELECT id, actor, action, target, detail_json, created_at
         FROM audit_events ORDER BY created_at DESC LIMIT ?`,
      )
        .bind(limit)
        .all<{
          id: string;
          actor: string;
          action: string;
          target: string | null;
          detail_json: string;
          created_at: string;
        }>();

  return (rows.results ?? []).map((r) => ({
    id: r.id,
    actor: r.actor,
    action: r.action,
    target: r.target,
    detail: safeJson(r.detail_json),
    created_at: r.created_at,
  }));
}

function safeJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}
