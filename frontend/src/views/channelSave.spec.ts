import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import { APP_STATE_KEY } from "../composables/appStateKey";
import { installScenePort, sceneSyncDetail, sceneSyncPhase, useLocalSceneBackend, type ScenePort } from "../runtime/scenePort";
import ChannelsView from "./ChannelsView.vue";

const channel = {
  local: 1,
  role: "whole_color",
  label: "Color",
  notes: "keep me",
  curve: "linear",
  segment_index: null,
  fixed_value: null,
  palette: { off: 0, red: 16, lime: 9 },
  control_values: { open: 10, custom: 3 },
};

const sharedProfile = {
  id: "par_7ch_provisional",
  label: "PAR 7ch",
  kind: "par",
  footprint: 7,
  notes: "profile note",
  hardware_verified: false,
  segment_count: 0,
  channels: [channel, { local: 2, role: "dimmer", label: "Dimmer", notes: "second", fixed_value: null }],
};

const otherProfile = {
  id: "bar_15ch",
  label: "Bar",
  kind: "bar",
  footprint: 15,
  segment_count: 8,
  channels: [{ local: 1, role: "dimmer", label: "Bar dimmer" }],
};

const patch = {
  fixtures: [
    { id: "par_1", profile_id: "par_7ch_provisional", label: "PAR 1", kind: "par", start_address: 1 },
    { id: "par_2", profile_id: "par_7ch_provisional", label: "PAR 2", kind: "par", start_address: 8 },
    { id: "bar_1", profile_id: "bar_15ch", label: "Bar 1", kind: "bar", start_address: 20 },
  ],
};

const roles = {
  roles: [
    { role: "unused", label: "Не використовується / завжди 0" },
    { role: "dimmer", label: "Master Dimmer" },
    { role: "fixed", label: "Фіксоване значення" },
    { role: "whole_color", label: "Whole Fixture Color / Palette" },
  ],
};

describe("channel mapping save", () => {
  const saved: { profile?: Record<string, unknown> } = {};
  let publishImpl: () => Promise<void> = async () => {
    sceneSyncPhase.value = "published";
  };

  beforeEach(() => {
    sessionStorage.clear();
    saved.profile = undefined;
    publishImpl = async () => {
      sceneSyncPhase.value = "published";
    };
    useLocalSceneBackend();
    installScenePort({
      async fetchPatch() {
        return structuredClone(patch);
      },
      async fetchProfiles() {
        return {
          par_7ch_provisional: structuredClone(sharedProfile),
          bar_15ch: structuredClone(otherProfile),
        };
      },
      async fetchChannelRoles() {
        return roles.roles;
      },
      async saveProfile(_id, profile) {
        saved.profile = structuredClone(profile);
        return { ok: true } as never;
      },
      async publishForLocation() {
        await publishImpl();
      },
    } as unknown as ScenePort);
  });

  afterEach(() => {
    installScenePort(null);
    sceneSyncPhase.value = "idle";
    sceneSyncDetail.value = null;
  });

  function mountView() {
    return mount(ChannelsView, {
      global: {
        provide: {
          [APP_STATE_KEY]: {
            state: ref({ scene_id: "loc-test" }),
            refreshRest: vi.fn(),
          },
        },
      },
    });
  }

  async function pick(wrapper: ReturnType<typeof mountView>, label: string) {
    const button = wrapper.findAll("button").find((item) => item.text() === label);
    expect(button).toBeTruthy();
    await button!.trigger("click");
    await flushPromises();
  }

  it("saves an unchanged profile with notes and extra values, then another reader sees it", async () => {
    const wrapper = await mountView();
    await flushPromises();
    await pick(wrapper, "PAR");
    expect(wrapper.text()).toContain("Профіль par_7ch_provisional змінить: PAR 1, PAR 2");
    await pick(wrapper, "Зберегти мапінг");
    expect(saved.profile?.notes).toBe("profile note");
    expect(saved.profile?.hardware_verified).toBe(false);
    expect(saved.profile?.channels).toEqual(sharedProfile.channels);
    expect(wrapper.text()).toContain("Опубліковано. На приладі ще не застосовано");
    expect(wrapper.text()).not.toContain("застосовано на приладі");

    const reader = {
      async fetchProfiles() {
        return { par_7ch_provisional: structuredClone(saved.profile!) };
      },
    };
    expect((await reader.fetchProfiles()).par_7ch_provisional.channels).toEqual(sharedProfile.channels);
    wrapper.unmount();
  });

  it("does not discard an edit when switching fixture or when publish fails", async () => {
    const wrapper = await mountView();
    await flushPromises();
    await pick(wrapper, "PAR");
    const select = wrapper.find("select");
    await select.setValue("dimmer");
    await pick(wrapper, "LED Bar");
    expect(wrapper.text()).toContain("Є незбережені зміни профілю");
    expect((wrapper.find("select").element as HTMLSelectElement).value).toBe("dimmer");

    publishImpl = async () => {
      sceneSyncPhase.value = "failed";
      sceneSyncDetail.value = "Публікація не вдалася";
      throw new Error("network down");
    };
    await pick(wrapper, "Зберегти мапінг");
    expect(wrapper.text()).toContain("Публікація не вдалася");
    expect(wrapper.text()).not.toContain("Опубліковано. На приладі ще не застосовано");
    expect((wrapper.find("select").element as HTMLSelectElement).value).toBe("dimmer");
    const written = saved.profile?.channels as Array<Record<string, unknown>>;
    expect(written[0]?.role).toBe("dimmer");
    expect(written[0]?.notes).toBe("keep me");
    expect(written[0]?.palette).toEqual(channel.palette);
    expect(written[1]?.notes).toBe("second");
    wrapper.unmount();
  });

  it("shows a bad fixed value beside the field and does not turn it into 0", async () => {
    const wrapper = await mountView();
    await flushPromises();
    await pick(wrapper, "PAR");
    await wrapper.find("select").setValue("fixed");
    const input = wrapper.get("input");
    await input.setValue("nope");
    await pick(wrapper, "Зберегти мапінг");
    expect(wrapper.text()).toContain("цілим 0..255");
    expect((input.element as HTMLInputElement).value).toBe("nope");
    expect(saved.profile).toBeUndefined();
    expect((wrapper.findAll("select")[1]?.element as HTMLSelectElement | undefined)?.value ?? "dimmer").toBe("dimmer");
    wrapper.unmount();
  });
});
