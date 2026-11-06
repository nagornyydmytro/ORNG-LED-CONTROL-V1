/** Desktop working copy. YAML on disk is the cache; publish goes to the same scene release as the web. */

import type { PresetDocument } from "../api/presets";
import type { ChannelRoleOption } from "../api/setup";
import { FALLBACK_CHANNEL_ROLES } from "../api/setup";
import type { AutoCatalog, AutoPreviewClip, CommandAck, PresetInfo, StageLayout } from "../vite-env";
import { sceneSyncDetail, sceneSyncPhase, type ScenePort } from "./scenePort";

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown; error?: { code?: string } };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
      if (body.error?.code) detail = `${body.error.code}: ${detail}`;
    } catch {
      /* plain text */
    }
    const error = new Error(detail);
    (error as Error & { status?: number }).status = response.status;
    throw error;
  }
  return (await response.json()) as T;
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  return parseJson(await fetch(path, init));
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function markDraft() {
  publishGeneration += 1;
  sceneSyncPhase.value = "draft";
  sceneSyncDetail.value = "Ще не опубліковано";
}

let publishGeneration = 0;

async function draftBase(): Promise<number | null> {
  const meta = await send<{ base_version?: number | null }>("/api/scene-draft").catch(() => null);
  if (typeof meta?.base_version === "number") return meta.base_version;
  const status = await send<{
    sync?: { last_synced_version?: number | null; scene_id?: string };
  }>("/api/cloud/status").catch(() => null);
  const synced = status?.sync?.last_synced_version;
  return typeof synced === "number" ? synced : null;
}

export function createLocalScenePort(): ScenePort {
  return {
    hardwarePreview: true,
    async listPresets() {
      const body = await send<{ presets: PresetInfo[] }>("/api/scene-draft/presets");
      return body.presets;
    },
    async fetchPresetSummaries() {
      return this.listPresets();
    },
    getPreset(id) {
      return send<PresetDocument>(`/api/scene-draft/presets/${encodeURIComponent(id)}`);
    },
    async createCustomPreset(id, label) {
      const ack = await send<CommandAck>(
        "/api/scene-draft/presets",
        jsonInit("POST", { id, label, base_version: await draftBase() }),
      );
      markDraft();
      return ack;
    },
    async savePreset(id, preset) {
      const ack = await send<CommandAck>(
        `/api/scene-draft/presets/${encodeURIComponent(id)}`,
        jsonInit("PUT", { preset, base_version: await draftBase() }),
      );
      markDraft();
      return ack;
    },
    async renamePreset(id, label) {
      const ack = await send<CommandAck>(
        `/api/scene-draft/presets/${encodeURIComponent(id)}/rename`,
        jsonInit("POST", { label, base_version: await draftBase() }),
      );
      markDraft();
      return ack;
    },
    async duplicatePreset(id, newId, label) {
      const ack = await send<CommandAck>(
        `/api/scene-draft/presets/${encodeURIComponent(id)}/duplicate`,
        jsonInit("POST", { new_id: newId, label, base_version: await draftBase() }),
      );
      markDraft();
      return ack;
    },
    async deletePreset(id) {
      const ack = await send<CommandAck>(
        `/api/scene-draft/presets/${encodeURIComponent(id)}`,
        jsonInit("DELETE", { base_version: await draftBase() }),
      );
      markDraft();
      return ack;
    },
    previewPreset(id, speed = 10) {
      return send<CommandAck>(
        `/api/presets/${encodeURIComponent(id)}/preview`,
        jsonInit("POST", { speed }),
      );
    },
    startEditorEpisodePreview(body) {
      return send<CommandAck>("/api/presets/editor-preview/start", jsonInit("POST", body));
    },
    stopEditorEpisodePreview() {
      return send<CommandAck>("/api/presets/editor-preview/stop", jsonInit("POST", {}));
    },
    fetchAppConfig() {
      return send<Record<string, unknown>>("/api/scene-draft/app");
    },
    fetchPatch() {
      return send<Record<string, unknown>>("/api/scene-draft/patch");
    },
    fetchProfiles() {
      return send<Record<string, Record<string, unknown>>>("/api/scene-draft/profiles");
    },
    fetchLayout() {
      return send<Record<string, unknown>>("/api/scene-draft/layout");
    },
    fetchReadiness() {
      return send<Record<string, unknown>>("/api/setup/readiness");
    },
    validatePatch(patch) {
      return send<{ ok: boolean; errors: string[] }>(
        "/api/setup/validate-patch",
        jsonInit("POST", { patch }),
      );
    },
    async saveAppConfig(config) {
      const ack = await send<CommandAck>(
        "/api/scene-draft/app",
        jsonInit("PUT", { config, base_version: await draftBase() }),
      );
      markDraft();
      return ack;
    },
    async savePatch(patch) {
      const ack = await send<CommandAck>(
        "/api/scene-draft/patch",
        jsonInit("PUT", { patch, base_version: await draftBase() }),
      );
      markDraft();
      return ack;
    },
    async saveProfile(profileId, profile) {
      const ack = await send<CommandAck>(
        `/api/scene-draft/profiles/${encodeURIComponent(profileId)}`,
        jsonInit("PUT", { profile, base_version: await draftBase() }),
      );
      markDraft();
      return ack;
    },
    async saveLayout(layout) {
      const ack = await send<CommandAck>(
        "/api/scene-draft/layout",
        jsonInit("PUT", { layout, base_version: await draftBase() }),
      );
      markDraft();
      return ack;
    },
    async setupPost(path, body = {}) {
      if (path === "beam-calibration/save") {
        const ack = await send<CommandAck>(
          "/api/scene-draft/beam-calibration",
          jsonInit("POST", { ...body, base_version: await draftBase() }),
        );
        markDraft();
        return ack;
      }
      const ack = await send<CommandAck>(`/api/setup/${path}`, jsonInit("POST", body));
      return ack;
    },
    async fetchChannelRoles(): Promise<ChannelRoleOption[]> {
      try {
        const body = await send<{ roles: ChannelRoleOption[] }>("/api/setup/channel-roles");
        if (Array.isArray(body.roles) && body.roles.length > 0) return body.roles;
      } catch {
        /* catalog fallback */
      }
      return FALLBACK_CHANNEL_ROLES;
    },
    fetchStageLayout() {
      return send<StageLayout>("/api/stage/layout");
    },
    listAutoCatalog() {
      return send<AutoCatalog>("/api/auto/catalog");
    },
    previewAutoClip(query) {
      const params = new URLSearchParams({
        look: query.look,
        cycle_beats: String(query.cycleBeats),
        palette: query.palette,
        intensity: query.intensity,
        bpm: String(query.bpm),
      });
      if (query.frames && query.frames > 0) params.set("frames", String(query.frames));
      return send<AutoPreviewClip>(`/api/auto/preview-clip?${params.toString()}`);
    },
    async publishForLocation() {
      const status = await send<{
        sync?: { last_synced_version?: number | null; scene_id?: string };
      }>("/api/cloud/status").catch(() => null);
      const base = await draftBase();
      const generation = ++publishGeneration;
      sceneSyncPhase.value = "publishing";
      sceneSyncDetail.value = null;
      try {
        const published = await send<{ draft_retained?: boolean }>("/api/cloud/publish", jsonInit("POST", {
          scene_id: status?.sync?.scene_id,
          base_version: base,
        }));
        if (generation !== publishGeneration || published.draft_retained) {
          sceneSyncPhase.value = "draft";
          sceneSyncDetail.value = "Є нові зміни, їх ще не опубліковано";
          return;
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Публікація не вдалася";
        if (/version_conflict|409|base_version|draft_base/i.test(message)) {
          sceneSyncPhase.value = "conflict";
          sceneSyncDetail.value = "Чернетку збережено. Хтось уже опублікував новішу версію.";
        } else {
          sceneSyncPhase.value = "failed";
          sceneSyncDetail.value = message;
        }
        throw err;
      }
      if (generation !== publishGeneration) {
        sceneSyncPhase.value = "draft";
        sceneSyncDetail.value = "Є нові зміни, їх ще не опубліковано";
        return;
      }
      sceneSyncPhase.value = "published";
      const synced = await send<{ status?: string }>("/api/cloud/sync", jsonInit("POST", {})).catch(
        () => ({ status: "update_available" }),
      );
      if (synced.status === "synced" || synced.status === "applied") {
        sceneSyncPhase.value = "synced";
        sceneSyncDetail.value = null;
        return;
      }
      sceneSyncPhase.value = "waiting";
      sceneSyncDetail.value = "Опубліковано. Застосування чекає безпечного стану.";
    },
  };
}

/** Pull the canonical release when it is safe. Offline keeps the last cache. */
export async function pullCanonicalScene(): Promise<void> {
  try {
    const body = await send<{ status?: string }>("/api/cloud/sync", jsonInit("POST", {}));
    const status = String(body.status ?? "");
    if (status === "synced" || status === "applied") {
      sceneSyncPhase.value = "synced";
      sceneSyncDetail.value = null;
    } else if (status === "update_available") {
      sceneSyncPhase.value = "waiting";
      sceneSyncDetail.value = "Є новіша версія. Застосування чекає безпечного стану.";
    } else if (status === "conflict" || status === "dirty") {
      sceneSyncPhase.value = "draft";
      sceneSyncDetail.value = "Ще не опубліковано";
    }
  } catch {
    if (sceneSyncPhase.value === "loading") {
      sceneSyncPhase.value = "idle";
      sceneSyncDetail.value = null;
    }
  }
}
