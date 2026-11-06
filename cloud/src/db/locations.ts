/** Operator locations. An installation is a member, not the location itself. */

import type { Env } from "../env";
import { getAssignment } from "../scenes/releases";
import { listInstallations, type InstallationRow } from "./installations";

export interface OperatorLocation {
  id: string;
  name: string;
  scene_id: string;
  installation_id: string | null;
  installations: Array<{
    id: string;
    name: string;
    platform: string;
    revoked: boolean;
  }>;
}

function controlInstallation(
  members: InstallationRow[],
  executorId: string | null,
): InstallationRow | null {
  const live = members.filter((row) => !row.revoked_at);
  if (executorId) {
    return live.find((row) => row.id === executorId) ?? null;
  }
  return live.find((row) => row.platform === "raspberry") ?? null;
}

function describe(row: InstallationRow) {
  return {
    id: row.id,
    name: row.name,
    platform: row.platform,
    revoked: Boolean(row.revoked_at),
  };
}

/**
 * Prefer the locations table. If it is not migrated yet, one row per assigned
 * scene so a second laptop does not become a second ORNG BAR.
 */
export async function listOperatorLocations(env: Env): Promise<OperatorLocation[]> {
  const installs = await listInstallations(env);
  const sceneByInstall = new Map<string, string>();
  for (const row of installs) {
    const assignment = await getAssignment(env, row.id);
    if (assignment?.scene_id) sceneByInstall.set(row.id, assignment.scene_id);
  }

  let stored: Array<{ id: string; name: string; scene_id: string; executor_installation_id?: string | null }> = [];
  const bound = new Map<string, string>();
  try {
    try {
      const locations = await env.DB.prepare(
        `SELECT id, name, scene_id, executor_installation_id FROM locations ORDER BY name`,
      ).all<{ id: string; name: string; scene_id: string; executor_installation_id: string | null }>();
      stored = locations.results ?? [];
    } catch {
      const locations = await env.DB.prepare(
        `SELECT id, name, scene_id FROM locations ORDER BY name`,
      ).all<{ id: string; name: string; scene_id: string }>();
      stored = locations.results ?? [];
    }
    const links = await env.DB.prepare(
      `SELECT id, location_id FROM installations WHERE location_id IS NOT NULL`,
    ).all<{ id: string; location_id: string }>();
    for (const link of links.results ?? []) {
      if (link.location_id) bound.set(link.id, link.location_id);
    }
  } catch {
    stored = [];
  }

  if (stored.length === 0) {
    const byScene = new Map<string, InstallationRow[]>();
    for (const row of installs) {
      if (row.revoked_at) continue;
      const sceneId = sceneByInstall.get(row.id);
      if (!sceneId) continue;
      const members = byScene.get(sceneId) ?? [];
      members.push(row);
      byScene.set(sceneId, members);
    }
    return [...byScene.entries()].map(([sceneId, members]) => ({
      id: sceneId === "orng-bar" ? "loc-orng-bar" : `scene:${sceneId}`,
      name: sceneId === "orng-bar" ? "ORNG BAR" : sceneId,
      scene_id: sceneId,
      installation_id: controlInstallation(members, null)?.id ?? null,
      installations: members.map(describe),
    }));
  }

  return stored.map((location) => {
    const linked = installs.filter((row) => {
      if (row.revoked_at) return false;
      return bound.get(row.id) === location.id;
    });
    const executorId = location.executor_installation_id ?? null;
    const executor = executorId
      ? installs.find((row) => row.id === executorId && !row.revoked_at)
      : undefined;
    const members =
      executor && !linked.some((row) => row.id === executor.id) ? [...linked, executor] : linked;
    return {
      id: location.id,
      name: location.name,
      scene_id: location.scene_id,
      installation_id: controlInstallation(members, executorId)?.id ?? null,
      installations: members.map(describe),
    };
  });
}
