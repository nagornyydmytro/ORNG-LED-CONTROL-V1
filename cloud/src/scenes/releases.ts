/**
 * Immutable scene release publish / list / download metadata.
 * Cloudflare never generates Art-Net вЂ” only stores allowlisted scene files.
 */
import { parse as parseYaml } from "yaml";
import { sceneCapabilityProblems } from "./capabilities";
import { sha256Bytes, sha256Hex } from "../protocol/crypto";
import type { Env } from "../env";
import { nowIso, uuid } from "../env";
import { writeAudit } from "../db/audit";
import { deviceStub } from "../devices";
export const ALLOWED_SCENE_PATHS = [
  "scene.yaml",
  "app.yaml",
  "patch.yaml",
  "layout.yaml",
  "controls.yaml",
  "profiles/",
  "presets/",
] as const;
export const REQUIRED_SCENE_FILES = [
  "scene.yaml",
  "app.yaml",
  "patch.yaml",
  "layout.yaml",
] as const;
export const MAX_BUNDLE_BYTES = 8 * 1024 * 1024;
export const MAX_FILE_BYTES = 2 * 1024 * 1024;
export const MAX_TOTAL_DECODED_BYTES = 8 * 1024 * 1024;
export const MAX_FILE_COUNT = 200;
/** Strict scene IDs used for routes, R2 keys, and scene.yaml вЂ” reject path traversal. */
export const STRICT_SCENE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const STRICT_B64_RE = /^[A-Za-z0-9+/]*={0,2}$/;
/** How many prior versions a device with scene:read_assigned may roll back to. */
export const ASSIGNED_ROLLBACK_WINDOW = 3;
export interface BundleFile {
  path: string;
  data: Uint8Array;
}
export interface ReleaseManifest {
  schema: number;
  scene_id: string;
  version: number;
  content_sha256: string;
  files: Record<string, string>;
  size_bytes: number;
  min_app_version: string | null;
}
export class ReleaseError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ReleaseError";
  }
}
/**
 * Reject unsafe scene IDs before any D1 / R2 / path construction.
 * Blocks `/`, `\`, `..`, control chars, uppercase, and oversize ids.
 */
export function assertStrictSceneId(sceneId: string): void {
  if (typeof sceneId !== "string" || !sceneId) {
    throw new ReleaseError(400, "invalid_scene_id", "scene_id is required");
  }
  if (
    sceneId.includes("/") ||
    sceneId.includes("\\") ||
    sceneId.includes("..") ||
    /[\x00-\x1f\x7f]/.test(sceneId)
  ) {
    throw new ReleaseError(400, "invalid_scene_id", `Unsafe scene_id: ${sceneId}`);
  }
  if (!STRICT_SCENE_ID_RE.test(sceneId)) {
    throw new ReleaseError(
      400,
      "invalid_scene_id",
      `scene_id must match ${STRICT_SCENE_ID_RE}`,
    );
  }
}
export function isAllowedScenePath(path: string): boolean {
  const normalized = path.replace(/\\/g, "/").replace(/^\/+/, "");
  if (!normalized || normalized.includes("..") || normalized.startsWith("../")) {
    return false;
  }
  if (normalized.includes("\0")) return false;
  if (/^[a-zA-Z]:/.test(normalized) || normalized.startsWith("/")) return false;
  for (const allowed of ALLOWED_SCENE_PATHS) {
    if (allowed.endsWith("/")) {
      if (normalized.startsWith(allowed) && normalized.length > allowed.length) {
        if (!normalized.endsWith(".yaml") && !normalized.endsWith(".yml")) return false;
        if (normalized.slice(allowed.length).includes("/")) {
          const rest = normalized.slice(allowed.length);
          if (rest.split("/").length > 1) return false;
        }
        return true;
      }
    } else if (normalized === allowed) {
      return true;
    }
  }
  return false;
}
/** Minimal deterministic tar-like bundle: JSON manifest header + base64 files sorted by path. */
export async function packBundle(files: BundleFile[]): Promise<{
  bytes: Uint8Array;
  contentHash: string;
  fileHashes: Record<string, string>;
  sizeBytes: number;
}> {
  if (files.length === 0) throw new ReleaseError(400, "empty", "Bundle has no files");
  if (files.length > MAX_FILE_COUNT) {
    throw new ReleaseError(400, "too_many_files", `Max ${MAX_FILE_COUNT} files`);
  }
  const sorted = [...files].sort((a, b) => a.path.localeCompare(b.path));
  const fileHashes: Record<string, string> = {};
  let total = 0;
  for (const f of sorted) {
    if (!isAllowedScenePath(f.path)) {
      throw new ReleaseError(400, "path_rejected", `Path not allowlisted: ${f.path}`);
    }
    if (f.data.byteLength > MAX_FILE_BYTES) {
      throw new ReleaseError(400, "file_too_large", `File too large: ${f.path}`);
    }
    total += f.data.byteLength;
    fileHashes[f.path] = await sha256Bytes(f.data);
  }
  if (total > MAX_BUNDLE_BYTES) {
    throw new ReleaseError(400, "bundle_too_large", `Bundle exceeds ${MAX_BUNDLE_BYTES} bytes`);
  }
  const payload = {
    format: "orng-scene-bundle-v1",
    files: sorted.map((f) => ({
      path: f.path,
      sha256: fileHashes[f.path],
      data_b64: bytesToB64(f.data),
    })),
  };
  const json = stableStringify(payload);
  const bytes = new TextEncoder().encode(json);
  const contentHash = await sha256Hex(json);
  return { bytes, contentHash, fileHashes, sizeBytes: bytes.byteLength };
}
/**
 * Unpack + validate a scene bundle.
 * When `expectedSceneId` is set, scene.yaml scene_id/id must strictly equal it.
 */
export async function unpackBundle(
  bytes: Uint8Array,
  expectedSceneId?: string,
): Promise<{
  files: BundleFile[];
  contentHash: string;
  fileHashes: Record<string, string>;
  sceneId: string;
}> {
  if (bytes.byteLength > MAX_BUNDLE_BYTES + 1024) {
    throw new ReleaseError(400, "bundle_too_large", "Bundle too large");
  }
  let parsed: {
    format?: string;
    files?: Array<{ path: string; sha256: string; data_b64: string }>;
  };
  try {
    parsed = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new ReleaseError(400, "corrupt", "Bundle is not valid JSON");
  }
  if (parsed.format !== "orng-scene-bundle-v1" || !Array.isArray(parsed.files)) {
    throw new ReleaseError(400, "corrupt", "Unrecognized bundle format");
  }
  if (parsed.files.length > MAX_FILE_COUNT) {
    throw new ReleaseError(400, "too_many_files", `Max ${MAX_FILE_COUNT} files`);
  }
  const files: BundleFile[] = [];
  const fileHashes: Record<string, string> = {};
  const seenPaths = new Set<string>();
  let totalDecoded = 0;
  for (const entry of parsed.files) {
    if (!entry || typeof entry !== "object") {
      throw new ReleaseError(400, "corrupt", "Invalid bundle entry");
    }
    const path = String(entry.path ?? "");
    if (!isAllowedScenePath(path)) {
      throw new ReleaseError(400, "path_rejected", `Path not allowlisted: ${path}`);
    }
    if (seenPaths.has(path)) {
      throw new ReleaseError(400, "duplicate_path", `Duplicate path in bundle: ${path}`);
    }
    seenPaths.add(path);
    const data = strictB64ToBytes(String(entry.data_b64 ?? ""));
    if (data.byteLength > MAX_FILE_BYTES) {
      throw new ReleaseError(400, "file_too_large", `Decoded file too large: ${path}`);
    }
    totalDecoded += data.byteLength;
    if (totalDecoded > MAX_TOTAL_DECODED_BYTES) {
      throw new ReleaseError(400, "bundle_too_large", "Total decoded size exceeds limit");
    }
    const hash = await sha256Bytes(data);
    if (hash !== entry.sha256) {
      throw new ReleaseError(400, "corrupt", `Hash mismatch for ${path}`);
    }
    files.push({ path, data });
    fileHashes[path] = hash;
  }
  for (const required of REQUIRED_SCENE_FILES) {
    if (!seenPaths.has(required)) {
      throw new ReleaseError(400, "missing_required", `Scene bundle requires ${required}`);
    }
  }
  const sceneYaml = files.find((f) => f.path === "scene.yaml");
  if (!sceneYaml) {
    throw new ReleaseError(400, "missing_required", "Scene bundle requires scene.yaml");
  }
  const sceneId = parseSceneIdFromYaml(new TextDecoder().decode(sceneYaml.data));
  assertStrictSceneId(sceneId);
  if (expectedSceneId !== undefined) {
    assertStrictSceneId(expectedSceneId);
    if (sceneId !== expectedSceneId) {
      throw new ReleaseError(
        400,
        "scene_id_mismatch",
        `scene.yaml scene_id ${JSON.stringify(sceneId)} != route ${JSON.stringify(expectedSceneId)}`,
      );
    }
  }
  const contentHash = await sha256Hex(new TextDecoder().decode(bytes));
  return { files, contentHash, fileHashes, sceneId };
}
export async function publishRelease(
  env: Env,
  opts: {
    sceneId: string;
    sceneName: string;
    bundleBytes: Uint8Array;
    baseVersion: number | null;
    publishedBy: string;
    minAppVersion?: string | null;
    /** When false, skip DeviceDO notify (tests can call notify separately). Default true. */
    notifyAssignments?: boolean;
  },
): Promise<{
  release: ReleaseManifest;
  idempotent: boolean;
  assignments_updated: number;
}> {
  // Validate scene_id before any D1/R2/path ops.
  assertStrictSceneId(opts.sceneId);
  const unpacked = await unpackBundle(opts.bundleBytes, opts.sceneId);
  const contentHash = await sha256Hex(new TextDecoder().decode(opts.bundleBytes));
  const now = nowIso();
  await env.DB.prepare(
    `INSERT INTO scenes (scene_id, name, created_at, updated_at)
     VALUES (?, ?, ?, ?)
     ON CONFLICT(scene_id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at`,
  )
    .bind(opts.sceneId, opts.sceneName, now, now)
    .run();
  const existing = await env.DB.prepare(
    `SELECT version, content_hash, manifest_json, size_bytes FROM scene_releases
     WHERE scene_id = ? AND content_hash = ?`,
  )
    .bind(opts.sceneId, contentHash)
    .first<{ version: number; content_hash: string; manifest_json: string; size_bytes: number }>();
  if (existing) {
    // Idempotent retry of EXISTING release must still restore track_latest desired_version
    // and best-effort notify online DOs (do not skip on early return).
    const manifest = JSON.parse(existing.manifest_json) as ReleaseManifest;
    const assignmentsUpdated = await repairTrackLatestAssignments(
      env,
      opts.sceneId,
      existing.version,
      now,
    );
    if (opts.notifyAssignments !== false && assignmentsUpdated > 0) {
      await notifyTrackLatestInstallations(env, opts.sceneId, existing.version);
    }
    return { release: manifest, idempotent: true, assignments_updated: assignmentsUpdated };
  }
  const latest = await env.DB.prepare(
    `SELECT MAX(version) AS v FROM scene_releases WHERE scene_id = ?`,
  )
    .bind(opts.sceneId)
    .first<{ v: number | null }>();
  const currentVersion = latest?.v ?? 0;
  if (opts.baseVersion !== null && opts.baseVersion !== currentVersion) {
    throw new ReleaseError(
      409,
      "version_conflict",
      `base_version ${opts.baseVersion} != current ${currentVersion}`,
    );
  }
  const problems = validateSceneSemantics(unpacked.files);
  if (problems.length > 0) {
    throw new ReleaseError(400, "scene_invalid", problems.join("; "));
  }
  const version = currentVersion + 1;
  const r2Key = `scenes/${opts.sceneId}/v${version}/${contentHash}.bundle`;
  const manifest: ReleaseManifest = {
    schema: 1,
    scene_id: opts.sceneId,
    version,
    content_sha256: contentHash,
    files: unpacked.fileHashes,
    size_bytes: opts.bundleBytes.byteLength,
    min_app_version: opts.minAppVersion ?? null,
  };
  await env.SCENE_RELEASES.put(r2Key, opts.bundleBytes, {
    httpMetadata: { contentType: "application/json" },
    customMetadata: {
      scene_id: opts.sceneId,
      version: String(version),
      content_hash: contentHash,
    },
  });
  try {
    // Insert release metadata + track_latest bump as one D1 batch (transactional).
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO scene_releases
          (scene_id, version, content_hash, r2_key, manifest_json, size_bytes, published_by, published_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        opts.sceneId,
        version,
        contentHash,
        r2Key,
        JSON.stringify(manifest),
        opts.bundleBytes.byteLength,
        opts.publishedBy,
        now,
      ),
      env.DB.prepare(
        `UPDATE scene_assignments
         SET desired_version = ?, updated_at = ?
         WHERE scene_id = ? AND track_latest = 1`,
      ).bind(version, now, opts.sceneId),
    ]);
  } catch (err) {
    // On conflict: never delete another concurrent publish's R2 object.
    // Same content_hash в†’ same key belongs to the winning insert; leave it.
    const again = await env.DB.prepare(
      `SELECT version, manifest_json FROM scene_releases WHERE scene_id = ? AND content_hash = ?`,
    )
      .bind(opts.sceneId, contentHash)
      .first<{ version: number; manifest_json: string }>();
    if (again) {
      const againManifest = JSON.parse(again.manifest_json) as ReleaseManifest;
      const assignmentsUpdated = await repairTrackLatestAssignments(
        env,
        opts.sceneId,
        again.version,
        now,
      );
      if (opts.notifyAssignments !== false && assignmentsUpdated > 0) {
        await notifyTrackLatestInstallations(env, opts.sceneId, again.version);
      }
      return {
        release: againManifest,
        idempotent: true,
        assignments_updated: assignmentsUpdated,
      };
    }
    // Our insert lost to a different content_hash at this version вЂ” safe to drop our orphan object.
    await env.SCENE_RELEASES.delete(r2Key).catch(() => undefined);
    throw err;
  }
  const assignmentsUpdated = await countTrackLatestAssignments(env, opts.sceneId);
  await writeAudit(env, opts.publishedBy, "scene.publish", opts.sceneId, {
    version,
    content_hash: contentHash,
    assignments_updated: assignmentsUpdated,
  });
  if (opts.notifyAssignments !== false && assignmentsUpdated > 0) {
    await notifyTrackLatestInstallations(env, opts.sceneId, version);
  }
  return {
    release: manifest,
    idempotent: false,
    assignments_updated: assignmentsUpdated,
  };
}
/**
 * Point track_latest at this release only when it is the current latest.
 * Republishing an older content hash must not roll desired_version backward.
 * An explicit rollback is a separate assignment, not an idempotent republish.
 */
async function repairTrackLatestAssignments(
  env: Env,
  sceneId: string,
  version: number,
  now: string,
): Promise<number> {
  const latest = await env.DB.prepare(
    `SELECT MAX(version) AS v FROM scene_releases WHERE scene_id = ?`,
  )
    .bind(sceneId)
    .first<{ v: number | null }>();
  const maxVersion = Number(latest?.v ?? 0);
  if (version < maxVersion) return 0;
  await env.DB.prepare(
    `UPDATE scene_assignments
     SET desired_version = ?, updated_at = ?
     WHERE scene_id = ? AND track_latest = 1
       AND (desired_version IS NULL OR desired_version < ?)`,
  )
    .bind(version, now, sceneId, version)
    .run();
  return countTrackLatestAssignments(env, sceneId);
}
async function countTrackLatestAssignments(env: Env, sceneId: string): Promise<number> {
  const rows = await env.DB.prepare(
    `SELECT installation_id FROM scene_assignments WHERE scene_id = ? AND track_latest = 1`,
  )
    .bind(sceneId)
    .all<{ installation_id: string }>();
  return (rows.results ?? []).length;
}
/** Notify online DeviceDOs with sync_scene_release using the real D1 desired version. */
export async function notifyTrackLatestInstallations(
  env: Env,
  sceneId: string,
  version: number,
): Promise<void> {
  const rows = await env.DB.prepare(
    `SELECT installation_id, desired_version, track_latest
     FROM scene_assignments WHERE scene_id = ? AND track_latest = 1`,
  )
    .bind(sceneId)
    .all<{ installation_id: string; desired_version: number | null; track_latest: number }>();
  for (const row of rows.results ?? []) {
    const desired = row.desired_version ?? version;
    const stub = deviceStub(env, row.installation_id);
    try {
      const statusRes = await stub.fetch("https://device-do/status");
      const live = (await statusRes.json()) as { device_connected?: boolean };
      if (!live.device_connected) {
        // Offline: D1 desired_version already updated; picked up on reconnect.
        continue;
      }
      await stub.fetch(
        new Request("https://device-do/command", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            type: "sync_scene_release",
            installation_id: row.installation_id,
            source: "cloud:track_latest",
            payload: {
              scene_id: sceneId,
              version: desired,
              track_latest: true,
            },
          }),
        }),
      );
    } catch {
      /* best-effort notify */
    }
  }
}
export async function listScenes(env: Env) {
  const scenes = await env.DB.prepare(`SELECT * FROM scenes ORDER BY scene_id`).all();
  return scenes.results ?? [];
}
export async function listReleases(env: Env, sceneId: string) {
  assertStrictSceneId(sceneId);
  const res = await env.DB.prepare(
    `SELECT scene_id, version, content_hash, r2_key, manifest_json, size_bytes, published_by, published_at
     FROM scene_releases WHERE scene_id = ? ORDER BY version DESC`,
  )
    .bind(sceneId)
    .all();
  return (res.results ?? []).map((row) => ({
    ...row,
    manifest: JSON.parse(String((row as { manifest_json: string }).manifest_json)),
  }));
}
/**
 * Download ACL: check assignment BEFORE R2 get.
 * - scene:read в†’ any scene/version in catalog
 * - scene:read_assigned в†’ only assigned scene_id and desired/current/rollback window
 */
export async function assertDownloadAllowed(
  env: Env,
  opts: {
    installationId: string;
    sceneId: string;
    version: number;
    scopes: readonly string[];
    revoked: boolean;
  },
): Promise<void> {
  assertStrictSceneId(opts.sceneId);
  if (opts.revoked) {
    throw new ReleaseError(403, "revoked", "Installation revoked");
  }
  if (opts.scopes.includes("scene:read")) {
    return;
  }
  if (!opts.scopes.includes("scene:read_assigned")) {
    throw new ReleaseError(403, "forbidden", "Missing scene download scope");
  }
  const assignment = await getAssignment(env, opts.installationId);
  if (!assignment || assignment.scene_id !== opts.sceneId) {
    throw new ReleaseError(403, "forbidden", "Scene not assigned to this installation");
  }
  const inst = await env.DB.prepare(
    `SELECT current_release_version FROM installations WHERE id = ?`,
  )
    .bind(opts.installationId)
    .first<{ current_release_version: number | null }>();
  const allowed = new Set<number>();
  if (assignment.desired_version != null) allowed.add(Number(assignment.desired_version));
  if (inst?.current_release_version != null) {
    allowed.add(Number(inst.current_release_version));
  }
  const anchor = assignment.desired_version ?? inst?.current_release_version ?? null;
  if (anchor != null) {
    for (let v = Number(anchor); v >= Math.max(1, Number(anchor) - ASSIGNED_ROLLBACK_WINDOW); v--) {
      allowed.add(v);
    }
  }
  if (!allowed.has(opts.version)) {
    throw new ReleaseError(403, "forbidden", "Version not allowed for this assignment");
  }
}
export async function getReleaseBundle(
  env: Env,
  sceneId: string,
  version: number,
): Promise<{ key: string; body: R2ObjectBody; manifest: ReleaseManifest } | null> {
  assertStrictSceneId(sceneId);
  const row = await env.DB.prepare(
    `SELECT r2_key, manifest_json FROM scene_releases WHERE scene_id = ? AND version = ?`,
  )
    .bind(sceneId, version)
    .first<{ r2_key: string; manifest_json: string }>();
  if (!row) return null;
  const body = await env.SCENE_RELEASES.get(row.r2_key);
  if (!body) return null;
  return {
    key: row.r2_key,
    body,
    manifest: JSON.parse(row.manifest_json) as ReleaseManifest,
  };
}
/** ACL-aware download: asserts then fetches R2. */
export async function getReleaseBundleAuthorized(
  env: Env,
  opts: {
    installationId: string;
    sceneId: string;
    version: number;
    scopes: readonly string[];
    revoked: boolean;
  },
): Promise<{ key: string; body: R2ObjectBody; manifest: ReleaseManifest }> {
  await assertDownloadAllowed(env, opts);
  const bundle = await getReleaseBundle(env, opts.sceneId, opts.version);
  if (!bundle) throw new ReleaseError(404, "not_found", "Release not found");
  return bundle;
}
export async function assignScene(
  env: Env,
  opts: {
    installationId: string;
    sceneId: string;
    desiredVersion: number | null;
    trackLatest: boolean;
    actor: string;
  },
): Promise<{ desired_version: number | null }> {
  assertStrictSceneId(opts.sceneId);
  const scene = await env.DB.prepare(`SELECT scene_id FROM scenes WHERE scene_id = ?`)
    .bind(opts.sceneId)
    .first();
  if (!scene) throw new ReleaseError(404, "scene_not_found", "Scene not found");
  let version = opts.desiredVersion;
  if (opts.trackLatest || version === null) {
    const latest = await env.DB.prepare(
      `SELECT MAX(version) AS v FROM scene_releases WHERE scene_id = ?`,
    )
      .bind(opts.sceneId)
      .first<{ v: number | null }>();
    version = latest?.v ?? null;
  }
  await env.DB.prepare(
    `INSERT INTO scene_assignments (installation_id, scene_id, desired_version, track_latest, updated_at)
     VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(installation_id) DO UPDATE SET
       scene_id = excluded.scene_id,
       desired_version = excluded.desired_version,
       track_latest = excluded.track_latest,
       updated_at = excluded.updated_at`,
  )
    .bind(
      opts.installationId,
      opts.sceneId,
      version,
      opts.trackLatest ? 1 : 0,
      nowIso(),
    )
    .run();
  await writeAudit(env, opts.actor, "scene.assign", opts.installationId, {
    scene_id: opts.sceneId,
    desired_version: version,
    track_latest: opts.trackLatest,
  });
  return { desired_version: version };
}
export async function getAssignment(env: Env, installationId: string) {
  return (
    (await env.DB.prepare(`SELECT * FROM scene_assignments WHERE installation_id = ?`)
      .bind(installationId)
      .first<{
        installation_id: string;
        scene_id: string;
        desired_version: number | null;
        track_latest: number;
        updated_at: string;
      }>()) ?? null
  );
}
function unquoteYaml(raw: string): string {
  let value = raw.trim();
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    value = value.slice(1, -1);
  }
  return value;
}

function textOf(files: BundleFile[], path: string): string {
  const file = files.find((item) => item.path === path);
  return file ? new TextDecoder().decode(file.data) : "";
}

/** Fixture blocks under patch.yaml `fixtures:`, enough to check links and DMX ranges. */
function parsePatchFixtures(text: string): Array<{ id: string; profileId: string; start: number }> {
  const fixtures: Array<{ id: string; profileId: string; start: number }> = [];
  let inFixtures = false;
  let current: { id: string; profileId: string; start: number } | null = null;
  const flush = () => {
    if (current) fixtures.push(current);
    current = null;
  };
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    if (!inFixtures) {
      if (/^fixtures\s*:/.test(trimmed)) inFixtures = true;
      continue;
    }
    if (!/^\s/.test(line) && !trimmed.startsWith("-")) {
      flush();
      inFixtures = false;
      continue;
    }
    const idMatch = /^\s*-\s+id:\s*(.+)$/.exec(line);
    if (idMatch) {
      flush();
      current = { id: unquoteYaml(idMatch[1]!), profileId: "", start: 0 };
      continue;
    }
    if (!current) continue;
    const profileMatch = /^\s+profile_id:\s*(.+)$/.exec(line);
    if (profileMatch) current.profileId = unquoteYaml(profileMatch[1]!);
    const startMatch = /^\s+start_address:\s*(\d+)/.exec(line);
    if (startMatch) current.start = Number(startMatch[1]);
  }
  flush();
  return fixtures;
}

const SECRET_KEYS = new Set([
  "password",
  "token",
  "device_token",
  "secret",
  "private_key",
  "api_key",
  "credential",
]);

function isInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

function numberIn(value: unknown, min: number, max: number, exclusiveMin = false): boolean {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    (exclusiveMin ? value > min : value >= min) &&
    value <= max
  );
}

function checkSpatial(id: string, spatial: Record<string, unknown> | undefined, errors: string[]): void {
  if (!spatial) return;
  for (const key of ["pan_offset", "tilt_offset"]) {
    if (spatial[key] == null) continue;
    if (!numberIn(spatial[key], -0.5, 0.5)) errors.push(`${id}: ${key} поза -0.5..0.5`);
  }
  for (const key of ["pan_min", "pan_max", "tilt_min", "tilt_max", "home_pan", "home_tilt"]) {
    if (spatial[key] == null) continue;
    if (!numberIn(spatial[key], 0, 1)) errors.push(`${id}: ${key} поза 0..1`);
  }
  for (const key of ["max_pan_speed", "max_tilt_speed"]) {
    if (spatial[key] == null) continue;
    if (!numberIn(spatial[key], 0, 2, true)) {
      errors.push(`${id}: ${key} має бути більшим за 0 і не більшим за 2`);
    }
  }
  if (typeof spatial.pan_min === "number" && typeof spatial.pan_max === "number" && spatial.pan_min >= spatial.pan_max) {
    errors.push(`${id}: pan_min має бути меншим за pan_max`);
  }
  if (typeof spatial.tilt_min === "number" && typeof spatial.tilt_max === "number" && spatial.tilt_min >= spatial.tilt_max) {
    errors.push(`${id}: tilt_min має бути меншим за tilt_max`);
  }
}

function byteBagError(id: string, local: number, bag: string, values: unknown): string | null {
  if (values == null) return null;
  if (typeof values !== "object") return `${id}: канал ${local}: ${bag} має бути цілим 0..255`;
  for (const [key, value] of Object.entries(values as Record<string, unknown>)) {
    if (!isInt(value) || value < 0 || value > 255) {
      return `${id}: канал ${local}: ${bag} ${key} має бути цілим 0..255`;
    }
  }
  return null;
}

function checkProfileChannels(
  id: string,
  record: { channels?: unknown; segment_count?: unknown; capabilities?: unknown },
  footprint: number,
  errors: string[],
): void {
  const capabilities = Array.isArray(record.capabilities) ? record.capabilities.map(String) : [];
  if (capabilities.includes("segments") && record.segment_count !== 8) {
    errors.push(`${id}: segment_count має бути 8`);
  }
  const segmentCount = isInt(record.segment_count) ? record.segment_count : null;
  const channels = Array.isArray(record.channels) ? record.channels : [];
  const seen = new Set<number>();
  for (const raw of channels) {
    if (!raw || typeof raw !== "object") continue;
    const channel = raw as Record<string, unknown>;
    if (!isInt(channel.local) || channel.local < 1 || channel.local > footprint) {
      errors.push(`${id}: канал ${String(channel.local)} поза footprint ${footprint}`);
      continue;
    }
    if (seen.has(channel.local)) errors.push(`${id}: дубль каналу ${channel.local}`);
    seen.add(channel.local);
    const role = String(channel.role ?? "");
    if (role === "segment" || role === "segment_color") {
      const index = channel.segment_index;
      if (!isInt(index) || index < 1 || index > 8) {
        errors.push(`${id}: канал ${channel.local}: індекс сегмента має бути цілим 1..8`);
      } else if (segmentCount != null && segmentCount > 0 && index > segmentCount) {
        errors.push(`${id}: канал ${channel.local}: індекс сегмента поза 1..${segmentCount}`);
      }
    }
    if (role === "fixed" && (!isInt(channel.fixed_value) || channel.fixed_value < 0 || channel.fixed_value > 255)) {
      errors.push(`${id}: канал ${channel.local}: фіксоване значення має бути цілим 0..255`);
    }
    const palette = byteBagError(id, channel.local, "палітра", channel.palette);
    if (palette) errors.push(palette);
    const control = byteBagError(id, channel.local, "значення", channel.control_values);
    if (control) errors.push(control);
  }
}

function walkSecrets(node: unknown, errors: string[], path: string): void {
  if (Array.isArray(node)) {
    node.forEach((value, index) => walkSecrets(value, errors, `${path}[${index}]`));
    return;
  }
  if (!node || typeof node !== "object") return;
  for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
    if (SECRET_KEYS.has(key) && value !== null && value !== "" && value !== false) {
      errors.push(`${path}: секретне поле ${key}`);
    }
    if (key === "output_armed" && value === true) {
      errors.push(`${path}: output_armed не належить канонічній сцені`);
    }
    if (key === "hardware_verified" && value === true) {
      errors.push(`${path}: hardware_verified не належить канонічній сцені`);
    }
    if (key === "executor_installation_id" && value !== null && value !== "") {
      errors.push(`${path}: executor_installation_id не належить канонічній сцені`);
    }
    walkSecrets(value, errors, `${path}.${key}`);
  }
}

/**
 * Full scene checks before a new release becomes canonical latest.
 * Empty fixture lists stay valid. An idempotent republish of stored bytes skips this.
 * Quoted and flow YAML go through the same parser as block YAML.
 */
export function validateSceneSemantics(files: BundleFile[]): string[] {
  const errors: string[] = [];
  const parsed = new Map<string, unknown>();
  for (const file of files) {
    if (!/\.ya?ml$/.test(file.path)) continue;
    const text = new TextDecoder().decode(file.data);
    try {
      parsed.set(file.path, text.trim() ? parseYaml(text) : null);
    } catch (err) {
      errors.push(`${file.path}: YAML ${err instanceof Error ? err.message : "invalid"}`);
      continue;
    }
    walkSecrets(parsed.get(file.path), errors, file.path);
  }
  const presetIds = new Set(
    files
      .filter((file) => file.path.startsWith("presets/") && /\.ya?ml$/.test(file.path))
      .map((file) => file.path.replace(/^presets\//, "").replace(/\.ya?ml$/, "")),
  );
  const app = parsed.get("app.yaml");
  if (app && typeof app === "object" && Array.isArray((app as { pad_presets?: unknown }).pad_presets)) {
    for (const slot of (app as { pad_presets: unknown[] }).pad_presets) {
      if (!slot || slot === "NONE") continue;
      if (!presetIds.has(String(slot))) errors.push(`слот пульта ${slot} не має пресета`);
    }
  }
  const footprints = new Map<string, number>();
  const profileKinds = new Map<string, string>();
  for (const [path, doc] of parsed) {
    if (!path.startsWith("profiles/") || !doc || typeof doc !== "object") continue;
    const id = path.replace(/^profiles\//, "").replace(/\.ya?ml$/, "");
    const record = doc as {
      footprint?: unknown;
      channels?: unknown;
      kind?: unknown;
      segment_count?: unknown;
      capabilities?: unknown;
    };
    const channels = Array.isArray(record.channels) ? record.channels.length : 0;
    let footprint: number | null = null;
    if (record.footprint == null) footprint = Math.max(channels, 1);
    else if (!isInt(record.footprint) || record.footprint < 1) errors.push(`${id}: footprint має бути цілим`);
    else footprint = record.footprint;
    if (footprint != null) {
      footprints.set(id, footprint);
      if (record.kind != null) profileKinds.set(id, String(record.kind));
      checkProfileChannels(id, record, footprint, errors);
    }
  }
  const patch = parsed.get("patch.yaml");
  const fixtures =
    patch && typeof patch === "object" && Array.isArray((patch as { fixtures?: unknown }).fixtures)
      ? ((patch as { fixtures: unknown[] }).fixtures)
      : null;
  const seen = new Set<string>();
  const ranges: Array<{ id: string; start: number; end: number }> = [];
  if (fixtures) {
    for (const fixture of fixtures) {
      if (!fixture || typeof fixture !== "object" || !(fixture as { id?: unknown }).id) {
        errors.push("прилад без id");
        continue;
      }
      const row = fixture as {
        id: string;
        profile_id?: string;
        kind?: unknown;
        start_address?: unknown;
        spatial?: Record<string, unknown>;
      };
      if (seen.has(row.id)) errors.push(`дубль приладу ${row.id}`);
      seen.add(row.id);
      const footprint = row.profile_id ? footprints.get(row.profile_id) : undefined;
      if (!row.profile_id || footprint == null) {
        errors.push(`${row.id}: невідомий профіль ${row.profile_id || "?"}`);
        continue;
      }
      const profileKind = profileKinds.get(row.profile_id);
      if (row.kind != null && profileKind != null && String(row.kind) !== profileKind) {
        errors.push(`${row.id}: тип ${String(row.kind)} не збігається з профілем`);
      }
      if (!isInt(row.start_address)) {
        errors.push(`${row.id}: адреса має бути цілим числом`);
        continue;
      }
      const end = row.start_address + footprint - 1;
      if (row.start_address < 1 || end > 512) {
        errors.push(`${row.id}: адреса ${row.start_address} поза 1..512`);
        continue;
      }
      ranges.push({ id: row.id, start: row.start_address, end });
      checkSpatial(row.id, row.spatial, errors);
    }
  }
  ranges.sort((a, b) => a.start - b.start);
  for (let i = 1; i < ranges.length; i += 1) {
    const prev = ranges[i - 1]!;
    const cur = ranges[i]!;
    if (cur.start <= prev.end) errors.push(`${prev.id} і ${cur.id} перетинаються`);
  }
  const layout = parsed.get("layout.yaml");
  const layoutIds: string[] = [];
  if (layout && typeof layout === "object") {
    const raw = (layout as { fixtures?: unknown }).fixtures;
    if (Array.isArray(raw)) {
      for (const item of raw) {
        if (typeof item === "string") layoutIds.push(item);
        else if (item && typeof item === "object") {
          const row = item as { fixture_id?: unknown; id?: unknown };
          layoutIds.push(String(row.fixture_id ?? row.id ?? ""));
        }
      }
    } else if (raw && typeof raw === "object") {
      layoutIds.push(...Object.keys(raw as object));
    }
  }
  if (fixtures) {
    for (const id of layoutIds) {
      if (id && !seen.has(id)) errors.push(`схема посилається на відсутній прилад ${id}`);
    }
  }
  errors.push(...sceneCapabilityProblems(parsed));
  return errors;
}

/** Extract scene_id (or id) from minimal scene.yaml text without a full YAML dependency. */
export function parseSceneIdFromYaml(text: string): string {
  const lines = text.split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const m = /^(?:scene_id|id)\s*:\s*(.+?)\s*$/.exec(trimmed);
    if (!m) continue;
    let raw = m[1]!.trim();
    if (
      (raw.startsWith('"') && raw.endsWith('"')) ||
      (raw.startsWith("'") && raw.endsWith("'"))
    ) {
      raw = raw.slice(1, -1);
    }
    if (!raw) {
      throw new ReleaseError(400, "invalid_scene_yaml", "scene.yaml missing scene_id");
    }
    return raw;
  }
  throw new ReleaseError(400, "invalid_scene_yaml", "scene.yaml missing scene_id");
}

/** Extract display name from scene.yaml (`name:`). */
export function parseSceneNameFromYaml(text: string): string | null {
  const lines = text.split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const m = /^name\s*:\s*(.+?)\s*$/.exec(trimmed);
    if (!m) continue;
    let raw = m[1]!.trim();
    if (
      (raw.startsWith('"') && raw.endsWith('"')) ||
      (raw.startsWith("'") && raw.endsWith("'"))
    ) {
      raw = raw.slice(1, -1);
    }
    return raw || null;
  }
  return null;
}

/**
 * Extract pad_presets list from app.yaml (block or inline) without a YAML dependency.
 * Returns up to 9 slot ids; empty when missing/malformed.
 */
export function parsePadPresetsFromAppYaml(text: string): string[] {
  const lines = text.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const trimmed = (lines[i] ?? "").trim();
    if (!trimmed || trimmed.startsWith("#")) {
      i += 1;
      continue;
    }
    const inline = /^pad_presets\s*:\s*\[(.*)\]\s*$/.exec(trimmed);
    if (inline) {
      return inline[1]!
        .split(",")
        .map((p) => p.trim().replace(/^["']|["']$/g, ""))
        .filter(Boolean)
        .slice(0, 9);
    }
    if (/^pad_presets\s*:\s*$/.test(trimmed)) {
      const out: string[] = [];
      i += 1;
      while (i < lines.length) {
        const row = lines[i] ?? "";
        const rowTrim = row.trim();
        if (!rowTrim || rowTrim.startsWith("#")) {
          i += 1;
          continue;
        }
        const item = /^-\s*(.+?)\s*$/.exec(rowTrim);
        if (!item) break;
        const raw = item[1]!.trim().replace(/^["']|["']$/g, "");
        if (raw) out.push(raw);
        i += 1;
        if (out.length >= 9) break;
      }
      return out.slice(0, 9);
    }
    i += 1;
  }
  return [];
}

/** Safe operator control-surface derived from the assigned/current scene release. */
export async function buildControlSurface(
  env: Env,
  opts: {
    sceneId: string | null;
    version: number | null;
    fallbackName?: string | null;
  },
): Promise<{
  scene_id: string | null;
  scene_name: string | null;
  version: number | null;
  pad_presets: string[];
  actions: string[];
}> {
  const actions = [
    "blackout",
    "arm",
    "disarm",
    "select_preset",
    "face_toggle",
    "strobe",
    "sweep",
    "vertical_sweep",
    "speed",
    "episode_nudge",
  ];
  if (!opts.sceneId || opts.version == null) {
    return {
      scene_id: opts.sceneId,
      scene_name: opts.fallbackName ?? null,
      version: opts.version,
      pad_presets: [],
      actions,
    };
  }
  const bundle = await getReleaseBundle(env, opts.sceneId, opts.version);
  if (!bundle) {
    return {
      scene_id: opts.sceneId,
      scene_name: opts.fallbackName ?? null,
      version: opts.version,
      pad_presets: [],
      actions,
    };
  }
  const bytes = new Uint8Array(await bundle.body.arrayBuffer());
  const unpacked = await unpackBundle(bytes, opts.sceneId);
  const app = unpacked.files.find((f) => f.path === "app.yaml");
  const scene = unpacked.files.find((f) => f.path === "scene.yaml");
  const pad =
    app != null ? parsePadPresetsFromAppYaml(new TextDecoder().decode(app.data)) : [];
  const sceneName =
    scene != null
      ? parseSceneNameFromYaml(new TextDecoder().decode(scene.data))
      : null;
  return {
    scene_id: opts.sceneId,
    scene_name: sceneName ?? opts.fallbackName ?? opts.sceneId,
    version: opts.version,
    pad_presets: pad,
    actions,
  };
}
function bytesToB64(data: Uint8Array): string {
  let s = "";
  for (let i = 0; i < data.length; i++) s += String.fromCharCode(data[i]!);
  return btoa(s);
}
/** Strict base64: alphabet + padding only; reject whitespace / URL-safe variants. */
function strictB64ToBytes(b64: string): Uint8Array {
  const cleaned = b64.trim();
  if (!cleaned || !STRICT_B64_RE.test(cleaned) || cleaned.length % 4 !== 0) {
    throw new ReleaseError(400, "invalid_base64", "Invalid base64 encoding in bundle");
  }
  try {
    const binary = atob(cleaned);
    const out = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
    return out;
  } catch {
    throw new ReleaseError(400, "invalid_base64", "Invalid base64 encoding in bundle");
  }
}
function stableStringify(value: unknown): string {
  return JSON.stringify(value, (_k, v) => {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const sorted: Record<string, unknown> = {};
      for (const key of Object.keys(v as object).sort()) {
        sorted[key] = (v as Record<string, unknown>)[key];
      }
      return sorted;
    }
    return v;
  });
}
void uuid;
