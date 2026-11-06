import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AppShell from "./AppShell.vue";

function mountShell() {
  return mount(AppShell, {
    global: {
      stubs: {
        RouterLink: { template: "<a><slot /></a>", props: ["to"] },
        RouterView: true,
        ToastStack: true,
        SceneSelector: true,
      },
    },
    props: {
      connection: "online",
      transport: "artnet",
      armed: true,
      outputError: null,
      locationName: "ORNG BAR",
      sceneId: "orng-bar",
      sceneName: "ORNG BAR",
      blackout: true,
      showLogout: true,
    },
  });
}

describe("AppShell phone menu", () => {
  it("opens from the burger and closes from the backdrop and Escape", async () => {
    const wrapper = mountShell();
    const burger = wrapper.get("button.topbar__burger");
    expect(burger.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find(".shell-menu").exists()).toBe(false);

    await burger.trigger("click");
    expect(burger.attributes("aria-expanded")).toBe("true");
    const menu = wrapper.get(".shell-menu");
    expect(menu.text()).toContain("ORNG BAR");
    expect(menu.text().indexOf("ORNG BAR")).toBeLessThan(menu.text().indexOf("Пресети"));
    expect(menu.text()).toContain("Пресети");
    expect(menu.text()).toContain("Світла");
    expect(menu.text()).toContain("Темна");
    expect(menu.text()).toContain("Вийти");
    expect(menu.get(".shell-menu__logout").exists()).toBe(true);
    expect(wrapper.get("aside .sidebar__logout").text()).toContain("Вийти");

    await wrapper.get("button.shell-menu__backdrop").trigger("click");
    expect(wrapper.find(".shell-menu").exists()).toBe(false);
    expect(burger.attributes("aria-expanded")).toBe("false");

    await burger.trigger("click");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shell-menu").exists()).toBe(false);
  });
});
