import { defineComponent, h, nextTick, ref } from "vue";
import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { useKeyboardPad } from "./useKeyboardPad";

function mountPad(enabled = ref(true)) {
  const calls: string[] = [];
  const Harness = defineComponent({
    setup() {
      useKeyboardPad({
        enabled: () => enabled.value,
        onState: () => undefined,
        dispatch: async (event) => {
          calls.push(`${event.type}:${event.code}`);
        },
      });
      return () => h("div");
    },
  });
  const wrapper = mount(Harness);
  return { calls, wrapper, enabled };
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("keyboard pad", () => {
  it("lets Escape close a dialog without Face", async () => {
    const { calls, wrapper } = mountPad();
    await nextTick();
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.append(dialog);
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "Escape", bubbles: true }));
    expect(calls).toEqual([]);
    dialog.remove();
    window.dispatchEvent(new KeyboardEvent("keyup", { code: "Escape", bubbles: true }));
    expect(calls).toEqual([]);
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "Escape", bubbles: true }));
    expect(calls).toContain("keydown:Escape");
    wrapper.unmount();
  });

  it("does not drive lights from a text field and still releases a held key", async () => {
    const { calls, wrapper } = mountPad();
    await nextTick();
    const input = document.createElement("input");
    document.body.append(input);
    input.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyZ", bubbles: true }));
    expect(calls).toEqual([]);
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyZ", bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keyup", { code: "KeyZ", bubbles: true }));
    expect(calls).toContain("keydown:KeyZ");
    expect(calls).toContain("keyup:KeyZ");
    wrapper.unmount();
  });

  it("leaves browser zoom shortcuts alone outside a text field", async () => {
    const { calls, wrapper } = mountPad();
    await nextTick();
    const event = new KeyboardEvent("keydown", {
      code: "Minus",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(calls.some((item) => item.includes("Minus"))).toBe(false);
    wrapper.unmount();
  });

  it("ignores ctrl+wheel outside the active pad", async () => {
    const { calls, wrapper } = mountPad();
    await nextTick();
    window.dispatchEvent(
      new WheelEvent("wheel", { deltaY: -40, ctrlKey: true, bubbles: true, cancelable: true }),
    );
    expect(calls.some((item) => item.includes("Zoom"))).toBe(false);
    const pad = document.createElement("div");
    pad.setAttribute("data-light-pad", "");
    document.body.append(pad);
    pad.dispatchEvent(
      new WheelEvent("wheel", { deltaY: -40, ctrlKey: true, bubbles: true, cancelable: true }),
    );
    expect(calls).toContain("keydown:ZoomIn");
    wrapper.unmount();
  });

  it("releases holds when the page asks and when the pad is disabled", async () => {
    const enabled = ref(true);
    const { calls, wrapper } = mountPad(enabled);
    await nextTick();
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyZ", bubbles: true }));
    window.dispatchEvent(new CustomEvent("orng-release-holds"));
    expect(calls).toContain("keyup:KeyZ");
    calls.length = 0;
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyX", bubbles: true }));
    enabled.value = false;
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    expect(calls).toContain("keyup:KeyX");
    expect(calls.some((item) => item.startsWith("keydown:Space"))).toBe(false);
    wrapper.unmount();
  });

  it("keeps Space while blackout is on, because the pad is not gated by blackout", async () => {
    const blackout = ref(true);
    const { calls, wrapper } = mountPad(ref(true));
    await nextTick();
    expect(blackout.value).toBe(true);
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    expect(calls).toContain("keydown:Space");
    wrapper.unmount();
  });
});
