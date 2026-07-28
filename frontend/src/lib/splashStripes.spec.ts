import { describe, expect, it } from "vitest";
import { SPLASH_MIN_MS, edgeFade } from "./splashStripes";

describe("splashStripes", () => {
  it("keeps splash visible at least 1.5s", () => {
    expect(SPLASH_MIN_MS).toBeGreaterThanOrEqual(1500);
  });

  it("fades slits before screen edges", () => {
    expect(edgeFade(0)).toBe(0);
    expect(edgeFade(1)).toBe(0);
    expect(edgeFade(0.5)).toBeGreaterThan(0.9);
  });
});
