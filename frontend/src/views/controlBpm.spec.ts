import { nextTick, ref } from "vue";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchAutoPreview } from "../api/auto";
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

vi.mock("../api/auto", () => ({
  fetchAutoPreview: vi.fn(async () => ({
    look: "heads_only_oval",
    frames: [],
    canonical: false,
    compatible: false,
    loop_beats: 16,
    cycle_beats: 8,
    seconds: 8,
    note: "Перегляд. Зал з цієї сторінки не керується.",
  })),
  fetchAutoCatalog: vi.fn(async () => ({ compositions: [], count: 0 })),
}));

beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation(() => ({
    matches: true,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  })) as unknown as typeof window.matchMedia;
});

function mountDesk(
  bpm: Record<string, unknown>,
  deskRole: "local" | "raspberry" = "local",
  extras?: { fixtures?: boolean; connection?: "online" | "offline"; executorOnline?: boolean },
) {
  const lit = extras?.fixtures !== false;
  const engine = ref({
    preset_id: "P01",
    episode_index: 0,
    episode_count: 1,
    blackout: false,
    face_on: false,
    strobe_held: false,
    sweep_active: false,
    vertical_sweep_active: false,
  });
  const state = ref({
    scene_id: "scene-a",
    desk_role: deskRole,
    capabilities: ["bpm_sync"],
    pad_presets: ["P01"],
    bpm,
    executor_online: extras?.executorOnline,
    simulator: lit
      ? {
          pars: [{ id: "par_1" }],
          bars: [{ id: "bar_1" }],
          beams: [{ id: "beam_left" }, { id: "beam_right" }],
          faces: [],
        }
      : { pars: [], bars: [], beams: [], faces: [] },
  });
  const api = {
    state,
    engine,
    availableIds: ref(new Set(["NONE", "P01"])),
    availablePresets: ref([{ id: "P01", label: "One", builtin: true }]),
    connection: ref(extras?.connection ?? "online"),
    loading: ref(false),
    lastError: ref<string | null>(null),
    liveView: () => ({ pars: [], bars: [], beams: [], faces: [], nonzero_channels: 0, blackout_visual: false }),
    liveFrame: () => [],
    selectPreset: vi.fn(async () => undefined),
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
  return { wrapper, router, state, engine };
}

describe("ControlView accepted BPM", () => {
  it("shows the confirmed number next to Музика on browser and Raspberry desks", async () => {
    for (const role of ["local", "raspberry"] as const) {
      const { wrapper, router } = mountDesk(
        {
          enabled: true,
          scope: "preset",
          mode: "smooth",
          bpm: 124.2,
          locked: true,
          sync: "locked",
          device: "Mock USB",
          capture: "listening",
        },
        role,
      );
      await router.isReady();
      await nextTick();
      expect(wrapper.get("[data-accepted-bpm]").text()).toBe("124 BPM");
      expect(wrapper.find(".bpm-row__reason").exists()).toBe(false);
      expect(wrapper.find(".bpm-device").exists()).toBe(false);
      expect(wrapper.text()).not.toContain("Синхрон");
      wrapper.unmount();
    }
  });

  it("shows a dash and a hold reason instead of the last number after release", async () => {
    const { wrapper, router } = mountDesk({
      enabled: true,
      scope: "auto",
      mode: "smooth",
      bpm: 124,
      locked: false,
      sync: "hold",
      section: "groove",
      device: "Mock USB",
      capture: "listening",
    });
    await router.isReady();
    await nextTick();
    expect(wrapper.get("[data-accepted-bpm]").text()).toBe("—");
    expect(wrapper.get(".bpm-row__reason").text()).toBe("Утримання");
    expect(wrapper.find(".bpm-device").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("124 BPM");
    wrapper.unmount();
  });

  it("puts Auto section next to the BPM number, not under the picker", async () => {
    const { wrapper, router } = mountDesk({
      enabled: true,
      scope: "auto",
      mode: "beat",
      bpm: 120.2,
      locked: true,
      sync: "locked",
      section: "groove",
      device: "Mock USB",
      capture: "listening",
    });
    await router.isReady();
    await nextTick();
    expect(wrapper.get("[data-accepted-bpm]").text()).toBe("120 BPM");
    expect(wrapper.get(".bpm-row__reason").text()).toBe("Рівний ритм");
    expect(wrapper.get(".bpm-row").text()).toContain("120 BPM");
    expect(wrapper.get(".bpm-row").text()).toContain("Рівний ритм");
    expect(wrapper.find(".bpm-device").exists()).toBe(false);
    wrapper.unmount();
  });

  it("keeps the performer plan when tempo or period changes and does not restart a clip", async () => {
    const bpm = {
      enabled: true,
      scope: "auto",
      mode: "smooth",
      look: "heads_only_oval",
      look_title: "Овал",
      palette: "orange",
      intensity: "full",
      period: 1,
      picture_cycle: 1,
      groups: ["par", "bar", "beam"],
      bpm: 119.8,
      locked: true,
      sync: "locked",
      program: "beat",
    };
    const { wrapper, router, state } = mountDesk(bpm);
    await router.isReady();
    await nextTick();
    expect(fetchAutoPreview).not.toHaveBeenCalled();
    expect(wrapper.get("[data-stage-note]").text()).toContain("бітова програма");
    expect(wrapper.get("[data-stage-note]").text()).toContain("1");
    state.value = {
      ...state.value,
      bpm: { ...bpm, bpm: 120.1, picture_cycle: 4, period: 4, program: "beat" },
    };
    await nextTick();
    expect(fetchAutoPreview).not.toHaveBeenCalled();
    expect(wrapper.get("[data-stage-note]").text()).toContain("4");
    expect(wrapper.get("[data-stage-note]").text()).toContain("бітова програма");
    wrapper.unmount();
  });

  it("names the smooth picture while the tempo stays locked, then blackout", async () => {
    const bpm = {
      enabled: true,
      scope: "auto",
      look: "vertical_wave",
      look_title: "Хвиля",
      picture_cycle: 4,
      groups: ["bar"],
      bpm: 120,
      locked: true,
      sync: "locked",
      program: "beat",
    };
    const { wrapper, router, state, engine } = mountDesk(bpm);
    await router.isReady();
    await nextTick();
    expect(wrapper.get("[data-stage-note]").text()).toContain("бітова програма");
    expect(wrapper.get("[data-stage-note]").text()).toContain("Бари");
    state.value = {
      ...state.value,
      bpm: { ...bpm, look: "sine_vertical", look_title: "Плавна", sync: "locked", locked: true, program: "smooth", bpm: 120 },
    };
    await nextTick();
    expect(wrapper.get("[data-stage-note]").text()).toContain("плавна програма");
    expect(wrapper.text()).toContain("Плавна");
    expect(fetchAutoPreview).not.toHaveBeenCalled();
    engine.value = { ...engine.value, blackout: true };
    await nextTick();
    expect(wrapper.get("[data-stage-note]").text()).toContain("Blackout");
    expect(wrapper.get("[data-stage-note]").text()).toContain("вихід темний");
    wrapper.unmount();
  });

  it("does not keep a confirmed frame while offline", async () => {
    const { wrapper, router } = mountDesk(
      { enabled: true, scope: "auto", look: "vertical_wave", sync: "locked", locked: true, bpm: 120 },
      "local",
      { connection: "offline" },
    );
    await router.isReady();
    await nextTick();
    expect(wrapper.get("[data-stage-note]").text()).toContain("Немає живого кадру");
    expect(fetchAutoPreview).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("drops a live picture that has gone stale", async () => {
    const { wrapper, router, state } = mountDesk(
      { enabled: true, scope: "auto", look: "vertical_wave", program: "beat", sync: "locked", locked: true, bpm: 120 },
      "raspberry",
    );
    state.value = { ...state.value, preview_at: Date.now() - 2000 };
    await router.isReady();
    await nextTick();
    expect(wrapper.get("[data-stage-note]").text()).toContain("Немає живого кадру");
    expect(fetchAutoPreview).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
