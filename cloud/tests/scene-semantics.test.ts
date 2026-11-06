import { describe, expect, it } from "vitest";
import { validateSceneSemantics, type BundleFile } from "../src/scenes/releases";

const enc = new TextEncoder();

function files(patch: string, profile: string, app = "schema_version: 1\n"): BundleFile[] {
  return [
    { path: "scene.yaml", data: enc.encode("scene_id: room\nname: room\n") },
    { path: "app.yaml", data: enc.encode(app) },
    { path: "patch.yaml", data: enc.encode(patch) },
    { path: "layout.yaml", data: enc.encode("fixtures: {}\n") },
    { path: "profiles/bar.yaml", data: enc.encode(profile) },
  ];
}

describe("validateSceneSemantics", () => {
  it("uses footprint for sparse channels and checks spatial fields", () => {
    const errors = validateSceneSemantics(files(
      [
        "fixtures:",
        "  - {id: a, profile_id: bar, kind: bar, start_address: 1, spatial: {pan_min: 0.8, pan_max: 0.2}}",
        "  - {id: b, profile_id: bar, kind: bar, start_address: 10}",
      ].join("\n"),
      "id: bar\nkind: bar\nfootprint: 15\nchannels:\n  - {local: 1, role: dimmer}\n  - {local: 3, role: dimmer}\n",
      "pad_presets:\n  - GONE\n",
    ));
    expect(errors).toContain("a і b перетинаються");
    expect(errors).toContain("a: pan_min має бути меншим за pan_max");
    expect(errors).toContain("слот пульта GONE не має пресета");
  });

  it("rejects a fractional address, an unknown profile, and a bad fixed value", () => {
    const errors = validateSceneSemantics(files(
      "fixtures:\n  - {id: a, profile_id: bar, kind: bar, start_address: 1.5}\n  - {id: a, profile_id: nope, start_address: 1}\n",
      "id: bar\nkind: bar\nfootprint: 4\nchannels:\n  - {local: 1, role: fixed, fixed_value: nope}\n",
    ));
    const text = errors.join("\n");
    expect(text).toContain("адреса має бути цілим числом");
    expect(text).toContain("невідомий профіль");
    expect(text).toContain("дубль приладу a");
    expect(text).toContain("фіксоване значення має бути цілим 0..255");
  });

  it("rejects a look the current fixtures cannot play", () => {
    const bundle = files(
      "fixtures:\n  - {id: par_1, profile_id: bar, kind: par, start_address: 1, groups: [par, all_rear]}\n",
      "id: bar\nkind: par\nfootprint: 1\nchannels:\n  - {local: 1, role: dimmer}\n",
    );
    bundle.push({
      path: "presets/P01.yaml",
      data: enc.encode("episodes:\n  - {id: beam, groups: [beam], effect: static}\n  - {id: rear, groups: [all_rear], effect: static}\n"),
    });
    const errors = validateSceneSemantics(bundle);
    expect(errors).toContain("P01/beam: група beam не має приладів");
    expect(errors.some((item) => item.includes("rear"))).toBe(false);
  });

  it("keeps an empty episode list as an unfinished document", () => {
    const bundle = files(
      "fixtures:\n  - {id: par_1, profile_id: bar, kind: par, start_address: 1, groups: [par]}\n",
      "id: bar\nkind: par\nfootprint: 1\nchannels:\n  - {local: 1, role: dimmer}\n",
    );
    bundle.push({
      path: "presets/P01.yaml",
      data: enc.encode("episodes: []\n"),
    });
    expect(validateSceneSemantics(bundle)).toContain("P01: епізоди мають бути списком");
  });
});
