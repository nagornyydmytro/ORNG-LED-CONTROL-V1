import { describe, expect, it } from "vitest";
import { router } from "../router";

describe("operator routes", () => {
  it("keeps an unknown address visible instead of a blank home", async () => {
    await router.push("/not-a-page");
    await router.isReady();
    expect(router.currentRoute.value.name).toBe("not-found");
  });

  it("sends /cloud and /settings to their existing pages", async () => {
    await router.push("/cloud");
    await router.isReady();
    expect(router.currentRoute.value.name).toBe("cloud");
    expect(router.currentRoute.value.path).toBe("/cloud");
    await router.push("/settings");
    await router.isReady();
    expect(router.currentRoute.value.path).toBe("/setup");
  });

  it("opens a direct address and walks back", async () => {
    await router.push("/presets");
    await router.isReady();
    expect(router.currentRoute.value.name).toBe("presets");
    await router.push("/channels");
    await router.isReady();
    expect(router.currentRoute.value.name).toBe("channels");
    expect(router.options.history.state.back).toBe("/presets");
    expect(router.options.history.state.current).toBe("/channels");
  });
});
