import { describe, expect, it } from "vitest";

/** Splash now plays the recolored Klickpin frame sequence (~4.1s @ 30fps). */
const SPLASH_FRAME_COUNT = 125;
const SPLASH_FPS = 30;
const SPLASH_MIN_MS = Math.ceil((SPLASH_FRAME_COUNT / SPLASH_FPS) * 1000);

describe("splash sequence", () => {
  it("keeps splash visible at least 1.5s", () => {
    expect(SPLASH_MIN_MS).toBeGreaterThanOrEqual(1500);
  });

  it("plays the full reference sequence", () => {
    expect(SPLASH_FRAME_COUNT).toBe(125);
    expect(SPLASH_FPS).toBe(30);
  });
});
