import { describe, expect, it } from "vitest";
import { createMemoryEnv } from "./helpers/memoryEnv";
import { normalizeShowPrefs, readShowPrefs, showPrefsKey, writeShowPrefs } from "../src/locations/showPrefs";

describe("location show prefs", () => {
  it("keeps musical fields and drops the live tempo", async () => {
    const env = createMemoryEnv();
    const saved = await writeShowPrefs(env, "loc-test", {
      mode: "bar",
      scope: "auto",
      palette: "blue",
      enabled: true,
      bpm: 128,
      device_id: "usb",
    });
    expect(saved).toMatchObject({ mode: "bar", scope: "auto", palette: "blue" });
    expect(saved).not.toHaveProperty("enabled");
    expect(saved).not.toHaveProperty("bpm");
    const raw = await env.SCENE_RELEASES.get(showPrefsKey("loc-test"));
    const text = await raw?.text();
    expect(text).not.toContain("128");
    expect(text).not.toContain("usb");
    const again = await writeShowPrefs(env, "loc-test", { palette: "violet" });
    expect(again.mode).toBe("bar");
    expect(again.palette).toBe("violet");
    expect(await readShowPrefs(env, "loc-other")).toBeNull();
  });

  it("rejects a location id that could escape the key", () => {
    expect(() => showPrefsKey("../other")).toThrow("bad_location");
    expect(normalizeShowPrefs({ mode: "nope" }).mode).toBe("smooth");
  });

  it("migrates bars/heads on this location only", async () => {
    const env = createMemoryEnv();
    const bars = await writeShowPrefs(env, "loc-a", { palette: "bars" });
    expect(bars.palette).toBe("orange");
    expect(bars.cast).toBe("bars");
    const heads = await writeShowPrefs(env, "loc-b", { palette: "heads", character: "impact" });
    expect(heads.palette).toBe("white");
    expect(heads.cast).toBe("heads");
    expect(heads.character).toBe("impact");
    expect(await readShowPrefs(env, "loc-a")).toMatchObject({ palette: "orange", cast: "bars" });
    const clash = await writeShowPrefs(env, "loc-a", {
      character: "smooth",
      period: 0.5,
    });
    expect(clash.character).toBe("smooth");
    expect(clash.period).toBe("auto");
  });
});
