/**
 * APPLICATION release publish / assign / download ACL (separate from scene releases).
 * Device Bearer may download only assigned/compatible releases — never publish.
 *
 * Download contract (Python consumes without guessing):
 *   GET /device/app/releases/:platform/:arch/:version/:build_id/manifest
 *     → JSON { manifest, signature_b64, key_id, content_sha256, package_size, package_url }
 *   GET /device/app/releases/:platform/:arch/:version/:build_id/package
 *     → raw gzip bytes + Content-Type / Content-Length / x-content-sha256
 * Legacy GET …/:build_id (no suffix) still returns the package with Deprecation headers.
 */
import { writeAudit } from "../db/audit";
import { ackCommand, getCommand, logCommand } from "../db/commands";
import { deviceStub } from "../devices";
import type { Env } from "../env";
import { nowIso, uuid } from "../env";
import { sha256Bytes } from "../protocol/crypto";
import {
  APP_ARCHES,
  APP_MANIFEST_SCHEMA,
  AppSignError,
  type AppSigningManifest,
  assertAppArch,
  assertAppChannel,
  assertAppPlatform,
  buildSigningManifest,
  verifyAppReleaseSignature,
} from "./signing";
import type { AppTrustContext } from "./trusted_keys";
/** Production-safe hard limit (≤ Cloudflare Free ~100MB request body; target ≤60MB). */
export const MAX_APP_PACKAGE_BYTES = 80_000_000;
export const UPDATER_PROTOCOL_VERSION = 1;
export class AppReleaseError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "AppReleaseError";
  }
}
export interface AppReleaseRow {
  id: string;
  version: string;
  build_id: string;
  platform: string;
  arch: string;
  channel: string;
  content_sha256: string;
  package_size: number;
  manifest_json: string;
  signature_b64: string;
  key_id: string;
  published_at: string;
  published_by: string | null;
  notes: string | null;
  min_updater_version: string | null;
  updater_protocol_version: number;
  r2_key: string;
}
export interface AppAssignmentRow {
  installation_id: string;
  desired_version: string | null;
  desired_build_id: string | null;
  channel: string;
  track_latest: number;
  /** DB column — API exposes as auto_stage (stage-only; device applies when safe). */
  auto_update: number;
  updated_at: string;
}
/** Public assignment shape: auto_stage is canonical; auto_update is a deprecated alias. */
export function publicAppAssignment(row: AppAssignmentRow) {
  const autoStage = Boolean(row.auto_update);
  return {
    installation_id: row.installation_id,
    desired_version: row.desired_version,
    desired_build_id: row.desired_build_id,
    channel: row.channel,
    track_latest: Boolean(row.track_latest),
    auto_stage: autoStage,
    /** @deprecated Alias of auto_stage — stage only; not full auto-apply. */
    auto_update: autoStage,
    updated_at: row.updated_at,
  };
}
/** Documented download manifest contract for device Python updater. */
export interface AppDownloadManifestContract {
  manifest: AppSigningManifest;
  signature_b64: string;
  key_id: string;
  content_sha256: string;
  package_size: number;
  /** Relative path the device can GET for package bytes. */
  package_url: string;
}
/**
 * Content-addressed package object. Concurrent publish of the same identity with
 * different hashes writes distinct keys — a losing insert deletes only its own
 * object and never the winner's.
 */
export function appPackageR2KeyByHash(contentSha256: string): string {
  return `app/by-hash/${contentSha256}.tar.gz`;
}
export function appManifestR2KeyByHash(contentSha256: string): string {
  return `app/by-hash/${contentSha256}.manifest.json`;
}
/** @deprecated Identity path — kept for reading older objects; new publishes use by-hash. */
export function appPackageR2Key(
  platform: string,
  arch: string,
  channel: string,
  version: string,
  buildId: string,
): string {
  return `app/${platform}/${arch}/${channel}/${version}-${buildId}.tar.gz`;
}
/** @deprecated See appPackageR2Key. */
export function appManifestR2Key(
  platform: string,
  arch: string,
  channel: string,
  version: string,
  buildId: string,
): string {
  return `${appPackageR2Key(platform, arch, channel, version, buildId)}.manifest.json`;
}
function installationArchFromCapabilities(
  capabilitiesJson: string | null | undefined,
): string | null {
  if (!capabilitiesJson) return null;
  try {
    const caps = JSON.parse(capabilitiesJson) as { arch?: unknown };
    return typeof caps.arch === "string" ? caps.arch : null;
  } catch {
    return null;
  }
}
/**
 * Prefer persisted installations.arch (heartbeat); fall back to capabilities_json.
 * Never invent arm64.
 */
export function resolveInstallationArch(inst: {
  arch?: string | null;
  capabilities_json?: string | null;
}): string | null {
  const col = typeof inst.arch === "string" ? inst.arch.trim() : "";
  if (col && (APP_ARCHES as readonly string[]).includes(col)) return col;
  const fromCaps = installationArchFromCapabilities(inst.capabilities_json);
  if (fromCaps && (APP_ARCHES as readonly string[]).includes(fromCaps)) return fromCaps;
  return null;
}
export function requireInstallationArch(inst: {
  arch?: string | null;
  capabilities_json?: string | null;
}): string {
  const arch = resolveInstallationArch(inst);
  if (!arch) {
    throw new AppReleaseError(
      400,
      "unknown_arch",
      "Installation arch unknown — device must report arch via heartbeat before assign/track_latest/download",
    );
  }
  return arch;
}
/** Map installation.platform → app release platform (development → linux for local). */
export function appPlatformForInstallation(platform: string): string {
  if (platform === "development") return "linux";
  return platform;
}
export function appDownloadPackagePath(
  platform: string,
  arch: string,
  version: string,
  buildId: string,
): string {
  return `/device/app/releases/${encodeURIComponent(platform)}/${encodeURIComponent(arch)}/${encodeURIComponent(version)}/${encodeURIComponent(buildId)}/package`;
}
export function appDownloadManifestPath(
  platform: string,
  arch: string,
  version: string,
  buildId: string,
): string {
  return `/device/app/releases/${encodeURIComponent(platform)}/${encodeURIComponent(arch)}/${encodeURIComponent(version)}/${encodeURIComponent(buildId)}/manifest`;
}
export async function listAppReleases(
  env: Env,
  opts?: { platform?: string; arch?: string; channel?: string },
): Promise<AppReleaseRow[]> {
  let sql = `SELECT * FROM app_releases WHERE 1=1`;
  const binds: unknown[] = [];
  if (opts?.platform) {
    sql += ` AND platform = ?`;
    binds.push(opts.platform);
  }
  if (opts?.arch) {
    sql += ` AND arch = ?`;
    binds.push(opts.arch);
  }
  if (opts?.channel) {
    sql += ` AND channel = ?`;
    binds.push(opts.channel);
  }
  sql += ` ORDER BY published_at DESC`;
  const res = await env.DB.prepare(sql)
    .bind(...binds)
    .all<AppReleaseRow>();
  return res.results ?? [];
}
export async function getAppRelease(
  env: Env,
  opts: {
    platform: string;
    arch: string;
    version: string;
    build_id: string;
    channel?: string;
  },
): Promise<AppReleaseRow | null> {
  if (opts.channel) {
    return (
      (await env.DB.prepare(
        `SELECT * FROM app_releases
         WHERE platform = ? AND arch = ? AND version = ? AND build_id = ? AND channel = ?`,
      )
        .bind(opts.platform, opts.arch, opts.version, opts.build_id, opts.channel)
        .first<AppReleaseRow>()) ?? null
    );
  }
  return (
    (await env.DB.prepare(
      `SELECT * FROM app_releases
       WHERE platform = ? AND arch = ? AND version = ? AND build_id = ?
       ORDER BY published_at DESC LIMIT 1`,
    )
      .bind(opts.platform, opts.arch, opts.version, opts.build_id)
      .first<AppReleaseRow>()) ?? null
  );
}
export async function publishAppRelease(
  env: Env,
  opts: {
    version: string;
    build_id: string;
    platform: string;
    arch: string;
    channel: string;
    packageBytes: Uint8Array;
    signatureB64: string;
    keyId: string;
    publishedBy: string;
    notes?: string | null;
    minUpdaterVersion?: string | null;
    updaterProtocolVersion?: number;
    /** Pre-built manifest; content_sha256 must match package. */
    manifest?: AppSigningManifest;
    notifyAssignments?: boolean;
    trust?: AppTrustContext;
  },
): Promise<{ release: AppReleaseRow; idempotent: boolean; assignments_updated: number }> {
  assertAppPlatform(opts.platform);
  assertAppArch(opts.arch);
  assertAppChannel(opts.channel);
  if (!opts.version?.trim() || !opts.build_id?.trim()) {
    throw new AppReleaseError(400, "bad_request", "version and build_id required");
  }
  if (opts.packageBytes.byteLength === 0) {
    throw new AppReleaseError(400, "empty_package", "Package is empty");
  }
  if (opts.packageBytes.byteLength > MAX_APP_PACKAGE_BYTES) {
    throw new AppReleaseError(400, "package_too_large", "Package exceeds size limit");
  }
  if (!opts.signatureB64?.trim()) {
    throw new AppReleaseError(400, "unsigned", "Application releases must be signed");
  }
  const trust: AppTrustContext = opts.trust ?? { env };
  const contentSha256 = await sha256Bytes(opts.packageBytes);
  const manifest =
    opts.manifest ??
    buildSigningManifest({
      version: opts.version,
      build_id: opts.build_id,
      platform: opts.platform,
      arch: opts.arch,
      channel: opts.channel,
      content_sha256: contentSha256,
      package_size: opts.packageBytes.byteLength,
      min_updater_version: opts.minUpdaterVersion,
      updater_protocol_version: opts.updaterProtocolVersion,
      notes: opts.notes,
    });
  if (manifest.schema !== APP_MANIFEST_SCHEMA) {
    throw new AppReleaseError(400, "bad_manifest", "Unsupported manifest schema");
  }
  if (manifest.content_sha256 !== contentSha256) {
    throw new AppReleaseError(
      400,
      "hash_mismatch",
      "manifest content_sha256 does not match package",
    );
  }
  if (manifest.package_size !== opts.packageBytes.byteLength) {
    throw new AppReleaseError(
      400,
      "size_mismatch",
      "manifest package_size does not match package",
    );
  }
  if (
    manifest.version !== opts.version ||
    manifest.build_id !== opts.build_id ||
    manifest.platform !== opts.platform ||
    manifest.arch !== opts.arch ||
    manifest.channel !== opts.channel
  ) {
    throw new AppReleaseError(400, "manifest_mismatch", "Manifest identity fields mismatch");
  }
  try {
    await verifyAppReleaseSignature({
      manifest,
      signatureB64: opts.signatureB64,
      keyId: opts.keyId,
      trust,
    });
  } catch (err) {
    if (err instanceof AppSignError) {
      throw new AppReleaseError(err.status, err.code, err.message);
    }
    throw err;
  }
  const now = nowIso();
  const existing = await env.DB.prepare(
    `SELECT * FROM app_releases
     WHERE version = ? AND build_id = ? AND platform = ? AND arch = ? AND channel = ?`,
  )
    .bind(opts.version, opts.build_id, opts.platform, opts.arch, opts.channel)
    .first<AppReleaseRow>();
  if (existing) {
    if (existing.content_sha256 !== contentSha256) {
      throw new AppReleaseError(
        409,
        "conflict",
        "Release identity exists with a different content hash",
      );
    }
    const assignmentsUpdated = await repairTrackLatestAppAssignments(env, {
      platform: opts.platform,
      arch: opts.arch,
      channel: opts.channel,
      version: opts.version,
      buildId: opts.build_id,
      now,
    });
    if (opts.notifyAssignments !== false && assignmentsUpdated > 0) {
      await notifyTrackLatestAppInstallations(env, {
        platform: opts.platform,
        arch: opts.arch,
        channel: opts.channel,
        version: opts.version,
        buildId: opts.build_id,
      });
    }
    return { release: existing, idempotent: true, assignments_updated: assignmentsUpdated };
  }
  const id = uuid();
  const r2Key = appPackageR2KeyByHash(contentSha256);
  const manifestKey = appManifestR2KeyByHash(contentSha256);
  const manifestJson = JSON.stringify(manifest);
  // Budget check BEFORE put — never upload then delete (GB-month peak).
  const { assertWithinBudget } = await import("./storage_budget");
  await assertWithinBudget(
    env,
    opts.packageBytes.byteLength + new TextEncoder().encode(manifestJson).byteLength,
  );
  await env.APP_RELEASES.put(r2Key, opts.packageBytes, {
    httpMetadata: { contentType: "application/gzip" },
    customMetadata: {
      version: opts.version,
      build_id: opts.build_id,
      platform: opts.platform,
      arch: opts.arch,
      channel: opts.channel,
      content_sha256: contentSha256,
      key_id: opts.keyId,
    },
  });
  await env.APP_RELEASES.put(
    manifestKey,
    new TextEncoder().encode(
      JSON.stringify({
        manifest,
        signature_b64: opts.signatureB64,
        key_id: opts.keyId,
      }),
    ),
    { httpMetadata: { contentType: "application/json" } },
  );
  try {
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO app_releases (
           id, version, build_id, platform, arch, channel, content_sha256, package_size,
           manifest_json, signature_b64, key_id, published_at, published_by, notes,
           min_updater_version, updater_protocol_version, r2_key
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        id,
        opts.version,
        opts.build_id,
        opts.platform,
        opts.arch,
        opts.channel,
        contentSha256,
        opts.packageBytes.byteLength,
        manifestJson,
        opts.signatureB64,
        opts.keyId,
        now,
        opts.publishedBy,
        opts.notes ?? null,
        opts.minUpdaterVersion ?? null,
        opts.updaterProtocolVersion ?? UPDATER_PROTOCOL_VERSION,
        r2Key,
      ),
    ]);
  } catch (err) {
    const again = await env.DB.prepare(
      `SELECT * FROM app_releases
       WHERE version = ? AND build_id = ? AND platform = ? AND arch = ? AND channel = ?`,
    )
      .bind(opts.version, opts.build_id, opts.platform, opts.arch, opts.channel)
      .first<AppReleaseRow>();
    if (again && again.content_sha256 === contentSha256) {
      const assignmentsUpdated = await repairTrackLatestAppAssignments(env, {
        platform: opts.platform,
        arch: opts.arch,
        channel: opts.channel,
        version: opts.version,
        buildId: opts.build_id,
        now,
      });
      if (opts.notifyAssignments !== false && assignmentsUpdated > 0) {
        await notifyTrackLatestAppInstallations(env, {
          platform: opts.platform,
          arch: opts.arch,
          channel: opts.channel,
          version: opts.version,
          buildId: opts.build_id,
        });
      }
      return { release: again, idempotent: true, assignments_updated: assignmentsUpdated };
    }
    // Delete ONLY our content-addressed objects — never the winner's different hash.
    await env.APP_RELEASES.delete(r2Key).catch(() => undefined);
    await env.APP_RELEASES.delete(manifestKey).catch(() => undefined);
    if (again && again.content_sha256 !== contentSha256) {
      throw new AppReleaseError(
        409,
        "conflict",
        "Release identity exists with a different content hash",
      );
    }
    throw err;
  }
  const assignmentsUpdated = await repairTrackLatestAppAssignments(env, {
    platform: opts.platform,
    arch: opts.arch,
    channel: opts.channel,
    version: opts.version,
    buildId: opts.build_id,
    now,
  });
  await writeAudit(env, opts.publishedBy, "app.publish", `${opts.platform}/${opts.arch}`, {
    version: opts.version,
    build_id: opts.build_id,
    channel: opts.channel,
    content_sha256: contentSha256,
    key_id: opts.keyId,
    assignments_updated: assignmentsUpdated,
  });
  if (opts.notifyAssignments !== false && assignmentsUpdated > 0) {
    await notifyTrackLatestAppInstallations(env, {
      platform: opts.platform,
      arch: opts.arch,
      channel: opts.channel,
      version: opts.version,
      buildId: opts.build_id,
    });
  }
  const release = (await getAppRelease(env, {
    platform: opts.platform,
    arch: opts.arch,
    version: opts.version,
    build_id: opts.build_id,
    channel: opts.channel,
  }))!;
  return { release, idempotent: false, assignments_updated: assignmentsUpdated };
}
/**
 * Bump track_latest assignments whose installation platform/arch/channel match.
 * Unknown arch installations are skipped (never defaulted to arm64).
 */
async function repairTrackLatestAppAssignments(
  env: Env,
  opts: {
    platform: string;
    arch: string;
    channel: string;
    version: string;
    buildId: string;
    now: string;
  },
): Promise<number> {
  const rows = await env.DB.prepare(
    `SELECT a.installation_id, a.channel, a.track_latest, i.platform, i.arch, i.capabilities_json
     FROM app_assignments a
     JOIN installations i ON i.id = a.installation_id
     WHERE a.track_latest = 1 AND a.channel = ? AND i.revoked_at IS NULL`,
  )
    .bind(opts.channel)
    .all<{
      installation_id: string;
      channel: string;
      track_latest: number;
      platform: string;
      arch: string | null;
      capabilities_json: string;
    }>();
  let n = 0;
  for (const row of rows.results ?? []) {
    const plat = appPlatformForInstallation(row.platform);
    const arch = resolveInstallationArch(row);
    if (plat !== opts.platform) continue;
    if (arch == null || arch !== opts.arch) continue;
    await env.DB.prepare(
      `UPDATE app_assignments
       SET desired_version = ?, desired_build_id = ?, updated_at = ?
       WHERE installation_id = ?`,
    )
      .bind(opts.version, opts.buildId, opts.now, row.installation_id)
      .run();
    n += 1;
  }
  return n;
}
export async function notifyTrackLatestAppInstallations(
  env: Env,
  opts: {
    platform: string;
    arch: string;
    channel: string;
    version: string;
    buildId: string;
  },
): Promise<void> {
  const rows = await env.DB.prepare(
    `SELECT a.installation_id, a.desired_version, a.desired_build_id, a.channel,
            a.auto_update, i.platform, i.arch, i.capabilities_json
     FROM app_assignments a
     JOIN installations i ON i.id = a.installation_id
     WHERE a.track_latest = 1 AND a.channel = ? AND i.revoked_at IS NULL`,
  )
    .bind(opts.channel)
    .all<{
      installation_id: string;
      desired_version: string | null;
      desired_build_id: string | null;
      channel: string;
      auto_update: number;
      platform: string;
      arch: string | null;
      capabilities_json: string;
    }>();
  for (const row of rows.results ?? []) {
    const plat = appPlatformForInstallation(row.platform);
    const arch = resolveInstallationArch(row);
    if (plat !== opts.platform) continue;
    if (arch == null || arch !== opts.arch) continue;
    const desiredVersion = row.desired_version ?? opts.version;
    const desiredBuild = row.desired_build_id ?? opts.buildId;
    const stub = deviceStub(env, row.installation_id);
    try {
      const statusRes = await stub.fetch("https://device-do/status");
      const live = (await statusRes.json()) as { device_connected?: boolean };
      if (!live.device_connected) continue;
      // auto_update DB flag = auto_stage: notify stage only. Apply is a separate command.
      const cmdType = row.auto_update ? "stage_app_update" : "check_app_update";
      await stub.fetch(
        new Request("https://device-do/command", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            type: cmdType,
            installation_id: row.installation_id,
            source: "cloud:track_latest_app",
            payload: {
              version: desiredVersion,
              build_id: desiredBuild,
              platform: opts.platform,
              arch: opts.arch,
              channel: opts.channel,
              track_latest: true,
              auto_stage: Boolean(row.auto_update),
            },
          }),
        }),
      );
    } catch {
      /* best-effort */
    }
  }
}
export async function assignAppRelease(
  env: Env,
  opts: {
    installationId: string;
    desiredVersion: string | null;
    desiredBuildId: string | null;
    channel: string;
    trackLatest: boolean;
    /** Stage-only policy (DB column auto_update). */
    autoStage?: boolean;
    /** @deprecated Use autoStage. */
    autoUpdate?: boolean;
    actor: string;
    notify?: boolean;
  },
): Promise<AppAssignmentRow> {
  assertAppChannel(opts.channel);
  const inst = await env.DB.prepare(
    `SELECT id, platform, arch, capabilities_json, revoked_at FROM installations WHERE id = ?`,
  )
    .bind(opts.installationId)
    .first<{
      id: string;
      platform: string;
      arch: string | null;
      capabilities_json: string;
      revoked_at: string | null;
    }>();
  if (!inst) throw new AppReleaseError(404, "not_found", "Installation not found");
  if (inst.revoked_at) throw new AppReleaseError(403, "revoked", "Installation revoked");
  let version = opts.desiredVersion;
  let buildId = opts.desiredBuildId;
  const platform = appPlatformForInstallation(inst.platform);
  const arch = requireInstallationArch(inst);
  const autoStage = opts.autoStage === true || opts.autoUpdate === true;
  if (opts.trackLatest || (version === null && buildId === null)) {
    const latest = await env.DB.prepare(
      `SELECT version, build_id FROM app_releases
       WHERE platform = ? AND arch = ? AND channel = ?
       ORDER BY published_at DESC LIMIT 1`,
    )
      .bind(platform, arch, opts.channel)
      .first<{ version: string; build_id: string }>();
    if (latest) {
      version = latest.version;
      buildId = latest.build_id;
    }
  } else if (version && buildId) {
    const release = await getAppRelease(env, {
      platform,
      arch,
      version,
      build_id: buildId,
      channel: opts.channel,
    });
    if (!release) {
      throw new AppReleaseError(
        404,
        "release_not_found",
        "App release not found for installation platform/arch",
      );
    }
  }
  const now = nowIso();
  await env.DB.prepare(
    `INSERT INTO app_assignments
       (installation_id, desired_version, desired_build_id, channel, track_latest, auto_update, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(installation_id) DO UPDATE SET
       desired_version = excluded.desired_version,
       desired_build_id = excluded.desired_build_id,
       channel = excluded.channel,
       track_latest = excluded.track_latest,
       auto_update = excluded.auto_update,
       updated_at = excluded.updated_at`,
  )
    .bind(
      opts.installationId,
      version,
      buildId,
      opts.channel,
      opts.trackLatest ? 1 : 0,
      autoStage ? 1 : 0,
      now,
    )
    .run();
  await writeAudit(env, opts.actor, "app.assign", opts.installationId, {
    desired_version: version,
    desired_build_id: buildId,
    channel: opts.channel,
    track_latest: opts.trackLatest,
    auto_stage: autoStage,
  });
  if (opts.notify !== false && version && buildId) {
    await notifyInstallationAppUpdate(env, opts.installationId, {
      version,
      buildId,
      platform,
      arch,
      channel: opts.channel,
      autoStage,
    });
  }
  return (await getAppAssignment(env, opts.installationId))!;
}
export async function bulkAppRollout(
  env: Env,
  opts: {
    installationIds: string[];
    desiredVersion: string;
    desiredBuildId: string;
    channel: string;
    trackLatest?: boolean;
    autoStage?: boolean;
    autoUpdate?: boolean;
    actor: string;
  },
): Promise<{ updated: string[]; errors: Array<{ installation_id: string; error: string }> }> {
  const updated: string[] = [];
  const errors: Array<{ installation_id: string; error: string }> = [];
  for (const id of opts.installationIds) {
    try {
      await assignAppRelease(env, {
        installationId: id,
        desiredVersion: opts.desiredVersion,
        desiredBuildId: opts.desiredBuildId,
        channel: opts.channel,
        trackLatest: opts.trackLatest === true,
        autoStage: opts.autoStage,
        autoUpdate: opts.autoUpdate,
        actor: opts.actor,
        notify: true,
      });
      updated.push(id);
    } catch (err) {
      errors.push({
        installation_id: id,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }
  await writeAudit(env, opts.actor, "app.rollout", null, {
    desired_version: opts.desiredVersion,
    desired_build_id: opts.desiredBuildId,
    channel: opts.channel,
    updated,
    errors,
  });
  return { updated, errors };
}
export async function rollbackAppAssignment(
  env: Env,
  opts: {
    installationId: string;
    desiredVersion: string;
    desiredBuildId: string;
    channel?: string;
    actor: string;
  },
): Promise<AppAssignmentRow> {
  const current = await getAppAssignment(env, opts.installationId);
  const channel = opts.channel ?? current?.channel ?? "stable";
  const assigned = await assignAppRelease(env, {
    installationId: opts.installationId,
    desiredVersion: opts.desiredVersion,
    desiredBuildId: opts.desiredBuildId,
    channel,
    trackLatest: false,
    autoStage: false,
    actor: opts.actor,
    notify: false,
  });
  const inst = await env.DB.prepare(
    `SELECT platform, arch, capabilities_json FROM installations WHERE id = ?`,
  )
    .bind(opts.installationId)
    .first<{ platform: string; arch: string | null; capabilities_json: string }>();
  const platform = appPlatformForInstallation(inst?.platform ?? "raspberry");
  const arch = requireInstallationArch(inst ?? {});
  await writeAudit(env, opts.actor, "app.rollback", opts.installationId, {
    desired_version: opts.desiredVersion,
    desired_build_id: opts.desiredBuildId,
    channel,
  });
  await recordAppUpdateEvent(env, opts.installationId, "rollback_requested", {
    version: opts.desiredVersion,
    build_id: opts.desiredBuildId,
    actor: opts.actor,
  });
  const stub = deviceStub(env, opts.installationId);
  try {
    const statusRes = await stub.fetch("https://device-do/status");
    const live = (await statusRes.json()) as { device_connected?: boolean };
    if (live.device_connected) {
      await stub.fetch(
        new Request("https://device-do/command", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            type: "rollback_app_update",
            installation_id: opts.installationId,
            source: `cloud:${opts.actor}`,
            payload: {
              version: opts.desiredVersion,
              build_id: opts.desiredBuildId,
              platform,
              arch,
              channel,
            },
          }),
        }),
      );
    }
  } catch {
    /* best-effort */
  }
  return assigned;
}
async function notifyInstallationAppUpdate(
  env: Env,
  installationId: string,
  opts: {
    version: string;
    buildId: string;
    platform: string;
    arch: string;
    channel: string;
    autoStage: boolean;
  },
): Promise<void> {
  const stub = deviceStub(env, installationId);
  try {
    const statusRes = await stub.fetch("https://device-do/status");
    const live = (await statusRes.json()) as { device_connected?: boolean };
    if (!live.device_connected) return;
    await stub.fetch(
      new Request("https://device-do/command", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          type: opts.autoStage ? "stage_app_update" : "check_app_update",
          installation_id: installationId,
          source: "cloud:app_assign",
          payload: {
            version: opts.version,
            build_id: opts.buildId,
            platform: opts.platform,
            arch: opts.arch,
            channel: opts.channel,
            auto_stage: opts.autoStage,
          },
        }),
      }),
    );
  } catch {
    /* best-effort */
  }
}
export async function getAppAssignment(
  env: Env,
  installationId: string,
): Promise<AppAssignmentRow | null> {
  return (
    (await env.DB.prepare(`SELECT * FROM app_assignments WHERE installation_id = ?`)
      .bind(installationId)
      .first<AppAssignmentRow>()) ?? null
  );
}
/**
 * Download ACL: device Bearer with app:read_assigned may fetch only the
 * assigned (or current) compatible release for its platform/arch.
 * Persisted arch is checked — never ephemeral-only. Unknown arch → clear error.
 */
export async function assertAppDownloadAllowed(
  env: Env,
  opts: {
    installationId: string;
    platform: string;
    arch: string;
    version: string;
    build_id: string;
    scopes: readonly string[];
    revoked: boolean;
  },
): Promise<AppReleaseRow> {
  if (opts.revoked) {
    throw new AppReleaseError(403, "revoked", "Installation revoked");
  }
  if (!opts.scopes.includes("app:read_assigned")) {
    throw new AppReleaseError(403, "forbidden", "Missing app:read_assigned scope");
  }
  const inst = await env.DB.prepare(
    `SELECT platform, arch, capabilities_json, app_version, build_id, revoked_at
     FROM installations WHERE id = ?`,
  )
    .bind(opts.installationId)
    .first<{
      platform: string;
      arch: string | null;
      capabilities_json: string;
      app_version: string | null;
      build_id: string | null;
      revoked_at: string | null;
    }>();
  if (!inst || inst.revoked_at) {
    throw new AppReleaseError(403, "revoked", "Installation revoked");
  }
  const plat = appPlatformForInstallation(inst.platform);
  const arch = requireInstallationArch(inst);
  if (plat !== opts.platform) {
    throw new AppReleaseError(403, "forbidden", "Release platform does not match installation");
  }
  if (arch !== opts.arch) {
    throw new AppReleaseError(403, "forbidden", "Release arch does not match installation");
  }
  const assignment = await getAppAssignment(env, opts.installationId);
  if (!assignment) {
    throw new AppReleaseError(403, "forbidden", "No app assignment for this installation");
  }
  const allowed = new Set<string>();
  if (assignment.desired_version && assignment.desired_build_id) {
    allowed.add(`${assignment.desired_version}/${assignment.desired_build_id}`);
  }
  if (inst.app_version && inst.build_id) {
    allowed.add(`${inst.app_version}/${inst.build_id}`);
  }
  const key = `${opts.version}/${opts.build_id}`;
  if (!allowed.has(key)) {
    throw new AppReleaseError(403, "forbidden", "Release not assigned to this installation");
  }
  const release = await getAppRelease(env, {
    platform: opts.platform,
    arch: opts.arch,
    version: opts.version,
    build_id: opts.build_id,
    channel: assignment.channel,
  });
  if (!release) {
    throw new AppReleaseError(404, "not_found", "App release not found");
  }
  return release;
}
async function verifyStoredReleaseSignature(
  env: Env,
  release: AppReleaseRow,
  trust?: AppTrustContext,
): Promise<AppSigningManifest> {
  try {
    const manifest = JSON.parse(release.manifest_json) as AppSigningManifest;
    await verifyAppReleaseSignature({
      manifest,
      signatureB64: release.signature_b64,
      keyId: release.key_id,
      trust: trust ?? { env },
    });
    return manifest;
  } catch (err) {
    if (err instanceof AppSignError) {
      throw new AppReleaseError(500, "bad_signature", "Stored release signature invalid");
    }
    throw err;
  }
}
export async function getAppManifestAuthorized(
  env: Env,
  opts: {
    installationId: string;
    platform: string;
    arch: string;
    version: string;
    build_id: string;
    scopes: readonly string[];
    revoked: boolean;
    trust?: AppTrustContext;
  },
): Promise<AppDownloadManifestContract> {
  const release = await assertAppDownloadAllowed(env, opts);
  const manifest = await verifyStoredReleaseSignature(env, release, opts.trust);
  return {
    manifest,
    signature_b64: release.signature_b64,
    key_id: release.key_id,
    content_sha256: release.content_sha256,
    package_size: release.package_size,
    package_url: appDownloadPackagePath(
      release.platform,
      release.arch,
      release.version,
      release.build_id,
    ),
  };
}
export async function getAppPackageAuthorized(
  env: Env,
  opts: {
    installationId: string;
    platform: string;
    arch: string;
    version: string;
    build_id: string;
    scopes: readonly string[];
    revoked: boolean;
    trust?: AppTrustContext;
  },
): Promise<{ release: AppReleaseRow; body: R2ObjectBody }> {
  // ACL before R2.
  const release = await assertAppDownloadAllowed(env, opts);
  await verifyStoredReleaseSignature(env, release, opts.trust);
  const body = await env.APP_RELEASES.get(release.r2_key);
  if (!body) throw new AppReleaseError(404, "not_found", "Package object missing in R2");
  return { release, body };
}
export async function recordAppUpdateEvent(
  env: Env,
  installationId: string,
  eventType: string,
  detail: Record<string, unknown> = {},
): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO app_update_events (id, installation_id, event_type, detail_json, created_at)
     VALUES (?, ?, ?, ?, ?)`,
  )
    .bind(uuid(), installationId, eventType, JSON.stringify(detail), nowIso())
    .run();
}
export async function listAppUpdateEvents(
  env: Env,
  opts?: { installationId?: string; limit?: number },
): Promise<
  Array<{
    id: string;
    installation_id: string;
    event_type: string;
    detail: unknown;
    created_at: string;
  }>
> {
  const limit = Math.min(Math.max(opts?.limit ?? 50, 1), 200);
  const rows = opts?.installationId
    ? await env.DB.prepare(
        `SELECT id, installation_id, event_type, detail_json, created_at
         FROM app_update_events WHERE installation_id = ?
         ORDER BY created_at DESC LIMIT ?`,
      )
        .bind(opts.installationId, limit)
        .all<{
          id: string;
          installation_id: string;
          event_type: string;
          detail_json: string;
          created_at: string;
        }>()
    : await env.DB.prepare(
        `SELECT id, installation_id, event_type, detail_json, created_at
         FROM app_update_events ORDER BY created_at DESC LIMIT ?`,
      )
        .bind(limit)
        .all<{
          id: string;
          installation_id: string;
          event_type: string;
          detail_json: string;
          created_at: string;
        }>();
  return (rows.results ?? []).map((r) => ({
    id: r.id,
    installation_id: r.installation_id,
    event_type: r.event_type,
    detail: safeJson(r.detail_json),
    created_at: r.created_at,
  }));
}
const TERMINAL_UPDATE_EVENTS = new Set(["applied", "failed", "rolled_back"]);
function commandStatusForUpdateEvent(
  eventType: "applied" | "failed" | "rolled_back" | "staged" | "cancelled",
): string {
  if (eventType === "failed") return "failed";
  if (eventType === "applied" || eventType === "rolled_back") return "applied";
  // staged / cancelled: acknowledged but not a terminal apply — keep accepted.
  return "accepted";
}
/**
 * A healthy apply is not a check ACK and not an older assignment.
 * Returns the current command status when the report must be ignored.
 */
async function staleAppliedResult(
  env: Env,
  installationId: string,
  commandId: string | null | undefined,
  detail: Record<string, unknown>,
): Promise<string | null> {
  const reported = typeof detail.build_id === "string" ? detail.build_id : "";
  const assignment = await getAppAssignment(env, installationId);
  const desired = assignment?.desired_build_id ?? "";
  const existing = commandId ? await getCommand(env, commandId) : null;
  let payloadBuild = "";
  if (existing) {
    try {
      payloadBuild = String(
        (JSON.parse(existing.payload_json) as { build_id?: string }).build_id ?? "",
      );
    } catch {
      payloadBuild = "";
    }
  }
  const checkOrStage =
    existing?.type === "check_app_update" || existing?.type === "stage_app_update";
  const id = commandId ?? "";
  const checkOrStageId =
    id.startsWith("desired-app-check:") ||
    id.startsWith("desired-app-stage:") ||
    (id.startsWith("desired-app:") && !id.startsWith("desired-app-apply:"));
  const replaced = Boolean(desired) && reported !== desired;
  const payloadMismatch = Boolean(payloadBuild) && payloadBuild !== reported;
  if (!reported || checkOrStage || checkOrStageId || replaced || payloadMismatch) {
    return existing?.status ?? "ignored";
  }
  return null;
}

/**
 * Device reports update outcome (applied / failed / rolled_back / staged / cancelled).
 * Terminal events finalize command_log; `accepted` ACK alone must never finalize.
 * A check or stage ACK is not a healthy apply. A replaced assignment is not revived.
 * Idempotent redelivery of the same terminal result converges to one state.
 *
 * REST: POST /device/app/update-result
 * WSS:  type=update_result (handled by DeviceDO → this function)
 */
export async function reportAppUpdateResult(
  env: Env,
  opts: {
    installationId: string;
    eventType: "applied" | "failed" | "rolled_back" | "staged" | "cancelled";
    commandId?: string | null;
    detail?: Record<string, unknown>;
  },
): Promise<{
  acked: boolean;
  event_id: string;
  command_status: string | null;
  terminal: boolean;
  idempotent: boolean;
}> {
  const terminal = TERMINAL_UPDATE_EVENTS.has(opts.eventType);
  const targetStatus = commandStatusForUpdateEvent(opts.eventType);
  const detail: Record<string, unknown> = {
    ...(opts.detail ?? {}),
    command_id: opts.commandId ?? null,
  };
  if (opts.eventType === "applied") {
    const stale = await staleAppliedResult(env, opts.installationId, opts.commandId, detail);
    if (stale) {
      return {
        acked: false,
        event_id: "",
        command_status: stale,
        terminal: false,
        idempotent: false,
      };
    }
  }
  let idempotent = false;
  let acked = false;
  let commandStatus: string | null = null;
  let priorEventId: string | null = null;

  // Same command_id + terminal event already recorded → idempotent redelivery.
  if (opts.commandId && terminal) {
    const priorRows = await env.DB.prepare(
      `SELECT id, detail_json FROM app_update_events
       WHERE installation_id = ? AND event_type = ?
       ORDER BY created_at DESC LIMIT 20`,
    )
      .bind(opts.installationId, opts.eventType)
      .all<{ id: string; detail_json: string }>();
    for (const row of priorRows.results ?? []) {
      try {
        const d = JSON.parse(row.detail_json) as { command_id?: string | null };
        if (d.command_id === opts.commandId) {
          priorEventId = row.id;
          idempotent = true;
          break;
        }
      } catch {
        /* ignore bad json */
      }
    }
  }

  if (opts.commandId) {
    const existing = await getCommand(env, opts.commandId);
    if (existing && existing.installation_id === opts.installationId) {
      if (
        existing.status === "failed" &&
        existing.error_code === "ttl_expired" &&
        (opts.eventType === "applied" || opts.eventType === "rolled_back")
      ) {
        const reported = String(detail.build_id ?? "");
        let expected = "";
        try {
          const payload = JSON.parse(existing.payload_json) as { build_id?: string };
          expected = String(payload.build_id ?? "");
        } catch {
          expected = "";
        }
        if (!reported || (expected && reported !== expected)) {
          return {
            acked: false,
            event_id: "",
            command_status: existing.status,
            terminal: false,
            idempotent: false,
          };
        }
      }
      if (
        terminal &&
        (existing.status === "applied" || existing.status === "failed") &&
        existing.status === targetStatus
      ) {
        idempotent = true;
        acked = true;
        commandStatus = existing.status;
      } else if (
        !idempotent &&
        (terminal || opts.eventType === "staged" || opts.eventType === "cancelled")
      ) {
        await ackCommand(
          env,
          opts.commandId,
          targetStatus,
          opts.eventType === "failed" ? "app_update_failed" : null,
          opts.eventType === "failed"
            ? String(detail.error_message ?? "update failed")
            : null,
          { installationId: opts.installationId },
        );
        acked = true;
        commandStatus = targetStatus;
      } else if (idempotent) {
        acked = true;
        commandStatus = existing.status === targetStatus ? existing.status : targetStatus;
        if (existing.status !== targetStatus && terminal) {
          await ackCommand(
            env,
            opts.commandId,
            targetStatus,
            null,
            null,
            { installationId: opts.installationId },
          );
          commandStatus = targetStatus;
        }
      }
    } else if (!existing && terminal) {
      // Reconnect: log a completed command so admin can see the result.
      // ON CONFLICT DO NOTHING — force terminal status in case a concurrent
      // DO insert already created the row as "accepted".
      if (!idempotent) {
        await logCommand(env, {
          command_id: opts.commandId,
          installation_id: opts.installationId,
          type: "apply_app_update",
          payload: detail,
          status: targetStatus,
          error_code: opts.eventType === "failed" ? "app_update_failed" : null,
          error_message:
            opts.eventType === "failed"
              ? String(detail.error_message ?? "update failed")
              : null,
        });
      }
      await ackCommand(
        env,
        opts.commandId,
        targetStatus,
        opts.eventType === "failed" ? "app_update_failed" : null,
        opts.eventType === "failed"
          ? String(detail.error_message ?? "update failed")
          : null,
        { installationId: opts.installationId },
      );
      acked = true;
      commandStatus = targetStatus;
    } else if (idempotent) {
      acked = true;
      commandStatus = targetStatus;
    }
  }

  if (idempotent) {
    return {
      acked,
      event_id: priorEventId ?? uuid(),
      command_status: commandStatus,
      terminal,
      idempotent: true,
    };
  }

  const assignment = await getAppAssignment(env, opts.installationId);
  const reportedBuild = typeof detail.build_id === "string" ? detail.build_id : "";
  const desiredBuild = assignment?.desired_build_id ?? "";
  if (
    (opts.eventType === "applied" || opts.eventType === "rolled_back") &&
    desiredBuild &&
    reportedBuild &&
    reportedBuild !== desiredBuild
  ) {
    detail.stale_assignment = true;
  }

  const eventId = uuid();
  await env.DB.prepare(
    `INSERT INTO app_update_events (id, installation_id, event_type, detail_json, created_at)
     VALUES (?, ?, ?, ?, ?)`,
  )
    .bind(eventId, opts.installationId, opts.eventType, JSON.stringify(detail), nowIso())
    .run();

  if (
    opts.eventType === "applied" &&
    typeof detail.version === "string" &&
    detail.stale_assignment !== true
  ) {
    await env.DB.prepare(
      `UPDATE installations SET app_version = ?, build_id = COALESCE(?, build_id)
       WHERE id = ?`,
    )
      .bind(
        detail.version,
        typeof detail.build_id === "string" ? detail.build_id : null,
        opts.installationId,
      )
      .run();
  }
  return {
    acked,
    event_id: eventId,
    command_status: commandStatus,
    terminal,
    idempotent: false,
  };
}
function safeJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}
export { AppSignError };
