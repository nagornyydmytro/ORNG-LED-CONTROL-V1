/** Location-scoped Auto catalog. Frames come from dumped canonical renderer clips. */

import catalog from "./autoCatalog.json";
import packMeta from "./autoPreviewPack.meta.json";
import { sha256Hex } from "../protocol/crypto";

const GROUP_KIND: Record<string, string> = { par: "par", bar: "bar", beam: "beam" };
const GROUP_TITLE: Record<string, string> = { par: "PAR", bar: "бари", beam: "голови" };
export const MISMATCH_REASON = "Ця картина не збігається з поточною сценою.";
export const CLIP_MISSING_REASON = "Цього варіанту немає серед зібраних кадрів.";

export type AutoCatalogFile = typeof catalog;
export type AutoComposition = AutoCatalogFile["compositions"][number];
export type PreviewAvailableClip = {
  look: string;
  cycle_beats: number;
  palette: string;
  intensity: string;
};
export type PreviewPackMeta = {
  scene_hash: string;
  library_version: string;
  library_count: number;
  clip_count?: number;
  looks?: string[];
  available?: PreviewAvailableClip[];
};
type LookPack = {
  scene_hash: string;
  library_version: string;
  clips: Record<string, { look: string; cycle_beats: number; palette: string; intensity: string; frames: CompactFrame[] }>;
};
type CompactFrame = {
  pars: Array<{ id: string; i: number; r: number; g: number; b: number }>;
  bars: Array<{
    id: string;
    d: number;
    r: number;
    g: number;
    b: number;
    s: number[];
    sr: number[];
    sg: number[];
    sb: number[];
  }>;
  beams: Array<{ id: string; d: number; o: boolean; r: number; g: number; b: number; t: number }>;
  n: number;
};

const PACK_META = packMeta as PreviewPackMeta;
const lookPacks = new Map<string, Promise<LookPack>>();

export function autoCatalogPayload(): AutoCatalogFile & {
  count: number;
  available: PreviewAvailableClip[];
} {
  const compositions = Array.isArray(catalog.compositions) ? catalog.compositions : [];
  return {
    ...catalog,
    count: compositions.length,
    default_look: catalog.default_look ?? "vertical_wave",
    note: catalog.note ?? "Перегляд. Зал з цієї сторінки не керується.",
    available: PACK_META.available ?? [],
  };
}

export function fixtureKindsFromPatch(text: string): Set<string> {
  const kinds = new Set<string>();
  for (const match of text.matchAll(/^\s*kind:\s*([A-Za-z0-9_]+)\s*$/gm)) {
    kinds.add(match[1]!);
  }
  return kinds;
}

export function findComposition(look: string): AutoComposition | undefined {
  return (catalog.compositions as AutoComposition[]).find((item) => item.id === look);
}

export function compatibilityForKinds(
  kinds: Set<string>,
  look: string,
): { ok: boolean; look: string; reason: string | null } {
  const composition = findComposition(look);
  if (!composition) {
    return { ok: false, look, reason: `Невідома композиція ${look}.` };
  }
  const missing = composition.groups.filter((group) => {
    const kind = GROUP_KIND[group];
    return Boolean(kind) && !kinds.has(kind);
  });
  if (missing.length) {
    const titles = missing.map((group) => GROUP_TITLE[group] ?? group).join(", ");
    const needed = composition.groups.map((group) => GROUP_TITLE[group] ?? group).join(", ");
    return {
      ok: false,
      look,
      reason: `На цій сцені немає приладів: ${titles}. «${composition.title}» потребує ${needed}. Композицію не замінено на wash.`,
    };
  }
  return { ok: true, look, reason: null };
}

export function cycleKey(cycle: number): string {
  return Number.isInteger(cycle) ? String(cycle) : String(cycle);
}

export function clipKey(look: string, cycle: number, palette: string, intensity: string): string {
  return `${look}|${cycleKey(cycle)}|${palette}|${intensity}`;
}

export function previewSourceText(files: Array<{ path: string; text: string }>): string {
  const normalized = files.map((file) => ({
    path: file.path.replace(/\\/g, "/"),
    text: file.text.replace(/\r\n/g, "\n"),
  }));
  const ordered = [
    ...normalized.filter((file) => file.path === "patch.yaml"),
    ...normalized.filter((file) => file.path === "layout.yaml"),
    ...normalized
      .filter((file) => file.path.startsWith("profiles/") && file.path.endsWith(".yaml"))
      .sort((left, right) => left.path.localeCompare(right.path)),
  ];
  return ordered.map((file) => `${file.path}\n${file.text}`).join("\n");
}

export async function previewSceneHash(files: Array<{ path: string; text: string }>): Promise<string> {
  return sha256Hex(previewSourceText(files));
}

export async function libraryVersionFromCatalog(): Promise<string> {
  return PACK_META.library_version;
}

export function packIdentity(): PreviewPackMeta {
  return PACK_META;
}

async function inflateGzip(bytes: Uint8Array): Promise<string> {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return await new Response(stream).text();
}

async function readAssetBytes(assets: Fetcher, path: string): Promise<Uint8Array> {
  const res = await assets.fetch(new Request(`https://assets.local/${path}`));
  if (!res.ok) throw new Error("Canonical preview pack missing");
  return new Uint8Array(await res.arrayBuffer());
}

export async function loadLookClips(look: string, assets?: Fetcher): Promise<LookPack> {
  if (!assets) throw new Error("Canonical preview pack missing");
  const key = look;
  let pending = lookPacks.get(key);
  if (!pending) {
    pending = (async () => {
      const text = await inflateGzip(await readAssetBytes(assets, `autoPreviewClips/${look}.json.gz`));
      return JSON.parse(text) as LookPack;
    })();
    lookPacks.set(key, pending);
  }
  try {
    return await pending;
  } catch (err) {
    lookPacks.delete(key);
    throw err;
  }
}

export async function loadClipPack(assets?: Fetcher): Promise<LookPack> {
  return loadLookClips("vertical_wave", assets);
}

function expandFrame(row: CompactFrame) {
  return {
    pars: (row.pars ?? []).map((item) => ({
      id: item.id,
      label: item.id,
      kind: "par",
      side: "",
      ring: "",
      face: false,
      r: item.r,
      g: item.g,
      b: item.b,
      w: 0,
      intensity: item.i,
    })),
    bars: (row.bars ?? []).map((item) => ({
      id: item.id,
      label: item.id,
      kind: "bar",
      side: "",
      ring: "",
      dimmer: item.d,
      r: item.r,
      g: item.g,
      b: item.b,
      segments: item.s,
      segment_r: item.sr,
      segment_g: item.sg,
      segment_b: item.sb,
    })),
    beams: (row.beams ?? []).map((item) => ({
      id: item.id,
      label: item.id,
      kind: "beam",
      side: "",
      pan: 0.5,
      tilt: 0.5,
      physical_pan: 0.5,
      physical_tilt: 0.5,
      dimmer: item.d,
      shutter_open: item.o,
      r: item.r,
      g: item.g,
      b: item.b,
      strobe: 0,
      dir_x: 0,
      dir_y: -1,
      dir_z: 0,
      hit_x: 0.5,
      hit_z: 0.5,
      throw: item.t,
      mount: "truss",
      calibration_confirmed: false,
      calibration_blocker: null,
    })),
    faces: [],
    nonzero_channels: row.n ?? 0,
    blackout_visual: false,
  };
}

export async function previewFromWorkspace(opts: {
  sceneId: string | null;
  sceneName: string | null;
  files: Array<{ path: string; text: string }> | null;
  look: string;
  cycleBeats: number;
  palette: string;
  intensity: string;
  bpm: number;
  assets?: Fetcher;
}): Promise<Record<string, unknown>> {
  const cycle = opts.cycleBeats;
  const tempo = Math.max(40, Math.min(220, opts.bpm));
  const seconds = cycle * 60 / tempo;
  const empty = {
    look: opts.look,
    title: findComposition(opts.look)?.title,
    cycle_beats: cycle,
    palette: opts.palette,
    intensity: opts.intensity,
    bpm: tempo,
    seconds,
    transport: "none",
    playback: "renderer",
    safe_mock_preview: true,
    isolated: true,
    source: "script",
    note: "Перегляд. Зал з цієї сторінки не керується.",
    scene_id: opts.sceneId,
    scene_name: opts.sceneName,
    library_count: autoCatalogPayload().count,
    frames: [] as unknown[],
    canonical: false,
  };
  if (!opts.sceneId || !opts.files?.length) {
    return {
      ...empty,
      compatible: false,
      reason: "Сцену ще не завантажено",
    };
  }
  const patchText = opts.files.find((file) => file.path === "patch.yaml")?.text ?? "";
  const check = compatibilityForKinds(fixtureKindsFromPatch(patchText), opts.look);
  if (!check.ok) {
    return { ...empty, compatible: false, reason: check.reason };
  }
  const sceneHash = await previewSceneHash(opts.files);
  const library = await libraryVersionFromCatalog();
  if (sceneHash !== PACK_META.scene_hash || library !== PACK_META.library_version) {
    return {
      ...empty,
      compatible: true,
      reason: MISMATCH_REASON,
      scene_hash: sceneHash,
      library_version: library,
      pack_scene_hash: PACK_META.scene_hash,
      pack_library_version: PACK_META.library_version,
    };
  }
  let stored: LookPack["clips"][string] | undefined;
  try {
    const pack = await loadLookClips(opts.look, opts.assets);
    if (pack.scene_hash !== sceneHash || pack.library_version !== library) {
      return {
        ...empty,
        compatible: true,
        reason: MISMATCH_REASON,
        scene_hash: sceneHash,
        library_version: library,
      };
    }
    stored = pack.clips[clipKey(opts.look, cycle, opts.palette, opts.intensity)];
  } catch {
    return {
      ...empty,
      compatible: true,
      reason: CLIP_MISSING_REASON,
      scene_hash: sceneHash,
      library_version: library,
    };
  }
  if (!stored?.frames?.length) {
    return {
      ...empty,
      compatible: true,
      reason: CLIP_MISSING_REASON,
      scene_hash: sceneHash,
      library_version: library,
    };
  }
  const frames = stored.frames.map(expandFrame);
  return {
    ...empty,
    compatible: true,
    reason: null,
    frames,
    canonical: true,
    scene_hash: sceneHash,
    library_version: library,
    seconds,
  };
}
