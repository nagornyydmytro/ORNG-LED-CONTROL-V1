/** Assigned scene release as text files — same bundle format, not a second model. */

import type { Env } from "../env";
import { deviceStub } from "../devices";
import {
  ReleaseError,
  assignScene,
  getAssignment,
  getReleaseBundle,
  packBundle,
  publishRelease,
  unpackBundle,
} from "./releases";

export type WorkspaceFile = { path: string; text: string };

export async function loadAssignedWorkspace(env: Env, installationId: string) {
  const assignment = await getAssignment(env, installationId);
  if (!assignment?.scene_id || assignment.desired_version == null) {
    throw new ReleaseError(404, "no_assignment", "Location has no assigned scene release");
  }
  const bundle = await getReleaseBundle(env, assignment.scene_id, assignment.desired_version);
  if (!bundle) {
    throw new ReleaseError(404, "not_found", "Assigned release bundle is missing");
  }
  const bytes = new Uint8Array(await bundle.body.arrayBuffer());
  const unpacked = await unpackBundle(bytes, assignment.scene_id);
  return {
    installation_id: installationId,
    scene_id: assignment.scene_id,
    version: assignment.desired_version,
    content_hash: unpacked.contentHash,
    track_latest: Boolean(assignment.track_latest),
    files: unpacked.files.map((file) => ({
      path: file.path,
      text: new TextDecoder().decode(file.data),
    })),
  };
}

export async function publishAssignedWorkspace(
  env: Env,
  opts: {
    installationId: string;
    sceneId: string;
    sceneName: string;
    baseVersion: number | null;
    files: WorkspaceFile[];
    actor: string;
  },
) {
  const before = await getAssignment(env, opts.installationId);
  if (before?.scene_id && before.scene_id !== opts.sceneId) {
    throw new ReleaseError(
      409,
      "scene_not_owned",
      "Installation is assigned to a different scene",
    );
  }
  const packed = await packBundle(
    opts.files.map((file) => ({
      path: file.path,
      data: new TextEncoder().encode(file.text),
    })),
  );
  const published = await publishRelease(env, {
    sceneId: opts.sceneId,
    sceneName: opts.sceneName,
    bundleBytes: packed.bytes,
    baseVersion: opts.baseVersion,
    publishedBy: opts.actor,
    notifyAssignments: true,
  });
  const currentDesired = before?.desired_version ?? null;
  if (currentDesired != null && published.release.version < currentDesired) {
    return {
      ok: true,
      status: "publishing" as const,
      scene_id: opts.sceneId,
      version: currentDesired,
      content_hash: published.release.content_sha256,
      idempotent: true,
      command_id: null,
      sync_status: "kept",
      sync_error: null,
      downgrade_ignored: true,
    };
  }
  const assigned = await assignScene(env, {
    installationId: opts.installationId,
    sceneId: opts.sceneId,
    desiredVersion: published.release.version,
    trackLatest: true,
    actor: opts.actor,
  });
  const stub = deviceStub(env, opts.installationId);
  const syncRes = await stub.fetch(
    new Request("https://device-do/command", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        type: "sync_scene_release",
        installation_id: opts.installationId,
        actor: opts.actor,
        payload: {
          scene_id: opts.sceneId,
          version: assigned.desired_version,
          track_latest: true,
        },
      }),
    }),
  );
  const sync = (await syncRes.json()) as {
    ok?: boolean;
    command_id?: string;
    status?: string;
    error?: string;
  };
  return {
    ok: sync.ok !== false,
    status: "publishing" as const,
    scene_id: opts.sceneId,
    version: published.release.version,
    content_hash: published.release.content_sha256,
    idempotent: published.idempotent,
    command_id: sync.command_id ?? null,
    sync_status: sync.status ?? "accepted",
    sync_error: sync.error ?? null,
    downgrade_ignored: false,
  };
}

/** Canonical scene for a location. The executor may be offline or absent. */
export async function loadLocationWorkspace(env: Env, locationId: string) {
  const location = await env.DB.prepare(
    `SELECT id, name, scene_id FROM locations WHERE id = ?`,
  )
    .bind(locationId)
    .first<{ id: string; name: string; scene_id: string }>();
  if (!location?.scene_id) {
    throw new ReleaseError(404, "no_location", "Location has no canonical scene");
  }
  const latest = await env.DB.prepare(
    `SELECT MAX(version) AS v FROM scene_releases WHERE scene_id = ?`,
  )
    .bind(location.scene_id)
    .first<{ v: number | null }>();
  const version = latest?.v ?? null;
  if (version == null) {
    throw new ReleaseError(404, "no_release", "Location scene has no published release");
  }
  const bundle = await getReleaseBundle(env, location.scene_id, version);
  if (!bundle) {
    throw new ReleaseError(404, "not_found", "Location release bundle is missing");
  }
  const bytes = new Uint8Array(await bundle.body.arrayBuffer());
  const unpacked = await unpackBundle(bytes, location.scene_id);
  return {
    installation_id: null,
    location_id: location.id,
    scene_id: location.scene_id,
    scene_name: location.name,
    version,
    content_hash: unpacked.contentHash,
    track_latest: true,
    files: unpacked.files.map((file) => ({
      path: file.path,
      text: new TextDecoder().decode(file.data),
    })),
  };
}

/**
 * Publish the location document. Assign and sync only an explicit executor.
 * Republishing older bytes does not move that executor backward.
 */
export async function publishLocationWorkspace(
  env: Env,
  opts: {
    locationId: string;
    sceneId: string;
    sceneName: string;
    baseVersion: number | null;
    files: WorkspaceFile[];
    actor: string;
    executorInstallationId: string | null;
  },
) {
  const location = await env.DB.prepare(`SELECT id, name, scene_id FROM locations WHERE id = ?`)
    .bind(opts.locationId)
    .first<{ id: string; scene_id: string | null }>();
  if (location?.scene_id && location.scene_id !== opts.sceneId) {
    throw new ReleaseError(409, "scene_not_owned", "Location does not own this scene");
  }
  if (opts.executorInstallationId) {
    return publishAssignedWorkspace(env, {
      installationId: opts.executorInstallationId,
      sceneId: opts.sceneId,
      sceneName: opts.sceneName,
      baseVersion: opts.baseVersion,
      files: opts.files,
      actor: opts.actor,
    });
  }
  const packed = await packBundle(
    opts.files.map((file) => ({
      path: file.path,
      data: new TextEncoder().encode(file.text),
    })),
  );
  const published = await publishRelease(env, {
    sceneId: opts.sceneId,
    sceneName: opts.sceneName,
    bundleBytes: packed.bytes,
    baseVersion: opts.baseVersion,
    publishedBy: opts.actor,
    notifyAssignments: true,
  });
  return {
    ok: true,
    status: "publishing" as const,
    scene_id: opts.sceneId,
    version: published.release.version,
    content_hash: published.release.content_sha256,
    idempotent: published.idempotent,
    command_id: null,
    sync_status: "no_executor",
    sync_error: null,
    downgrade_ignored: false,
  };
}
