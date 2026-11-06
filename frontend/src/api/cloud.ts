/** Local backend `/api/cloud/*` helpers + optional direct admin Worker calls. */

export type SyncStatus = {
  status: string;
  local_hash?: string | null;
  remote_hash?: string | null;
  last_synced_hash?: string | null;
  last_synced_version?: number | null;
  applied_version?: number | null;
  applied_hash?: string | null;
  desired_version?: number | null;
  desired_hash?: string | null;
  diverged?: boolean;
  scene_id?: string;
  version?: number;
  error?: string | null;
  content_hash?: string;
};

export type CloudStatus = {
  agent: {
    enabled: boolean;
    connected: boolean;
    url: string | null;
    installation_id: string | null;
    last_error: string | null;
    has_token: boolean;
  };
  token_path: string;
  sync: SyncStatus;
  admin_url: string | null;
  device_url: string | null;
};

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return (await res.json()) as T;
}

export function fetchCloudStatus(): Promise<CloudStatus> {
  return jsonFetch("/api/cloud/status");
}

export function cloudPairStart(): Promise<{
  pairing_id: string;
  code: string;
  expires_at: string;
  client_challenge: string;
}> {
  return jsonFetch("/api/cloud/pair/start", { method: "POST", body: "{}" });
}

export function cloudPairComplete(body: {
  pairing_id: string;
  client_challenge: string;
}): Promise<{ installation_id: string; token_saved: boolean }> {
  return jsonFetch("/api/cloud/pair/complete", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function cloudPublish(body: {
  admin_url?: string;
  scene_id?: string;
  base_version?: number | null;
}): Promise<{ release: unknown; idempotent: boolean }> {
  return jsonFetch("/api/cloud/publish", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function cloudSync(force = false): Promise<SyncStatus> {
  return jsonFetch("/api/cloud/sync", {
    method: "POST",
    body: JSON.stringify({ force }),
  });
}

/** Local managed updater — `/api/app/update/*` (never returns tokens). */

export type AppUpdateStatus = {
  install_mode: "managed" | "developer" | string;
  self_update_allowed: boolean;
  refusal_reason: string | null;
  app_version: string;
  build_id: string;
  platform: string;
  arch: string;
  updater_protocol_version?: number;
  current_release?: Record<string, unknown>;
  previous_release?: Record<string, unknown>;
  update_state: string;
  update_progress?: number | null;
  desired_version?: string | null;
  desired_build_id?: string | null;
  staged_version?: string | null;
  staged_build_id?: string | null;
  staged_content_sha256?: string | null;
  update_phase?: string | null;
  last_error?: string | null;
  last_successful_version?: string | null;
  pending_result?: unknown;
};

async function appUpdateFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/app/update/${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    let detail = text || res.statusText;
    try {
      const body = JSON.parse(text) as {
        detail?: { code?: string; message?: string } | string;
      };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail && typeof body.detail === "object" && body.detail.message) {
        detail = body.detail.code
          ? `${body.detail.code}: ${body.detail.message}`
          : body.detail.message;
      }
    } catch {
      /* keep raw text */
    }
    throw new Error(detail || res.statusText);
  }
  return (await res.json()) as T;
}

export function fetchAppUpdateStatus(): Promise<AppUpdateStatus> {
  return appUpdateFetch("status");
}

export function appUpdateCheck(): Promise<{
  assignment?: Record<string, unknown>;
  update_available?: boolean;
  status?: AppUpdateStatus;
}> {
  return appUpdateFetch("check", { method: "POST", body: "{}" });
}

export function appUpdateStage(body?: {
  version?: string;
  build_id?: string;
}): Promise<Record<string, unknown>> {
  return appUpdateFetch("stage", {
    method: "POST",
    body: JSON.stringify(body ?? {}),
  });
}

export function appUpdateApply(
  force = false,
  identity?: {
    version?: string | null;
    build_id?: string | null;
    content_sha256?: string | null;
  },
): Promise<Record<string, unknown>> {
  return appUpdateFetch("apply", {
    method: "POST",
    body: JSON.stringify({
      force,
      version: identity?.version ?? undefined,
      build_id: identity?.build_id ?? undefined,
      content_sha256: identity?.content_sha256 ?? undefined,
    }),
  });
}

export function appUpdateCancel(): Promise<Record<string, unknown>> {
  return appUpdateFetch("cancel", { method: "POST", body: "{}" });
}

export function appUpdateRollback(): Promise<Record<string, unknown>> {
  return appUpdateFetch("rollback", { method: "POST", body: "{}" });
}

/** @deprecated Prefer `adminCloud.ts` — kept for older imports. */
export async function adminListDevices(
  adminBase: string,
  accessEmail = "dev@localhost",
): Promise<{ devices: Array<Record<string, unknown>> }> {
  return jsonFetch(`${adminBase.replace(/\/$/, "")}/admin/devices`, {
    headers: { "x-dev-access-email": accessEmail },
  });
}

/** Approve returns no plaintext token — device completes via /pair/complete. */
export async function adminApprovePair(
  adminBase: string,
  body: {
    pairing_id: string;
    code: string;
    name: string;
    platform: string;
  },
  accessEmail = "dev@localhost",
): Promise<{ approved: boolean; installation_id: string; pairing: string }> {
  return jsonFetch(`${adminBase.replace(/\/$/, "")}/admin/pair/approve`, {
    method: "POST",
    headers: { "x-dev-access-email": accessEmail },
    body: JSON.stringify(body),
  });
}
