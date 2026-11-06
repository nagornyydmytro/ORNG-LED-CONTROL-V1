import { describe, expect, it } from "vitest";
import {
  parsePadPresetsFromAppYaml,
  parseSceneNameFromYaml,
} from "../src/scenes/releases";

describe("control surface yaml parsers", () => {
  it("parses ORNG BAR pad_presets block list", () => {
    const yaml = `schema_version: 1
transport: artnet
pad_presets:
- A01
- A02
- A03
- P01
- P02
- P03
- P04
- P05
- P06
strobe_speed: 0.1
`;
    expect(parsePadPresetsFromAppYaml(yaml)).toEqual([
      "A01",
      "A02",
      "A03",
      "P01",
      "P02",
      "P03",
      "P04",
      "P05",
      "P06",
    ]);
  });

  it("parses scene name", () => {
    expect(
      parseSceneNameFromYaml(`scene_id: orng-bar\nname: ORNG BAR\n`),
    ).toBe("ORNG BAR");
  });

  it("returns empty pad list when missing", () => {
    expect(parsePadPresetsFromAppYaml("transport: artnet\n")).toEqual([]);
  });
});
