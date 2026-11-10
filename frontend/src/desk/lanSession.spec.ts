import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import App from "../App.vue";
import { loginLanDesk, probeLanSession } from "./lanSession";

describe("venue LAN session", () => {
  it("treats a missing endpoint as the loopback desk", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 404 })));
    await expect(probeLanSession()).resolves.toBe("loopback");
    vi.unstubAllGlobals();
  });

  it("asks for a login when the venue desk is closed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ lan_desk: true, authenticated: false, role: null }),
      ),
    );
    await expect(probeLanSession()).resolves.toBe("login");
    vi.unstubAllGlobals();
  });

  it("posts the password in the body and not in the address", async () => {
    const fetchMock = vi.fn(async (url: string) => Response.json({ ok: true, role: "operator" }));
    vi.stubGlobal("fetch", fetchMock);
    await loginLanDesk("hall-secret");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/lan/login");
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("hall-secret");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(String(init.body)).toContain("hall-secret");
    expect(String(init.body)).not.toContain("token");
    vi.unstubAllGlobals();
  });
});

describe("local desk gate", () => {
  it("shows the login form before the control shell", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ lan_desk: true, authenticated: false, role: null }),
      ),
    );
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Локальний пульт зали");
    expect(wrapper.text()).toContain("без інтернету");
    expect(wrapper.find("input[type='password']").exists()).toBe(true);
    expect(wrapper.find(".nav-link").exists()).toBe(false);
    vi.unstubAllGlobals();
  });
});
