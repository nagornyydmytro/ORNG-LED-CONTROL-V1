import { defineComponent, nextTick, ref } from "vue";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { APP_STATE_KEY } from "../../composables/appStateKey";
import BpmModePicker from "./BpmModePicker.vue";

beforeEach(() => {
  localStorage.clear();
});

describe("BpmModePicker", () => {
  it("does not paint a refused scope as active", async () => {
    const state = ref({
      scene_id: "scene-a",
      bpm: { enabled: true, scope: "preset", mode: "smooth", palette: "orange" },
    });
    const setBpmSync = vi.fn(async () => false);
    const Parent = defineComponent({
      components: { BpmModePicker },
      setup() {
        return { state };
      },
      template: "<BpmModePicker />",
    });
    const wrapper = mount(Parent, {
      global: {
        provide: {
          [APP_STATE_KEY as symbol]: { state, setBpmSync },
        },
      },
    });
    const buttons = wrapper.findAll("button");
    const preset = buttons.find((button) => button.text() === "Пресет");
    const auto = buttons.find((button) => button.text() === "Авто");
    expect(preset?.classes()).toContain("active");
    await auto?.trigger("click");
    await nextTick();
    expect(setBpmSync).toHaveBeenCalled();
    expect(auto?.classes()).not.toContain("active");
    expect(preset?.classes()).toContain("active");
    expect(localStorage.getItem("orng-bpm-choice:scene-a")).toBeNull();
    expect(localStorage.getItem("orng-bpm-choice:scene:scene-a")).toBeNull();
    wrapper.unmount();
  });

  it("keeps a late choice on the location that started it", async () => {
    const state = ref({
      location_id: "loc-a" as string | null,
      scene_id: "scene-shared",
      bpm: { enabled: true, scope: "preset", mode: "smooth", palette: "orange" },
    });
    let finish: (ok: boolean) => void = () => undefined;
    const setBpmSync = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          finish = resolve;
        }),
    );
    const Parent = defineComponent({
      components: { BpmModePicker },
      setup() {
        return { state };
      },
      template: "<BpmModePicker />",
    });
    const wrapper = mount(Parent, {
      global: {
        provide: {
          [APP_STATE_KEY as symbol]: { state, setBpmSync },
        },
      },
    });
    const auto = wrapper.findAll("button").find((button) => button.text() === "Авто");
    await auto?.trigger("click");
    state.value = { ...state.value, location_id: "loc-b", scene_id: "scene-shared" };
    await nextTick();
    finish(true);
    await nextTick();
    expect(localStorage.getItem("orng-bpm-choice:loc:loc-b")).toBeNull();
    expect(localStorage.getItem("orng-bpm-choice:scene:scene-shared")).toBeNull();
    const kept = localStorage.getItem("orng-bpm-choice:loc:loc-a");
    expect(kept).toContain("auto");
    wrapper.unmount();
  });

  it("shows Auto swatches and extras only with auto_director", async () => {
    const state = ref({
      scene_id: "scene-a",
      capabilities: ["bpm_sync"],
      bpm: { enabled: true, scope: "auto", mode: "smooth", palette: "orange" },
    });
    const setBpmSync = vi.fn(async () => true);
    const Parent = defineComponent({
      components: { BpmModePicker },
      setup() {
        return { state };
      },
      template: "<BpmModePicker />",
    });
    const wrapper = mount(Parent, {
      global: {
        provide: {
          [APP_STATE_KEY as symbol]: { state, setBpmSync },
        },
      },
    });
    expect(wrapper.findAll(".palette-swatch").length).toBe(4);
    expect(wrapper.text()).not.toContain("Характер");
    expect(wrapper.find(".bpm-extra__toggle").exists()).toBe(false);
    state.value = {
      ...state.value,
      capabilities: ["bpm_sync", "auto_director"],
      bpm: {
        ...state.value.bpm,
        character: "smooth",
        intensity: "full",
        period: "auto",
        cast: "all",
      },
    };
    await nextTick();
    expect(wrapper.findAll(".palette-swatch").length).toBeGreaterThanOrEqual(12);
    expect(wrapper.text()).toContain("Характер");
    expect(wrapper.text()).toContain("Інтенсивність");
    const extra = wrapper.get(".bpm-extra__toggle");
    expect(extra.element.tagName).toBe("BUTTON");
    expect(extra.attributes("aria-expanded")).toBe("false");
    expect(extra.text()).toContain("Ще");
    setBpmSync.mockClear();
    await extra.trigger("click");
    await nextTick();
    expect(setBpmSync).not.toHaveBeenCalled();
    expect(extra.attributes("aria-expanded")).toBe("true");
    expect(extra.text()).toContain("Згорнути");
    expect(wrapper.text()).toContain("Склад приладів");
    expect(wrapper.text()).toContain("Музичний період");
    const periodButtons = wrapper
      .find("[aria-label=\"Музичний період\"]")
      .findAll("button")
      .map((button) => button.text());
    expect(periodButtons).toEqual(["Авто", "2", "4", "8"]);
    wrapper.unmount();
  });

  it("sends period, character, intensity, cast and palette; a painted button is not enough", async () => {
    const bpm = {
      enabled: true,
      scope: "auto",
      mode: "smooth",
      palette: "orange",
      cast: "all",
      character: "rhythmic",
      intensity: "full",
      period: "auto" as const,
    };
    const state = ref({
      scene_id: "scene-a",
      capabilities: ["bpm_sync", "auto_director"],
      bpm: { ...bpm },
    });
    const setBpmSync = vi.fn(async () => false);
    const Parent = defineComponent({
      components: { BpmModePicker },
      setup() {
        return { state };
      },
      template: "<BpmModePicker />",
    });
    const wrapper = mount(Parent, {
      global: {
        provide: {
          [APP_STATE_KEY as symbol]: { state, setBpmSync },
        },
      },
    });
    await wrapper.get(".bpm-extra__toggle").trigger("click");
    await nextTick();
    const impact = wrapper.findAll("button").find((button) => button.text() === "Ударний");
    await impact?.trigger("click");
    expect(setBpmSync).toHaveBeenCalledWith({ character: "impact" });
    expect(impact?.classes()).not.toContain("active");
    setBpmSync.mockClear();
    const intensityButtons = wrapper.find("[aria-label=\"Інтенсивність\"]").findAll("button");
    await intensityButtons[0].trigger("click");
    expect(setBpmSync).toHaveBeenCalledWith({ intensity: "soft" });
    const extra = wrapper.find("[aria-label=\"Склад приладів\"]").findAll("button");
    setBpmSync.mockClear();
    await extra[1].trigger("click");
    expect(setBpmSync).toHaveBeenCalledWith({ cast: "bars" });
    const periods = wrapper.find("[aria-label=\"Музичний період\"]").findAll("button");
    setBpmSync.mockClear();
    await periods[periods.length - 1].trigger("click");
    expect(setBpmSync).toHaveBeenCalledWith({ period: 8 });
    const swatch = wrapper.findAll(".palette-swatch")[1];
    setBpmSync.mockClear();
    await swatch.trigger("click");
    expect(setBpmSync.mock.calls[0][0]).toHaveProperty("palette");
    wrapper.unmount();
  });
});
