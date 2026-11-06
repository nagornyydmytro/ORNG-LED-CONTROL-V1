import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import { APP_STATE_KEY } from "../composables/appStateKey";
import { installScenePort, sceneSyncDetail, sceneSyncPhase, useLocalSceneBackend, type ScenePort } from "../runtime/scenePort";
import { validatePatchShape } from "../runtime/sceneValidate";
import SetupView from "./SetupView.vue";

const profiles = {
  par_7ch_provisional: { id: "par_7ch_provisional", kind: "par", footprint: 7, channels: [{ local: 1, role: "dimmer" }] },
  bar_provisional: { id: "bar_provisional", kind: "bar", footprint: 9, channels: [{ local: 1, role: "dimmer" }] },
  beam_provisional: { id: "beam_provisional", kind: "beam", footprint: 8, channels: [{ local: 1, role: "pan_coarse" }] },
};

function samplePatch() {
  return {
    schema_version: 1,
    universe: 0,
    fixtures: [
      {
        id: "bar_1",
        profile_id: "bar_provisional",
        label: "Bar 1",
        start_address: 10,
        kind: "bar",
        spatial: { side: "left", invert_segments: false },
      },
      {
        id: "beam_left",
        profile_id: "beam_provisional",
        label: "Beam Left",
        start_address: 20,
        kind: "beam",
        spatial: {
          side: "left",
          pan_invert: false,
          tilt_invert: false,
          pan_offset: 0,
          tilt_offset: 0,
          pan_min: 0,
          pan_max: 1,
          tilt_min: 0,
          tilt_max: 1,
          home_pan: 0.5,
          home_tilt: 0.5,
          max_pan_speed: 0.35,
          max_tilt_speed: 0.25,
          beam_calibration_notes: null,
        },
      },
      {
        id: "beam_right",
        profile_id: "beam_provisional",
        label: "Beam Right",
        start_address: 40,
        kind: "beam",
        spatial: {
          side: "right",
          pan_offset: 0,
          tilt_offset: 0,
          pan_min: 0,
          pan_max: 1,
          tilt_min: 0,
          tilt_max: 1,
          home_pan: 0.5,
          home_tilt: 0.5,
          max_pan_speed: 0.35,
          max_tilt_speed: 0.25,
          beam_calibration_notes: null,
        },
      },
    ],
  };
}

function otherPatch() {
  const next = samplePatch();
  next.fixtures[0]!.label = "Bar B";
  return next;
}

describe("location calibration save", () => {
  const sceneId = ref("loc-a");
  const scenes: Record<string, ReturnType<typeof samplePatch>> = {};
  let publishImpl: () => Promise<void> = async () => {
    sceneSyncPhase.value = "published";
  };
  const calls: string[] = [];

  function portFor(readerScenes = scenes): ScenePort {
    return {
      async fetchAppConfig() {
        return { transport: "mock" };
      },
      async fetchPatch() {
        return JSON.parse(JSON.stringify(readerScenes[sceneId.value] ?? samplePatch()));
      },
      async fetchProfiles() {
        return profiles;
      },
      async fetchReadiness() {
        return { patch_ok: true };
      },
      async fetchStageLayout() {
        return { placements: [] } as never;
      },
      async validatePatch(patch) {
        const result = validatePatchShape(patch, profiles);
        return { ok: result.ok, errors: result.errors };
      },
      async savePatch(patch) {
        calls.push("draft");
        readerScenes[sceneId.value] = JSON.parse(JSON.stringify(patch)) as ReturnType<typeof samplePatch>;
        return { ok: true } as never;
      },
      async publishForLocation() {
        calls.push("publish");
        await publishImpl();
      },
    } as unknown as ScenePort;
  }

  beforeEach(() => {
    sessionStorage.clear();
    calls.length = 0;
    sceneId.value = "loc-a";
    scenes["loc-a"] = samplePatch();
    scenes["loc-b"] = otherPatch();
    publishImpl = async () => {
      sceneSyncPhase.value = "published";
    };
    useLocalSceneBackend();
    installScenePort(portFor());
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => ({ ok: true, blockers: [] }),
    })));
  });

  afterEach(() => {
    installScenePort(null);
    sceneSyncPhase.value = "idle";
  });

  function mountPage() {
    return mount(SetupView, {
      global: {
        stubs: { RouterLink: true, StageSimulator: true },
        provide: {
          [APP_STATE_KEY]: {
            state: sceneId.value ? ref({ scene_id: sceneId.value }) : ref({ scene_id: "loc-a" }),
            output: ref(null),
            engine: ref(null),
            connection: ref("online"),
            liveView: () => null,
            liveFrame: null,
            refreshRest: vi.fn(async () => undefined),
          },
        },
      },
    });
  }

  function mountBound() {
    const state = ref({ scene_id: "loc-a" });
    const wrapper = mount(SetupView, {
      global: {
        stubs: { RouterLink: true, StageSimulator: true },
        provide: {
          [APP_STATE_KEY]: {
            state,
            output: ref(null),
            engine: ref(null),
            connection: ref("online"),
            liveView: () => null,
            liveFrame: null,
            refreshRest: vi.fn(async () => undefined),
          },
        },
      },
    });
    return { wrapper, state };
  }

  async function editVisible(wrapper: ReturnType<typeof mountPage>) {
    await flushPromises();
    const invert = wrapper.findAll("button").find((button) => button.text() === "Інверсія");
    await invert!.trigger("click");
    const offset = wrapper.get('[aria-label="Pan offset"]');
    await offset.setValue("0.2");
    await flushPromises();
  }

  it("publishes the visible inversion and beam draft, then another reader sees them", async () => {
    const wrapper = mountPage();
    await editVisible(wrapper);
    const save = wrapper.findAll("button").find((button) => button.text() === "Зберегти для локації");
    await save!.trigger("click");
    await flushPromises();

    expect(calls).toEqual(["draft", "publish"]);
    expect(wrapper.text()).toContain("Опубліковано. На приладі ще не застосовано");
    expect(wrapper.text()).not.toContain("застосовано на приладі");
    const saved = scenes["loc-a"]!;
    expect(saved.fixtures[0]?.spatial.invert_segments).toBe(true);
    expect(saved.fixtures[1]?.spatial.pan_offset).toBe(0.2);
    expect(saved.fixtures[2]?.spatial.pan_offset).toBe(0);

    wrapper.unmount();
    sceneId.value = "loc-a";
    const again = mount(SetupView, {
      global: {
        stubs: { RouterLink: true, StageSimulator: true },
        provide: {
          [APP_STATE_KEY]: {
            state: ref({ scene_id: "loc-a" }),
            output: ref(null),
            engine: ref(null),
            connection: ref("online"),
            liveView: () => null,
            liveFrame: null,
            refreshRest: vi.fn(),
          },
        },
      },
    });
    await flushPromises();
    const pressed = again.findAll("button").find((button) => button.text() === "Інверсія");
    expect(pressed!.attributes("aria-pressed")).toBe("true");
    expect((again.get('[aria-label="Pan offset"]').element as HTMLInputElement).value).toBe("0.2");
    again.unmount();
  });

  it("keeps the form when publication fails and does not call that a success", async () => {
    publishImpl = async () => {
      sceneSyncPhase.value = "failed";
      sceneSyncDetail.value = "Публікація не вдалася";
      throw new Error("network down");
    };
    const wrapper = mountPage();
    await editVisible(wrapper);
    await wrapper.findAll("button").find((button) => button.text() === "Зберегти для локації")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Публікація не вдалася");
    expect(wrapper.text()).not.toContain("Опубліковано. На приладі ще не застосовано");
    expect(wrapper.findAll("button").find((button) => button.text() === "Інверсія")!.attributes("aria-pressed")).toBe("true");
    wrapper.unmount();
  });

  it("does not replace the page with a newer release or drop the other field", async () => {
    const wrapper = mountPage();
    await editVisible(wrapper);
    scenes["loc-a"]!.fixtures[0]!.label = "Remote bar";
    await wrapper.findAll("button").find((button) => button.text() === "Зберегти для локації")!.trigger("click");
    await flushPromises();
    expect(calls).not.toContain("draft");
    expect(wrapper.text()).toContain("новіша сцена");
    expect(wrapper.findAll("button").find((button) => button.text() === "Інверсія")!.attributes("aria-pressed")).toBe("true");
    expect((wrapper.get('[aria-label="Pan offset"]').element as HTMLInputElement).value).toBe("0.2");
    wrapper.unmount();
  });

  it("keeps a later edit out of the snapshot that was already being published", async () => {
    let release: () => void = () => undefined;
    publishImpl = () => new Promise((resolve) => {
      release = () => {
        sceneSyncPhase.value = "published";
        resolve();
      };
    });
    const wrapper = mountPage();
    await editVisible(wrapper);
    const pending = wrapper.findAll("button").find((button) => button.text() === "Зберегти для локації")!.trigger("click");
    await flushPromises();
    expect(calls).toEqual(["draft", "publish"]);
    await wrapper.get('[aria-label="Tilt offset"]').setValue("0.15");
    release();
    await pending;
    await flushPromises();
    expect(wrapper.text()).toContain("Новіші лишились у чернетці");
    expect(scenes["loc-a"]!.fixtures[1]?.spatial.tilt_offset).toBe(0.15);
    expect(scenes["loc-a"]!.fixtures[1]?.spatial.pan_offset).toBe(0.2);
    wrapper.unmount();
  });

  it("restores an unsaved form after switching location and does not clear the other beam", async () => {
    const { wrapper, state } = mountBound();
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "Інверсія")!.trigger("click");
    await wrapper.get('[aria-label="Pan min"]').setValue("9");
    state.value = { scene_id: "loc-b" };
    sceneId.value = "loc-b";
    await flushPromises();
    expect(wrapper.text()).toContain("Bar B");
    state.value = { scene_id: "loc-a" };
    sceneId.value = "loc-a";
    await flushPromises();
    expect(wrapper.findAll("button").find((button) => button.text() === "Інверсія")!.attributes("aria-pressed")).toBe("true");
    expect((wrapper.get('[aria-label="Pan min"]').element as HTMLInputElement).value).toBe("9");
    const save = wrapper.findAll("button").find((button) => button.text() === "Зберегти для локації");
    await save!.trigger("click");
    await flushPromises();
    expect(calls).not.toContain("draft");
    expect(wrapper.text()).toContain("pan_min");
    expect((wrapper.get('[aria-label="Tilt offset"]').element as HTMLInputElement).value).toBe("0");
    wrapper.unmount();
  });
});
