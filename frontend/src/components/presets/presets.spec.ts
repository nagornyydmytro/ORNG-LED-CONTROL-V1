import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { computed, ref } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import PresetsView from "../../views/PresetsView.vue";
import { APP_STATE_KEY } from "../../composables/appStateKey";

const sampleDoc = {
  schema_version: 1,
  id: "P05",
  label: "Універсальний",
  hardware_tuned: false,
  builtin: true,
  episodes: [
    {
      id: "ep1",
      duration_s: 18,
      groups: ["all_rear"],
      palette: "warm_orange",
      effect: "pulse",
      speed: 0.5,
      intensity: 0.7,
      transition: "soft",
    },
    {
      id: "ep2",
      duration_s: 18,
      groups: ["par"],
      palette: "amber",
      effect: "wave",
      speed: 0.4,
      intensity: 0.6,
      transition: "soft",
    },
  ],
};

describe("PresetsView editor", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        const json = (body: unknown, ok = true) =>
          Promise.resolve({
            ok,
            status: ok ? 200 : 400,
            json: async () => body,
          });
        if (url.endsWith("/api/presets") && method === "GET") {
          return json({
            presets: [
              {
                id: "P05",
                label: "Універсальний",
                builtin: true,
                episode_count: 10,
                total_duration_s: 180,
              },
            ],
          });
        }
        if (url.endsWith("/api/presets/P05") && method === "GET") {
          return json(sampleDoc);
        }
        if (url.includes("/api/presets/P05") && method === "PUT") {
          return json({ ok: true, state: { presets: ["P05"] } });
        }
        if (url.includes("/preview")) {
          return json({
            ok: true,
            state: {
              presets: ["P05"],
              preview_speed: 10,
              output: { transport: "mock", armed: false },
            },
          });
        }
        if (url.endsWith("/api/state")) {
          return json({
            engine: { preset_id: "P05" },
            output: { transport: "mock", armed: false, last_error: null },
            presets: ["P05"],
            frame: [],
            sequence: 1,
            preview_speed: 1,
            simulator: { pars: [], bars: [], beams: [], faces: [], nonzero_channels: 0, blackout_visual: true },
          });
        }
        return json({ ok: true, presets: [] });
      }),
    );
  });

  function mountView() {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/", name: "control", component: { template: "<div />" } },
        { path: "/presets", name: "presets", component: PresetsView },
      ],
    });
    return mount(PresetsView, {
      global: {
        plugins: [router],
        stubs: { BlackoutButton: true },
        provide: {
          [APP_STATE_KEY as symbol]: {
            selectPreset: vi.fn(),
            connection: ref("online"),
            engine: computed(() => ({
              preset_id: "P05",
              episode_index: 0,
              episode_count: 10,
              cycle_duration_s: 180,
              preset_time_s: 0,
              blackout: true,
              strobe_held: false,
              white_hit_active: false,
              face_on: false,
            })),
            output: computed(() => ({
              transport: "mock",
              armed: false,
              udp_active: false,
              source_owner: "none",
              source_nonzero_channels: 0,
              wire_nonzero_channels: 0,
            })),
            state: computed(() => ({
              frame: [],
              preview_speed: 1,
              simulator: {
                pars: [],
                bars: [],
                beams: [],
                faces: [],
                nonzero_channels: 0,
                blackout_visual: true,
              },
              preset_editor_preview: { active: false },
            })),
            setPreviewSpeed: vi.fn(),
            toggleBlackout: vi.fn(),
            refreshRest: vi.fn(async () => undefined),
          },
        },
      },
    });
  }

  it("lists presets and opens episode editor", async () => {
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain("Пресети");
    expect(wrapper.text()).toContain("P05");
    const editBtn = wrapper.findAll("button").find((b) => b.text() === "Редагувати");
    expect(editBtn).toBeTruthy();
    await editBtn!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Редактор");
    expect(wrapper.findAll(".episode-card").length).toBeGreaterThan(0);
    expect(wrapper.text()).toContain("Зберегти пресет");
    expect(wrapper.text()).toContain("Відтворити на обладнанні");
    expect(wrapper.text()).toContain("Застосувати до візуалізації");
  });

  it("can reorder episodes with buttons", async () => {
    const wrapper = mountView();
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text() === "Редагувати")!.trigger("click");
    await flushPromises();
    const firstId = wrapper.find(".episode-card strong").text();
    await wrapper.findAll("button").find((b) => b.text() === "↓")!.trigger("click");
    expect(wrapper.find(".episode-card strong").text()).not.toBe(firstId);
  });
});
