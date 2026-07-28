import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import HardwareBadge from "./HardwareBadge.vue";
import SetupView from "../../views/SetupView.vue";
import { APP_STATE_KEY } from "../../composables/appStateKey";

const sampleApp = {
  schema_version: 1,
  transport: "mock",
  output_armed: false,
  master_brightness: 1,
  artnet: {
    target_ip: null,
    universe: 0,
    fps: 30,
    udp_port: 6454,
    hardware_verified: false,
  },
};

const samplePatch = {
  schema_version: 1,
  universe: 0,
  fixtures: [
    {
      id: "par_1",
      profile_id: "par_7ch_provisional",
      label: "PAR 1",
      start_address: 1,
      kind: "par",
      groups: ["par"],
      spatial: { side: "left", ring: "outer", face: false, order: 1 },
    },
    {
      id: "bar_1",
      profile_id: "bar_provisional",
      label: "Bar 1",
      start_address: 10,
      kind: "bar",
      groups: ["bar"],
      spatial: { side: "left", ring: "outer", face: false, order: 1, invert_segments: false },
    },
    {
      id: "beam_left",
      profile_id: "beam_provisional",
      label: "Beam Left",
      start_address: 20,
      kind: "beam",
      groups: ["beam"],
      spatial: {
        side: "left",
        ring: "none",
        face: false,
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
        beam_calibration_confirmed: false,
      },
    },
    {
      id: "beam_right",
      profile_id: "beam_provisional",
      label: "Beam Right",
      start_address: 30,
      kind: "beam",
      groups: ["beam"],
      spatial: {
        side: "right",
        ring: "none",
        face: false,
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
        beam_calibration_confirmed: false,
      },
    },
  ],
};

const sampleProfiles = {
  par_7ch_provisional: {
    id: "par_7ch_provisional",
    label: "PAR",
    kind: "par",
    footprint: 7,
    hardware_verified: false,
    channels: [{ local: 1, role: "dimmer" }],
  },
  bar_provisional: {
    id: "bar_provisional",
    label: "Bar",
    kind: "bar",
    footprint: 9,
    hardware_verified: false,
    channels: [{ local: 1, role: "dimmer" }],
  },
  beam_provisional: {
    id: "beam_provisional",
    label: "Beam",
    kind: "beam",
    footprint: 8,
    hardware_verified: false,
    channels: [
      { local: 1, role: "pan_coarse" },
      { local: 2, role: "pan_fine" },
      { local: 3, role: "tilt_coarse" },
      { local: 4, role: "tilt_fine" },
    ],
  },
};

describe("HardwareBadge", () => {
  it("shows unverified hardware label", () => {
    const wrapper = mount(HardwareBadge);
    expect(wrapper.text()).toContain("Не перевірено на обладнанні");
  });
});

describe("SetupView wizard", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const json = (body: unknown) =>
          Promise.resolve({
            ok: true,
            json: async () => body,
          });
        if (url.endsWith("/api/config/app")) return json(sampleApp);
        if (url.endsWith("/api/config/patch")) return json(samplePatch);
        if (url.endsWith("/api/config/profiles")) return json(sampleProfiles);
        if (url.endsWith("/api/config/layout")) {
          return json({
            schema_version: 1,
            viewer_facing: true,
            description: "test",
            fixtures: ["par_1"],
          });
        }
        if (url.endsWith("/api/setup/readiness")) {
          return json({
            transport_preferred: "mock",
            runtime_transport: "mock",
            output_armed: false,
            artnet_network_enabled: false,
            udp_active: false,
            blackout: true,
            artnet_badge: "Не перевірено на обладнанні",
            patch_ok: true,
            fixture_count: 1,
            profiles: [],
            source_frame_sum: 0,
            source_nonzero_channels: 0,
            wire_frame_sum: 0,
            wire_nonzero_channels: 0,
            arm_blockers: ["Runtime має бути Art-Net (у Mock Arm неможливий)"],
          });
        }
        if (url.endsWith("/api/output/arm-blockers")) {
          return json({
            ok: false,
            blockers: ["Runtime має бути Art-Net (у Mock Arm неможливий)"],
          });
        }
        if (url.endsWith("/api/output/arm") || url.endsWith("/api/output/disarm")) {
          return json({
            ok: true,
            state: {
              output: { transport: "mock", armed: false, udp_active: false },
              engine: { blackout: true },
            },
          });
        }
        if (url.includes("/api/setup/validate-patch")) {
          return json({ ok: true, errors: [] });
        }
        if (url.includes("/api/setup/raw-tester/")) {
          return json({
            ok: true,
            state: {
              raw_tester: { active: url.includes("/enter"), nonzero_channels: 0 },
            },
          });
        }
        return json({ ok: true });
      }),
    );
  });

  function mountSetup(options: Parameters<typeof mount>[1] = {}) {
    const globalOpts = (options?.global ?? {}) as {
      stubs?: Record<string, unknown>;
      provide?: Record<string | symbol, unknown>;
    };
    return mount(SetupView, {
      ...options,
      global: {
        ...globalOpts,
        stubs: {
          RouterLink: { template: '<a class="router-link-stub"><slot /></a>' },
          StageSimulator: true,
          ...(globalOpts.stubs ?? {}),
        },
      },
    });
  }

  it("renders ten setup steps and hardware badge", async () => {
    const wrapper = mountSetup();
    await flushPromises();
    expect(wrapper.text()).toContain("Налаштування");
    expect(wrapper.findAll(".wizard-step")).toHaveLength(10);
    expect(wrapper.text()).toContain("Не перевірено на обладнанні");
    expect(wrapper.text()).toContain("Mock / Art-Net");
    expect(wrapper.text()).toContain("Зведення готовності");
    expect(wrapper.text()).toContain("Відкрити налаштування каналів");
  });

  it("renders Arm controls with blockers while Mock", async () => {
    const wrapper = mountSetup();
    await flushPromises();
    expect(wrapper.text()).toContain("Увімкнути Arm");
    expect(wrapper.text()).toContain("Вимкнути Arm");
    expect(wrapper.text()).toContain("Source none");
    expect(wrapper.text()).toContain("Wire");
    expect(wrapper.text()).toContain("Runtime має бути Art-Net");

    const enableArm = wrapper
      .findAll("button")
      .find((btn) => btn.text() === "Увімкнути Arm");
    expect(enableArm).toBeTruthy();
    expect(enableArm!.attributes("disabled")).toBeDefined();

    const disableArm = wrapper
      .findAll("button")
      .find((btn) => btn.text() === "Вимкнути Arm");
    expect(disableArm).toBeTruthy();
    expect(disableArm!.attributes("disabled")).toBeDefined();
  });

  it("requires confirm before enabling Arm", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) =>
        Promise.resolve({
          ok: true,
          json: async () => body,
        });
      if (url.endsWith("/api/output/arm-blockers")) {
        return json({ ok: true, blockers: [] });
      }
      if (url.endsWith("/api/setup/readiness")) {
        return json({
          transport_preferred: "artnet",
          runtime_transport: "artnet",
          output_armed: false,
          artnet_network_enabled: true,
          udp_active: true,
          blackout: true,
          artnet_badge: "Не перевірено на обладнанні",
          patch_ok: true,
          fixture_count: 1,
          profiles: [],
          source_frame_sum: 0,
          source_nonzero_channels: 0,
          wire_frame_sum: 120,
          wire_nonzero_channels: 4,
          arm_blockers: [],
        });
      }
      if (url.endsWith("/api/config/app")) return json(sampleApp);
      if (url.endsWith("/api/config/patch")) return json(samplePatch);
      if (url.endsWith("/api/config/profiles")) return json(sampleProfiles);
      if (url.endsWith("/api/config/layout")) {
        return json({
          schema_version: 1,
          viewer_facing: true,
          description: "test",
          fixtures: ["par_1"],
        });
      }
      return json({ ok: true });
    });

    const wrapper = mountSetup({
      global: {
        provide: {
          [APP_STATE_KEY]: {
            output: {
              value: {
                transport: "artnet",
                udp_active: true,
                network_allowed: true,
                armed: false,
                // Safe Beam axes may leave wire non-zero — must not block Arm.
                wire_nonzero_channels: 4,
                nonzero_channels: 4,
                frame_sum: 120,
                wire_frame_sum: 120,
                source_frame_sum: 0,
                source_nonzero_channels: 0,
              },
            },
            engine: { value: { blackout: true } },
            state: { value: null },
            connection: { value: "online" },
            liveView: () => null,
            liveFrame: null,
            refreshRest: vi.fn(async () => undefined),
            setPreviewSpeed: vi.fn(),
          },
        },
      },
    });
    await flushPromises();

    const enableArm = wrapper
      .findAll("button")
      .find((btn) => btn.text() === "Увімкнути Arm");
    expect(enableArm).toBeTruthy();
    expect(enableArm!.attributes("disabled")).toBeUndefined();

    const callsBefore = fetchMock.mock.calls.length;
    await enableArm!.trigger("click");
    await flushPromises();

    expect(confirmSpy).toHaveBeenCalled();
    const confirmMsg = String(confirmSpy.mock.calls[0]?.[0] ?? "");
    expect(confirmMsg).toContain("Увімкнути Arm");
    expect(confirmMsg).toContain("Blackout");
    expect(
      fetchMock.mock.calls
        .slice(callsBefore)
        .some(([url]) => {
          const path = String(url);
          return path.endsWith("/api/output/arm") || path.includes("/api/output/arm?");
        }),
    ).toBe(false);
    confirmSpy.mockRestore();
  });

  it("enables Arm when backend blockers empty even if wire_nonzero > 0", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) =>
        Promise.resolve({ ok: true, json: async () => body });
      if (url.endsWith("/api/output/arm-blockers")) {
        return json({ ok: true, blockers: [] });
      }
      if (url.endsWith("/api/config/app")) return json(sampleApp);
      if (url.endsWith("/api/config/patch")) return json(samplePatch);
      if (url.endsWith("/api/config/profiles")) return json(sampleProfiles);
      if (url.endsWith("/api/config/layout")) {
        return json({
          schema_version: 1,
          viewer_facing: true,
          description: "test",
          fixtures: ["par_1"],
        });
      }
      if (url.endsWith("/api/setup/readiness")) {
        return json({
          runtime_transport: "artnet",
          output_armed: false,
          artnet_network_enabled: true,
          udp_active: true,
          blackout: true,
          wire_nonzero_channels: 8,
          arm_blockers: [],
          artnet_badge: "Не перевірено на обладнанні",
          patch_ok: true,
          fixture_count: 1,
          profiles: [],
        });
      }
      return json({ ok: true });
    });

    const wrapper = mountSetup({
      global: {
        provide: {
          [APP_STATE_KEY]: {
            output: {
              value: {
                transport: "artnet",
                udp_active: true,
                network_allowed: true,
                armed: false,
                wire_nonzero_channels: 8,
                nonzero_channels: 8,
              },
            },
            engine: { value: { blackout: true } },
            state: { value: null },
            connection: { value: "online" },
            liveView: () => null,
            liveFrame: null,
            refreshRest: vi.fn(async () => undefined),
            setPreviewSpeed: vi.fn(),
          },
        },
      },
    });
    await flushPromises();
    const enableArm = wrapper
      .findAll("button")
      .find((btn) => btn.text() === "Увімкнути Arm");
    expect(enableArm!.attributes("disabled")).toBeUndefined();
    expect(wrapper.text()).not.toContain("Світлові канали");
  });

  it("disables Arm and shows backend blocker reasons", async () => {
    const wrapper = mountSetup({
      global: {
        provide: {
          [APP_STATE_KEY]: {
            output: {
              value: {
                transport: "artnet",
                udp_active: true,
                network_allowed: true,
                armed: false,
                wire_nonzero_channels: 0,
                nonzero_channels: 0,
              },
            },
            engine: { value: { blackout: true } },
            state: { value: null },
            connection: { value: "online" },
            liveView: () => null,
            liveFrame: null,
            refreshRest: vi.fn(async () => undefined),
            setPreviewSpeed: vi.fn(),
          },
        },
      },
    });
    await flushPromises();
    // Default mock returns Mock transport blocker from beforeEach.
    const enableArm = wrapper
      .findAll("button")
      .find((btn) => btn.text() === "Увімкнути Arm");
    expect(enableArm!.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("Runtime має бути Art-Net");
  });

  it("refreshes arm blockers when runtime transport changes", async () => {
    const fetchMock = vi.mocked(fetch);
    let blockers: string[] = ["Runtime має бути Art-Net (у Mock Arm неможливий)"];
    const output = ref({
      transport: "mock",
      udp_active: false,
      network_allowed: false,
      armed: false,
      wire_nonzero_channels: 2,
      nonzero_channels: 2,
    });
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) =>
        Promise.resolve({ ok: true, json: async () => body });
      if (url.endsWith("/api/output/arm-blockers")) {
        return json({ ok: blockers.length === 0, blockers });
      }
      if (url.endsWith("/api/config/app")) return json(sampleApp);
      if (url.endsWith("/api/config/patch")) return json(samplePatch);
      if (url.endsWith("/api/config/profiles")) return json(sampleProfiles);
      if (url.endsWith("/api/config/layout")) {
        return json({
          schema_version: 1,
          viewer_facing: true,
          description: "test",
          fixtures: ["par_1"],
        });
      }
      if (url.endsWith("/api/setup/readiness")) {
        return json({
          runtime_transport: output.value.transport,
          output_armed: false,
          artnet_network_enabled: output.value.network_allowed,
          udp_active: output.value.udp_active,
          blackout: true,
          wire_nonzero_channels: output.value.wire_nonzero_channels,
          arm_blockers: blockers,
          artnet_badge: "Не перевірено на обладнанні",
          patch_ok: true,
          fixture_count: 1,
          profiles: [],
        });
      }
      return json({ ok: true });
    });

    const wrapper = mountSetup({
      global: {
        provide: {
          [APP_STATE_KEY]: {
            output,
            engine: { value: { blackout: true } },
            state: { value: null },
            connection: { value: "online" },
            liveView: () => null,
            liveFrame: null,
            refreshRest: vi.fn(async () => undefined),
            setPreviewSpeed: vi.fn(),
          },
        },
      },
    });
    await flushPromises();
    let enableArm = wrapper
      .findAll("button")
      .find((btn) => btn.text() === "Увімкнути Arm");
    expect(enableArm!.attributes("disabled")).toBeDefined();

    blockers = [];
    output.value = {
      transport: "artnet",
      udp_active: true,
      network_allowed: true,
      armed: false,
      wire_nonzero_channels: 2,
      nonzero_channels: 2,
    };
    await flushPromises();
    enableArm = wrapper
      .findAll("button")
      .find((btn) => btn.text() === "Увімкнути Arm");
    expect(enableArm!.attributes("disabled")).toBeUndefined();
  });

  it("can open raw tester step", async () => {
    const wrapper = mountSetup();
    await flushPromises();
    const steps = wrapper.findAll(".wizard-step");
    await steps[5].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Raw DMX tester");
    expect(wrapper.text()).toContain("Вийти та скинути в нулі");
    expect(wrapper.text()).toContain("Підготовлений source-кадр");
  });

  it("shows Beam Left / Beam Right calibration cards on step 8 (not draft checkboxes)", async () => {
    const wrapper = mountSetup();
    await flushPromises();
    const steps = wrapper.findAll(".wizard-step");
    await steps[7].trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("8. Калібрація Bars / Beam");
    expect(wrapper.text()).toContain("Beam Left");
    expect(wrapper.text()).toContain("Beam Right");
    expect(wrapper.text()).toContain("pan_invert");
    expect(wrapper.text()).toContain("tilt_invert");
    expect(wrapper.text()).not.toContain("pan_invert (draft)");
    expect(wrapper.text()).not.toContain("tilt_invert (draft)");
    // Bar invert_segments UI stays as-is.
    expect(wrapper.text()).toContain("invert_segments (draft)");
    // Synced slider+number fields for offsets / home are present.
    expect(wrapper.text()).toContain("Pan offset");
    expect(wrapper.text()).toContain("Home Pan");
    expect(wrapper.text()).toContain("Home Tilt");
    expect(wrapper.text()).toContain("Не відкалібровано");
    expect(wrapper.text()).toContain("Почати калібрування руху");
  });

  it("keeps Raw session across 6→1→6 without calling exit", async () => {
    const fetchMock = vi.mocked(fetch);
    const appState = {
      output: {
        value: {
          transport: "mock",
          udp_active: false,
          network_allowed: false,
          armed: false,
          source_owner: "raw_tester",
          source_frame_sum: 319,
          source_nonzero_channels: 2,
          wire_frame_sum: 0,
          wire_nonzero_channels: 0,
          frame_sum: 0,
          nonzero_channels: 0,
        },
      },
      engine: { value: { blackout: true } },
      state: {
        value: {
          raw_tester: {
            active: true,
            nonzero_channels: 2,
            frame: [64, 255, ...Array(510).fill(0)],
            prepared_channels: [
              { channel: 1, value: 64 },
              { channel: 2, value: 255 },
            ],
            universe_size: 512,
          },
        },
      },
      connection: { value: "online" },
      liveView: () => null,
      liveFrame: null,
      refreshRest: vi.fn(async () => undefined),
      setPreviewSpeed: vi.fn(),
    };
    const wrapper = mountSetup({
      global: {
        provide: { [APP_STATE_KEY]: appState },
      },
    });
    await flushPromises();

    const steps = wrapper.findAll(".wizard-step");
    await steps[5].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("CH 1 = 64");
    expect(wrapper.text()).toContain("CH 2 = 255");
    expect(wrapper.text()).toContain("активний");

    const callsBeforeNav = fetchMock.mock.calls.length;
    await steps[0].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Source RAW");
    expect(wrapper.text()).toContain("Σ319 / 2");
    expect(wrapper.text()).toContain("Σ0 / 0");

    await steps[5].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("CH 1 = 64");
    expect(wrapper.text()).toContain("CH 2 = 255");

    const exitCalls = fetchMock.mock.calls
      .slice(callsBeforeNav)
      .filter(([url]) => String(url).includes("/api/setup/raw-tester/exit"));
    expect(exitCalls).toHaveLength(0);
  });
});
