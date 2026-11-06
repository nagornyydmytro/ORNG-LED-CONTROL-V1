import { nextTick, ref } from "vue";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { APP_STATE_KEY } from "../composables/appStateKey";
import ControlView from "./ControlView.vue";

vi.mock("../api/presets", () => ({
  getPreset: vi.fn(async () => {
    throw new Error("Пресет не прочитано");
  }),
}));

vi.mock("../api/client", () => ({
  fetchStageLayout: vi.fn(async () => ({ fixtures: [] })),
}));

beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation(() => ({
    matches: true,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  })) as unknown as typeof window.matchMedia;
});

function deferred() {
  let resolve: () => void = () => undefined;
  let reject: (err: Error) => void = () => undefined;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("ControlView optimistic holds", () => {
  it("shows a load error and drops a failed preset hold", async () => {
    const engine = ref({
      preset_id: "P01",
      episode_index: 0,
      episode_count: 2,
      blackout: true,
      face_on: false,
      strobe_held: false,
      sweep_active: false,
      vertical_sweep_active: false,
    });
    const state = ref({
      scene_id: "scene-a",
      pad_presets: ["P01", "P02"],
      bpm: { enabled: false },
      simulator: { pars: [], bars: [], beams: [], faces: [] },
    });
    const pending = deferred();
    const selectPreset = vi.fn(() => pending.promise);
    const api = {
      state,
      engine,
      availableIds: ref(new Set(["NONE", "P01", "P02"])),
      availablePresets: ref([
        { id: "P01", label: "One", builtin: true },
        { id: "P02", label: "Two", builtin: true },
      ]),
      connection: ref("online"),
      loading: ref(false),
      lastError: ref<string | null>(null),
      liveView: () => ({ pars: [], bars: [], beams: [], faces: [], nonzero_channels: 0, blackout_visual: true }),
      liveFrame: () => [],
      selectPreset,
      nudgeEpisode: vi.fn(async () => undefined),
      strobePress: vi.fn(),
      strobeRelease: vi.fn(),
      toggleBlackout: vi.fn(async () => undefined),
      setFace: vi.fn(async () => undefined),
      setPreviewSpeed: vi.fn(),
      setBpmSync: vi.fn(async () => true),
      sweepPress: vi.fn(),
      sweepRelease: vi.fn(),
      verticalSweepPress: vi.fn(),
      verticalSweepRelease: vi.fn(),
      setLiveFxSpeeds: vi.fn(),
    };
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: { template: "<div />" } }],
    });
    const wrapper = mount(ControlView, {
      global: {
        plugins: [router],
        provide: { [APP_STATE_KEY as symbol]: api },
        stubs: { StageSimulator: true, LiveFxPanel: true, BpmModePicker: true },
      },
    });
    await router.isReady();
    await nextTick();
    await nextTick();
    expect(wrapper.text()).toContain("Пресет не прочитано");
    const next = wrapper.findAll("button.preset-btn").find((button) => button.text().includes("Look 2"));
    await next?.trigger("click");
    expect(next?.classes()).toContain("active");
    pending.reject(new Error("Команду не застосовано"));
    await pending.promise.catch(() => undefined);
    await nextTick();
    expect(next?.classes()).not.toContain("active");
    state.value = { ...state.value, scene_id: "scene-b" };
    await nextTick();
    expect(wrapper.find("button.preset-btn.active")?.text() ?? "").not.toContain("Look 2");
    wrapper.unmount();
  });

  it("clears a hold when the location changes before the command settles", async () => {
    const engine = ref({
      preset_id: "P01",
      episode_index: 0,
      episode_count: 2,
      blackout: false,
      face_on: false,
      strobe_held: false,
      sweep_active: false,
      vertical_sweep_active: false,
    });
    const state = ref({
      scene_id: "scene-a",
      pad_presets: ["P01"],
      bpm: { enabled: false },
      simulator: { pars: [], bars: [], beams: [], faces: [] },
    });
    const pending = deferred();
    const api = {
      state,
      engine,
      availableIds: ref(new Set(["NONE", "P01"])),
      availablePresets: ref([{ id: "P01", label: "One", builtin: true }]),
      connection: ref("online"),
      loading: ref(false),
      lastError: ref<string | null>(null),
      liveView: () => ({ pars: [], bars: [], beams: [], faces: [], nonzero_channels: 0, blackout_visual: false }),
      liveFrame: () => [],
      selectPreset: vi.fn(() => pending.promise),
      nudgeEpisode: vi.fn(async () => undefined),
      strobePress: vi.fn(),
      strobeRelease: vi.fn(),
      toggleBlackout: vi.fn(() => pending.promise),
      setFace: vi.fn(async () => undefined),
      setPreviewSpeed: vi.fn(),
      setBpmSync: vi.fn(async () => true),
      sweepPress: vi.fn(),
      sweepRelease: vi.fn(),
      verticalSweepPress: vi.fn(),
      verticalSweepRelease: vi.fn(),
      setLiveFxSpeeds: vi.fn(),
    };
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: { template: "<div />" } }],
    });
    const wrapper = mount(ControlView, {
      global: {
        plugins: [router],
        provide: { [APP_STATE_KEY as symbol]: api },
        stubs: { StageSimulator: true, LiveFxPanel: true, BpmModePicker: true },
      },
    });
    await router.isReady();
    await nextTick();
    await wrapper.get("button.blackout, button").trigger("click").catch(() => undefined);
    const blackout = wrapper.findAll("button").find((button) => button.text().includes("BLACKOUT"));
    await blackout?.trigger("click");
    expect(blackout?.classes()).toContain("active");
    state.value = { ...state.value, scene_id: "scene-b" };
    await nextTick();
    expect(blackout?.classes()).not.toContain("active");
    pending.resolve();
    wrapper.unmount();
  });
});
