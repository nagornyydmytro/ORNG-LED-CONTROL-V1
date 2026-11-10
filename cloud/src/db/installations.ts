import type { Env } from "../env";
import { nowIso } from "../env";

export type Platform =
  | "raspberry"
  | "desktop_windows"
  | "desktop_macos"
  | "development";

export interface InstallationRow {
  id: string;
  name: string;
  platform: Platform;
  app_version: string | null;
  build_id: string | null;
  /** Persisted from heartbeat — used for app release ACL (never default arm64). */
  arch: string | null;
  install_mode: string | null;
  updater_protocol_version: number | null;
  capabilities_json: string;
  current_scene_id: string | null;
  current_release_version: number | null;
  current_content_hash: string | null;
  last_synced_hash: string | null;
  armed: number;
  blackout: number;
  transport: string | null;
  udp_active: number;
  last_error: string | null;
  uptime_s: number | null;
  last_seen_at: string | null;
  created_at: string;
  revoked_at: string | null;
}

export async function getInstallation(
  env: Env,
  id: string,
): Promise<InstallationRow | null> {
  return (
    (await env.DB.prepare(`SELECT * FROM installations WHERE id = ?`)
      .bind(id)
      .first<InstallationRow>()) ?? null
  );
}

export async function listInstallations(env: Env): Promise<InstallationRow[]> {
  const res = await env.DB.prepare(
    `SELECT * FROM installations ORDER BY created_at DESC`,
  ).all<InstallationRow>();
  return res.results ?? [];
}

export async function touchHeartbeat(
  env: Env,
  id: string,
  payload: {
    uptime_s?: number;
    armed?: boolean;
    blackout?: boolean;
    transport?: string;
    udp_active?: boolean;
    current_scene_id?: string;
    current_release_version?: number;
    current_content_hash?: string;
    last_synced_hash?: string;
    last_error?: string | null;
    app_version?: string;
    build_id?: string;
    arch?: string;
    install_mode?: string;
    updater_protocol_version?: number;
  },
): Promise<void> {
  // When arch is reported, also mirror into capabilities_json.arch for older readers.
  let capabilitiesPatch: string | null = null;
  if (payload.arch) {
    const row = await env.DB.prepare(`SELECT capabilities_json FROM installations WHERE id = ?`)
      .bind(id)
      .first<{ capabilities_json: string }>();
    let caps: Record<string, unknown> = {};
    try {
      caps = JSON.parse(row?.capabilities_json ?? "{}") as Record<string, unknown>;
    } catch {
      caps = {};
    }
    caps.arch = payload.arch;
    capabilitiesPatch = JSON.stringify(caps);
  }

  await env.DB.prepare(
    `UPDATE installations SET
       last_seen_at = ?,
       uptime_s = COALESCE(?, uptime_s),
       armed = COALESCE(?, armed),
       blackout = COALESCE(?, blackout),
       transport = COALESCE(?, transport),
       udp_active = COALESCE(?, udp_active),
       current_scene_id = COALESCE(?, current_scene_id),
       current_release_version = COALESCE(?, current_release_version),
       current_content_hash = COALESCE(?, current_content_hash),
       last_synced_hash = COALESCE(?, last_synced_hash),
       last_error = ?,
       app_version = COALESCE(?, app_version),
       build_id = COALESCE(?, build_id),
       arch = COALESCE(?, arch),
       install_mode = COALESCE(?, install_mode),
       updater_protocol_version = COALESCE(?, updater_protocol_version),
       capabilities_json = COALESCE(?, capabilities_json)
     WHERE id = ?`,
  )
    .bind(
      nowIso(),
      payload.uptime_s ?? null,
      payload.armed === undefined ? null : payload.armed ? 1 : 0,
      payload.blackout === undefined ? null : payload.blackout ? 1 : 0,
      payload.transport ?? null,
      payload.udp_active === undefined ? null : payload.udp_active ? 1 : 0,
      payload.current_scene_id ?? null,
      payload.current_release_version ?? null,
      payload.current_content_hash ?? null,
      payload.last_synced_hash ?? null,
      payload.last_error === undefined ? null : payload.last_error,
      payload.app_version ?? null,
      payload.build_id ?? null,
      payload.arch ?? null,
      payload.install_mode ?? null,
      payload.updater_protocol_version ?? null,
      capabilitiesPatch,
      id,
    )
    .run();
}

export function publicInstallation(
  row: InstallationRow,
  opts?: {
    online?: boolean;
    rotationPending?: boolean;
  },
) {
  return {
    id: row.id,
    name: row.name,
    platform: row.platform,
    app_version: row.app_version,
    build_id: row.build_id,
    arch: row.arch,
    install_mode: row.install_mode,
    updater_protocol_version: row.updater_protocol_version,
    capabilities: safeJson(row.capabilities_json),
    current_scene_id: row.current_scene_id,
    current_release_version: row.current_release_version,
    current_content_hash: row.current_content_hash,
    last_synced_hash: row.last_synced_hash ?? null,
    armed: Boolean(row.armed),
    blackout: Boolean(row.blackout),
    transport: row.transport,
    udp_active: Boolean(row.udp_active),
    last_error: row.last_error,
    uptime_s: row.uptime_s,
    last_seen_at: row.last_seen_at,
    /** Heartbeat recency — not a substitute for live DO session. */
    recently_seen: isRecentlySeen(row.last_seen_at),
    created_at: row.created_at,
    revoked: Boolean(row.revoked_at),
    revoked_at: row.revoked_at,
    /** Live online = active DeviceDO device WebSocket session. */
    online: opts?.online ?? false,
    rotation_pending: opts?.rotationPending ?? false,
  };
}

export function isRecentlySeen(lastSeen: string | null): boolean {
  if (!lastSeen) return false;
  return Date.now() - Date.parse(lastSeen) < 180_000;
}

function safeJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}
