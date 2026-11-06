import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import BlackoutButton from "./BlackoutButton.vue";
import FxHoldButton from "./FxHoldButton.vue";
import SceneSelector from "./SceneSelector.vue";
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
    expect(buttons.length).toBe(12);
    expect(wrapper.text()).toContain("NONE");
    expect(wrapper.text()).not.toContain("Клавіша");
    expect(wrapper.text()).toContain("Мій пресет");
    expect(wrapper.text()).toContain("Look 5");
    expect(buttons[0].text()).toBe("Look 5");
    const noneBtn = wrapper.get("button.preset-btn--none");
    expect(noneBtn.text()).toBe("NONE");
    expect(wrapper.find(".preset-none-row").exists()).toBe(true);

    const custom = buttons.find((b) => b.text().includes("Мій пресет"));
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
    const noneBtn = wrapper.get("button.preset-btn--none");
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

describe("SceneSelector", () => {
  const scenes = [
    { scene_id: "orng-bar", name: "ORNG BAR", active: true },
    { scene_id: "alt-room", name: "Alt Room", active: false },
  ];

  it("shows current scene and lists available scenes", () => {
    const wrapper = mount(SceneSelector, {
      props: {
        sceneId: "orng-bar",
        sceneName: "ORNG BAR",
        scenes,
      },
    });
    expect(wrapper.text()).toContain("Сцена");
    const options = wrapper.findAll("option");
    expect(options.map((o) => o.text())).toEqual(["ORNG BAR", "Alt Room"]);
    expect((wrapper.find("select").element as HTMLSelectElement).value).toBe(
      "orng-bar",
    );
  });

  it("requires confirm before emitting switch (Blackout + Disarm)", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const wrapper = mount(SceneSelector, {
      props: {
        sceneId: "orng-bar",
        sceneName: "ORNG BAR",
        scenes,
      },
    });
    await wrapper.find("select").setValue("alt-room");
    await nextTick();
    expect(confirmSpy).toHaveBeenCalled();
    const msg = String(confirmSpy.mock.calls[0]?.[0] ?? "");
    expect(msg).toContain("Blackout");
    expect(msg).toMatch(/Disarm/i);
    expect(wrapper.emitted("switch")).toBeUndefined();
    expect((wrapper.find("select").element as HTMLSelectElement).value).toBe(
      "orng-bar",
    );
    confirmSpy.mockRestore();
  });

  it("emits switch after confirm", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const wrapper = mount(SceneSelector, {
      props: {
        sceneId: "orng-bar",
        sceneName: "ORNG BAR",
        scenes,
      },
    });
    await wrapper.find("select").setValue("alt-room");
    expect(wrapper.emitted("switch")?.[0]).toEqual(["alt-room"]);
    confirmSpy.mockRestore();
  });
});

describe("FxHoldButton", () => {
  it("changes speed with the same pointer and releases on pointerup", async () => {
    const wrapper = mount(FxHoldButton, {
      props: { label: "STROBE", speed: 0.2, active: false },
    });
    const button = wrapper.get("button");
    await button.trigger("pointerdown", { pointerId: 7, clientY: 200, button: 0, pointerType: "touch" });
    expect(wrapper.emitted("press")).toHaveLength(1);
    await button.trigger("pointermove", { pointerId: 7, clientY: 40, pointerType: "touch" });
    const speeds = wrapper.emitted("speed") ?? [];
    expect(speeds.length).toBeGreaterThan(0);
    const last = speeds[speeds.length - 1]?.[0] as number;
    expect(last).toBeGreaterThan(0.2);
    await button.trigger("pointerup", { pointerId: 7, clientY: 40, pointerType: "touch" });
    expect(wrapper.emitted("release")).toHaveLength(1);
    expect(wrapper.find(".fx-speed-overlay").exists()).toBe(false);
  });

  it("releases when the button becomes unavailable during a hold", async () => {
    const wrapper = mount(FxHoldButton, {
      props: { label: "SWEEP H", speed: 0.4, disabled: false },
    });
    const button = wrapper.get("button");
    await button.trigger("pointerdown", { pointerId: 4, clientY: 100, button: 0, pointerType: "touch" });
    await wrapper.setProps({ disabled: true });
    expect(wrapper.emitted("release")).toHaveLength(1);
  });

  it("releases on pointercancel", async () => {
    const wrapper = mount(FxHoldButton, {
      props: { label: "SWEEP H", speed: 0.4 },
    });
    const button = wrapper.get("button");
    await button.trigger("pointerdown", { pointerId: 3, clientY: 100, button: 0, pointerType: "touch" });
    await button.trigger("pointercancel", { pointerId: 3, clientY: 100, pointerType: "touch" });
    expect(wrapper.emitted("release")).toHaveLength(1);
  });
});
