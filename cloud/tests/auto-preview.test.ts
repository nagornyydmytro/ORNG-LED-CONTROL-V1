import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  CLIP_MISSING_REASON,
  HEAD_PERIOD_REASON,
  HEAD_TEMPO_REASON,
  HEAD_TRAVEL_REASON,
  MISMATCH_REASON,
  headClipLimit,
  autoCatalogPayload,
  compatibilityForKinds,
  expandFrame,
  fixtureKindsFromPatch,
  libraryVersionFromCatalog,
  loadClipPack,
  loadLookClips,
  packIdentity,
  previewFromWorkspace,
  previewSceneHash,
} from "../src/scenes/autoPreview";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function scenePreviewFiles(sceneDir: string): Array<{ path: string; text: string }> {
  const files: Array<{ path: string; text: string }> = [];
  for (const name of ["patch.yaml", "layout.yaml"]) {
    files.push({
      path: name,
      text: readFileSync(path.join(sceneDir, name), "utf8"),
    });
  }
  const profiles = path.join(sceneDir, "profiles");
  for (const name of readdirSync(profiles).sort()) {
    if (!name.endsWith(".yaml")) continue;
    files.push({
      path: `profiles/${name}`,
      text: readFileSync(path.join(profiles, name), "utf8"),
    });
  }
  return files;
}

const orngBarFiles = () => scenePreviewFiles(path.join(repoRoot, "config/scenes/orng-bar"));

function assetsFrom(root: string): Fetcher {
  return {
    fetch: async (input: RequestInfo | URL) => {
      const url = String(input instanceof Request ? input.url : input);
      const pathname = new URL(url).pathname.replace(/^\//, "");
      try {
        return new Response(readFileSync(path.join(root, pathname)));
      } catch {
        return new Response(null, { status: 404 });
      }
    },
  } as Fetcher;
}

const dumpAssets = () => assetsFrom(path.join(repoRoot, "cloud/src/scenes"));

describe("auto preview catalog", () => {
  it("exposes the dumped runtime library, not a hardcoded 101", () => {
    const payload = autoCatalogPayload();
    expect(payload.count).toBe(payload.compositions.length);
    expect(payload.compositions.some((item) => item.id === "vertical_wave")).toBe(true);
    expect(payload.default_look).toBe("vertical_wave");
    expect(Array.isArray(payload.available)).toBe(true);
  });

  it("explains a missing fixture kind and does not substitute wash", () => {
    const kinds = fixtureKindsFromPatch("fixtures:\n  - id: par_1\n    kind: par\n");
    const check = compatibilityForKinds(kinds, "vertical_wave");
    expect(check.ok).toBe(false);
    expect(check.reason ?? "").toContain("не замінено на wash");
  });

  it("keeps the location scene id and never fills in ORNG BAR", async () => {
    const clip = await previewFromWorkspace({
      sceneId: "other-room",
      sceneName: "Other",
      files: [
        {
          path: "patch.yaml",
          text: "fixtures:\n  - id: par_1\n    kind: par\n  - id: bar_1\n    kind: bar\n  - id: beam_1\n    kind: beam\n",
        },
        { path: "layout.yaml", text: "placements: []\n" },
      ],
      look: "vertical_wave",
      cycleBeats: 4,
      palette: "orange",
      intensity: "full",
      bpm: 120,
    });
    expect(clip.scene_id).toBe("other-room");
    expect(clip.scene_name).toBe("Other");
    expect(clip.compatible).toBe(true);
    expect(clip.canonical).toBe(false);
    expect(clip.frames).toEqual([]);
    expect(clip.reason).toBe(MISMATCH_REASON);
  });

  it("says the scene is missing instead of borrowing another location", async () => {
    const clip = await previewFromWorkspace({
      sceneId: null,
      sceneName: null,
      files: null,
      look: "vertical_wave",
      cycleBeats: 4,
      palette: "orange",
      intensity: "full",
      bpm: 120,
    });
    expect(clip.compatible).toBe(false);
    expect(clip.canonical).toBe(false);
    expect(String(clip.reason)).toContain("Сцену ще не завантажено");
    expect(clip.scene_id).toBeNull();
    expect(clip.frames).toEqual([]);
  });

  it("invalidates a pack when the scene hash or library version does not match", async () => {
    const files = orngBarFiles();
    const layout = files.find((file) => file.path === "layout.yaml");
    if (layout) layout.text += "\n# calibration-edit\n";
    const clip = await previewFromWorkspace({
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      files,
      look: "vertical_wave",
      cycleBeats: 4,
      palette: "orange",
      intensity: "full",
      bpm: 120,
    });
    expect(clip.compatible).toBe(true);
    expect(clip.canonical).toBe(false);
    expect(clip.frames).toEqual([]);
    expect(clip.reason).toBe(MISMATCH_REASON);
    expect(clip.pack_scene_hash).toBe(packIdentity().scene_hash);
  });

  it("does not read checkout files when Worker assets are missing", async () => {
    await expect(loadClipPack()).rejects.toThrow(/missing/);
    const empty = {
      fetch: async () => new Response(null, { status: 404 }),
    } as unknown as Fetcher;
    await expect(loadLookClips("vertical_wave", empty)).rejects.toThrow(/missing/);
  });

  it("serves canonical renderer frames for the exact orng-bar revision", async () => {
    const files = orngBarFiles();
    const assets = dumpAssets();
    const sceneHash = await previewSceneHash(files);
    const library = await libraryVersionFromCatalog();
    const pack = await loadClipPack(assets);
    expect(sceneHash).toBe(packIdentity().scene_hash);
    expect(library).toBe(packIdentity().library_version);
    expect(pack.scene_hash).toBe(sceneHash);
    expect(pack.clips["vertical_wave|4|orange|full"]?.frames.length).toBe(48);
    expect((packIdentity().available ?? []).some((row) => row.look === "hit_bars")).toBe(true);
    const clip = await previewFromWorkspace({
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      files,
      look: "vertical_wave",
      cycleBeats: 4,
      palette: "orange",
      intensity: "full",
      bpm: 60,
      assets,
    });
    expect(clip.compatible).toBe(true);
    expect(clip.canonical).toBe(true);
    expect(clip.reason).toBeNull();
    expect(clip.scene_hash).toBe(packIdentity().scene_hash);
    expect(clip.library_version).toBe(packIdentity().library_version);
    const frames = clip.frames as Array<{ bars: Array<{ segments: number[] }>; nonzero_channels: number }>;
    expect(frames.length).toBe(48);
    expect(clip.seconds).toBe(4);
    expect(frames.some((row) => row.nonzero_channels > 0)).toBe(true);
    expect(frames[0]).not.toEqual(frames[12]);
    expect(frames[0]?.bars[0]?.segments).not.toEqual(frames[24]?.bars[0]?.segments);
    const other = await previewFromWorkspace({
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      files,
      look: "hit_bars",
      cycleBeats: 4,
      palette: "orange",
      intensity: "full",
      bpm: 60,
      assets,
    });
    expect(other.canonical).toBe(true);
    expect((other.frames as unknown[]).length).toBeGreaterThan(0);
  });

  it("keeps 8-beat clips at phase-sample density instead of 24 frames", async () => {
    const clip = await previewFromWorkspace({
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      files: orngBarFiles(),
      look: "vertical_wave",
      cycleBeats: 8,
      palette: "orange",
      intensity: "full",
      bpm: 40,
      assets: dumpAssets(),
    });
    const frames = clip.frames as unknown[];
    expect(clip.canonical).toBe(true);
    expect(frames.length).toBe(72);
    expect(clip.seconds).toBe(12);
  });

  it("does not replay a packed head path at another BPM", async () => {
    const clip = await previewFromWorkspace({
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      files: orngBarFiles(),
      look: "heads_only_oval",
      cycleBeats: 8,
      palette: "orange",
      intensity: "full",
      bpm: 90,
      assets: dumpAssets(),
    });
    expect(clip.canonical).toBe(false);
    expect(clip.frames).toEqual([]);
    expect(clip.reason).toBe(HEAD_TEMPO_REASON);
  });

  it("rejects a packed head loop that is not the performer period", () => {
    expect(headClipLimit("heads_only_oval", 120, 32)).toBe(HEAD_PERIOD_REASON);
    expect(headClipLimit("vertical_wave", 90, 32)).toBeNull();
  });

  it("serves the settled head cycle at the packed tempo", async () => {
    const clip = await previewFromWorkspace({
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      files: orngBarFiles(),
      look: "heads_only_oval",
      cycleBeats: 8,
      palette: "orange",
      intensity: "full",
      bpm: 120,
      assets: dumpAssets(),
    });
    expect(clip.canonical).toBe(true);
    expect(clip.loop_beats).toBe(8);
    const frames = clip.frames as Array<{ beams: Array<{ id: string; pan: number }> }>;
    const mid = frames[Math.floor(frames.length / 3)];
    const left = mid?.beams.find((beam) => beam.id === "beam_left")?.pan ?? 0;
    const right = mid?.beams.find((beam) => beam.id === "beam_right")?.pan ?? 0;
    expect(Math.abs(left + right - 1)).toBeLessThan(0.05);
    const pans = frames.map((frame) => frame.beams.find((beam) => beam.id === "beam_left")?.pan ?? 0);
    const steps = pans.slice(1).map((pan, index) => Math.abs(pan - (pans[index] ?? pan)));
    expect(Math.abs((pans[0] ?? 0) - (pans[pans.length - 1] ?? 0))).toBeLessThanOrEqual(Math.max(...steps) + 1 / 255);
  });

  it("does not pretend a head clip matches after the preset ranges change", async () => {
    const files = [...orngBarFiles(), { path: "presets/other.yaml", text: "id: other\n" }];
    const heads = await previewFromWorkspace({
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      files,
      look: "heads_only_oval",
      cycleBeats: 8,
      palette: "orange",
      intensity: "full",
      bpm: 120,
      assets: dumpAssets(),
    });
    expect(heads.canonical).toBe(false);
    expect(heads.frames).toEqual([]);
    expect(heads.reason).toBe(HEAD_TRAVEL_REASON);
    const bars = await previewFromWorkspace({
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      files,
      look: "vertical_wave",
      cycleBeats: 4,
      palette: "orange",
      intensity: "full",
      bpm: 60,
      assets: dumpAssets(),
    });
    expect(bars.canonical).toBe(true);
  });

  it("keeps packed head axes instead of parking them at center", () => {
    const view = expandFrame({
      pars: [],
      bars: [],
      beams: [{ id: "beam_left", d: 1, o: true, r: 1, g: 0.1, b: 0, t: 0.4, p: 0.22, ti: 0.71, pp: 0.22, pt: 0.71 }],
      n: 4,
    });
    const beam = (view.beams as Array<{ pan: number; tilt: number; physical_pan: number; physical_tilt: number }>)[0];
    expect(beam.pan).toBe(0.22);
    expect(beam.tilt).toBe(0.71);
    expect(beam.physical_pan).toBe(0.22);
    expect(beam.physical_tilt).toBe(0.71);
  });

  it("does not offer a missing palette combo as canonical frames", async () => {
    const clip = await previewFromWorkspace({
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      files: orngBarFiles(),
      look: "vertical_wave",
      cycleBeats: 4,
      palette: "not-a-palette",
      intensity: "full",
      bpm: 120,
      assets: dumpAssets(),
    });
    expect(clip.canonical).toBe(false);
    expect(clip.frames).toEqual([]);
    expect(clip.reason).toBe(CLIP_MISSING_REASON);
  });
});
