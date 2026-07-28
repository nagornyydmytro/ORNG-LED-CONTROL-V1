import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import type { SimulatorView } from "../../vite-env";
import StageSimulator from "./StageSimulator.vue";

const emptySim: SimulatorView = {
  pars: [
    {
      id: "par_1",
      label: "PAR 1",
      kind: "par",
      side: "left",
      ring: "outer",
      face: false,
      order: 1,
      r: 1,
      g: 0,
      b: 0,
      w: 0,
      intensity: 1,
    },
  ],
  bars: [
    {
      id: "bar_1",
      label: "Bar 1",
      kind: "bar",
      side: "left",
      ring: "outer",
      order: 1,
      dimmer: 1,
      segments: [1, 0.5, 0, 0, 0, 0, 0, 0.2],
    },
  ],
  beams: [
    {
      id: "beam_left",
      label: "Beam Left",
      kind: "beam",
      side: "left",
      order: 2,
      pan: 0.25,
      tilt: 0.5,
      dimmer: 0.8,
      shutter_open: true,
    },
    {
      id: "beam_right",
      label: "Beam Right",
      kind: "beam",
      side: "right",
      order: 1,
      pan: 0.75,
      tilt: 0.5,
      dimmer: 0.8,
      shutter_open: true,
    },
  ],
  faces: [
    {
      id: "face_l",
      label: "Face L",
      kind: "face_par",
      side: "left",
      ring: "none",
      face: true,
      order: 1,
      r: 1,
      g: 1,
      b: 1,
      w: 1,
      intensity: 0.6,
    },
  ],
  nonzero_channels: 12,
  blackout_visual: false,
};

describe("StageSimulator", () => {
  it("renders spatial fixtures from simulator state", () => {
    const wrapper = mount(StageSimulator, {
      props: {
        simulator: emptySim,
        frame: Array.from({ length: 512 }, (_, i) => (i < 3 ? 40 : 0)),
        enginePresetId: "P05",
        presetTimeS: 12.5,
        episodeIndex: 2,
        blackout: false,
        strobeHeld: false,
        whiteHitActive: false,
        faceOn: true,
        previewSpeed: 1,
      },
    });
    expect(wrapper.text()).toContain("Симулятор");
    expect(wrapper.find('[data-id="par_1"]').exists()).toBe(true);
    expect(wrapper.find('[data-id="beam_left"]').attributes("data-side")).toBe("left");
    expect(wrapper.find('[data-id="beam_right"]').attributes("data-side")).toBe("right");
    expect(wrapper.findAll(".segments i")).toHaveLength(8);
    expect(wrapper.find('[data-testid="channel-inspector"]').exists()).toBe(true);
  });

  it("shows blackout visual chip when frame is zero", () => {
    const wrapper = mount(StageSimulator, {
      props: {
        simulator: { ...emptySim, blackout_visual: true, nonzero_channels: 0 },
        frame: Array(512).fill(0),
        enginePresetId: "P05",
        presetTimeS: 0,
        episodeIndex: 0,
        blackout: true,
        strobeHeld: false,
        whiteHitActive: false,
        faceOn: false,
        previewSpeed: 10,
      },
    });
    expect(wrapper.text()).toContain("Кадр = 0");
    expect(wrapper.find(".stage").classes()).toContain("blackout");
  });

  it("emits preview speed changes", async () => {
    const wrapper = mount(StageSimulator, {
      props: {
        simulator: emptySim,
        frame: Array(512).fill(0),
        enginePresetId: "P05",
        presetTimeS: 0,
        episodeIndex: 0,
        blackout: false,
        strobeHeld: false,
        whiteHitActive: false,
        faceOn: true,
        previewSpeed: 1,
      },
    });
    await wrapper.find("select").setValue("60");
    expect(wrapper.emitted("update:previewSpeed")?.[0]).toEqual([60]);
  });

  it("renders Beam Left before Beam Right visually", () => {
    const wrapper = mount(StageSimulator, {
      props: {
        simulator: emptySim,
        frame: Array(512).fill(0),
        enginePresetId: "P05",
        presetTimeS: 0,
        episodeIndex: 0,
        episodeCount: 10,
        cycleDurationS: 180,
        blackout: false,
        strobeHeld: false,
        whiteHitActive: false,
        faceOn: true,
        previewSpeed: 1,
      },
    });
    const beams = wrapper.findAll(".row.beams .fixture.beam");
    expect(beams[0].attributes("data-id")).toBe("beam_left");
    expect(beams[1].attributes("data-id")).toBe("beam_right");
  });
});
