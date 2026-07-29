import { describe, expect, it } from "vitest";
import { SPLASH_MIN_MS, SPLASH_ORANGE, SPLASH_TEXT_BOTTOM, SPLASH_TEXT_TOP } from "./splashBrand";

describe("splashBrand", () => {
  it("keeps splash visible at least 1.5s", () => {
    expect(SPLASH_MIN_MS).toBeGreaterThanOrEqual(1500);
  });

  it("keeps the current brand color and text", () => {
    expect(SPLASH_ORANGE).toBe("#ff4d00");
    expect(SPLASH_TEXT_TOP).toBe("ORNG");
    expect(SPLASH_TEXT_BOTTOM).toBe("HOTBOX");
  });
});
