import { describe, expect, it } from "vitest";
import { beamRay, placementMap, stageBox } from "../lib/stage";
import type { StageLayout } from "../vite-env";
import { mixCatalogStamps, mixEpisodeStamp, sliceMixCatalog } from "./mixCatalogClip";

const layout: StageLayout = {
  description: "",
  placements: [
    { fixture_id: "beam_left", kind: "beam", x: 0.4, y: 0.255, z: 0.85, groups: ["beam"] },
    { fixture_id: "beam_right", kind: "beam", x: 0.6, y: 0.255, z: 0.85, groups: ["beam"] },
  ],
  cable_chain: [],
  artnet_node: null,
  hardware_verified: false,
  notes: "",
};

describe("mixCatalogClip", () => {
  it("moves the drawn ray end with the decoder hit while pan changes", () => {
    const stamps = mixCatalogStamps();
    expect(stamps.version).toBe("preset-catalog-2");
    expect(stamps.fps).toBe(8);
    const clip = sliceMixCatalog(12, 8, 90);
    expect(clip).not.toBeNull();
    expect(clip!.fps).toBe(8);
    expect(clip!.playback).toBe("engine");
    const left = clip!.frames.map((frame) => frame.beams.find((beam) => beam.id === "beam_left")).filter(Boolean);
    expect(left.length).toBeGreaterThan(10);
    const pans = new Set(left.map((beam) => beam!.pan.toFixed(3)));
    expect(pans.size).toBeGreaterThan(1);
    const hits = new Set(left.map((beam) => `${beam!.hit_x.toFixed(3)},${beam!.hit_z.toFixed(3)}`));
    expect(hits.size).toBeGreaterThan(1);
    const box = stageBox(1024, 724);
    const map = placementMap(layout.placements);
    const rays = left.map((beam) => {
      const { hit } = beamRay(box, map.beam_left!, beam!);
      return `${hit.x.toFixed(1)},${hit.y.toFixed(1)}`;
    });
    expect(new Set(rays).size).toBeGreaterThan(1);
  });

  it("does not invent a higher frame rate than the packed renderer clip", () => {
    const clip = sliceMixCatalog(2, 15, 90);
    expect(clip).not.toBeNull();
    expect(clip!.fps).toBe(8);
    expect(clip!.frames).toHaveLength(16);
  });

  it("keeps the wrap past 756s off the first-cycle frames", () => {
    const first = sliceMixCatalog(1, 8, 0);
    const wrap = sliceMixCatalog(1, 8, 756);
    expect(first).not.toBeNull();
    expect(wrap).not.toBeNull();
    expect(wrap!.frames[0]).not.toEqual(first!.frames[0]);
  });

  it("changes the episode stamp when cycle_beats or actions change", async () => {
    const base = [
      {
        id: "ep",
        duration_s: 18,
        groups: ["all_rear"],
        palette: "deep_red",
        effect: "pulse",
        speed: 0.5,
        intensity: 0.7,
        transition: "soft",
        actions: [],
        cycle_beats: null,
      },
    ];
    const stamped = await mixEpisodeStamp(base);
    const withBeats = await mixEpisodeStamp([{ ...base[0]!, cycle_beats: 8 }]);
    const withAction = await mixEpisodeStamp([
      {
        ...base[0]!,
        actions: [{ id: "a", groups: ["par"], palette: "cyan", effect: "static", intensity: 1 }],
      },
    ]);
    expect(withBeats).not.toBe(stamped);
    expect(withAction).not.toBe(stamped);
    expect(withAction).not.toBe(withBeats);
  });
});
