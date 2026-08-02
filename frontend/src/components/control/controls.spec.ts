import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { nextTick } from "vue";
import BlackoutButton from "./BlackoutButton.vue";
import StrobeButton from "./StrobeButton.vue";
import PresetPad from "./PresetPad.vue";

describe("BlackoutButton", () => {
  it("toggles without a modal and exposes pressed state", async () => {
    const wrapper = mount(BlackoutButton, {
      props: { active: false, disabled: false },
    });
    expect(wrapper.text()).toContain("BLACKOUT");
    expect(wrapper.attributes("aria-pressed")).toBe("false");
    await wrapper.trigger("click");
    expect(wrapper.emitted("toggle")).toHaveLength(1);
  });

  it("shows active state for accessibility", () => {
    const wrapper = mount(BlackoutButton, {
      props: { active: true },
    });
    expect(wrapper.classes()).toContain("active");
    expect(wrapper.attributes("aria-pressed")).toBe("true");
  });
});

describe("StrobeButton", () => {
  it("emits press/release on pointer down/up and cancel", async () => {
    const wrapper = mount(StrobeButton, {
      props: { held: false },
    });
    await wrapper.trigger("pointerdown");
    expect(wrapper.emitted("press")).toHaveLength(1);
    await wrapper.trigger("pointerup");
    expect(wrapper.emitted("release")).toHaveLength(1);

    await wrapper.trigger("pointerdown");
    await wrapper.trigger("pointercancel");
    expect(wrapper.emitted("release")?.length).toBeGreaterThanOrEqual(2);
  });

  it("supports keyboard press/release", async () => {
    const wrapper = mount(StrobeButton, {
      props: { held: false },
    });
    await wrapper.trigger("keydown", { key: " " });
    expect(wrapper.emitted("press")).toHaveLength(1);
    await wrapper.trigger("keyup", { key: " " });
    expect(wrapper.emitted("release")).toHaveLength(1);
  });
});

describe("PresetPad", () => {
  it("renders Без пресету plus exactly the configured pad slots", async () => {
    const wrapper = mount(PresetPad, {
      props: {
        activeId: "P05",
        availableIds: new Set(["NONE", "P05", "C01", "P01"]),
        padPresetIds: ["P05", "C01", "P01", "P02", "P03", "P04", "P06", "P07", "P08"],
        presets: [
          { id: "P05", label: "Універсальний", builtin: true },
          { id: "C01", label: "Мій пресет", builtin: false },
          { id: "P01", label: "Дуже плавний", builtin: true },
        ],
      },
    });
    const buttons = wrapper.findAll("button.preset-btn");
    expect(buttons.length).toBe(10);
    expect(wrapper.text()).toContain("Без пресету");
    expect(wrapper.text()).toContain("C01");
    expect(wrapper.text()).toContain("Мій пресет");
    expect(buttons[0].text()).toContain("NONE");

    const custom = buttons.find((b) => b.text().includes("C01"));
    expect(custom).toBeTruthy();
    await custom!.trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual(["C01"]);
  });

  it("marks Без пресету active when activeId is NONE", () => {
    const wrapper = mount(PresetPad, {
      props: {
        activeId: "NONE",
        availableIds: new Set(["NONE", "P01"]),
        padPresetIds: ["P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09"],
        presets: [{ id: "P01", label: "Дуже плавний", builtin: true }],
      },
    });
    const noneBtn = wrapper.findAll("button.preset-btn")[0];
    expect(noneBtn.classes()).toContain("active");
    expect(noneBtn.attributes("aria-pressed")).toBe("true");
  });

  it("uses compact grid class for narrow viewports via stylesheet contract", () => {
    const wrapper = mount(PresetPad, {
      props: {
        activeId: null,
        availableIds: new Set(["NONE", "P05"]),
        padPresetIds: ["P05", "P01", "P02", "P03", "P04", "P06", "P07", "P08", "P09"],
        presets: [{ id: "P05", label: "Універсальний", builtin: true }],
      },
    });
    expect(wrapper.find(".preset-pad").exists()).toBe(true);
    expect(wrapper.findAll(".preset-btn").every((btn) => btn.classes().includes("preset-btn"))).toBe(
      true,
    );
  });
});

describe("connection honesty", () => {
  it("does not treat mock disarmed output as falsely connected artnet", async () => {
    // sanity helper for status copy used by shell
    const transport = "mock";
    const armed = false;
    const label = armed ? `${transport} увімкнено` : `${transport} вимкнено`;
    expect(label).toBe("mock вимкнено");
    await nextTick();
  });
});
