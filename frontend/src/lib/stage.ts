/**
 * Pure stage-geometry helpers shared by the canvas simulator and its tests.
 *
 * Coordinates come from the backend layout config: `x` grows to the audience
 * right, `y` grows downwards in the stage picture, `z` grows upstage. Nothing
 * here is hardware-verified — it mirrors the operator's rig sketch.
 */

import type { BarFixtureView, BeamFixtureView, ParFixtureView, StagePlacement } from "../vite-env";

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

/** Keep the drawn stage at a stable aspect ratio inside any container. */
export const STAGE_ASPECT = 1024 / 724;

export function stageBox(width: number, height: number, padding = 12): Box {
  const availableW = Math.max(1, width - padding * 2);
  const availableH = Math.max(1, height - padding * 2);
  let w = availableW;
  let h = w / STAGE_ASPECT;
  if (h > availableH) {
    h = availableH;
    w = h * STAGE_ASPECT;
  }
  return {
    x: (width - w) / 2,
    y: (height - h) / 2,
    width: w,
    height: h,
  };
}

/** Normalized layout coordinates → canvas pixels. */
export function project(box: Box, x: number, y: number): Point {
  return { x: box.x + x * box.width, y: box.y + y * box.height };
}

/**
 * Floor point (stage x/z) → canvas pixels.
 * Upstage points sit slightly higher and narrower, which reads as depth.
 */
export function projectFloor(box: Box, x: number, z: number): Point {
  const depth = Math.max(0, Math.min(1, z));
  const shrink = 0.82 + 0.18 * (1 - depth);
  return {
    x: box.x + box.width * (0.5 + (x - 0.5) * shrink),
    y: box.y + box.height * (0.965 - 0.11 * depth),
  };
}

export function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

/** RGB(W) view → 0..255 colour, white channel folded into the mix. */
export function fixtureRgb(view: { r: number; g: number; b: number; w?: number }): Rgb {
  const white = clamp01(view.w ?? 0) * 0.75;
  return {
    r: Math.round(clamp01(view.r * (1 - white) + white) * 255),
    g: Math.round(clamp01(view.g * (1 - white) + white) * 255),
    b: Math.round(clamp01(view.b * (1 - white) + white) * 255),
  };
}

/** A fixture with no colour data but some output still has to be visible. */
export function safeRgb(view: { r: number; g: number; b: number; w?: number }): Rgb {
  const rgb = fixtureRgb(view);
  if (rgb.r + rgb.g + rgb.b < 12) return { r: 230, g: 230, b: 235 };
  return rgb;
}

export function css(rgb: Rgb, alpha = 1): string {
  return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha})`;
}

export function parBrightness(par: ParFixtureView): number {
  const colour = Math.max(par.r, par.g, par.b, par.w ?? 0);
  return clamp01(par.intensity * (colour > 0 ? 1 : 0.35));
}

export function barSegmentLevel(bar: BarFixtureView, index: number): number {
  return clamp01((bar.segments[index] ?? 0) * bar.dimmer);
}

/** Per-segment colour for the stage canvas; falls back to bar.r/g/b. */
export function barSegmentRgb(
  bar: BarFixtureView,
  index: number,
): { r: number; g: number; b: number } {
  const sr = bar.segment_r?.[index];
  const sg = bar.segment_g?.[index];
  const sb = bar.segment_b?.[index];
  if (
    typeof sr === "number" &&
    typeof sg === "number" &&
    typeof sb === "number" &&
    sr + sg + sb > 0.02
  ) {
    return { r: sr, g: sg, b: sb };
  }
  return { r: bar.r, g: bar.g, b: bar.b };
}

/** Screen-space start and end of a beam ray. */
export function beamRay(
  box: Box,
  placement: StagePlacement,
  beam: BeamFixtureView,
): { head: Point; hit: Point } {
  const head = project(box, placement.x, placement.y);
  const hit = projectFloor(box, beam.hit_x, beam.hit_z);
  return { head, hit };
}

/** Ordered polyline of the DMX daisy chain, starting at the Art-Net node. */
export function cablePolyline(
  box: Box,
  chain: string[] | undefined,
  placements: Record<string, StagePlacement>,
  node?: StagePlacement | null,
): Point[] {
  const points: Point[] = [];
  if (node) points.push(project(box, node.x, node.y));
  for (const id of chain ?? []) {
    const placement = placements[id];
    if (placement) points.push(project(box, placement.x, placement.y));
  }
  return points;
}

export function placementMap(
  placements: StagePlacement[] | undefined,
): Record<string, StagePlacement> {
  const map: Record<string, StagePlacement> = {};
  for (const placement of placements ?? []) map[placement.fixture_id] = placement;
  return map;
}

export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60)
    .toString()
    .padStart(2, "0");
  const s = (total % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}
