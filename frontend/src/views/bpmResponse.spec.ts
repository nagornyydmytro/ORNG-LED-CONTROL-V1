import { defineComponent, h, nextTick, provide, ref } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { APP_STATE_KEY } from "../composables/appStateKey";
import { useAppState } from "../composables/useAppState";
import { useCloudAppState } from "../composables/useCloudAppState";
import { resetBpmIntent } from "../lib/bpmIntent";
import ControlView from "./ControlView.vue";
import type { CloudControlTransport } from "../cloud/transport";
import type { AppState } from "../vite-env";

const prefsHang = vi.hoisted(() => vi.fn(() => new Promise(() => undefined)));

vi.mock("../api/adminCloud", () => ({
  adminGetLocationShowPrefs: vi.fn(async () => ({ prefs: null })),
  adminPutLocationShowPrefs: (...args: unknown[]) => prefsHang(...args),
}));

const hangs: Array<(value: Response) => void> = [];
const sent: Array<Record<string, unknown>> = [];
let socket: EventTarget | null = null;
let bpmPatch: Record<string, unknown> = {};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function deskState(bpm: Record<string, unknown> = {}): AppState {
  return {
    scene_id: "scene-a",
    location_id: "loc-a",
    capabilities: ["bpm_sync", "auto_director"],
    engine: { preset_id: null, episode_index: 0, blackout: false, face: false },
    output: { armed: false },
    bpm: {
      enabled: false,
      scope: "preset",
      mode: "smooth",
      palette: "orange",
      cast: "all",
      groups: ["bar", "par", "beam"],
      character: "rhythmic",
      intensity: "full",
      period: "auto",
      par_rhythm: false,
      accent: 0.5,
      ...bpm,
    },
  } as AppState;
}

class FakeSocket extends EventTarget {
  readyState = 1;
  constructor(url: string) {
    super();
    socket = this;
    void url;
    queueMicrotask(() => this.dispatchEvent(new Event("open")));
  }
  send() {}
  close() {
    this.dispatchEvent(new Event("close"));
  }
}

beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
  localStorage.clear();
  resetBpmIntent();
  hangs.length = 0;
  bpmPatch = {};
  sent.length = 0;
  socket = null;
  prefsHang.mockClear();
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/commands/bpm-sync")) {
        sent.push(JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);
        return new Promise<Response>((resolve) => hangs.push(resolve));
      }
      if (url.includes("/api/state")) return json(deskState(bpmPatch));
      if (url.includes("/api/presets")) return json({ presets: [] });
      if (url.includes("/api/scenes")) return json({ scenes: [] });
      if (url.includes("/api/stage/layout")) return json({ fixtures: [], zones: [] });
      if (url.includes("/api/config/app")) return json({ pad_presets: [] });
      return json({});
    }),
  );
});

function releaseHangs() {
  while (hangs.length) hangs.shift()?.(json({ state: deskState() }));
}

async function mountDesk() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/", component: { template: "<div />" } }],
  });
  const Parent = defineComponent({
    setup() {
      provide(APP_STATE_KEY, useAppState({ push() {} }));
      return () => h(ControlView);
    },
  });
  const wrapper = mount(Parent, { global: { plugins: [router] } });
  await flushPromises();
  await nextTick();
  return wrapper;
}

describe("BPM desk response", () => {
  it("paints the switch before the command resolves and keeps the last press", async () => {
    const wrapper = await mountDesk();
    const samples: number[] = [];
    const press = async (expected: string) => {
      const started = performance.now();
      await wrapper.get("[aria-label='Музика']").trigger("click");
      await nextTick();
      samples.push(performance.now() - started);
      expect(wrapper.get("[aria-label='Музика']").attributes("aria-checked")).toBe(expected);
    };
    await press("true");
    await press("false");
    await press("true");
    expect(sent.map((body) => body.enabled)).toEqual([true, false, true]);
    expect(Math.max(...samples)).toBeLessThan(50);
    hangs[0]?.(json({ state: deskState({ enabled: true }) }));
    hangs[1]?.(json({ state: deskState({ enabled: false }) }));
    await flushPromises();
    expect(wrapper.get("[aria-label='Музика']").attributes("aria-checked")).toBe("true");
    await wrapper.findAll("button").find((button) => button.text().includes("Авто"))?.trigger("click");
    await nextTick();
    expect(wrapper.findAll("button").find((button) => button.text().includes("Авто"))?.classes()).toContain("active");
    expect(sent.at(-1)?.scope).toBe("auto");
    releaseHangs();
    wrapper.unmount();
  });

  it("keeps the last ON when earlier acknowledgements match that value", async () => {
    bpmPatch = { enabled: false, revision: 0, session: "A", epoch: 2 };
    const wrapper = await mountDesk();
    const toggle = () => wrapper.get("[aria-label='Музика']");
    await toggle().trigger("click");
    await toggle().trigger("click");
    await toggle().trigger("click");
    await nextTick();
    expect(toggle().attributes("aria-checked")).toBe("true");
    const picture = (enabled: boolean, revision: number) => deskState({
      enabled,
      revision,
      session: "A",
      epoch: 2,
    });
    hangs[0]?.(json({ state: picture(true, 1) }));
    hangs[1]?.(json({ state: picture(false, 2) }));
    await flushPromises();
    expect(toggle().attributes("aria-checked")).toBe("true");
    releaseHangs();
    wrapper.unmount();
  });

  it("keeps the last ON when those pictures arrive on the websocket", async () => {
    bpmPatch = { enabled: false, revision: 0, session: "A", epoch: 2 };
    const wrapper = await mountDesk();
    const toggle = () => wrapper.get("[aria-label='Музика']");
    await toggle().trigger("click");
    await toggle().trigger("click");
    await toggle().trigger("click");
    await nextTick();
    const deliver = (enabled: boolean, revision: number) => {
      socket?.dispatchEvent(new MessageEvent("message", {
        data: JSON.stringify({
          type: "state",
          state: deskState({ enabled, revision, session: "A", epoch: 2 }),
        }),
      }));
    };
    deliver(true, 1);
    await nextTick();
    deliver(false, 2);
    await flushPromises();
    expect(toggle().attributes("aria-checked")).toBe("true");
    releaseHangs();
    wrapper.unmount();
  });

  it("keeps later palette, intensity and groups when older replies fail or arrive late", async () => {
    bpmPatch = { scope: "auto" };
    const wrapper = await mountDesk();
    await wrapper.get("[aria-label='Музика']").trigger("click");
    await nextTick();
    const palette = wrapper.get("[aria-label='Палітра']");
    const intensity = wrapper.get("[aria-label='Інтенсивність']");
    const groups = wrapper.get("[aria-label='Склад приладів']");
    const red = palette.findAll("button").find((button) => button.text().includes("Червоний"));
    const soft = intensity.findAll("button").find((button) => button.text().includes("М'яка"));
    const bars = groups.findAll("button").find((button) => button.text() === "Бари");
    const mark = sent.length;
    await red?.trigger("click");
    await soft?.trigger("click");
    await bars?.trigger("click");
    await nextTick();
    const painted = () => ({
      red: wrapper.get("[aria-label='Палітра']").findAll("button").find((button) => button.text().includes("Червоний")),
      soft: wrapper.get("[aria-label='Інтенсивність']").findAll("button").find((button) => button.text().includes("М'яка")),
      bars: wrapper.get("[aria-label='Склад приладів']").findAll("button").find((button) => button.text() === "Бари"),
    });
    expect(painted().red?.classes()).toContain("active");
    expect(painted().soft?.classes()).toContain("active");
    expect(painted().bars?.classes()).not.toContain("active");
    const bodies = sent.slice(mark);
    expect(bodies[0]?.palette).toBe("red");
    expect(bodies[1]?.palette).toBe("red");
    expect(bodies[1]?.intensity).toBe("soft");
    expect(bodies[2]?.intensity).toBe("soft");
    expect(bodies[2]?.groups).not.toContain("bar");
    hangs[mark + 2]?.(json({ state: deskState({ enabled: true, scope: "auto", palette: "red", intensity: "soft" }) }));
    hangs[mark + 1]?.(json({ detail: "old" }, 500));
    hangs[mark]?.(json({ state: deskState({ enabled: true, scope: "auto", palette: "orange", intensity: "full" }) }));
    await flushPromises();
    socket?.dispatchEvent(
      new MessageEvent("message", {
        data: JSON.stringify({
          type: "state",
          state: deskState({
            enabled: true,
            scope: "auto",
            palette: "orange",
            intensity: "full",
            groups: ["bar", "par", "beam"],
          }),
        }),
      }),
    );
    await nextTick();
    expect(painted().red?.classes()).toContain("active");
    expect(painted().soft?.classes()).toContain("active");
    expect(painted().bars?.classes()).not.toContain("active");
    releaseHangs();
    wrapper.unmount();
  });

  it("follows the executor Auto scope before any local choice exists", async () => {
    bpmPatch = { enabled: true, scope: "auto", palette: "red", locked: false };
    const wrapper = await mountDesk();
    expect(wrapper.get("[aria-label='Змінити ефект']").exists()).toBe(true);
    expect(wrapper.get("[aria-label='speed'] input").attributes("disabled")).toBeDefined();
    expect(wrapper.get("[aria-label='Режим роботи']").text()).toContain("Авто");
    expect(wrapper.findAll("button").find((button) => button.text().includes("Авто"))?.classes()).toContain("active");
    wrapper.unmount();
  });

  it("adopts a newer executor picture without resending the stale palette", async () => {
    bpmPatch = { enabled: true, scope: "auto", palette: "red", revision: 2, session: "player-a", locked: false };
    const wrapper = await mountDesk();
    socket?.dispatchEvent(new MessageEvent("message", {
      data: JSON.stringify({
        type: "state",
        state: deskState({
          enabled: true,
          scope: "auto",
          palette: "blue",
          revision: 4,
          session: "player-a",
          locked: false,
        }),
      }),
    }));
    await nextTick();
    const blue = () => wrapper.get("[aria-label='Палітра']").findAll("button").find((button) => button.text().includes("Синій"));
    const red = () => wrapper.get("[aria-label='Палітра']").findAll("button").find((button) => button.text().includes("Червоний"));
    expect(blue()?.classes()).toContain("active");
    expect(red()?.classes()).not.toContain("active");
    const mark = sent.length;
    await wrapper.get("[aria-label='Інтенсивність']").findAll("button")[0]?.trigger("click");
    await nextTick();
    expect(sent[mark]?.palette).toBe("blue");
    expect(sent[mark]?.intensity).toBe("soft");
    expect(sent[mark]?.enabled).toBe(true);
    socket?.dispatchEvent(new MessageEvent("message", {
      data: JSON.stringify({
        type: "state",
        state: deskState({ enabled: true, scope: "auto", palette: "red", revision: 2, session: "player-a" }),
      }),
    }));
    await nextTick();
    expect(blue()?.classes()).toContain("active");
    releaseHangs();
    wrapper.unmount();
  });

  it("keeps a pending choice across reload and still accepts newer untouched fields", async () => {
    bpmPatch = { enabled: true, scope: "auto", palette: "orange", revision: 1, session: "player-a" };
    const wrapper = await mountDesk();
    await wrapper.get("[aria-label='Палітра']").findAll("button").find((button) => button.text().includes("Червоний"))?.trigger("click");
    await nextTick();
    wrapper.unmount();
    resetBpmIntent();
    const again = await mountDesk();
    const red = () => again.get("[aria-label='Палітра']").findAll("button").find((button) => button.text().includes("Червоний"));
    expect(red()?.classes()).toContain("active");
    socket?.dispatchEvent(new MessageEvent("message", {
      data: JSON.stringify({
        type: "state",
        state: deskState({
          enabled: true,
          scope: "auto",
          palette: "blue",
          intensity: "soft",
          revision: 5,
          session: "player-a",
        }),
      }),
    }));
    await nextTick();
    expect(red()?.classes()).toContain("active");
    expect(again.get("[aria-label='Інтенсивність']").findAll("button")[0]?.classes()).toContain("active");
    releaseHangs();
    again.unmount();
  });

  it("finishes palette after intensity when the older ack arrives last", async () => {
    bpmPatch = {
      enabled: true,
      scope: "auto",
      palette: "orange",
      intensity: "full",
      revision: 0,
      session: "A",
      epoch: 2,
    };
    const wrapper = await mountDesk();
    const red = () => wrapper.get("[aria-label='Палітра']").findAll("button").find((button) => button.text().includes("Червоний"));
    const soft = () => wrapper.get("[aria-label='Інтенсивність']").findAll("button")[0];
    await red()?.trigger("click");
    await soft()?.trigger("click");
    await nextTick();
    const before = hangs.length;
    expect(before).toBeGreaterThanOrEqual(2);
    hangs[before - 1]?.(json({
      state: deskState({
        enabled: true,
        scope: "auto",
        palette: "red",
        intensity: "soft",
        revision: 2,
        session: "A",
        epoch: 2,
      }),
    }));
    await flushPromises();
    hangs[before - 2]?.(json({
      state: deskState({
        enabled: true,
        scope: "auto",
        palette: "red",
        intensity: "full",
        revision: 1,
        session: "A",
        epoch: 2,
      }),
    }));
    await flushPromises();
    expect(red()?.classes()).toContain("active");
    expect(soft()?.classes()).toContain("active");
    const { bpmDesk } = await import("../lib/bpmIntent");
    expect(bpmDesk("loc:loc-a").pending).toBe(false);
    socket?.dispatchEvent(new MessageEvent("message", {
      data: JSON.stringify({
        type: "state",
        state: deskState({
          enabled: true,
          scope: "auto",
          palette: "blue",
          intensity: "soft",
          revision: 3,
          session: "A",
          epoch: 2,
        }),
      }),
    }));
    await nextTick();
    const blue = wrapper.get("[aria-label='Палітра']").findAll("button").find((button) => button.text().includes("Синій"));
    expect(blue?.classes()).toContain("active");
    expect(red()?.classes()).not.toContain("active");
    releaseHangs();
    wrapper.unmount();
  });

  it("clears pending after reload by settling the stored command_id", async () => {
    bpmPatch = {
      enabled: true,
      scope: "auto",
      palette: "orange",
      revision: 1,
      session: "A",
      epoch: 2,
    };
    const first = await mountDesk();
    await first.get("[aria-label='Палітра']").findAll("button").find((button) => button.text().includes("Червоний"))?.trigger("click");
    await nextTick();
    const commandId = String(sent.at(-1)?.client_command_id ?? "");
    expect(commandId).toMatch(/^bpm-sync-/);
    first.unmount();
    // Full reload: in-flight callbacks are gone; only localStorage remains.
    hangs.length = 0;
    resetBpmIntent();
    bpmPatch = {
      enabled: true,
      scope: "auto",
      palette: "red",
      intensity: "full",
      revision: 4,
      session: "A",
      epoch: 2,
    };
    const again = await mountDesk();
    await flushPromises();
    const settleIndex = sent.findIndex((body, index) => index >= sent.length - hangs.length && body.client_command_id === commandId);
    const hangOffset = settleIndex - (sent.length - hangs.length);
    expect(hangOffset).toBeGreaterThanOrEqual(0);
    hangs[hangOffset]?.(json({
      state: deskState({
        enabled: true,
        scope: "auto",
        palette: "red",
        intensity: "full",
        revision: 4,
        session: "A",
        epoch: 2,
      }),
    }));
    await flushPromises();
    const { bpmDesk } = await import("../lib/bpmIntent");
    expect(bpmDesk("loc:loc-a").pending).toBe(false);
    expect(bpmDesk("loc:loc-a").choice.palette).toBe("red");
    const red = again.get("[aria-label='Палітра']").findAll("button").find((button) => button.text().includes("Червоний"));
    expect(red?.classes()).toContain("active");
    expect(sent.filter((body) => body.client_command_id === commandId).length).toBeGreaterThanOrEqual(2);
    releaseHangs();
    again.unmount();
  });

  it("restores the unconfirmed choice after a remount", async () => {
    const wrapper = await mountDesk();
    await wrapper.get("[aria-label='Музика']").trigger("click");
    await nextTick();
    wrapper.unmount();
    resetBpmIntent();
    const again = await mountDesk();
    expect(again.get("[aria-label='Музика']").attributes("aria-checked")).toBe("true");
    releaseHangs();
    again.unmount();
  });
});

describe("cloud BPM command", () => {
  it("sends the live command while show-prefs is still hanging", async () => {
    vi.useFakeTimers();
    const sentCloud: unknown[] = [];
    const transport = {
      kind: "cloud",
      installationId: "inst",
      sourceId: "src",
      connect: vi.fn(async () => undefined),
      dispose: vi.fn(),
      syncSceneRelease: vi.fn(async () => undefined),
      onLiveState: vi.fn(() => () => undefined),
      onCommandAck: vi.fn(() => () => undefined),
      setLiveFxSpeed: vi.fn(async () => undefined),
      setProgramSpeed: vi.fn(async () => undefined),
      setMasterBrightness: vi.fn(async () => undefined),
      sendHeadDesk: vi.fn(),
      releaseSource: vi.fn(async () => undefined),
      setBpmSync: vi.fn(async () => {
        sentCloud.push(true);
      }),
      blackout: vi.fn(),
      arm: vi.fn(),
      disarm: vi.fn(),
      selectPreset: vi.fn(),
      selectPad: vi.fn(),
      strobe: vi.fn(),
      sweep: vi.fn(),
      faceToggle: vi.fn(),
      episodeNudge: vi.fn(),
      speed: vi.fn(),
      activateScene: vi.fn(),
    } as unknown as CloudControlTransport;
    let api: ReturnType<typeof useCloudAppState> | null = null;
    const Host = defineComponent({
      setup() {
        api = useCloudAppState(
          ref(transport),
          { push: vi.fn() },
          ref({ id: "loc-a", name: "Hall", sceneId: "scene-a", sceneName: "Hall" }),
        );
        return () => h("div");
      },
    });
    const wrapper = mount(Host);
    await flushPromises();
    const pending = api?.setBpmSync({ palette: "red" });
    await flushPromises();
    expect(sentCloud).toEqual([true]);
    expect(prefsHang).toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(4500);
    await pending;
    wrapper.unmount();
    vi.useRealTimers();
  });
});
