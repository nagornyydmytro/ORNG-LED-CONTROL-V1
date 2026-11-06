import { defineComponent, h, nextTick, ref, shallowRef } from "vue";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import type { CloudControlTransport, CloudDeviceLiveState } from "../cloud/transport";
import { useCloudAppState } from "./useCloudAppState";

vi.mock("../api/adminCloud", () => ({
  adminGetLocationShowPrefs: vi.fn(async () => ({ prefs: null })),
  adminPutLocationShowPrefs: vi.fn(async () => ({})),
}));

vi.mock("../api/client", () => ({
  fetchPresets: vi.fn(() => new Promise(() => undefined)),
}));

vi.mock("../api/setup", () => ({
  fetchAppConfig: vi.fn(async () => ({})),
  saveAppConfig: vi.fn(async () => ({})),
}));

function fakeTransport() {
  const listeners = new Set<(state: CloudDeviceLiveState) => void>();
  const transport = {
    sourceId: "web:test",
    connect: vi.fn(async () => undefined),
    dispose: vi.fn(),
    onLiveState(cb: (state: CloudDeviceLiveState) => void) {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    onCommandAck() {
      return () => undefined;
    },
    releaseSource: vi.fn(async () => undefined),
    emit(state: CloudDeviceLiveState) {
      for (const cb of [...listeners]) cb(state);
    },
  };
  return transport;
}

function frame(presetId: string): CloudDeviceLiveState {
  return {
    online: true,
    armed: false,
    blackout: true,
    control_status: "open",
    preset_id: presetId,
  } as CloudDeviceLiveState;
}

describe("cloud location binding", () => {
  it("drops a live frame from the previous executor", async () => {
    const first = fakeTransport();
    const second = fakeTransport();
    const transport = shallowRef<CloudControlTransport | null>(first as unknown as CloudControlTransport);
    const location = ref({ id: "A", name: "A", sceneId: "scene-a", sceneName: "A" });
    let preset = "";
    const Harness = defineComponent({
      setup() {
        const api = useCloudAppState(transport, { push: vi.fn() }, location);
        return () => {
          preset = api.engine.value?.preset_id ?? "";
          return h("div");
        };
      },
    });
    const wrapper = mount(Harness);
    await nextTick();
    first.emit(frame("P01"));
    await nextTick();
    expect(preset).toBe("P01");
    location.value = { id: "B", name: "B", sceneId: "scene-b", sceneName: "B" };
    transport.value = second as unknown as CloudControlTransport;
    await nextTick();
    first.emit(frame("OLD"));
    await nextTick();
    expect(preset).not.toBe("OLD");
    second.emit(frame("P09"));
    await nextTick();
    expect(preset).toBe("P09");
    wrapper.unmount();
  });
});
