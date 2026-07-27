import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HardwareBadge from "./HardwareBadge.vue";
import SetupView from "../../views/SetupView.vue";

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
            artnet_badge: "Не перевірено на обладнанні",
            patch_ok: true,
            fixture_count: 1,
            profiles: [],
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

  it("renders ten setup steps and hardware badge", async () => {
    const wrapper = mount(SetupView);
    await flushPromises();
    expect(wrapper.text()).toContain("Налаштування");
    expect(wrapper.findAll(".wizard-step")).toHaveLength(10);
    expect(wrapper.text()).toContain("Не перевірено на обладнанні");
    expect(wrapper.text()).toContain("Mock / Art-Net");
    expect(wrapper.text()).toContain("Зведення готовності");
  });

  it("can open raw tester step", async () => {
    const wrapper = mount(SetupView);
    await flushPromises();
    const steps = wrapper.findAll(".wizard-step");
    await steps[5].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Raw DMX tester");
    expect(wrapper.text()).toContain("Починає з нулів");
  });
});
