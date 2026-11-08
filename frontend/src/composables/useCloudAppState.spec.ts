import { describe, expect, it } from "vitest";
import { buildShowPrefsWrite, shouldApplyLocationPrefs } from "./useCloudAppState";

const next = {
  enabled: true,
  mode: "beat" as const,
  scope: "auto" as const,
  palette: "red",
  cast: "all" as const,
  groups: ["par", "bar", "beam"] as Array<"par" | "bar" | "beam">,
  excluded: [] as string[],
  character: "rhythmic" as const,
  intensity: "full" as const,
  period: "auto" as const,
  parRhythm: false,
  accent: 0,
};

const base = {
  mode: "beat" as const,
  scope: "auto" as const,
  palette: "orange",
  cast: "all" as const,
  groups: ["par", "bar", "beam"] as Array<"par" | "bar" | "beam">,
  excluded: [] as string[],
  character: "rhythmic" as const,
  intensity: "full" as const,
  period: "auto",
  par_rhythm: false,
  accent: 0,
};

describe("show prefs writes", () => {
  it("sends only the palette when the other desk owns classes and exclusions", () => {
    const body = buildShowPrefsWrite({ palette: "red" }, next, base);
    expect(body.palette).toBe("red");
    expect(body).not.toHaveProperty("groups");
    expect(body).not.toHaveProperty("excluded");
    expect(body).not.toHaveProperty("par_rhythm");
    expect(body).not.toHaveProperty("accent");
    expect(body.base).toEqual({ palette: "orange" });
  });

  it("sends par rhythm and accent only when those fields change", () => {
    const body = buildShowPrefsWrite({ parRhythm: true, accent: 1 }, { ...next, parRhythm: true, accent: 1 }, base);
    expect(body.par_rhythm).toBe(true);
    expect(body.accent).toBe(1);
    expect(body).not.toHaveProperty("groups");
    expect(body.base).toEqual({ par_rhythm: false, accent: 0 });
  });

  it("drops a location reply that is older than the latest write", () => {
    expect(shouldApplyLocationPrefs("loc-a", "loc-a", 2, 2)).toBe(true);
    expect(shouldApplyLocationPrefs("loc-a", "loc-b", 2, 2)).toBe(false);
    expect(shouldApplyLocationPrefs("loc-a", "loc-a", 2, 3)).toBe(false);
  });
});
