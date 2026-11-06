import type { Ref } from "vue";
import { ref } from "vue";
import type { PresetDocument } from "../api/presets";
import type { PresetInfo } from "../vite-env";
import type { ChannelRoleOption } from "../api/setup";
import type {
  AutoCatalog,
  AutoPreviewClip,
  AutoPreviewQuery,
  CommandAck,
  PreviewClip,
  StageLayout,
} from "../vite-env";

export type SceneSyncPhase =
  | "idle"
  | "loading"
  | "draft"
  | "saving"
  | "publishing"
  | "published"
  | "syncing"
  | "waiting"
  | "synced"
  | "failed"
  | "conflict";

export interface ScenePort {
  listPresets(): Promise<PresetInfo[]>;
  getPreset(id: string): Promise<PresetDocument>;
  createCustomPreset(id: string, label: string): Promise<CommandAck>;
  savePreset(id: string, preset: PresetDocument): Promise<CommandAck>;
  renamePreset(id: string, label: string): Promise<CommandAck>;
  duplicatePreset(id: string, newId: string, label?: string): Promise<CommandAck>;
  deletePreset(id: string): Promise<CommandAck>;
  previewPreset(id: string, speed?: number): Promise<CommandAck>;
  startEditorEpisodePreview(body: {
    preset_id: string;
    preset_label: string;
    episode_index: number;
    episode: PresetDocument["episodes"][number];
  }): Promise<CommandAck>;
  stopEditorEpisodePreview(): Promise<CommandAck>;
  fetchAppConfig(): Promise<Record<string, unknown>>;
  fetchPatch(): Promise<Record<string, unknown>>;
  fetchProfiles(): Promise<Record<string, Record<string, unknown>>>;
  fetchLayout(): Promise<Record<string, unknown>>;
  fetchReadiness(): Promise<Record<string, unknown>>;
  validatePatch(patch: Record<string, unknown>): Promise<{ ok: boolean; errors: string[] }>;
  saveAppConfig(config: Record<string, unknown>): Promise<CommandAck>;
  savePatch(patch: Record<string, unknown>): Promise<CommandAck>;
  saveProfile(profileId: string, profile: Record<string, unknown>): Promise<CommandAck>;
  saveLayout(layout: Record<string, unknown>): Promise<CommandAck>;
  setupPost(path: string, body?: Record<string, unknown>): Promise<CommandAck>;
  fetchChannelRoles(): Promise<ChannelRoleOption[]>;
  fetchStageLayout(): Promise<StageLayout>;
  fetchPresetSummaries(): Promise<PresetInfo[]>;
  /** Publish the working copy into the canonical scene release. */
  publishForLocation?(): Promise<void>;
  /** Cloud editing cannot drive the room. Desktop preview stays on the local renderer. */
  hardwarePreview?: boolean;
  /** Isolated preview. Absent on desktop, which uses the local renderer. */
  previewClip?(
    id: string,
    seconds?: number,
    fps?: number,
    startS?: number,
  ): Promise<PreviewClip>;
  listAutoCatalog?(): Promise<AutoCatalog>;
  previewAutoClip?(query: AutoPreviewQuery): Promise<AutoPreviewClip>;
}

/** One short status. Hashes stay off this label. */
export function locationStatusLabel(phase: SceneSyncPhase): string | null {
  if (phase === "draft") return "Чернетка";
  if (phase === "saving" || phase === "publishing" || phase === "syncing") return "Оновлюється";
  if (phase === "waiting") return "Опубліковано, ще не на приладі";
  if (phase === "published") return "Опубліковано";
  if (phase === "synced") return "На приладі";
  if (phase === "conflict" || phase === "failed") return "Потрібна увага";
  return null;
}

/** After a page save. Draft, publish, and executor apply stay different sentences. */
export function savedLocationMessage(): string {
  const phase = sceneSyncPhase.value;
  const detail = sceneSyncDetail.value;
  if (phase === "synced") return "Опубліковано і застосовано на приладі";
  if (phase === "published" || phase === "waiting") {
    return "Опубліковано. На приладі ще не застосовано";
  }
  if (phase === "draft") return detail || "Чернетку збережено. Нові правки ще не опубліковано";
  if (phase === "conflict" || phase === "failed") return detail || "Публікація не вдалася";
  return "Чернетку збережено";
}

let sceneEpoch = 0;
let backend: "local" | "cloud" = "local";

export function currentSceneEpoch(): number {
  return sceneEpoch;
}

export function useLocalSceneBackend(): void {
  backend = "local";
}

export function useCloudSceneBackend(): void {
  backend = "cloud";
}

export class SceneUnavailableError extends Error {
  constructor() {
    super("Сцена ще не готова");
    this.name = "SceneUnavailableError";
  }
}

let installed: ScenePort | null = null;

export const sceneSyncPhase: Ref<SceneSyncPhase> = ref("idle");
export const sceneSyncDetail: Ref<string | null> = ref(null);
export const scenePortReady: Ref<boolean> = ref(false);

export function installScenePort(port: ScenePort | null): void {
  sceneEpoch += 1;
  installed = port;
  scenePortReady.value = port != null;
  sceneSyncPhase.value = port ? "loading" : "idle";
  sceneSyncDetail.value = null;
}

export function activeScenePort(): ScenePort | null {
  return installed;
}

/** Cloud pages must wait for the location port. Desktop may still use the local API. */
export function takeScenePort(): ScenePort | null {
  if (installed) return installed;
  if (backend === "cloud") throw new SceneUnavailableError();
  return null;
}

export async function publishLocationDraft(): Promise<void> {
  const port = takeScenePort();
  if (port?.publishForLocation) await port.publishForLocation();
}
