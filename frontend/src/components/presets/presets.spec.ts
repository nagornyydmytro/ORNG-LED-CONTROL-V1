import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { computed, ref } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import PresetsView from "../../views/PresetsView.vue";
import AutoCatalogPanel from "./AutoCatalogPanel.vue";
import { APP_STATE_KEY } from "../../composables/appStateKey";
import { cloneEpisode, effectUsesSpeed } from "../../api/presets";

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
    expect(wrapper.text()).toContain("Look 5");
    const editBtn = wrapper.findAll("button").find((b) => b.text() === "Редагувати");
    expect(editBtn).toBeTruthy();
    await editBtn!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Редактор");
    expect(wrapper.findAll(".episode-card").length).toBeGreaterThan(0);
    expect(wrapper.text()).toContain("Зберегти для локації");
    expect(wrapper.text()).toContain("Відтворити на обладнанні");
    expect(wrapper.text()).toContain("На сцену");
  });

  it("can reorder episodes with buttons", async () => {
    const wrapper = mountView();
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text() === "Редагувати")!.trigger("click");
    await flushPromises();
    const firstPalette = (wrapper.find(".episode-card select").element as HTMLSelectElement).value;
    await wrapper.findAll("button").find((b) => b.text() === "↓")!.trigger("click");
    expect((wrapper.find(".episode-card select").element as HTMLSelectElement).value).not.toBe(firstPalette);
  });

  it("previews unequal episodes from their real start and keeps actions", async () => {
    const musical = {
      schema_version: 1,
      id: "M01",
      label: "Музичний",
      hardware_tuned: false,
      builtin: false,
      musical_version: 1,
      episodes: [
        {
          id: "short",
          duration_s: 4,
          groups: ["par"],
          palette: "warm_orange",
          effect: "pulse",
          speed: 0.5,
          intensity: 0.8,
          transition: "cut",
          actions: [
            { id: "a1", groups: ["par"], effect: "pulse", speed: 0.5, intensity: 0.8, cycle_beats: 1, palette: "warm_orange" },
            { id: "a2", groups: ["bar"], effect: "chase", speed: 0.5, intensity: 0.7, cycle_beats: 2, palette: "cool_blue" },
            { id: "a4", groups: ["left"], effect: "wave", speed: 0.4, intensity: 0.6, cycle_beats: 4, palette: "violet" },
            { id: "a8", groups: ["beam"], effect: "breathe", speed: 0.3, intensity: 0.5, cycle_beats: 8, palette: "amber" },
          ],
        },
        {
          id: "long",
          duration_s: 11,
          groups: ["bar"],
          palette: "cool_blue",
          effect: "wave",
          speed: 0.4,
          intensity: 0.6,
          transition: "soft",
          cycle_beats: 4,
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        const json = (body: unknown) =>
          Promise.resolve({ ok: true, status: 200, json: async () => body });
        if (url.endsWith("/api/presets") && method === "GET") {
          return json({
            presets: [{ id: "M01", label: "Музичний", builtin: false, episode_count: 2, total_duration_s: 15 }],
          });
        }
        if (url.endsWith("/api/presets/M01") && method === "GET") return json(musical);
        if (url.includes("/preview-clip")) {
          return json({ fps: 15, transport: "mock", frames: [] });
        }
        if (url.endsWith("/api/presets/M01") && method === "PUT") return json({ ok: true });
        if (url.endsWith("/api/state")) {
          return json({
            engine: { preset_id: "M01", blackout: true },
            output: { transport: "mock", armed: false },
            presets: ["M01"],
            frame: [],
            sequence: 1,
            simulator: { pars: [], bars: [], beams: [], faces: [], nonzero_channels: 0, blackout_visual: true },
          });
        }
        return json({ ok: true });
      }),
    );
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get("[data-id='M01']").trigger("click");
    await flushPromises();
    const calls = () => (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((call) => String(call[0]));
    expect(calls().some((url) => url.includes("/api/presets/M01/preview-clip") && url.includes("start_s=0"))).toBe(true);
    expect(calls().some((url) => url.includes("start_s=7.5"))).toBe(false);
    await wrapper.findAll(".episode-chip")[1]!.trigger("click");
    await flushPromises();
    expect(calls().some((url) => url.includes("start_s=4"))).toBe(true);

    await wrapper.findAll("button").find((button) => button.text() === "Редагувати")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Цикл, долі");
    const cards = wrapper.findAll(".episode-card");
    expect(cards[0]!.text()).toContain("Швидкість");
    expect(cards[0]!.text()).toContain("Інтенсивність");
    expect(cards[0]!.text()).not.toContain("Музичний цикл");
    expect(cards[1]!.text()).toContain("Ефект");
    const cycle = wrapper.findAll(".action-list select").find((select) =>
      Array.from((select.element as HTMLSelectElement).options).some((option) => option.value === "8"),
    );
    expect(cycle).toBeTruthy();
    await cycle!.setValue("8");
    const firstCard = wrapper.findAll(".episode-card")[0]!;
    await firstCard.find(".action-list").findAll("button").find((button) => button.text() === "↓")!.trigger("click");
    await firstCard.findAll(".row-actions")[0]!.findAll("button").find((button) => button.text() === "Копія")!.trigger("click");
    await wrapper.findAll("button").find((button) => button.text() === "Зберегти для локації")!.trigger("click");
    await flushPromises();
    const put = (fetch as unknown as { mock: { calls: [RequestInfo | URL, RequestInit?][] } }).mock.calls.find(
      (call) => String(call[0]).endsWith("/api/presets/M01") && call[1]?.method === "PUT",
    );
    const saved = JSON.parse(String(put?.[1]?.body)).preset;
    expect(saved.musical_version).toBe(1);
    expect(saved.episodes[0].actions.map((action: { id: string }) => action.id)).toEqual(["a2", "a1", "a4", "a8"]);
    expect(saved.episodes[0].actions.map((action: { cycle_beats: number }) => action.cycle_beats)).toEqual([2, 8, 4, 8]);
    expect(saved.episodes.map((episode: { duration_s: number }) => episode.duration_s)).toEqual([4, 4, 11]);
    expect(saved.episodes[1].id).not.toBe(saved.episodes[0].id);
    const kept = new Set(saved.episodes[0].actions.map((action: { id: string }) => action.id));
    for (const action of saved.episodes[1].actions) {
      expect(kept.has(action.id)).toBe(false);
    }
  });

  it("gives a copied episode its own ids and hides a musical cycle on static", () => {
    const copy = cloneEpisode({
      id: "ep",
      duration_s: 4,
      groups: ["par"],
      effect: "static",
      actions: [{ id: "a1", groups: ["par"], effect: "static", speed: 0.5, intensity: 0.7 }],
    });
    expect(copy.id).not.toBe("ep");
    expect(copy.actions?.[0]?.id).not.toBe("a1");
    expect(effectUsesSpeed("static")).toBe(false);
    expect(effectUsesSpeed("wave")).toBe(true);
  });

  it("keeps a saved beam group and explains it on a PAR-only rig", async () => {
    const doc = {
      ...sampleDoc,
      episodes: [{ ...sampleDoc.episodes[0], groups: ["beam"] }, sampleDoc.episodes[1]],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        const json = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => body });
        if (url.endsWith("/api/presets") && method === "GET") {
          return json({ presets: [{ id: "P05", label: "Універсальний", builtin: true, episode_count: 2, total_duration_s: 36 }] });
        }
        if (url.endsWith("/api/presets/P05") && method === "GET") return json(doc);
        if (url.endsWith("/api/config/patch")) {
          return json({
            fixtures: [{ id: "par_1", kind: "par", profile_id: "par", groups: ["par", "all_rear"] }],
          });
        }
        if (url.endsWith("/api/config/profiles")) {
          return json({ par: { kind: "par", channels: [{ local: 1, role: "dimmer" }] } });
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
    const wrapper = mountView();
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "Редагувати")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("група beam не має приладів");
    const beam = wrapper.findAll("button").find((button) => button.text() === "beam");
    expect(beam?.text()).toBe("beam");
  });

  it("opens Auto episodes from the runtime catalog and keeps the preset editor intact", async () => {
    const clip = {
      look: "vertical_wave",
      cycle_beats: 4,
      palette: "orange",
      intensity: "full",
      bpm: 120,
      seconds: 2,
      transport: "none",
      playback: "renderer",
      safe_mock_preview: true,
      compatible: true,
      canonical: true,
      frames: [
        {
          pars: [],
          bars: [
            {
              id: "bar_1",
              label: "bar_1",
              kind: "bar",
              side: "left",
              ring: "",
              dimmer: 0.8,
              r: 1,
              g: 0.3,
              b: 0,
              segments: [0, 0, 1, 1, 1, 0, 0, 0],
            },
          ],
          beams: [],
          faces: [],
          nonzero_channels: 4,
          blackout_visual: false,
        },
        {
          pars: [],
          bars: [
            {
              id: "bar_1",
              label: "bar_1",
              kind: "bar",
              side: "left",
              ring: "",
              dimmer: 0.4,
              r: 1,
              g: 0.3,
              b: 0,
              segments: [1, 1, 0, 0, 0, 0, 0, 0],
            },
          ],
          beams: [],
          faces: [],
          nonzero_channels: 4,
          blackout_visual: false,
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        const json = (body: unknown) =>
          Promise.resolve({ ok: true, status: 200, json: async () => body });
        if (url.endsWith("/api/presets") && method === "GET") {
          return json({
            presets: [{ id: "P05", label: "Універсальний", builtin: true, episode_count: 2, total_duration_s: 36 }],
          });
        }
        if (url.includes("/api/auto/catalog")) {
          return json({
            count: 2,
            default_look: "vertical_wave",
            families: [{ id: "vertical_wave", title: "Вертикальна хвиля", motion: "знизу вгору", envelope: "трикутник" }],
            compositions: [
              {
                id: "vertical_wave",
                family: "vertical_wave",
                family_title: "Вертикальна хвиля",
                title: "Вертикальна хвиля синхронно",
                groups: ["bar"],
                palettes: ["orange"],
                motion: "знизу вгору і назад по сегментах",
              },
              {
                id: "hit_bars",
                family: "hit_decay",
                family_title: "Удар і експонента",
                title: "Удар барів",
                groups: ["bar"],
                palettes: ["orange"],
                motion: "різкий пік",
              },
            ],
            cycles: [0.5, 1, 2, 4, 8],
            palettes: ["orange"],
            intensities: ["soft", "normal", "full"],
          });
        }
        if (url.includes("/api/auto/preview-clip")) {
          const look = new URL(url, "http://local").searchParams.get("look");
          return json({ ...clip, look, canonical: true });
        }
        if (url.includes("/api/stage/layout")) {
          return json({
            description: "",
            placements: [{ fixture_id: "bar_1", x: 0.2, y: 0.4, z: 0, kind: "bar" }],
            cable_chain: [],
            artnet_node: null,
            hardware_verified: false,
            notes: "",
          });
        }
        if (url.includes("/api/config/patch")) {
          return json({ fixtures: [{ id: "bar_1", kind: "bar", label: "Бар" }] });
        }
        if (url.includes("/api/config/profiles")) {
          return json({});
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
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain("Пресети");
    expect(wrapper.text()).toContain("Авто-епізоди");
    expect(wrapper.text()).toContain("Look 5");
    await wrapper.find("[data-catalog=auto]").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Вертикальна хвиля синхронно");
    expect(wrapper.text()).toContain("Перегляд");
    expect(wrapper.text()).toContain("Зал з цієї сторінки не керується");
    expect(wrapper.text()).not.toContain("канонічн");
    const catalogHtml = wrapper.find(".auto-library").html();
    expect(catalogHtml.indexOf("auto-preview")).toBeLessThan(catalogHtml.indexOf("auto-library__list"));
    expect(wrapper.find("[data-look=vertical_wave]").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("Редагувати");
    const calls = () =>
      (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((call) => String(call[0]));
    expect(calls().some((url) => url.includes("/api/auto/catalog"))).toBe(true);
    expect(calls().some((url) => url.includes("look=vertical_wave"))).toBe(true);
    expect(calls().some((url) => url.includes("frames=24"))).toBe(false);
    expect(calls().some((url) => url.includes("/api/commands/"))).toBe(false);
    await wrapper.get("input[type=search]").setValue("удар");
    await flushPromises();
    expect(wrapper.find("[data-look=hit_bars]").exists()).toBe(true);
    expect(wrapper.find("[data-look=vertical_wave]").exists()).toBe(false);
    await wrapper.get("input[type=search]").setValue("");
    await flushPromises();
    const next = wrapper.findAll("button").find((button) => button.text() === "Наступна");
    await next!.trigger("click");
    await flushPromises();
    expect(wrapper.find("[data-look=hit_bars]").classes()).toContain("active");
    const prev = wrapper.findAll("button").find((button) => button.text() === "Попередня");
    await prev!.trigger("click");
    await flushPromises();
    expect(wrapper.find("[data-look=vertical_wave]").classes()).toContain("active");
    const bpm = wrapper.get("input[type=number]");
    await bpm.setValue(60);
    await flushPromises();
    expect(wrapper.text()).toContain("4.00 с");
    await wrapper.findAll("button").find((button) => button.text() === "На весь екран")!.trigger("click");
    expect(wrapper.find(".auto-library--wide").exists()).toBe(true);
    expect(wrapper.findAll("button").some((button) => button.text() === "Вийти")).toBe(true);
    expect(wrapper.text()).toContain("Пауза");
  });

  it("clears Auto preview as soon as the scene is not ready", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const json = (body: unknown) =>
          Promise.resolve({ ok: true, status: 200, json: async () => body });
        if (url.includes("/api/auto/catalog")) {
          return json({
            count: 1,
            default_look: "vertical_wave",
            families: [],
            compositions: [
              {
                id: "vertical_wave",
                family: "vertical_wave",
                family_title: "Вертикальна хвиля",
                title: "Вертикальна хвиля синхронно",
                groups: ["bar"],
                palettes: ["orange"],
                motion: "хвиля",
              },
            ],
            cycles: [4],
            palettes: ["orange"],
            intensities: ["full"],
          });
        }
        if (url.includes("/api/auto/preview-clip")) {
          return json({
            look: "vertical_wave",
            cycle_beats: 4,
            palette: "orange",
            intensity: "full",
            bpm: 120,
            seconds: 2,
            transport: "none",
            compatible: true,
            canonical: true,
            frames: [
              {
                pars: [],
                bars: [{ id: "bar_1", dimmer: 1, r: 1, g: 0, b: 0, segments: [1, 0, 0, 0, 0, 0, 0, 0] }],
                beams: [],
                faces: [],
                nonzero_channels: 1,
                blackout_visual: false,
              },
            ],
          });
        }
        return json({ ok: true });
      }),
    );
    const layout = {
      description: "",
      placements: [{ fixture_id: "bar_1", x: 0.2, y: 0.4, z: 0, kind: "bar" }],
      cable_chain: [],
      artnet_node: null,
      hardware_verified: false,
      notes: "",
    };
    const wrapper = mount(AutoCatalogPanel, {
      props: {
        layout,
        sceneId: "orng-bar",
        locationId: "loc-a",
        sceneRevision: "rev1",
        sceneReady: true,
      },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Вертикальна хвиля синхронно");
    expect(wrapper.text()).not.toContain("Сцену ще не завантажено");
    await wrapper.setProps({ sceneReady: false, sceneError: "Сцену ще не завантажено" });
    await flushPromises();
    expect(wrapper.text()).toContain("Сцену ще не завантажено");
    await wrapper.setProps({ sceneRevision: "rev2", sceneReady: true, sceneError: null });
    await flushPromises();
    const previewCalls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .map((call) => String(call[0]))
      .filter((url) => url.includes("/api/auto/preview-clip"));
    expect(previewCalls.length).toBeGreaterThan(1);
  });
});

