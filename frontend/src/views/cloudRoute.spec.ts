import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import CloudView from "./CloudView.vue";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("local pairing panel", () => {
  it("shows the existing unpaired panel and does not start pairing", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const path = String(url);
      if (path.includes("/api/cloud/status")) {
        return {
          ok: true,
          json: async () => ({
            agent: {
              enabled: false,
              connected: false,
              url: null,
              installation_id: null,
              last_error: null,
              has_token: false,
            },
            token_path: "",
            sync: { status: "local" },
            admin_url: null,
            device_url: null,
          }),
        };
      }
      return { ok: false, statusText: "missing", text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(CloudView);
    await flushPromises();
    expect(wrapper.text()).toContain("Парування");
    expect(wrapper.text()).toContain("Хмара");
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("pair"))).toBe(false);
    wrapper.unmount();
  });
});
