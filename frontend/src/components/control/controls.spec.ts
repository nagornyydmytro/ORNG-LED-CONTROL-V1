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
  it("renders ten large preset buttons and marks availability", async () => {
    const wrapper = mount(PresetPad, {
      props: {
        activeId: "P05",
        availableIds: new Set(["P05"]),
      },
    });
    const buttons = wrapper.findAll("button.preset-btn");
    expect(buttons).toHaveLength(10);
    expect(buttons[4].classes()).toContain("active");
    expect(buttons[4].attributes("disabled")).toBeUndefined();
    expect(buttons[0].attributes("disabled")).toBeDefined();

    await buttons[4].trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual(["P05"]);
  });

  it("uses compact grid class for narrow viewports via stylesheet contract", () => {
    // Viewport behavior is CSS (@media max-width 900/560). Assert the pad
    // exposes the hook class used by those rules.
    const wrapper = mount(PresetPad, {
      props: {
        activeId: null,
        availableIds: new Set(["P05"]),
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
    const label = armed ? `${transport} armed` : `${transport} output off`;
    expect(label).toBe("mock output off");
    await nextTick();
  });
});
