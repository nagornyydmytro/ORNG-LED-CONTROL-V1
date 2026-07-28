import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import type { SimulatorView, StageLayout } from "../../vite-env";
import {
  beamRay,
  cablePolyline,
  fixtureRgb,
  placementMap,
  project,
  projectFloor,
  safeRgb,
  stageBox,
} from "../../lib/stage";
import ChannelInspector from "./ChannelInspector.vue";
import StageCanvas from "./StageCanvas.vue";
import StageSimulator from "./StageSimulator.vue";

function placement(id: string, kind: string, x: number, y: number, extra = {}) {
  return {
    fixture_id: id,
    kind,
    label: id,
    x,
    y,
    z: 0.85,
    width: 0.045,
    height: 0.045,
    rotation_deg: 0,
    orientation: "point" as const,
    mount: "truss",
    aim_x: 0,
    aim_y: -1,
    aim_z: 0,
    ...extra,
  };
}

const layout: StageLayout = {
  description: "test",
  placements: [
    placement("par_1", "par", 0.26, 0.745),
    placement("par_2", "par", 0.37, 0.865),
    placement("par_3", "par", 0.63, 0.865),
    placement("par_4", "par", 0.74, 0.745),
    placement("beam_left", "beam", 0.4, 0.255),
    placement("beam_right", "beam", 0.6, 0.255),
    placement("bar_1", "bar", 0.33, 0.525, {
      orientation: "vertical" as const,
      width: 0.017,
      height: 0.3,
    }),
    placement("bar_2", "bar", 0.443, 0.525, {
      orientation: "vertical" as const,
      width: 0.017,
      height: 0.3,
    }),
    placement("bar_3", "bar", 0.557, 0.525, {
      orientation: "vertical" as const,
      width: 0.017,
      height: 0.3,
    }),
    placement("bar_4", "bar", 0.67, 0.525, {
      orientation: "vertical" as const,
      width: 0.017,
      height: 0.3,
    }),
    placement("face_par_1", "face_par", 0.08, 0.235),
    placement("face_par_2", "face_par", 0.92, 0.235),
  ],
  cable_chain: [
    "par_1",
    "par_2",
    "par_3",
    "par_4",
    "beam_right",
    "beam_left",
    "bar_1",
    "bar_2",
    "bar_3",
    "bar_4",
    "face_par_2",
    "face_par_1",
  ],
  artnet_node: placement("artnet_node", "par", 0.1, 0.94),
  hardware_verified: false,
  notes: "provisional",
};

const view: SimulatorView = {
  pars: [
    {
      id: "par_1",
      label: "PAR 1",
      kind: "par",
      side: "left",
      ring: "outer",
      face: false,
      order: 1,
      r: 0.1,
      g: 0.3,
      b: 1,
      w: 0,
      intensity: 0.8,
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
      r: 0,
      g: 0.9,
      b: 0.4,
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
      tilt: 0.4,
      dimmer: 0.8,
      shutter_open: true,
      r: 1,
      g: 0,
      b: 0.6,
      strobe: 0,
      dir_x: -0.4,
      dir_y: -0.8,
      dir_z: -0.4,
      hit_x: 0.2,
      hit_z: 0.4,
      throw: 1.2,
    },
  ],
  faces: [],
  nonzero_channels: 12,
  blackout_visual: false,
};

describe("stage geometry", () => {
  it("keeps a stable stage aspect inside any container", () => {
    const wide = stageBox(1200, 400);
    expect(wide.width / wide.height).toBeCloseTo(1024 / 724, 3);
    const tall = stageBox(400, 1200);
    expect(tall.width / tall.height).toBeCloseTo(1024 / 724, 3);
    expect(tall.width).toBeLessThanOrEqual(400);
  });

  it("projects normalized coordinates into the box", () => {
    const box = { x: 0, y: 0, width: 1000, height: 700 };
    expect(project(box, 0.5, 0.5)).toEqual({ x: 500, y: 350 });
    const near = projectFloor(box, 0.5, 0);
    const far = projectFloor(box, 0.5, 1);
    expect(far.y).toBeLessThan(near.y);
  });

  it("decodes RGB per fixture instead of a fixed brand orange", () => {
    expect(fixtureRgb({ r: 0, g: 0.9, b: 0.4 })).toEqual({ r: 0, g: 230, b: 102 });
    expect(fixtureRgb({ r: 1, g: 0, b: 0.6 })).toEqual({ r: 255, g: 0, b: 153 });
    // Never substitutes #FF6A00 for a colourless fixture; falls back to neutral.
    expect(safeRgb({ r: 0, g: 0, b: 0 })).toEqual({ r: 230, g: 230, b: 235 });
  });

  it("builds a beam ray from the head down to the geometric hit point", () => {
    const box = stageBox(1024, 724);
    const map = placementMap(layout.placements);
    const { head, hit } = beamRay(box, map.beam_left, view.beams[0]);
    expect(hit.y).toBeGreaterThan(head.y); // ray goes downwards
    expect(hit.x).toBeLessThan(head.x); // pan 0.25 throws to the left
  });

  it("draws the cable chain from the Art-Net node through every fixture", () => {
    const box = stageBox(1024, 724);
    const points = cablePolyline(
      box,
      layout.cable_chain,
      placementMap(layout.placements),
      layout.artnet_node,
    );
    expect(points).toHaveLength(13);
    expect(points[0].x).toBeLessThan(points[1].x);
  });
});

describe("StageCanvas", () => {
  it("exposes every placed fixture for assistive tech and tests", () => {
    const wrapper = mount(StageCanvas, {
      props: { layout, view: () => view },
    });
    expect(wrapper.find("canvas").exists()).toBe(true);
    expect(wrapper.find('[data-id="beam_left"]').exists()).toBe(true);
    expect(wrapper.find('[data-id="bar_1"]').attributes("data-orientation")).toBe("vertical");
    expect(wrapper.findAll("li")).toHaveLength(12);
  });

  it("runs a full render pass without throwing and always draws the newest view", async () => {
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d") as CanvasRenderingContext2D;
    const fills: string[] = [];
    const spy = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      new Proxy(ctx, {
        get(target, prop) {
          if (prop === "fillStyle" || prop === "strokeStyle") return "";
          return Reflect.get(target, prop);
        },
        set(target, prop, value) {
          if (prop === "fillStyle" || prop === "strokeStyle") fills.push(String(value));
          return Reflect.set(target, prop, value);
        },
      }) as never,
    );

    let current: SimulatorView | null = view;
    const wrapper = mount(StageCanvas, {
      props: { layout, view: () => current, showCables: true },
      attachTo: document.body,
    });
    await new Promise((resolve) => setTimeout(resolve, 60));
    // The blue-dominant PAR and the magenta beam must reach the canvas as-is.
    expect(fills.some((f) => f.includes("26, 77, 255"))).toBe(true);
    expect(fills.some((f) => f.includes("255, 0, 153"))).toBe(true);

    current = null;
    await new Promise((resolve) => setTimeout(resolve, 60));
    wrapper.unmount();
    spy.mockRestore();
  });
});

describe("StageSimulator", () => {
  it("renders the stage with overlay chips and a cable toggle", () => {
    const wrapper = mount(StageSimulator, {
      props: {
        layout,
        view: () => view,
        nonzeroChannels: 12,
        enginePresetId: "P05",
        presetTimeS: 12.5,
        episodeIndex: 2,
        episodeCount: 10,
        cycleDurationS: 180,
        blackout: false,
        strobeHeld: false,
        whiteHitActive: false,
        faceOn: true,
        previewSpeed: 1,
      },
    });
    expect(wrapper.text()).toContain("Симулятор");
    expect(wrapper.text()).toContain("Показувати з'єднання");
    expect(wrapper.text()).toContain("P05 · епізод 3/10");
    expect(wrapper.find('[data-id="beam_left"]').exists()).toBe(true);
  });

  it("marks the zero frame and dims the stage on blackout", () => {
    const wrapper = mount(StageSimulator, {
      props: {
        layout,
        view: () => null,
        nonzeroChannels: 0,
        enginePresetId: "P05",
        blackout: true,
        previewSpeed: 10,
      },
    });
    expect(wrapper.text()).toContain("Кадр = 0");
    expect(wrapper.find(".stage").classes()).toContain("blackout");
  });

  it("emits preview speed changes", async () => {
    const wrapper = mount(StageSimulator, {
      props: { layout, view: () => view, previewSpeed: 1 },
    });
    await wrapper.find("select").setValue("60");
    expect(wrapper.emitted("update:previewSpeed")?.[0]).toEqual([60]);
  });
});

describe("ChannelInspector", () => {
  it("renders nothing until opened, then samples the frame on demand", async () => {
    const frame = Array.from({ length: 512 }, (_, i) => (i < 3 ? 40 : 0));
    const wrapper = mount(ChannelInspector, {
      props: { frame: () => frame, nonzero: 3 },
    });
    expect(wrapper.find('[data-testid="channel-inspector"]').exists()).toBe(false);
    await wrapper.find("button").trigger("click");
    expect(wrapper.find('[data-testid="channel-inspector"]').exists()).toBe(true);
    expect(wrapper.findAll(".ch")).toHaveLength(512);
    expect(wrapper.findAll(".ch.lit")).toHaveLength(3);
  });
});
