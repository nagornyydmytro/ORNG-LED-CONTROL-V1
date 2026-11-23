import { describe, expect, it } from "vitest";
import { barPreviewRgb, buildPresetPreviewClip, effectMotion, paletteRgb, previewFrameAt } from "./presetPreviewSim";
import type { StageLayout } from "../vite-env";

const layout: StageLayout = {
  description: "",
  placements: [
    { fixture_id: "bar-1", kind: "bar", x: 0, y: 0, z: 0, groups: ["bar", "all_rear"] },
    { fixture_id: "par-1", kind: "par", x: 1, y: 0, z: 0, groups: ["par", "all_rear"] },
  ],
  cable_chain: [],
  artnet_node: null,
  hardware_verified: false,
  notes: "",
};

describe("presetPreviewSim", () => {
  it("keeps palette channels in 0..1", () => {
    const color = paletteRgb("cool_blue");
    expect(color.r).toBeLessThanOrEqual(1);
    expect(color.b).toBeCloseTo(1, 5);
  });

  it("moves chase segments over time", () => {
    const a = effectMotion("chase", 0.7, 0, 0, 8).seg;
    const b = effectMotion("chase", 0.7, 0.4, 0, 8).seg;
    expect(a).not.toBeCloseTo(b, 2);
  });

  it("builds a looping motion clip for breathe", () => {
    const clip = buildPresetPreviewClip(
      [{ palette: "warm_orange", groups: ["all_rear"], effect: "breathe", speed: 0.5, intensity: 0.7, duration_s: 18 }],
      layout,
      { presetId: "P01", seconds: 2, fps: 10 },
    );
    expect(clip.playback).toBe("sim");
    expect(clip.frames).toHaveLength(20);
    const dimmers = clip.frames.map((frame) => frame.bars[0]!.dimmer);
    expect(new Set(dimmers.map((value) => value.toFixed(3))).size).toBeGreaterThan(1);
  });

  it("changes the picture when the look or the head changes", () => {
    const stage: StageLayout = {
      ...layout,
      placements: [
        ...layout.placements,
        { fixture_id: "bar-2", kind: "bar", x: 0.5, y: 0, z: 0, groups: ["bar"] },
        { fixture_id: "beam-l", kind: "beam", x: 0.2, y: 0, z: 0.2, groups: ["beam"] },
        { fixture_id: "beam-r", kind: "beam", x: 0.8, y: 0, z: 0.2, groups: ["beam"] },
      ],
    };
    const band = previewFrameAt(
      { palette: "cyan", groups: ["bar"], effect: "wave", look: "band_left", head: "sweep", intensity: 0.9 },
      stage,
      1.2,
    );
    const iris = previewFrameAt(
      { palette: "cyan", groups: ["bar"], effect: "wave", look: "iris", head: "nod", intensity: 0.9 },
      stage,
      1.2,
    );
    expect(band.bars[0]!.segments.join(",")).not.toBe(iris.bars[0]!.segments.join(","));
    expect(band.beams.length).toBeGreaterThan(0);
    expect(band.beams[0]!.pan).not.toBeCloseTo(iris.beams[0]!.pan, 2);
    expect(band.beams.every((beam) => beam.tilt <= 0.64)).toBe(true);
    const quiet = previewFrameAt(
      { palette: "deep_red", groups: ["bar"], effect: "breathe", look: "breathe_bars", intensity: 0.9 },
      stage,
      4,
    );
    expect(quiet.beams).toHaveLength(0);
  });

  it("snaps magenta bars to the red wheel and keeps pink on RGB", () => {
    const stage: StageLayout = {
      ...layout,
      placements: [
        ...layout.placements,
        { fixture_id: "bar-2", kind: "bar", x: 1, y: 0, z: 0, groups: ["bar"] },
        { fixture_id: "beam-1", kind: "beam", x: 0.4, y: 0, z: 0.2, groups: ["beam"] },
      ],
    };
    const frame = previewFrameAt(
      {
        palette: "magenta",
        groups: ["bar", "par", "beam"],
        effect: "glow_eq",
        intensity: 0.92,
        speed: 0.2,
      },
      stage,
      1.4,
      { mix: true },
    );
    expect(barPreviewRgb("magenta", true).slot).toBe("red");
    expect(frame.bars[0]!.r).toBeGreaterThan(0.9);
    expect(frame.bars[0]!.b).toBeLessThan(0.1);
    expect(frame.pars[0]!.r).toBeGreaterThan(0.9);
    expect(frame.pars[0]!.b).toBeGreaterThan(0.4);
    expect(frame.beams[0]!.b).toBeGreaterThan(0.4);
    const heights = frame.bars.map((bar) => bar.segments.filter((segment) => segment > 0).length);
    expect(new Set(heights).size).toBeGreaterThan(1);
  });

  it("fills the top-down wave from the top segment", () => {
    const frame = previewFrameAt(
      {
        palette: "deep_red",
        groups: ["bar"],
        effect: "wave",
        look: "fill_down",
        intensity: 0.92,
        speed: 0.22,
      },
      layout,
      0.4,
      { mix: true },
    );
    const segments = frame.bars[0]!.segments;
    const lit = segments.flatMap((value, index) => (value > 0.2 ? [index] : []));
    expect(lit.length).toBeGreaterThan(0);
    expect(lit.length).toBeLessThan(segments.length);
    expect(lit).toEqual(Array.from({ length: lit.length }, (_, offset) => segments.length - lit.length + offset));
    expect(frame.beams).toHaveLength(0);
  });

  it("keeps a copied Mix card under the tilt cap and off an amber blend", () => {
    const stage: StageLayout = {
      ...layout,
      placements: [
        ...layout.placements,
        { fixture_id: "beam-1", kind: "beam", x: 0.2, y: 0, z: 0.2, groups: ["beam"] },
        { fixture_id: "beam-2", kind: "beam", x: 0.8, y: 0, z: 0.2, groups: ["beam"] },
      ],
    };
    const pictured = previewFrameAt(
      {
        palette: "deep_red",
        groups: ["beam"],
        effect: "pulse",
        head: "nod",
        intensity: 0.94,
        mix_style: "picture",
      },
      stage,
      4,
      { mixStyle: "picture" },
    );
    expect(pictured.beams[0]!.tilt).toBeCloseTo(0.64, 5);
    expect(pictured.beams.every((beam) => beam.tilt <= 0.64)).toBe(true);
    const classic = previewFrameAt(
      {
        palette: "deep_red",
        groups: ["beam"],
        effect: "pulse",
        head: "nod",
        intensity: 0.94,
        mix_style: "classic",
      },
      stage,
      0.4,
      { mixStyle: "classic" },
    );
    expect(classic.beams[0]!.tilt).toBeCloseTo(0.24, 5);
    expect(classic.beams[0]!.dimmer).not.toBeCloseTo(pictured.beams[0]!.dimmer, 2);
    const clip = buildPresetPreviewClip(
      [
        {
          palette: "warm_orange",
          groups: ["bar"],
          effect: "glow",
          intensity: 0.92,
          transition: "fade",
          duration_s: 18,
          mix_style: "classic",
        },
        {
          palette: "cyan",
          groups: ["bar"],
          effect: "glow",
          look: "band_diag",
          intensity: 0.92,
          transition: "fade",
          duration_s: 18,
          mix_style: "classic",
        },
      ],
      layout,
      { presetId: "MixCopy", seconds: 2, fps: 8, startS: 18.533 },
    );
    expect(clip.start_s).toBeCloseTo(18.533, 3);
    expect(clip.frames[0]!.bars[0]!.g).toBeCloseTo(0.42, 2);
    expect(clip.frames[0]!.bars[0]!.g).toBeLessThan(0.7);
  });
});
