/** Typed admin Worker REST client (cookie session + CSRF). Never expects plaintext device tokens. */

import { endAdminSession } from "../cloud/adminSession";
import { defaultAccessEmail, defaultAdminBase, isCloudAdminBuild } from "../cloud/mode";
import type { CloudDeviceSummary } from "../cloud/transport";

export type AdminDevice = CloudDeviceSummary & {
  app_version?: string | null;
  build_id?: string | null;
  capabilities?: { arch?: string; [key: string]: unknown } | unknown;
  current_content_hash?: string | null;
  last_synced_hash?: string | null;
  transport?: string | null;
  udp_active?: boolean;
  last_error?: string | null;
  uptime_s?: number | null;
  recently_seen?: boolean;
  rotation_pending?: boolean;
  created_at?: string;
};

export type AdminAppRelease = {
  id: string;
  version: string;
  build_id: string;
  platform: string;
  arch: string;
  channel: string;
  content_sha256: string;
  package_size: number;
  published_at: string;
  published_by?: string | null;
  notes?: string | null;
  min_updater_version?: string | null;
  updater_protocol_version?: number;
  manifest?: Record<string, unknown>;
  signature_b64?: string;
  key_id?: string;
};

export type AdminAppAssignment = {
  installation_id: string;
  desired_version: string | null;
  desired_build_id: string | null;
  channel: string;
  track_latest: number | boolean;
  auto_update: number | boolean;
  updated_at: string;
};

export type AdminAppUpdateEvent = {
  id: string;
  installation_id: string;
  event_type: string;
  detail_json?: string | null;
  created_at: string;
};

export type AdminScene = { scene_id: string; name: string };
export type AdminRelease = {
  scene_id: string;
  version: number;
  content_hash: string;
  published_at: string;
  published_by?: string | null;
};

export type AuditEvent = {
  id: string;
  actor: string;
  action: string;
  target: string | null;
  detail: unknown;
  created_at: string;
};

export type CommandLogRow = {
  command_id: string;
  installation_id: string;
  type: string;
  status: string;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  acked_at: string | null;
};

export type AdminClientOptions = {
  adminBase?: string;
  accessEmail?: string;
};

const CSRF_COOKIE = "orng_admin_csrf";

function baseUrl(opts?: AdminClientOptions): string {
  return (opts?.adminBase ?? defaultAdminBase()).replace(/\/$/, "");
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const parts = document.cookie.split(";");
  for (const part of parts) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    const k = part.slice(0, idx).trim();
    if (k === name) return decodeURIComponent(part.slice(idx + 1).trim());
  }
  return null;
}

function headers(opts?: AdminClientOptions, method = "GET"): HeadersInit {
  const h: Record<string, string> = {};
  if (method !== "GET" && method !== "HEAD") {
    h["content-type"] = "application/json";
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) h["x-csrf-token"] = csrf;
  }
  // Local wrangler DEV bypass only — never used on cloud admin builds.
  if (!isCloudAdminBuild()) {
    const email = opts?.accessEmail ?? defaultAccessEmail();
    if (email) h["x-dev-access-email"] = email;
  }
  return h;
}

export class AdminAuthError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function readErrorMessage(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const parsed = JSON.parse(text) as {
      error?: { code?: string; message?: string };
      message?: string;
    };
    const code = parsed.error?.code ?? "";
    const msg = parsed.error?.message ?? parsed.message;
    if (code === "csrf_failed") {
      return "Сесія застаріла — увійдіть знову";
    }
    if (typeof msg === "string" && msg.trim()) {
      if (code === "version_conflict" || code === "scene_invalid") {
        return `${code}: ${msg.trim()}`;
      }
      return msg.trim();
    }
  } catch {
    /* plain text */
  }
  return text || res.statusText || `HTTP ${res.status}`;
}

async function jsonFetch<T>(url: string, init?: RequestInit, opts?: AdminClientOptions): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const res = await fetch(url, {
    ...init,
    credentials: "include",
    headers: {
      ...headers(opts, method),
      ...(init?.headers ?? {}),
    },
  });
  if (res.status === 401) {
    endAdminSession("expired");
    throw new AdminAuthError(401, "Потрібен вхід");
  }
  if (res.status === 403) {
    const msg = await readErrorMessage(res);
    if (/csrf|сесія застаріла/i.test(msg)) {
      endAdminSession("expired");
      throw new AdminAuthError(401, msg);
    }
    throw new Error(msg);
  }
  if (res.status === 503) {
    throw new Error("Сервіс тимчасово недоступний");
  }
  if (!res.ok) {
    throw new Error(await readErrorMessage(res));
  }
  return (await res.json()) as T;
}

export async function adminLogin(
  username: string,
  password: string,
  opts?: AdminClientOptions,
): Promise<{ ok: boolean; username: string }> {
  const res = await fetch(`${baseUrl(opts)}/admin/auth/login`, {
    method: "POST",
    credentials: "include",
    // Login must not send a stale CSRF header — endpoint issues fresh cookies.
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    throw new Error(await readErrorMessage(res));
  }
  return (await res.json()) as { ok: boolean; username: string };
}

export async function adminLogout(opts?: AdminClientOptions): Promise<void> {
  await fetch(`${baseUrl(opts)}/admin/auth/logout`, {
    method: "POST",
    credentials: "include",
    headers: headers(opts, "POST"),
  });
}

export async function adminMe(
  opts?: AdminClientOptions,
): Promise<{ ok: boolean; username: string } | null> {
  const res = await fetch(`${baseUrl(opts)}/admin/auth/me`, {
    credentials: "include",
    headers: headers(opts, "GET"),
  });
  if (res.status === 401) {
    endAdminSession("expired");
    return null;
  }
  if (res.status === 503) {
    throw new Error("Сервіс тимчасово недоступний");
  }
  if (!res.ok) {
    throw new Error("Сесію не перевірено");
  }
  try {
    return (await res.json()) as { ok: boolean; username: string };
  } catch {
    throw new Error("Сесію не перевірено");
  }
}

export function adminListDevices(opts?: AdminClientOptions): Promise<{ devices: AdminDevice[] }> {
  return jsonFetch(`${baseUrl(opts)}/admin/devices`, undefined, opts);
}

export type AdminLocation = {
  id: string;
  name: string;
  scene_id: string;
  installation_id: string | null;
  installations: Array<{ id: string; name: string; platform: string; revoked: boolean }>;
};

export function adminListLocations(
  opts?: AdminClientOptions,
): Promise<{ locations: AdminLocation[] }> {
  return jsonFetch(`${baseUrl(opts)}/admin/locations`, undefined, opts);
}

export function adminGetDevice(
  installationId: string,
  opts?: AdminClientOptions,
): Promise<{ device: AdminDevice; live?: Record<string, unknown> }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/devices/${encodeURIComponent(installationId)}`,
    undefined,
    opts,
  );
}

export function adminApprovePair(
  body: {
    pairing_id: string;
    code: string;
    name?: string;
    platform?: string;
    venue_id?: string | null;
  },
  opts?: AdminClientOptions,
): Promise<{ installation_id: string; installation?: AdminDevice; envelope_ready?: boolean }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/pair/approve`,
    { method: "POST", body: JSON.stringify(body) },
    opts,
  );
}

export function adminRevokeDevice(
  installationId: string,
  opts?: AdminClientOptions,
): Promise<{ ok: boolean; installation_id?: string }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/devices/${encodeURIComponent(installationId)}/revoke`,
    { method: "POST", body: "{}" },
    opts,
  );
}

export function adminRotateDevice(
  installationId: string,
  opts?: AdminClientOptions,
): Promise<{
  installation_id: string;
  rotation_pending: boolean;
  envelope_ready?: boolean;
  note?: string;
}> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/devices/${encodeURIComponent(installationId)}/rotate`,
    { method: "POST", body: "{}" },
    opts,
  );
}

export function adminAssignScene(
  installationId: string,
  body: {
    scene_id: string;
    desired_version?: number | null;
    track_latest?: boolean;
  },
  opts?: AdminClientOptions,
): Promise<{ assignment?: unknown; ok?: boolean }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/devices/${encodeURIComponent(installationId)}/assign`,
    { method: "POST", body: JSON.stringify(body) },
    opts,
  );
}

export function adminListScenes(opts?: AdminClientOptions): Promise<{ scenes: AdminScene[] }> {
  return jsonFetch(`${baseUrl(opts)}/admin/scenes`, undefined, opts);
}

export function adminListReleases(
  sceneId: string,
  opts?: AdminClientOptions,
): Promise<{ releases: AdminRelease[] }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/scenes/${encodeURIComponent(sceneId)}/releases`,
    undefined,
    opts,
  );
}

export function adminListAudit(
  query?: { target?: string; limit?: number },
  opts?: AdminClientOptions,
): Promise<{ events: AuditEvent[] }> {
  const q = new URLSearchParams();
  if (query?.target) q.set("target", query.target);
  if (query?.limit) q.set("limit", String(query.limit));
  const suffix = q.toString() ? `?${q}` : "";
  return jsonFetch(`${baseUrl(opts)}/admin/audit${suffix}`, undefined, opts);
}

export function adminListCommands(
  installationId: string,
  query?: { limit?: number },
  opts?: AdminClientOptions,
): Promise<{ commands: CommandLogRow[] }> {
  const q = new URLSearchParams();
  if (query?.limit) q.set("limit", String(query.limit));
  const suffix = q.toString() ? `?${q}` : "";
  return jsonFetch(
    `${baseUrl(opts)}/admin/devices/${encodeURIComponent(installationId)}/commands${suffix}`,
    undefined,
    opts,
  );
}

export function adminWsUrl(
  installationId: string,
  opts?: AdminClientOptions & { sessionHint?: string },
): string {
  const base = baseUrl(opts);
  const u = new URL(
    `${base}/admin/ws/device/${encodeURIComponent(installationId)}`,
    typeof window !== "undefined" ? window.location.origin : "http://localhost",
  );
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  if (!isCloudAdminBuild()) {
    const email = opts?.accessEmail ?? defaultAccessEmail();
    if (email) u.searchParams.set("dev_email", email);
  }
  if (opts?.sessionHint) u.searchParams.set("session", opts.sessionHint);
  return u.toString();
}

export function adminListAppReleases(
  query?: { platform?: string; arch?: string; channel?: string },
  opts?: AdminClientOptions,
): Promise<{ releases: AdminAppRelease[] }> {
  const q = new URLSearchParams();
  if (query?.platform) q.set("platform", query.platform);
  if (query?.arch) q.set("arch", query.arch);
  if (query?.channel) q.set("channel", query.channel);
  const suffix = q.toString() ? `?${q}` : "";
  return jsonFetch(`${baseUrl(opts)}/admin/app/releases${suffix}`, undefined, opts);
}

export async function adminPublishAppRelease(
  body: {
    packageFile: File;
    manifestJson: string;
    version?: string;
    build_id?: string;
    platform?: string;
    arch?: string;
    channel?: string;
    signature_b64?: string;
    key_id?: string;
    notes?: string | null;
  },
  opts?: AdminClientOptions,
): Promise<{
  release: AdminAppRelease;
  idempotent: boolean;
  assignments_updated: number;
}> {
  const form = new FormData();
  form.append("package", body.packageFile);
  form.append("manifest", body.manifestJson);
  if (body.version) form.append("version", body.version);
  if (body.build_id) form.append("build_id", body.build_id);
  if (body.platform) form.append("platform", body.platform);
  if (body.arch) form.append("arch", body.arch);
  if (body.channel) form.append("channel", body.channel);
  if (body.signature_b64) form.append("signature_b64", body.signature_b64);
  if (body.key_id) form.append("key_id", body.key_id);
  if (body.notes != null) form.append("notes", body.notes);

  const h: Record<string, string> = {};
  const csrf = readCookie(CSRF_COOKIE);
  if (csrf) h["x-csrf-token"] = csrf;
  if (!isCloudAdminBuild()) {
    const email = opts?.accessEmail ?? defaultAccessEmail();
    if (email) h["x-dev-access-email"] = email;
  }

  const res = await fetch(`${baseUrl(opts)}/admin/app/releases/publish`, {
    method: "POST",
    credentials: "include",
    headers: h,
    body: form,
  });
  if (res.status === 401) throw new AdminAuthError(401, "Login required");
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return (await res.json()) as {
    release: AdminAppRelease;
    idempotent: boolean;
    assignments_updated: number;
  };
}

export function adminAssignAppRelease(
  installationId: string,
  body: {
    desired_version?: string | null;
    desired_build_id?: string | null;
    channel?: string;
    track_latest?: boolean;
    auto_update?: boolean;
  },
  opts?: AdminClientOptions,
): Promise<{ assignment: AdminAppAssignment }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/installations/${encodeURIComponent(installationId)}/app/assign`,
    { method: "POST", body: JSON.stringify(body) },
    opts,
  );
}

export function adminBulkAppRollout(
  body: {
    installation_ids: string[];
    desired_version: string;
    desired_build_id: string;
    channel?: string;
    track_latest?: boolean;
    auto_update?: boolean;
  },
  opts?: AdminClientOptions,
): Promise<{ updated: string[]; errors?: Array<{ id: string; error: string }> }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/app/rollout`,
    { method: "POST", body: JSON.stringify(body) },
    opts,
  );
}

export function adminRollbackAppAssignment(
  installationId: string,
  body: {
    desired_version: string;
    desired_build_id: string;
    channel?: string;
  },
  opts?: AdminClientOptions,
): Promise<{ assignment: AdminAppAssignment }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/installations/${encodeURIComponent(installationId)}/app/rollback`,
    { method: "POST", body: JSON.stringify(body) },
    opts,
  );
}

export function adminListAppEvents(
  query?: { installation_id?: string; limit?: number },
  opts?: AdminClientOptions,
): Promise<{ events: AdminAppUpdateEvent[] }> {
  const q = new URLSearchParams();
  if (query?.installation_id) q.set("installation_id", query.installation_id);
  if (query?.limit) q.set("limit", String(query.limit));
  const suffix = q.toString() ? `?${q}` : "";
  return jsonFetch(`${baseUrl(opts)}/admin/app/events${suffix}`, undefined, opts);
}

export function adminDeviceCommand(
  installationId: string,
  type: string,
  payload: Record<string, unknown> = {},
  opts?: AdminClientOptions,
): Promise<unknown> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/devices/${encodeURIComponent(installationId)}/command`,
    { method: "POST", body: JSON.stringify({ type, payload }) },
    opts,
  );
}

export type AdminControlSurface = {
  installation_id: string;
  name: string;
  online: boolean;
  armed: boolean;
  blackout: boolean;
  transport: string | null;
  scene_id: string | null;
  scene_name: string | null;
  version: number | null;
  pad_presets: string[];
  actions: string[];
};

export function adminGetControlSurface(
  installationId: string,
  opts?: AdminClientOptions,
): Promise<AdminControlSurface> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/devices/${encodeURIComponent(installationId)}/control-surface`,
    undefined,
    opts,
  );
}

export type SceneWorkspaceFile = { path: string; text: string };

export type SceneWorkspace = {
  installation_id: string;
  scene_id: string;
  version: number;
  content_hash: string;
  track_latest: boolean;
  files: SceneWorkspaceFile[];
};

export type SceneWorkspacePublish = {
  ok: boolean;
  status: "publishing";
  scene_id: string;
  version: number;
  content_hash: string;
  idempotent: boolean;
  command_id: string | null;
  sync_status: string;
  sync_error: string | null;
};

export function adminLoadSceneWorkspace(
  installationId: string,
  opts?: AdminClientOptions,
): Promise<SceneWorkspace> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/devices/${encodeURIComponent(installationId)}/scene-workspace`,
    undefined,
    opts,
  );
}

export function adminLoadLocationWorkspace(
  locationId: string,
  opts?: AdminClientOptions,
): Promise<SceneWorkspace> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/locations/${encodeURIComponent(locationId)}/scene-workspace`,
    undefined,
    opts,
  );
}

export type LocationShowPrefs = {
  mode: "smooth" | "bar" | "beat";
  scope: "preset" | "auto";
  palette: string;
  cast?: "all" | "bars" | "heads";
  groups?: Array<"par" | "bar" | "beam">;
  excluded?: string[];
  character?: "smooth" | "rhythmic" | "impact";
  intensity?: "soft" | "normal" | "full";
  period?: string | number;
  par_rhythm?: boolean;
  accent?: number;
  updated_at: string;
};

export function adminGetLocationShowPrefs(
  locationId: string,
  opts?: AdminClientOptions,
): Promise<{ prefs: LocationShowPrefs | null }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/locations/${encodeURIComponent(locationId)}/show-prefs`,
    undefined,
    opts,
  );
}

export function adminPutLocationShowPrefs(
  locationId: string,
  body: {
    mode?: string;
    scope?: string;
    palette?: string;
    cast?: string;
    groups?: Array<"par" | "bar" | "beam">;
    excluded?: string[];
    character?: string;
    intensity?: string;
    period?: string | number;
    par_rhythm?: boolean;
    accent?: number;
    base?: {
      mode?: string;
      scope?: string;
      palette?: string;
      cast?: string;
      groups?: Array<"par" | "bar" | "beam">;
      excluded?: string[];
      character?: string;
      intensity?: string;
      period?: string | number;
      par_rhythm?: boolean;
      accent?: number;
    };
  },
  opts?: AdminClientOptions,
): Promise<{ prefs: LocationShowPrefs }> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/locations/${encodeURIComponent(locationId)}/show-prefs`,
    { method: "PUT", body: JSON.stringify(body) },
    opts,
  );
}

export function adminAutoCatalog(
  locationId: string,
  opts?: AdminClientOptions,
): Promise<import("../vite-env").AutoCatalog> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/locations/${encodeURIComponent(locationId)}/auto/catalog`,
    undefined,
    opts,
  );
}

export function adminAutoPreview(
  locationId: string,
  query: {
    look: string;
    cycleBeats: number;
    palette: string;
    intensity: string;
    bpm: number;
    frames?: number;
  },
  opts?: AdminClientOptions,
): Promise<import("../vite-env").AutoPreviewClip> {
  const params = new URLSearchParams({
    look: query.look,
    cycle_beats: String(query.cycleBeats),
    palette: query.palette,
    intensity: query.intensity,
    bpm: String(query.bpm),
  });
  if (query.frames && query.frames > 0) params.set("frames", String(query.frames));
  return jsonFetch(
    `${baseUrl(opts)}/admin/locations/${encodeURIComponent(locationId)}/auto/preview?${params.toString()}`,
    undefined,
    opts,
  );
}

export function adminPublishLocationWorkspace(
  locationId: string,
  body: {
    scene_id: string;
    scene_name?: string;
    base_version: number | null;
    files: SceneWorkspaceFile[];
  },
  opts?: AdminClientOptions,
): Promise<SceneWorkspacePublish> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/locations/${encodeURIComponent(locationId)}/scene-workspace`,
    { method: "POST", body: JSON.stringify(body) },
    opts,
  );
}

export function adminPublishSceneWorkspace(
  installationId: string,
  body: {
    scene_id: string;
    scene_name?: string;
    base_version: number | null;
    files: SceneWorkspaceFile[];
  },
  opts?: AdminClientOptions,
): Promise<SceneWorkspacePublish> {
  return jsonFetch(
    `${baseUrl(opts)}/admin/devices/${encodeURIComponent(installationId)}/scene-workspace`,
    { method: "POST", body: JSON.stringify(body) },
    opts,
  );
}
