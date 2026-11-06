import type { Env } from "../env";
import { nowIso } from "../env";

export async function logCommand(
  env: Env,
  row: {
    command_id: string;
    installation_id: string;
    type: string;
    payload: Record<string, unknown>;
    status: string;
    error_code?: string | null;
    error_message?: string | null;
  },
): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO command_log
      (command_id, installation_id, type, payload_json, status, error_code, error_message, created_at, acked_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
     ON CONFLICT(command_id) DO NOTHING`,
  )
    .bind(
      row.command_id,
      row.installation_id,
      row.type,
      JSON.stringify(row.payload),
      row.status,
      row.error_code ?? null,
      row.error_message ?? null,
      nowIso(),
    )
    .run();
}

export async function getCommand(
  env: Env,
  commandId: string,
): Promise<{
  command_id: string;
  installation_id: string;
  type: string;
  payload_json: string;
  status: string;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  acked_at: string | null;
} | null> {
  return (
    (await env.DB.prepare(`SELECT * FROM command_log WHERE command_id = ?`)
      .bind(commandId)
      .first()) ?? null
  );
}

/**
 * ACK a command. When `scope` is provided, the UPDATE is limited to that
 * installation (and optional type) so a device cannot ACK another install's command.
 */
export async function ackCommand(
  env: Env,
  commandId: string,
  status: string,
  errorCode?: string | null,
  errorMessage?: string | null,
  scope?: { installationId?: string; type?: string },
): Promise<void> {
  const ackedAt = nowIso();
  if (scope?.installationId && scope?.type) {
    await env.DB.prepare(
      `UPDATE command_log SET status = ?, error_code = ?, error_message = ?, acked_at = ?
       WHERE command_id = ? AND installation_id = ? AND type = ?`,
    )
      .bind(
        status,
        errorCode ?? null,
        errorMessage ?? null,
        ackedAt,
        commandId,
        scope.installationId,
        scope.type,
      )
      .run();
    return;
  }
  if (scope?.installationId) {
    await env.DB.prepare(
      `UPDATE command_log SET status = ?, error_code = ?, error_message = ?, acked_at = ?
       WHERE command_id = ? AND installation_id = ?`,
    )
      .bind(
        status,
        errorCode ?? null,
        errorMessage ?? null,
        ackedAt,
        commandId,
        scope.installationId,
      )
      .run();
    return;
  }
  await env.DB.prepare(
    `UPDATE command_log SET status = ?, error_code = ?, error_message = ?, acked_at = ?
     WHERE command_id = ?`,
  )
    .bind(status, errorCode ?? null, errorMessage ?? null, ackedAt, commandId)
    .run();
}

export async function listCommands(
  env: Env,
  installationId: string,
  opts?: { limit?: number; status?: string | null },
): Promise<
  Array<{
    command_id: string;
    installation_id: string;
    type: string;
    status: string;
    error_code: string | null;
    error_message: string | null;
    created_at: string;
    acked_at: string | null;
  }>
> {
  const limit = Math.min(Math.max(opts?.limit ?? 40, 1), 100);
  const status = opts?.status?.trim() || null;
  const rows = status
    ? await env.DB.prepare(
        `SELECT command_id, installation_id, type, status, error_code, error_message, created_at, acked_at
         FROM command_log
         WHERE installation_id = ? AND status = ?
         ORDER BY created_at DESC LIMIT ?`,
      )
        .bind(installationId, status, limit)
        .all()
    : await env.DB.prepare(
        `SELECT command_id, installation_id, type, status, error_code, error_message, created_at, acked_at
         FROM command_log
         WHERE installation_id = ?
         ORDER BY created_at DESC LIMIT ?`,
      )
        .bind(installationId, limit)
        .all();
  return (rows.results ?? []) as Array<{
    command_id: string;
    installation_id: string;
    type: string;
    status: string;
    error_code: string | null;
    error_message: string | null;
    created_at: string;
    acked_at: string | null;
  }>;
}
