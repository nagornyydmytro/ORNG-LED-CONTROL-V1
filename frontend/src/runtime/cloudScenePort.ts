import { parse, stringify } from "yaml";
import {
  adminAutoCatalog,
  adminAutoPreview,
  adminLoadLocationWorkspace,
  adminLoadSceneWorkspace,
  adminPublishLocationWorkspace,
  adminPublishSceneWorkspace,
  type SceneWorkspaceFile,
} from "../api/adminCloud";
import { FALLBACK_CHANNEL_ROLES, type ChannelRoleOption } from "../api/setup";
import type { PresetDocument } from "../api/presets";
import type { AutoCatalog, AutoPreviewClip, CommandAck, PreviewClip, StageLayout } from "../vite-env";
import type { PresetInfo } from "../vite-env";
import { sendCalibrationCommand } from "./calibrationChannel";
import { currentSceneEpoch, sceneSyncDetail, sceneSyncPhase, type ScenePort } from "./scenePort";
import { mixCatalogStamps, mixEpisodeStamp, mixSceneStamp, sliceMixCatalog } from "./mixCatalogClip";
import { buildPresetPreviewClip } from "./presetPreviewSim";
import { sceneLookErrors, validatePadSlots, validatePatchShape } from "./sceneValidate";

type LiveSnap = {
  last_synced_hash?: string | null;
  sync_status?: string | null;
  current_release_version?: number | null;
  desired_scene_version?: number | null;
};

const EMPTY_ACK = {
  ok: true,
  idempotent_replay: false,
  state: {},
} as CommandAck;

function bundleLookError(snapshot: Map<string, string>): string | null {
  let patch: Record<string, unknown> = {};
  const profiles: Record<string, Record<string, unknown>> = {};
  const presets: Array<{ id: string; episodes?: unknown }> = [];
  for (const [path, text] of snapshot) {
    if (!path.endsWith(".yaml") && !path.endsWith(".yml")) continue;
    let doc: unknown = null;
    try {
      doc = text.trim() ? parse(text) : null;
    } catch {
      continue;
    }
    if (!doc || typeof doc !== "object") continue;
    const row = doc as Record<string, unknown>;
    if (path === "patch.yaml") patch = row;
    else if (path.startsWith("profiles/")) {
      profiles[path.slice("profiles/".length).replace(/\.ya?ml$/, "")] = row;
    } else if (path.startsWith("presets/")) {
      presets.push({
        id: path.slice("presets/".length).replace(/\.ya?ml$/, ""),
        episodes: row.episodes,
      });
    }
  }
  const fixtures = Array.isArray(patch.fixtures)
    ? (patch.fixtures as Array<Record<string, unknown>>)
    : [];
  return sceneLookErrors({ fixtures, profiles, presets })[0] ?? null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function presetInfo(doc: PresetDocument): PresetInfo {
  const episodes = doc.episodes ?? [];
  return {
    id: doc.id,
    label: doc.label,
    builtin: doc.builtin,
    hardware_tuned: doc.hardware_tuned,
    episode_count: episodes.length,
    total_duration_s: episodes.reduce((sum, ep) => sum + Number(ep.duration_s || 0), 0),
  };
}

export function draftStorageKey(accountId: string, locationId: string, sceneId: string): string {
  return `orng-scene-draft:v2:${encodeURIComponent(accountId)}:${encodeURIComponent(locationId)}:${encodeURIComponent(sceneId)}`;
}

export function legacyDraftStorageKey(locationId: string): string {
  return `orng-scene-draft:${locationId}`;
}

/** A pre-account draft has no proven owner. It stays here until someone claims it. */
export function unclaimedDraftKey(locationId: string, sceneId: string): string {
  return `orng-scene-draft:unclaimed:${encodeURIComponent(locationId)}:${encodeURIComponent(sceneId)}`;
}

export function draftRecoveryKey(accountId: string, locationId: string, sceneId: string): string {
  return `orng-scene-draft:recovery:v2:${encodeURIComponent(accountId)}:${encodeURIComponent(locationId)}:${encodeURIComponent(sceneId)}`;
}

type StoredDraft = {
  files?: Array<[string, string]>;
  version?: number | null;
  sceneId?: string;
  revision?: number;
  token?: string;
  owner?: string | null;
};

let draftBarrier: (() => Promise<void>) | null = null;

/** Test seam. Two saves can pause between the revision check and the write. */
export function setDraftBarrierForTests(barrier: (() => Promise<void>) | null): void {
  draftBarrier = barrier;
}

export function claimUnclaimedDraft(accountId: string, locationId: string, sceneId: string): boolean {
  const parked = unclaimedDraftKey(locationId, sceneId);
  const raw = localStorage.getItem(parked);
  if (!raw) return false;
  const dest = draftStorageKey(accountId, locationId, sceneId);
  if (localStorage.getItem(dest)) return false;
  localStorage.setItem(dest, raw);
  localStorage.removeItem(parked);
  return true;
}

export function createCloudScenePort(opts: {
  installationId: string | null;
  locationId?: string | null;
  sceneId?: string | null;
  accountId?: string | null;
  live: () => LiveSnap;
}): ScenePort {
  let files = new Map<string, string>();
  let sceneId = opts.sceneId?.trim() || "";
  let sceneName = "";
  let version: number | null = null;
  let contentHash = "";
  let loaded = false;
  let boundEpoch: number | null = null;

  function mine(): boolean {
    if (boundEpoch === null) boundEpoch = currentSceneEpoch();
    return boundEpoch === currentSceneEpoch();
  }

  function readDoc(path: string): Record<string, unknown> {
    const text = files.get(path);
    if (!text) throw new Error(`У сцені немає ${path}`);
    return asRecord(parse(text));
  }

  function writeDoc(path: string, doc: Record<string, unknown>) {
    files.set(path, stringify(doc));
  }

  function presetPath(id: string): string {
    return `presets/${id}.yaml`;
  }

  function listPresetDocs(): PresetDocument[] {
    const docs: PresetDocument[] = [];
    for (const [path, text] of files) {
      if (!path.startsWith("presets/") || !path.endsWith(".yaml")) continue;
      docs.push(parse(text) as PresetDocument);
    }
    docs.sort((a, b) => a.id.localeCompare(b.id));
    return docs;
  }

  let storedRevision = 0;
  let committedToken = "";
  const writerId = Math.random().toString(36).slice(2);

  function scopeIds(): { account: string; location: string; scene: string } {
    return {
      account: opts.accountId?.trim() || "account",
      location: opts.locationId?.trim() || "desk",
      scene: opts.sceneId?.trim() || sceneId,
    };
  }

  function storageKey(): string {
    const scope = scopeIds();
    return draftStorageKey(scope.account, scope.location, scope.scene);
  }

  function readStored(): StoredDraft | null {
    try {
      const key = storageKey();
      let raw = localStorage.getItem(key);
      if (!raw) raw = migrateLegacy();
      if (!raw) return null;
      return JSON.parse(raw) as StoredDraft;
    } catch {
      return null;
    }
  }

  function migrateLegacy(): string | null {
    const scope = scopeIds();
    const legacyKey = legacyDraftStorageKey(scope.location);
    const raw = localStorage.getItem(legacyKey);
    if (!raw) return null;
    let parsed: StoredDraft;
    try {
      parsed = JSON.parse(raw) as StoredDraft;
    } catch {
      return null;
    }
    if (parsed.sceneId && parsed.sceneId !== scope.scene) return null;
    const scene = parsed.sceneId || scope.scene;
    const parked = unclaimedDraftKey(scope.location, scene);
    if (!localStorage.getItem(parked)) {
      localStorage.setItem(parked, JSON.stringify({ ...parsed, owner: null }));
      localStorage.removeItem(legacyKey);
    }
    return null;
  }

  function parkRecovery(snapshot: StoredDraft) {
    const scope = scopeIds();
    const key = draftRecoveryKey(scope.account, scope.location, scope.scene);
    let rows: StoredDraft[] = [];
    try {
      const raw = localStorage.getItem(key);
      if (raw) rows = JSON.parse(raw) as StoredDraft[];
    } catch {
      rows = [];
    }
    rows.push(snapshot);
    if (rows.length > 8) rows = rows.slice(rows.length - 8);
    try {
      localStorage.setItem(key, JSON.stringify(rows));
    } catch {
      /* private mode */
    }
  }

  async function persistDraft(): Promise<boolean> {
    if (draftBarrier) await draftBarrier();
    const existing = readStored();
    if (
      existing &&
      typeof existing.revision === "number" &&
      existing.revision !== storedRevision
    ) {
      parkRecovery({
        sceneId,
        version,
        revision: storedRevision,
        token: committedToken,
        files: [...files.entries()],
      });
      sceneSyncPhase.value = "conflict";
      sceneSyncDetail.value = "Чернетку в іншій вкладці не перезаписано.";
      return false;
    }
    const next = storedRevision + 1;
    const token = `${writerId}:${next}`;
    const record: StoredDraft = {
      sceneId,
      version,
      revision: next,
      token,
      files: [...files.entries()],
    };
    try {
      localStorage.setItem(storageKey(), JSON.stringify(record));
    } catch {
      return false;
    }
    if (draftBarrier) await draftBarrier();
    const after = readStored();
    if (!after || after.token !== token) {
      parkRecovery(record);
      sceneSyncPhase.value = "conflict";
      sceneSyncDetail.value = "Чернетку в іншій вкладці не перезаписано.";
      return false;
    }
    storedRevision = next;
    committedToken = token;
    return true;
  }

  function restoreDraft(): boolean {
    const saved = readStored();
    if (!saved || !Array.isArray(saved.files) || saved.files.length === 0) return false;
    if (
      loaded &&
      sceneSyncPhase.value === "draft" &&
      typeof saved.revision === "number" &&
      saved.revision > storedRevision
    ) {
      sceneSyncPhase.value = "conflict";
      sceneSyncDetail.value = "Чернетку в іншій вкладці не перезаписано.";
      return true;
    }
    files = new Map(saved.files);
    if (typeof saved.version === "number") version = saved.version;
    storedRevision = typeof saved.revision === "number" ? saved.revision : 0;
    loaded = true;
    sceneSyncPhase.value = "draft";
    sceneSyncDetail.value = "Ще не опубліковано";
    return true;
  }

  function canonicalVersion(): number | null {
    const live = opts.live();
    return live.desired_scene_version ?? live.current_release_version ?? null;
  }

  async function ensureLoaded() {
    if (!mine()) return;
    const remoteVersion = canonicalVersion();
    const saved = readStored();
    // Draft base older than the live release is leftover after a newer
    // publish (e.g. Mix grew from 27→42). Park it and load the workspace.
    if (
      saved &&
      Array.isArray(saved.files) &&
      saved.files.length > 0 &&
      remoteVersion != null &&
      typeof saved.version === "number" &&
      saved.version < remoteVersion
    ) {
      parkRecovery(saved);
      try {
        localStorage.removeItem(storageKey());
      } catch {
        /* private mode */
      }
    } else if (restoreDraft()) {
      return;
    }
    const stale =
      loaded &&
      remoteVersion != null &&
      version != null &&
      remoteVersion !== version &&
      sceneSyncPhase.value !== "draft" &&
      sceneSyncPhase.value !== "conflict" &&
      sceneSyncPhase.value !== "failed" &&
      sceneSyncPhase.value !== "publishing";
    if (loaded && !stale) return;
    sceneSyncPhase.value = "loading";
    const workspace = opts.locationId
      ? await adminLoadLocationWorkspace(opts.locationId)
      : await adminLoadSceneWorkspace(opts.installationId ?? "");
    if (!mine()) return;
    files = new Map(workspace.files.map((file) => [file.path, file.text]));
    sceneId = workspace.scene_id;
    version = workspace.version;
    contentHash = workspace.content_hash;
    const scene = readDoc("scene.yaml");
    sceneName = String(scene.name ?? sceneId);
    loaded = true;
    const liveNow = opts.live();
    const applied =
      liveNow.last_synced_hash === contentHash &&
      (liveNow.sync_status === "synced" || liveNow.sync_status === "applied");
    const waiting =
      !applied &&
      liveNow.desired_scene_version != null &&
      liveNow.current_release_version != null &&
      liveNow.desired_scene_version !== liveNow.current_release_version;
    sceneSyncPhase.value = applied ? "synced" : waiting ? "waiting" : "idle";
    sceneSyncDetail.value = waiting ? "Опубліковано. Застосування чекає безпечного стану." : null;
  }

  let editGeneration = 0;

  function markDraft() {
    if (!mine()) return Promise.resolve();
    editGeneration += 1;
    sceneSyncPhase.value = "draft";
    sceneSyncDetail.value = "Ще не опубліковано";
    return persistDraft();
  }

  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey() || !event.newValue) return;
    let saved: StoredDraft;
    try {
      saved = JSON.parse(event.newValue) as StoredDraft;
    } catch {
      return;
    }
    if (!saved.token || saved.token === committedToken) return;
    if (typeof saved.revision === "number" && saved.revision < storedRevision) return;
    parkRecovery({
      sceneId,
      version,
      revision: storedRevision,
      token: committedToken,
      files: [...files.entries()],
    });
    sceneSyncPhase.value = "conflict";
    sceneSyncDetail.value = "Чернетку в іншій вкладці не перезаписано.";
  });

  async function pushCanonical() {
    await ensureLoaded();
    if (!mine()) return;
    if (!sceneId) throw new Error("Локація не має своєї сцени");
    const snapshot = new Map(files);
    const blocked = bundleLookError(snapshot);
    if (blocked) {
      sceneSyncPhase.value = "failed";
      sceneSyncDetail.value = blocked;
      throw new Error(blocked);
    }
    const baseVersion = version;
    const bodyFiles: SceneWorkspaceFile[] = [...snapshot.entries()].map(([path, text]) => ({
      path,
      text,
    }));
    const generation = editGeneration;
    const publishedRevision = storedRevision;
    const publishedToken = committedToken;
    sceneSyncPhase.value = "publishing";
    sceneSyncDetail.value = null;
    let published;
    try {
      published = opts.locationId
        ? await adminPublishLocationWorkspace(opts.locationId, {
            scene_id: sceneId,
            scene_name: sceneName,
            base_version: baseVersion,
            files: bodyFiles,
          })
        : await adminPublishSceneWorkspace(opts.installationId ?? "", {
            scene_id: sceneId,
            scene_name: sceneName,
            base_version: baseVersion,
            files: bodyFiles,
          });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не вдалося опублікувати сцену";
      if (/version_conflict|409|base_version/i.test(message)) {
        sceneSyncPhase.value = "conflict";
        sceneSyncDetail.value = "Чернетку збережено. Хтось уже опублікував новішу версію.";
      } else {
        sceneSyncPhase.value = "failed";
        sceneSyncDetail.value = message;
      }
      throw err;
    }
    if (!mine()) return;
    version = published.version;
    const saved = readStored();
    const kept =
      saved != null &&
      typeof saved.revision === "number" &&
      (saved.revision !== publishedRevision ||
        (publishedToken !== "" && saved.token !== publishedToken));
    if (kept) {
      if (Array.isArray(saved.files)) files = new Map(saved.files);
      storedRevision = saved.revision ?? storedRevision;
      committedToken = saved.token ?? committedToken;
      sceneSyncPhase.value = "draft";
      sceneSyncDetail.value = "Чернетку в іншій вкладці не перезаписано.";
      return;
    }
    if (generation !== editGeneration) {
      sceneSyncPhase.value = "draft";
      sceneSyncDetail.value = "Є нові зміни, їх ще не опубліковано";
      await persistDraft();
      return;
    }
    contentHash = published.content_hash;
    try {
      localStorage.removeItem(storageKey());
    } catch {
      /* ignore */
    }
    storedRevision = 0;
    committedToken = "";
    sceneSyncPhase.value = "published";
    sceneSyncDetail.value = null;
    const applied = await waitUntilSynced(published.content_hash, published.version);
    if (!mine()) return;
    if (!applied) {
      sceneSyncPhase.value = "waiting";
      sceneSyncDetail.value = "Підтвердження ще не отримано. Застосування чекає безпечного стану.";
      return;
    }
    sceneSyncPhase.value = "synced";
    sceneSyncDetail.value = null;
  }

  function waitUntilSynced(hash: string, nextVersion: number): Promise<boolean> {
    const started = Date.now();
    return new Promise((resolve) => {
      const timer = window.setInterval(() => {
        if (!mine()) {
          window.clearInterval(timer);
          resolve(false);
          return;
        }
        const live = opts.live();
        const hashOk = live.last_synced_hash === hash;
        const versionOk = live.current_release_version === nextVersion;
        const statusOk = live.sync_status === "synced" || live.sync_status === "applied";
        if ((hashOk && versionOk) || (hashOk && statusOk)) {
          window.clearInterval(timer);
          resolve(true);
          return;
        }
        if (Date.now() - started > 45_000) {
          window.clearInterval(timer);
          resolve(false);
        }
      }, 500);
    });
  }

  async function draft(change: () => void): Promise<CommandAck> {
    await ensureLoaded();
    if (!mine() || sceneSyncPhase.value === "conflict") return EMPTY_ACK;
    change();
    await markDraft();
    return EMPTY_ACK;
  }

  return {
    async listPresets() {
      await ensureLoaded();
      return listPresetDocs().map(presetInfo);
    },
    async fetchPresetSummaries() {
      return this.listPresets();
    },
    async getPreset(id) {
      if (id.trim().toUpperCase() === "NONE") {
        throw new Error("NONE — порожній пресет. Його не редагують.");
      }
      await ensureLoaded();
      const path = [...files.keys()].find(
        (item) => item === presetPath(id) || item.toLowerCase() === presetPath(id).toLowerCase(),
      );
      if (!path) throw new Error(`Пресет ${id} не знайдено`);
      return parse(files.get(path)!) as PresetDocument;
    },
    createCustomPreset(id, label) {
      return draft(() => {
        const doc: PresetDocument = {
          schema_version: 1,
          id,
          label,
          hardware_tuned: false,
          builtin: false,
          episodes: [],
        };
        writeDoc(presetPath(id), doc as unknown as Record<string, unknown>);
      });
    },
    savePreset(id, preset) {
      return draft(() => {
        writeDoc(presetPath(id), preset as unknown as Record<string, unknown>);
      });
    },
    renamePreset(id, label) {
      return draft(() => {
        const doc = parse(files.get(presetPath(id))!) as PresetDocument;
        doc.label = label;
        writeDoc(presetPath(id), doc as unknown as Record<string, unknown>);
      });
    },
    duplicatePreset(id, newId, label) {
      return draft(() => {
        if (files.has(presetPath(newId))) throw new Error(`Пресет ${newId} уже існує`);
        const doc = structuredClone(parse(files.get(presetPath(id))!)) as PresetDocument;
        doc.id = newId;
        if (label) doc.label = label;
        doc.builtin = false;
        writeDoc(presetPath(newId), doc as unknown as Record<string, unknown>);
      });
    },
    deletePreset(id) {
      return draft(() => {
        files.delete(presetPath(id));
        const app = asRecord(parse(files.get("app.yaml") ?? ""));
        const slots = Array.isArray(app.pad_presets) ? app.pad_presets.map(String) : [];
        if (!slots.includes(id)) return;
        const remaining = [...files.keys()]
          .filter((path) => path.startsWith("presets/") && path.endsWith(".yaml"))
          .map((path) => path.slice("presets/".length, -".yaml".length))
          .filter((presetId) => presetId !== id);
        const used = slots.filter((slot) => slot !== id);
        const pool = remaining.filter((presetId) => !used.includes(presetId));
        const next: string[] = [];
        for (const slot of slots) {
          if (slot !== id) {
            next.push(slot);
            continue;
          }
          const replacement = pool.shift();
          if (!replacement) return;
          next.push(replacement);
        }
        writeDoc("app.yaml", { ...app, pad_presets: next });
      });
    },
    hardwarePreview: false,
    async previewPreset() {
      throw new Error("Апаратний перегляд з хмари вимкнено, поки тримається blackout");
    },
    async startEditorEpisodePreview() {
      throw new Error("Апаратний перегляд з хмари вимкнено, поки тримається blackout");
    },
    async stopEditorEpisodePreview() {
      return EMPTY_ACK;
    },
    async fetchAppConfig() {
      await ensureLoaded();
      return readDoc("app.yaml");
    },
    async fetchPatch() {
      await ensureLoaded();
      return readDoc("patch.yaml");
    },
    async fetchProfiles() {
      await ensureLoaded();
      const profiles: Record<string, Record<string, unknown>> = {};
      for (const [path, text] of files) {
        if (!path.startsWith("profiles/") || !path.endsWith(".yaml")) continue;
        const id = path.slice("profiles/".length, -".yaml".length);
        profiles[id] = asRecord(parse(text));
      }
      return profiles;
    },
    async fetchLayout() {
      await ensureLoaded();
      return readDoc("layout.yaml");
    },
    async fetchReadiness() {
      await ensureLoaded();
      return {
        hardware_verified: false,
        scene_id: sceneId,
        scene_name: sceneName,
        release_version: version,
        content_hash: contentHash,
      };
    },
    async validatePatch(patch) {
      await ensureLoaded();
      const profiles = await this.fetchProfiles();
      const result = validatePatchShape(patch, profiles);
      let app: Record<string, unknown> | null = null;
      try {
        app = readDoc("app.yaml");
      } catch {
        app = null;
      }
      const presetIds = listPresetDocs().map((doc) => doc.id);
      const slotErrors = validatePadSlots(app, presetIds);
      const errors = [...result.errors, ...slotErrors];
      return { ok: errors.length === 0, errors };
    },
    saveAppConfig(config) {
      return draft(() => writeDoc("app.yaml", config));
    },
    savePatch(patch) {
      return draft(() => writeDoc("patch.yaml", patch));
    },
    saveProfile(profileId, profile) {
      return draft(() => writeDoc(`profiles/${profileId}.yaml`, profile));
    },
    saveLayout(layout) {
      return draft(() => writeDoc("layout.yaml", layout));
    },
    async publishForLocation() {
      await pushCanonical();
    },
    async previewClip(
      id: string,
      seconds = 8,
      fps = 12,
      startS = 0,
    ): Promise<PreviewClip> {
      await ensureLoaded();
      const layout = await this.fetchStageLayout();
      const doc = parse(files.get(presetPath(id)) ?? "") as PresetDocument;
      const episodes = doc.episodes?.length
        ? doc.episodes
        : [{ palette: "deep_red", effect: "breathe", duration_s: 18 }];
      const baked = mixCatalogStamps();
      const sameCards = (await mixEpisodeStamp(episodes)) === baked.episodes;
      const sameScene = (await mixSceneStamp(files)) === baked.scene;
      if (sameCards && sameScene && baked.version === "preset-catalog-2") {
        const clip = sliceMixCatalog(seconds, fps, startS);
        if (clip) return { ...clip, preset_id: id };
      }
      return buildPresetPreviewClip(episodes, layout, {
        presetId: id,
        seconds,
        fps,
        startS,
      });
    },
    async setupPost(path, body = {}) {
      if (path === "beam-calibration/save") {
        await ensureLoaded();
        const patch = readDoc("patch.yaml");
        const fixtures = Array.isArray(patch.fixtures) ? patch.fixtures.map(asRecord) : [];
        const fixtureId = String(body.fixture_id ?? "");
        const next = fixtures.map((fixture) => {
          if (String(fixture.id) !== fixtureId) return fixture;
          const spatial = asRecord(fixture.spatial);
          const update = { ...body };
          delete update.fixture_id;
          return { ...fixture, spatial: { ...spatial, ...update } };
        });
        writeDoc("patch.yaml", { ...patch, fixtures: next });
        markDraft();
        return EMPTY_ACK;
      }
      const command: Record<string, string> = {
        "beam-calibration/begin": "calibration_begin",
        "beam-calibration/set": "calibration_set",
        "beam-calibration/visible": "calibration_visible",
        "beam-calibration/home": "calibration_home",
        "beam-calibration/end": "calibration_end",
      };
      const type = command[path];
      if (!type) {
        throw new Error("Ця дія не входить до віддаленого калібрування. Світло лишається як є.");
      }
      const result = await sendCalibrationCommand(type, body);
      if (result.ok === false || result.status === "failed") {
        throw new Error(String(result.error || "Калібрування не прийнято"));
      }
      if (String(result.status ?? "") !== "applied") {
        throw new Error("Команду прийнято, але прилад її ще не застосував");
      }
      return EMPTY_ACK;
    },
    async fetchChannelRoles(): Promise<ChannelRoleOption[]> {
      return FALLBACK_CHANNEL_ROLES;
    },
    async fetchStageLayout(): Promise<StageLayout> {
      await ensureLoaded();
      const layout = readDoc("layout.yaml");
      const patch = readDoc("patch.yaml");
      const fixtures = Array.isArray(patch.fixtures) ? patch.fixtures.map(asRecord) : [];
      const byId = new Map(fixtures.map((fx) => [String(fx.id), fx]));
      const placements = (Array.isArray(layout.placements) ? layout.placements : []).map((raw) => {
        const place = asRecord(raw);
        const fx = byId.get(String(place.fixture_id));
        return {
          ...place,
          label: String(fx?.label ?? place.fixture_id ?? ""),
          groups: Array.isArray(fx?.groups) ? fx.groups : [],
          kind: String(fx?.kind ?? place.kind ?? ""),
        };
      });
      return {
        description: String(layout.description ?? ""),
        placements,
        cable_chain: Array.isArray(layout.cable_chain) ? layout.cable_chain.map(String) : [],
        artnet_node: layout.artnet_node ?? null,
        hardware_verified: false,
        notes: String(layout.notes ?? ""),
      } as unknown as StageLayout;
    },
    async listAutoCatalog(): Promise<AutoCatalog> {
      const locationId = opts.locationId?.trim();
      if (!locationId) throw new Error("Сцену ще не завантажено");
      return adminAutoCatalog(locationId);
    },
    async previewAutoClip(query): Promise<AutoPreviewClip> {
      const locationId = opts.locationId?.trim();
      if (!locationId) throw new Error("Сцену ще не завантажено");
      await ensureLoaded();
      return adminAutoPreview(locationId, query);
    },
  };
}
