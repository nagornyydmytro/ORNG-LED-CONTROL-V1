import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { APP_STATE_KEY } from "../composables/appStateKey";
import ChannelsView from "./ChannelsView.vue";

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
    },
    {
      id: "bar_1",
      profile_id: "bar_15ch",
      label: "Bar 1",
      start_address: 93,
      kind: "bar",
    },
    {
      id: "beam_1",
      profile_id: "beam_13ch",
      label: "Beam 1",
      start_address: 29,
      kind: "beam",
    },
  ],
};

function profileChannels(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    local: i + 1,
    role: i === 0 ? "dimmer" : "unused",
    label: i === 0 ? "Master Dimmer" : "Не призначено",
  }));
}

const sampleProfiles = {
  par_7ch_provisional: {
    id: "par_7ch_provisional",
    label: "PAR 7ch",
    kind: "par",
    footprint: 7,
    channels: profileChannels(7),
  },
  bar_15ch: {
    id: "bar_15ch",
    label: "LED Bar 15ch",
    kind: "bar",
    footprint: 15,
    channels: profileChannels(15),
  },
  beam_13ch: {
    id: "beam_13ch",
    label: "Beam Head 13ch",
    kind: "beam",
    footprint: 13,
    channels: profileChannels(13),
  },
};

const sampleRoles = {
  roles: [
    { role: "unused", label: "Не використовується / завжди 0" },
    { role: "dimmer", label: "Master Dimmer" },
    { role: "fixed", label: "Фіксоване значення" },
    { role: "whole_color", label: "Whole Fixture Color / Palette" },
  ],
};

describe("ChannelsView", () => {
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
        if (url.endsWith("/api/config/patch")) return json(samplePatch);
        if (url.endsWith("/api/config/profiles")) return json(sampleProfiles);
        if (url.endsWith("/api/setup/channel-roles")) return json(sampleRoles);
        if (url.includes("/api/setup/fixture-channel-test/")) {
          return json({ ok: true, state: {} });
        }
        if (url.includes("/api/config/profiles/")) {
          return json({ ok: true, state: {} });
        }
        return json({ ok: true });
      }),
    );
  });

  async function mountView() {
    const wrapper = mount(ChannelsView, {
      global: {
        provide: {
          [APP_STATE_KEY]: {
            refreshRest: vi.fn(async () => undefined),
          },
        },
      },
    });
    await flushPromises();
    return wrapper;
  }

  it("shows 7 channels for PAR type", async () => {
    const wrapper = await mountView();
    const typeSelect = wrapper.find("select");
    await typeSelect.setValue("par");
    await flushPromises();
    expect(wrapper.findAll(".channel-row")).toHaveLength(7);
    expect(wrapper.find('[data-testid="channel-list"]').exists()).toBe(true);
  });

  it("shows 15 channels for LED Bar type", async () => {
    const wrapper = await mountView();
    await wrapper.find("select").setValue("bar");
    await flushPromises();
    expect(wrapper.findAll(".channel-row")).toHaveLength(15);
  });

  it("shows 13 channels for Beam Head type", async () => {
    const wrapper = await mountView();
    await wrapper.find("select").setValue("beam");
    await flushPromises();
    expect(wrapper.findAll(".channel-row")).toHaveLength(13);
  });

  it("renders Ukrainian controls and fixture mapping", async () => {
    const wrapper = await mountView();
    expect(wrapper.text()).toContain("Налаштування каналів");
    await wrapper.find("select").setValue("par");
    await flushPromises();

    const fixtureSelect = wrapper.findAll("select")[1];
    await fixtureSelect.setValue("par_1");
    await flushPromises();

    expect(wrapper.text()).toContain("→ DMX 1");
    expect(wrapper.text()).toContain("Почати тестування");
    expect(wrapper.text()).toContain("Зберегти мапінг");
    expect(wrapper.text()).toContain("Функція каналу");
  });
});
