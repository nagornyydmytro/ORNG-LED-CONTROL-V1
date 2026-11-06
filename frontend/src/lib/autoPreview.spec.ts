import { describe, expect, it } from "vitest";
import { phaseFrameCount, previewDurationS } from "./autoPreview";

describe("auto preview timing", () => {
  it("samples the cycle by phase, not a flat 24 frames", () => {
    expect(phaseFrameCount(0.5)).toBe(16);
    expect(phaseFrameCount(4)).toBe(48);
    expect(phaseFrameCount(8)).toBe(72);
  });

  it("keeps duration on the live BPM, not a cached clip length", () => {
    expect(previewDurationS(4, 120)).toBe(2);
    expect(previewDurationS(4, 60)).toBe(4);
    expect(previewDurationS(8, 40)).toBe(12);
  });
});
