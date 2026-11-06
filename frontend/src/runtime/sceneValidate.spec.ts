import { describe, expect, it } from "vitest";
import { savedLocationMessage, sceneSyncPhase } from "./scenePort";
import { lookCaption, sceneLookErrors, profileFootprint, validatePadSlots, validatePatchShape } from "./sceneValidate";

const bar = {
  id: "bar",
  kind: "bar",
  footprint: 15,
  channels: [
    { local: 1, role: "dimmer" },
    { local: 3, role: "dimmer" },
  ],
};

function patch(fixtures: Record<string, unknown>[]) {
  return validatePatchShape({ fixtures }, { bar });
}

describe("validatePatchShape", () => {
  it("uses footprint when channels are sparse", () => {
    expect(profileFootprint(bar)).toBe(15);
    const result = patch([
      { id: "a", profile_id: "bar", kind: "bar", start_address: 1 },
      { id: "b", profile_id: "bar", kind: "bar", start_address: 10 },
    ]);
    expect(result.ok).toBe(false);
    expect(result.errors).toContain("a і b перетинаються");
  });

  it("rejects unknown profiles, duplicate ids, and non-integer addresses", () => {
    const result = patch([
      { id: "a", profile_id: "missing", kind: "bar", start_address: 1 },
      { id: "a", profile_id: "bar", kind: "bar", start_address: 1.5 },
      { id: "b", profile_id: "bar", kind: "bar", start_address: "" },
      { id: "c", profile_id: "bar", kind: "bar", start_address: Number.NaN },
      { id: "d", profile_id: "bar", kind: "par", start_address: 500 },
    ]);
    const text = result.errors.join("\n");
    expect(text).toContain("невідомий профіль");
    expect(text).toContain("дубль приладу a");
    expect(text).toContain("адреса має бути цілим числом");
    expect(text).toContain("поза 1..512");
    expect(text).toContain("не збігається з профілем");
    expect(result.fields.some((field) => field.field === "start_address")).toBe(true);
  });

  it("checks beam range, home, offset, and speed without coercing them", () => {
    const result = patch([
      {
        id: "beam",
        profile_id: "bar",
        kind: "bar",
        start_address: 1,
        spatial: {
          pan_min: 0.8,
          pan_max: 0.2,
          tilt_min: "0",
          tilt_max: 1,
          home_pan: 2,
          pan_offset: -0.9,
          max_pan_speed: 0,
          max_tilt_speed: 3,
        },
      },
    ]);
    const text = result.errors.join("\n");
    expect(text).toContain("pan_min має бути меншим за pan_max");
    expect(text).toContain("tilt_min поза 0..1");
    expect(text).toContain("home_pan поза 0..1");
    expect(text).toContain("pan_offset поза -0.5..0.5");
    expect(text).toContain("max_pan_speed");
    expect(text).toContain("max_tilt_speed");
    expect(result.fields.find((field) => field.field === "pan_min")?.id).toBe("beam");
  });
});

describe("validatePadSlots", () => {
  it("names a slot whose preset was removed", () => {
    expect(validatePadSlots({ pad_presets: ["A01", "NONE", "GONE"] }, ["A01"])).toEqual([
      "слот пульта GONE не має пресета",
    ]);
  });
});

describe("sceneLookErrors", () => {
  const par = [{ id: "par_1", kind: "par", profile_id: "par", groups: ["par", "all_rear"] }];
  const profiles = { par: { kind: "par", channels: [{ local: 1, role: "dimmer" }] } };

  it("names an empty, unknown, or missing group and keeps all_rear", () => {
    expect(
      sceneLookErrors({
        fixtures: par,
        profiles,
        presets: [{ id: "P01", episodes: [{ id: "blank", groups: [], effect: "pulse" }] }],
      }),
    ).toEqual(["P01/blank: дія не називає групу"]);
    expect(
      lookCaption(par, profiles, ["beam"], "static"),
    ).toBe("дія: група beam не має приладів");
    expect(lookCaption(par, profiles, ["all_rear"], "static")).toBe("");
    expect(lookCaption(par, profiles, ["stage-left"], "static")).toBe("дія: невідома група stage-left");
  });

  it("refuses a patterned bar without a complete segment map", () => {
    const errors = sceneLookErrors({
      fixtures: [{ id: "bar_1", kind: "bar", profile_id: "bar", groups: ["bar"] }],
      profiles: {
        bar: {
          kind: "bar",
          segment_count: 2,
          channels: [
            { local: 1, role: "dimmer" },
            { local: 2, role: "segment_color", segment_index: 1 },
          ],
        },
      },
      presets: [{ id: "P01", episodes: [{ id: "chase", groups: ["bar"], effect: "chase" }] }],
    });
    expect(errors).toContain("bar: сегменти описані неповністю");
    expect(errors).toContain("P01/chase: bar_1: ефект chase потребує сегменти");
  });

  it("treats an empty episode list as unfinished", () => {
    expect(
      sceneLookErrors({
        fixtures: par,
        profiles,
        presets: [{ id: "P01", episodes: [] }],
      }),
    ).toEqual(["P01: епізоди мають бути списком"]);
  });
});

describe("savedLocationMessage", () => {
  it("keeps draft, publish, and executor apply apart", () => {
    sceneSyncPhase.value = "draft";
    expect(savedLocationMessage()).toContain("Чернет");
    sceneSyncPhase.value = "published";
    expect(savedLocationMessage()).toBe("Опубліковано. На приладі ще не застосовано");
    sceneSyncPhase.value = "waiting";
    expect(savedLocationMessage()).toBe("Опубліковано. На приладі ще не застосовано");
    sceneSyncPhase.value = "synced";
    expect(savedLocationMessage()).toBe("Опубліковано і застосовано на приладі");
    sceneSyncPhase.value = "idle";
  });
});
