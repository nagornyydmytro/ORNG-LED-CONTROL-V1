import { describe, expect, it } from "vitest";
import { previewRelayPayload } from "../src/durable/previewRelay";

describe("preview relay", () => {
  it("forwards the current effect and drops a fixture picture", () => {
    expect(
      previewRelayPayload({
        session: "render-1",
        sequence: 4,
        age_ms: 30,
        program: "beat",
        look: "bars",
        preset_id: "P01",
        stage: { pars: [{ id: "par_1" }] },
        frames: [{ stage: { pars: [] } }],
      }),
    ).toEqual({
      session: "render-1",
      sequence: 4,
      age_ms: 30,
      program: "beat",
      look: "bars",
      preset_id: "P01",
    });
  });
});
